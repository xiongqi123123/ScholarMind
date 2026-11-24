"""Accounts: scrypt password hashes, signed session cookies, hashed personal tokens."""

import base64
import hashlib
import hmac
import logging
import secrets
import time
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import Token, User, engine, get_session

log = logging.getLogger(__name__)

COOKIE = "sm_session"
SESSION_SECONDS = 30 * 24 * 3600
MIN_PASSWORD = 8
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"  # a plain shape check: something@something.tld
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    _, salt, digest = stored.split("$")
    actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), **_SCRYPT)
    return hmac.compare_digest(actual, base64.b64decode(digest))


_DUMMY_HASH = hash_password("dummy")


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.username == username))
    # Hash even for unknown users so response time does not reveal which names exist.
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    return user if user and ok else None


def users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.id)))


class Taken(Exception):
    """Another account has this username or email; the argument says which."""


def add_user(
    session: Session, username: str, password: str, email: str | None = None, is_admin=False
) -> User:
    """A new account, without a token; Taken if the username or email is another's."""
    user = User(
        username=username, email=email, password_hash=hash_password(password), is_admin=is_admin
    )
    try:
        with session.begin_nested():
            session.add(user)
    except IntegrityError as e:  # users_username_key or users_email_key
        which = "email" if "email" in (e.orig.diag.constraint_name or "") else "username"
        raise Taken(which) from None
    return user


class Refused(Exception):
    """A change to accounts that is not allowed; the message says why, for the admin to read."""


def _locked(session: Session, admin: User, user_id: int) -> User | None:
    """The account, locked, after locking every admin's row first: changes to accounts go one
    after the other, so two admins demoting or deleting each other cannot both succeed. The
    admin making the change must still be one now; with the account changed never their own,
    there is an admin left afterwards.
    """
    admins = select(User.id).where(User.is_admin).order_by(User.id).with_for_update()
    if admin.id not in session.scalars(admins).all():
        raise Refused("You are no longer an admin")
    return session.get(User, user_id, with_for_update=True, populate_existing=True)


def set_admin(session: Session, admin: User, user_id: int, is_admin: bool) -> User | None:
    """Promote or demote an account; None if there is none with that id."""
    if user_id == admin.id and not is_admin:
        raise Refused("You cannot remove your own admin role")
    user = _locked(session, admin, user_id)
    if user and user.is_admin != is_admin:
        user.is_admin = is_admin
        change = "made %s an admin" if is_admin else "removed the admin role of %s"
        log.info("%s " + change, admin.username, user.username)
    return user


def delete_user(session: Session, admin: User, user_id: int) -> bool:
    """Delete an account: its stars, collections, notes and tokens go with it; its tag and
    topic edits and settings stay, without the author (the foreign keys' ON DELETE rules).
    False if there is none with that id.
    """
    if user_id == admin.id:
        raise Refused("You cannot delete your own account")
    user = _locked(session, admin, user_id)
    if user is None:
        return False
    session.execute(delete(User).where(User.id == user_id))
    log.info("%s deleted the account %s", admin.username, user.username)
    return True


def reset_password(session: Session, admin: User, user_id: int, password: str) -> bool:
    """Set another account's password, which signs it out everywhere; its tokens stay. False
    if there is none with that id.
    """
    if user_id == admin.id:
        raise Refused("Change your own password on your profile, where your current one is asked")
    user = _locked(session, admin, user_id)
    if user is None:
        return False
    user.password_hash = hash_password(password)
    log.info("%s reset the password of %s", admin.username, user.username)
    return True


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(
    session: Session, user: User, name: str, expires_at: datetime | None = None
) -> tuple[Token, str]:
    """A new personal token of the user's, and its plain value: returned once, never stored."""
    value = "sm_" + secrets.token_urlsafe(32)
    token = Token(user=user, name=name, token_hash=hash_token(value), expires_at=expires_at)
    session.add(token)
    session.flush()
    return token, value


def tokens(session: Session, user_id: int) -> list[Token]:
    """The user's personal tokens, newest first, expired ones included."""
    query = select(Token).where(Token.user_id == user_id)
    return list(session.scalars(query.order_by(Token.created_at.desc(), Token.id.desc())))


def revoke_token(session: Session, user_id: int, token_id: int) -> bool:
    """Delete one of the user's tokens; False if the user has no token with that id."""
    query = delete(Token).where(Token.id == token_id, Token.user_id == user_id)
    return session.execute(query).rowcount > 0


def _sign(payload: str) -> str:
    return hmac.new(settings().secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _password_mark(user: User) -> str:
    """Changes whenever the password is set, so a new password ends every older session."""
    return _sign(user.password_hash)[:16]


# ponytail: stateless sessions, logout only clears the cookie; a password change revokes all of
# a user's sessions, rotating SECRET_KEY everyone's. Add a session table for per-session logout.
def make_session_cookie(user: User) -> str:
    payload = f"{user.id}.{int(time.time()) + SESSION_SECONDS}.{_password_mark(user)}"
    return f"{payload}.{_sign(payload)}"


def read_session_cookie(session: Session, value: str) -> User | None:
    payload, _, signature = value.rpartition(".")
    if not hmac.compare_digest(signature.encode(), _sign(payload).encode()):
        return None
    parts = payload.split(".")
    if len(parts) != 3:  # signed by an older version without the password mark
        return None
    user_id, expires, mark = parts
    user = session.get(User, int(user_id)) if int(expires) > time.time() else None
    return user if user and hmac.compare_digest(mark, _password_mark(user)) else None


_USE_GRAIN = timedelta(minutes=1)


def bearer_user(session: Session, authorization: str | None) -> User | None:
    """The owner of the personal token in an `Authorization: Bearer <token>` header value;
    None for an unknown or expired token. Marks the token used, at most once a minute.
    """
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    stale = or_(Token.last_used_at.is_(None), Token.last_used_at < func.now() - _USE_GRAIN)
    found = session.execute(
        select(User, Token.id, stale)
        .join(Token, Token.user_id == User.id)
        .where(Token.token_hash == hash_token(value))
        .where(or_(Token.expires_at.is_(None), Token.expires_at > func.now()))
    ).first()
    if found is None:
        return None
    user, token_id, is_stale = found
    if is_stale:
        # In a short transaction of its own: no row lock held for the rest of the request, no
        # rollback with it. The condition keeps concurrent first uses to one write.
        with engine().begin() as connection:
            connection.execute(
                update(Token).where(Token.id == token_id, stale).values(last_used_at=func.now())
            )
    return user


def current_user(request: Request, session: Annotated[Session, Depends(get_session)]) -> User:
    """Accept either `Authorization: Bearer <personal token>` or the session cookie."""
    user = None
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token:
        user = bearer_user(session, request.headers["authorization"])
    elif cookie := request.cookies.get(COOKIE):
        user = read_session_cookie(session, cookie)
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return user


def session_user(request: Request, session: Annotated[Session, Depends(get_session)]) -> User:
    """Only the session cookie, never a personal token: changing the password or managing the
    tokens takes someone signed in, not an agent holding a token.
    """
    cookie = request.cookies.get(COOKIE)
    user = read_session_cookie(session, cookie) if cookie else None
    if user is None:
        raise HTTPException(403, "Sign in on the web page for this; a personal token cannot")
    return user
