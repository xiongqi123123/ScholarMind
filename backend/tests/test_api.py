import email.message
import hashlib
import http.client
import io
import json
import logging
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import arxiv_lock_free
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.orm import Session
from starlette.requests import Request

from scholarmind import (
    agent,
    api,
    arxiv,
    auth,
    brief,
    chat,
    cli,
    ingest,
    jobs,
    pi,
    queries,
    translate,
)
from scholarmind.api import app
from scholarmind.config import config, settings
from scholarmind.db import (
    Analysis,
    ChatMessage,
    Collection,
    CollectionPaper,
    Figure,
    Note,
    Paper,
    PaperText,
    PaperTopic,
    Setting,
    SyncState,
    Tag,
    Token,
    Topic,
    TopicMerge,
    User,
    UserPaper,
    engine,
)
from scholarmind.ingest import upsert_papers


def _paper(arxiv_id, title, categories, day, abstract=""):
    return {
        "arxiv_id": arxiv_id,
        "arxiv_version": 1,
        "title": title,
        "abstract": abstract,
        "primary_category": categories[0],
        "categories": categories,
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}v1",
        "published_at": datetime(2026, 11, day, 12, tzinfo=UTC),
    }


@pytest.fixture
def token(session):
    with Session(engine()) as s, s.begin():
        user = User(username="alice", password_hash=auth.hash_password("pw-alice"))
        s.add(user)
        _, token = auth.new_token(s, user, "test")
        upsert_papers(
            s,
            [
                _paper(
                    "2611.00001",
                    "Diffusion policies for grasping",
                    ["cs.RO", "cs.LG"],
                    18,
                    "We study robot manipulation with diffusion models.",
                ),
                _paper(
                    "2611.00002",
                    "Open-vocabulary segmentation",
                    ["cs.CV"],
                    19,
                    "Segment anything with text prompts.",
                ),
                _paper("2611.00003", "Visual odometry at night", ["cs.CV", "cs.RO"], 20),
            ],
        )
    return token


@pytest.fixture
def client(schema):
    return TestClient(app)


def test_password_hash():
    stored = auth.hash_password("secret")
    assert stored.startswith("scrypt$") and "secret" not in stored
    assert auth.verify_password("secret", stored)
    assert not auth.verify_password("Secret", stored)


def test_session_cookie(session, monkeypatch):
    session.add(User(username="bob", password_hash=auth.hash_password("pw-bob-123")))
    session.commit()
    user = session.scalar(select(User))
    cookie = auth.make_session_cookie(user)
    assert auth.read_session_cookie(session, cookie) == user
    assert auth.read_session_cookie(session, cookie.replace(f"{user.id}.", "99.", 1)) is None
    unmarked = f"{user.id}.9999999999"  # validly signed, by the version before password marks
    assert auth.read_session_cookie(session, f"{unmarked}.{auth._sign(unmarked)}") is None
    user.password_hash = auth.hash_password("pw-bob-123")  # set again, even to the same value
    assert auth.read_session_cookie(session, cookie) is None
    cookie = auth.make_session_cookie(user)
    monkeypatch.setattr(time, "time", lambda: 10**12)
    assert auth.read_session_cookie(session, cookie) is None  # expired


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_requires_credentials(client, token):
    assert client.get("/api/papers").status_code == 401
    assert client.get("/api/papers/1").status_code == 401
    bad = {"Authorization": "Bearer sm_wrong"}
    assert client.get("/api/papers", headers=bad).status_code == 401
    client.cookies.set(auth.COOKIE, "1.9999999999.forged")
    assert client.get("/api/papers").status_code == 401


def test_login_logout(client, token):
    assert client.post("/api/login", json={"username": "alice", "password": "x"}).status_code == 401
    assert client.post("/api/login", json={"username": "bob", "password": "x"}).status_code == 401
    r = client.post("/api/login", json={"username": "alice", "password": "pw-alice"})
    assert r.status_code == 200
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie
    assert client.get("/api/papers").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/papers").status_code == 401


def test_list_and_detail(client, token):
    client.headers["Authorization"] = f"Bearer {token}"

    def ids(**params):
        body = client.get("/api/papers", params=params).json()
        return body["total"], [p["arxiv_id"] for p in body["items"]]

    assert ids() == (3, ["2611.00003", "2611.00002", "2611.00001"])
    assert ids(category="cs.RO") == (2, ["2611.00003", "2611.00001"])
    assert ids(date_from="2026-11-19", date_to="2026-11-19") == (1, ["2611.00002"])
    assert ids(q="diffusion manipulation") == (1, ["2611.00001"])
    assert ids(q="segmentation", category="cs.RO") == (0, [])
    assert ids(page=2, page_size=2) == (3, ["2611.00001"])
    assert client.get("/api/papers", params={"page_size": 101}).status_code == 422

    paper_id = client.get("/api/papers").json()["items"][0]["id"]
    detail = client.get(f"/api/papers/{paper_id}").json()
    assert detail["arxiv_id"] == "2611.00003" and detail["categories"] == ["cs.CV", "cs.RO"]
    assert client.get("/api/papers/999999").status_code == 404


def test_date_filter_time_zone(client, token):
    client.headers["Authorization"] = f"Bearer {token}"
    late = _paper("2611.00004", "Late night", ["cs.CV"], 19)
    late["published_at"] = datetime(2026, 11, 19, 18, tzinfo=UTC)  # 02:00 on the 20th in Beijing
    with Session(engine()) as s, s.begin():
        upsert_papers(s, [late])

    def ids(day, **params):
        params |= {"date_from": day, "date_to": day}
        return [p["arxiv_id"] for p in client.get("/api/papers", params=params).json()["items"]]

    assert ids("2026-11-20") == ids("2026-11-20", tz="UTC") == ["2611.00003"]
    assert ids("2026-11-20", tz="Asia/Shanghai") == ["2611.00003", "2611.00004"]
    assert ids("2026-11-19", tz="Asia/Shanghai") == ["2611.00002"]
    assert ids("2026-11-19", tz="America/Los_Angeles") == ["2611.00004", "2611.00002"]
    for bad in ["Mars/Olympus", "../../etc/passwd", ""]:
        assert client.get("/api/papers", params={"tz": bad}).status_code == 422


def test_change_password(client, token):
    client.post("/api/login", json={"username": "alice", "password": "pw-alice"})
    other = TestClient(app)  # another browser, signed in before the change
    other.cookies.set(auth.COOKIE, client.cookies.get(auth.COOKIE))
    assert other.get("/api/me").status_code == 200
    url = "/api/me/password"
    wrong = {"old_password": "nope", "new_password": "long enough"}
    assert client.post(url, json=wrong).status_code == 400
    short = {"old_password": "pw-alice", "new_password": "short"}
    assert client.post(url, json=short).status_code == 422
    r = client.post(url, json={"old_password": "pw-alice", "new_password": "new-pw-alice"})
    assert r.status_code == 200 and auth.COOKIE in r.cookies
    assert client.get("/api/me").json()["username"] == "alice"  # this browser got a new cookie
    assert other.get("/api/me").status_code == 401  # every older session is over

    def login(password):
        return TestClient(app).post("/api/login", json={"username": "alice", "password": password})

    assert login("pw-alice").status_code == 401 and login("new-pw-alice").status_code == 200
    bearer = {"Authorization": f"Bearer {token}"}
    assert TestClient(app).get("/api/me", headers=bearer).status_code == 200  # token unaffected


def test_avatar(client, token):
    """A member picks a preset avatar, by session or by token alike; null is the default."""
    assert client.patch("/api/me", json={"avatar": "cat-red"}).status_code == 401
    client.post("/api/login", json={"username": "alice", "password": "pw-alice"})
    assert client.get("/api/me").json()["avatar"] is None  # never chosen
    r = client.patch("/api/me", json={"avatar": "cat-red"})
    assert r.status_code == 200 and r.json() == client.get("/api/me").json()
    assert r.json()["avatar"] == "cat-red"
    agent = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    assert agent.patch("/api/me", json={"avatar": "owl-teal"}).json()["avatar"] == "owl-teal"
    assert client.get("/api/me").json()["avatar"] == "owl-teal"
    for bad in [
        {},
        {"avatar": ""},
        {"avatar": "cat"},
        {"avatar": "Cat-red"},
        {"avatar": "cat-red-x"},
        {"avatar": "cat-red\n"},
        {"avatar": "a-" + "b" * 31},
        {"avatar": 7},
    ]:
        assert client.patch("/api/me", json=bad).status_code == 422, bad
    assert client.patch("/api/me", json={"avatar": None}).json()["avatar"] is None
    assert client.get("/api/me").json()["avatar"] is None


def test_tokens(client, session):
    """A signed-in member keeps several named tokens, each may expire and is revoked on its own;
    a token can neither manage tokens nor change the password.
    """
    session.add(User(username="dora", password_hash=auth.hash_password("pw-dora-123")))
    session.commit()
    url = "/api/me/tokens"
    assert client.get(url).status_code == 401
    client.post("/api/login", json={"username": "dora", "password": "pw-dora-123"})
    assert client.get(url).json() == []

    def use(value):
        bearer = {"Authorization": f"Bearer {value}"}
        return TestClient(app).get("/api/me", headers=bearer).status_code

    def listed(name):
        return next(t for t in client.get(url).json() if t["name"] == name)

    soon = datetime.now(UTC) + timedelta(days=30)
    r = client.post(url, json={"name": " laptop ", "expires_at": soon.isoformat()})
    assert r.status_code == 201 and r.json()["value"].startswith("sm_")
    laptop, laptop_value = r.json()["token"], r.json()["value"]
    assert laptop.keys() == {"id", "name", "created_at", "expires_at", "last_used_at"}
    assert laptop["name"] == "laptop" and laptop["last_used_at"] is None
    assert datetime.fromisoformat(laptop["expires_at"]) == soon
    agent = client.post(url, json={"name": "agent", "expires_at": None}).json()
    assert agent["token"]["expires_at"] is None  # never
    assert client.get(url).json() == [agent["token"], laptop]  # newest first

    # Using a token marks it, at most once a minute.
    assert use(laptop_value) == 200
    used = listed("laptop")["last_used_at"]
    assert used and use(laptop_value) == 200 and listed("laptop")["last_used_at"] == used
    with Session(engine()) as s, s.begin():
        earlier = func.now() - timedelta(minutes=2)
        s.execute(update(Token).where(Token.name == "laptop").values(last_used_at=earlier))
    earlier = listed("laptop")["last_used_at"]
    assert use(laptop_value) == 200
    assert datetime.fromisoformat(listed("laptop")["last_used_at"]) > datetime.fromisoformat(
        earlier
    )

    # An expired token is refused like an unknown one; it is still listed.
    with Session(engine()) as s, s.begin():
        s.execute(update(Token).where(Token.name == "laptop").values(expires_at=func.now()))
    assert use(laptop_value) == 401 and use(agent["value"]) == 200 and use("sm_wrong") == 401
    assert [t["name"] for t in client.get(url).json()] == ["agent", "laptop"]

    # Revoking one leaves the others working.
    erin = User(username="erin", password_hash=auth.hash_password("pw-erin-123"))
    session.add(erin)
    theirs, theirs_value = auth.new_token(session, erin, "theirs")
    session.commit()
    assert client.delete(f"{url}/{theirs.id}").status_code == 404  # another member's
    assert client.delete(f"{url}/{agent['token']['id']}").status_code == 204
    assert use(agent["value"]) == 401 and use(theirs_value) == 200
    assert client.delete(f"{url}/{agent['token']['id']}").status_code == 404  # gone
    assert [t["name"] for t in client.get(url).json()] == ["laptop"]

    too_long = {"name": "x" * 65}
    past = {"name": "a", "expires_at": "2020-01-01T00:00:00Z"}
    naive = {"name": "a", "expires_at": soon.replace(tzinfo=None).isoformat()}  # no offset
    for bad in [
        {},
        {"name": ""},
        {"name": "   "},
        too_long,
        past,
        naive,
        {"name": "a", "expires_at": "soon"},
    ]:
        assert client.post(url, json=bad).status_code == 422, bad
    assert client.post(url, json={"name": "x" * 64}).status_code == 201
    assert client.post("/api/me/token").status_code == 404  # replaced by /api/me/tokens

    token_caller = TestClient(app, headers={"Authorization": f"Bearer {theirs_value}"})
    assert token_caller.get(url).status_code == 403
    assert token_caller.post(url, json={"name": "more"}).status_code == 403
    assert token_caller.delete(f"{url}/{theirs.id}").status_code == 403
    change = {"old_password": "pw-erin-123", "new_password": "pw-erin-456"}
    assert token_caller.post("/api/me/password", json=change).status_code == 403
    assert use(theirs_value) == 200 and auth.authenticate(session, "erin", "pw-erin-123")


def test_my_notes(client, token):
    """All of one's notes, newest first, a page at a time, each with its paper."""
    assert client.get("/api/notes").status_code == 401
    client.headers["Authorization"] = f"Bearer {token}"
    client.post("/api/papers/1/notes", json={"content": "first"})
    loose = _user_note(token, "idea")
    client.post("/api/papers/3/notes", json={"content": "third"})
    with Session(engine()) as s, s.begin():
        bob = User(username="bob", password_hash=auth.hash_password("pw-bob-123"))
        s.add(bob)
        _, bob_token = auth.new_token(s, bob, "test")
    as_bob = {"Authorization": f"Bearer {bob_token}"}
    client.post("/api/papers/1/notes", json={"content": "Bob's"}, headers=as_bob)

    body = client.get("/api/notes").json()
    assert (body["total"], body["page"], body["page_size"]) == (3, 1, 20)
    assert [(n["content"], n["paper_id"], n["paper_title"]) for n in body["items"]] == [
        ("third", 3, "Visual odometry at night"),
        ("idea", None, None),
        ("first", 1, "Diffusion policies for grasping"),
    ]
    assert body["items"][1]["id"] == loose and body["items"][0]["created_at"]
    page = client.get("/api/notes", params={"page": 2, "page_size": 2}).json()
    assert page["total"] == 3 and [n["content"] for n in page["items"]] == ["first"]
    assert client.get("/api/notes", headers=as_bob).json()["total"] == 1
    assert client.get("/api/notes", params={"page_size": 101}).status_code == 422
    with Session(engine()) as s, s.begin():  # a note outlives its paper
        s.execute(delete(Paper).where(Paper.id == 3))
    first = client.get("/api/notes").json()["items"][0]
    assert (first["content"], first["paper_id"], first["paper_title"]) == ("third", None, None)


def _user_note(token, content):
    """A note on no paper, as MCP saves them (REST notes are always on a paper)."""
    with Session(engine()) as s, s.begin():
        user_id = s.scalar(select(Token.user_id).where(Token.token_hash == auth.hash_token(token)))
        return queries.add_note(s, user_id, content).id


def _web_admin(session, client) -> str:
    """An admin, signed in on the web with `client`; returns the admin's personal token."""
    admin = User(username="root", password_hash=auth.hash_password("pw-root-123"), is_admin=True)
    session.add(admin)
    _, value = auth.new_token(session, admin, "test")
    session.commit()
    assert client.post(
        "/api/login", json={"username": "root", "password": "pw-root-123"}
    ).is_success
    return value


def _callers(token, admin_token) -> dict[str, TestClient]:
    """Callers that must not reach admin pages: nobody, a member, an admin's token."""
    member = TestClient(app)
    member.post("/api/login", json={"username": "alice", "password": "pw-alice"})
    return {
        "nobody": TestClient(app),
        "member": member,
        "member's token": TestClient(app, headers={"Authorization": f"Bearer {token}"}),
        "admin's token": TestClient(app, headers={"Authorization": f"Bearer {admin_token}"}),
    }


def test_admin_users(client, session, token):
    """An admin signed in on the web creates accounts, members' unless is_admin; nobody else
    can, not even with the admin's token.
    """
    admin_token = _web_admin(session, client)
    url, new = (
        "/api/admin/users",
        {"username": "erin", "email": "Erin@Lab.org ", "password": "pw-erin-123"},
    )
    for who, caller in _callers(token, admin_token).items():
        expected = 401 if who == "nobody" else 403
        assert caller.get(url).status_code == expected, who
        assert caller.post(url, json=new).status_code == expected, who

    r = client.post(url, json=new | {"username": " erin "})
    assert r.status_code == 201
    erin = r.json()
    assert erin.keys() == {"id", "username", "email", "is_admin", "created_at"}
    assert (erin["username"], erin["email"], erin["is_admin"]) == ("erin", "erin@lab.org", False)
    listed = client.get(url).json()
    assert [u["username"] for u in listed] == ["alice", "root", "erin"] and listed[2] == erin
    other = TestClient(app)
    assert other.post("/api/login", json={"username": "erin", "password": "pw-erin-123"}).is_success
    me = other.get("/api/me").json()
    assert (me["username"], me["email"], me["is_admin"]) == ("erin", "erin@lab.org", False)
    assert other.get("/api/me/tokens").json() == []  # no token made for it

    taken = client.post(url, json=new | {"email": "someone@lab.org"})
    assert taken.status_code == 409 and "username" in taken.json()["detail"]
    taken = client.post(url, json=new | {"username": "erin2", "email": "ERIN@lab.org"})
    assert taken.status_code == 409 and "email" in taken.json()["detail"]
    for bad in [
        {"username": ""},
        {"username": "erin smith"},
        {"username": "erin/1"},
        {"username": "e" * 65},
        {"email": "erin"},
        {"email": "erin@lab"},
        {"email": "erin x@lab.org"},
        {"email": "a@b.c" + "d" * 250},
        {"password": "short"},
    ]:
        assert client.post(url, json=new | {"username": "frank"} | bad).status_code == 422, bad
    for field in new:
        partial = {k: v for k, v in new.items() if k != field}
        assert client.post(url, json=partial).status_code == 422, field
    assert len(client.get(url).json()) == 3
    gail = {
        "username": "gail",
        "email": "gail@lab.org",
        "password": "pw-gail-123",
        "is_admin": True,
    }
    assert client.post(url, json=gail).json()["is_admin"] is True


def test_admin_manages_accounts(client, session, token, caplog):
    """An admin promotes, demotes, resets the password of and deletes other accounts, which
    lose access at once. Never their own account, which takes their old password elsewhere.
    Deleting takes what was the member's own; what they did for everyone stays, authorless.
    """
    admin_token = _web_admin(session, client)
    url = "/api/admin/users"
    bob = client.post(
        url, json={"username": "bob", "email": "bob@lab.org", "password": "pw-bob-123"}
    )
    bob_id, root_id = (
        bob.json()["id"],
        session.scalar(select(User.id).where(User.username == "root")),
    )
    calls = [
        ("PATCH", f"{url}/{bob_id}", {"is_admin": True}),
        ("DELETE", f"{url}/{bob_id}", None),
        ("PUT", f"{url}/{bob_id}/password", {"password": "pw-new-1234"}),
    ]
    for who, caller in _callers(token, admin_token).items():
        for method, path, body in calls:
            status = caller.request(method, path, json=body).status_code
            assert status == (401 if who == "nobody" else 403), (who, method, path)

    def signed_in(password):
        browser = TestClient(app)
        browser.post("/api/login", json={"username": "bob", "password": password})
        return browser

    as_bob = signed_in("pw-bob-123")
    assert as_bob.get(url).status_code == 403
    with caplog.at_level(logging.INFO, logger="scholarmind.auth"):
        r = client.patch(f"{url}/{bob_id}", json={"is_admin": True})
        assert r.status_code == 200 and r.json() == client.get(url).json()[2]
        assert r.json()["is_admin"] is True and as_bob.get(url).status_code == 200  # at once
        # What Bob does as an admin and a member: some for everyone, some his own.
        with Session(engine()) as s, s.begin():
            topic = Topic(domain="robotics", name="Soft Grippers", source="model")
            s.add(topic)
            s.flush()
            topic_id = topic.id
            merge = dict(source_id=0, source={}, target_id=topic_id, target_name="Soft Grippers")
            s.add(TopicMerge(**merge, merged_by="bob", reason="same", papers=0))
        assert as_bob.put("/api/admin/agent/prompts/report", json={"text": "Write it."}).is_success
        assert as_bob.post(
            "/api/papers/1/tags", json={"kind": "keyword", "value": "bob's"}
        ).is_success
        assert as_bob.post("/api/papers/1/topics", json={"topic_id": topic_id}).is_success
        as_bob.put("/api/papers/2/star")
        mine = as_bob.post("/api/collections", json={"name": "Bob's"}).json()["id"]
        as_bob.put(f"/api/collections/{mine}/papers/3")
        as_bob.post("/api/papers/1/notes", json={"content": "Bob's note"})
        bob_token = as_bob.post("/api/me/tokens", json={"name": "agent"}).json()["value"]
        as_bob_agent = TestClient(app, headers={"Authorization": f"Bearer {bob_token}"})

        r = client.patch(f"{url}/{bob_id}", json={"is_admin": False})
        assert r.json()["is_admin"] is False and as_bob.get(url).status_code == 403  # same cookie
        assert (
            client.put(f"{url}/{bob_id}/password", json={"password": "pw-new-1234"}).status_code
            == 204
        )
        assert as_bob.get("/api/me").status_code == 401  # signed out everywhere
        assert as_bob_agent.get("/api/me").status_code == 200  # the token stays
        assert (
            TestClient(app)
            .post("/api/login", json={"username": "bob", "password": "pw-bob-123"})
            .status_code
            == 401
        )
        as_bob = signed_in("pw-new-1234")
        assert as_bob.get("/api/me").json()["username"] == "bob"

        assert client.delete(f"{url}/{bob_id}").status_code == 204
    assert (
        as_bob.get("/api/me").status_code == 401 and as_bob_agent.get("/api/me").status_code == 401
    )
    assert [u["username"] for u in client.get(url).json()] == ["alice", "root"]
    with Session(engine()) as s:
        for table in (UserPaper, Collection, Note, Token):  # Bob's own
            assert not s.scalars(select(table).where(table.user_id == bob_id)).all(), table
        assert not s.scalars(
            select(CollectionPaper).where(CollectionPaper.collection_id == mine)
        ).all()
        tag = s.scalar(select(Tag).where(Tag.value == "bob's"))
        link = s.scalar(select(PaperTopic).where(PaperTopic.topic_id == topic_id))
        setting = s.get(Setting, "prompt:report")
        assert tag.user_id is link.user_id is setting.updated_by is None  # kept, authorless
        assert s.scalar(select(TopicMerge.merged_by)) == "bob"  # a name, not a reference
    log = [r.getMessage() for r in caplog.records]
    assert log == [
        "root made bob an admin",
        "root removed the admin role of bob",
        "root reset the password of bob",
        "root deleted the account bob",
    ]

    # Never one's own account; an unknown one is a 404.
    for method, path, body, detail in [
        ("PATCH", f"{url}/{root_id}", {"is_admin": False}, "You cannot remove your own admin role"),
        ("DELETE", f"{url}/{root_id}", None, "You cannot delete your own account"),
        ("PUT", f"{url}/{root_id}/password", {"password": "pw-new-1234"},
         "Change your own password on your profile, where your current one is asked"),
    ]:  # fmt: skip
        r = client.request(method, path, json=body)
        assert r.status_code == 409 and r.json() == {"detail": detail}, method
    assert client.patch(f"{url}/{root_id}", json={"is_admin": True}).json()["is_admin"] is True
    assert client.patch(f"{url}/999", json={"is_admin": True}).status_code == 404
    assert client.delete(f"{url}/999").status_code == 404
    assert client.put(f"{url}/999/password", json={"password": "pw-new-1234"}).status_code == 404
    assert client.patch(f"{url}/{bob_id}", json={}).status_code == 422
    assert client.put(f"{url}/{root_id}/password", json={"password": "short"}).status_code == 422


def test_admins_cannot_both_demote_each_other(session):
    """Two admins demoting (or deleting) each other at the same moment: the second waits for
    the first, then finds it is no longer an admin, so there is always one left.
    """
    for name in ("ann", "ben"):
        session.add(User(username=name, password_hash="x", is_admin=True))
    session.commit()
    ann, ben = session.scalars(select(User.id).order_by(User.id)).all()

    def demote(actor_id, user_id):
        with Session(engine()) as s, s.begin():
            return auth.set_admin(s, s.get(User, actor_id), user_id, False).username

    with ThreadPoolExecutor(1) as pool, Session(engine()) as first, first.begin():
        assert auth.set_admin(first, first.get(User, ann), ben, False).username == "ben"
        second = pool.submit(demote, ben, ann)  # Ben, who still is an admin when he asks
        time.sleep(0.5)
        assert not second.done()  # waits for Ann's change
    with pytest.raises(auth.Refused, match="You are no longer an admin"):
        second.result()
    session.expire_all()
    assert session.scalars(select(User.username).where(User.is_admin)).all() == ["ann"]


def test_admin_agent(client, session, token, monkeypatch, caplog):
    """An admin signed in on the web sets the model API key with the base URL (kept encrypted,
    never shown) and edits prompts; nobody else can, not even with the admin's token. Existing
    analyses are shown as before after a prompt changes.
    """
    admin_token = _web_admin(session, client)
    url = "/api/admin/agent"
    base = config().analysis.base_url
    calls = [
        ("GET", url, None),
        ("POST", f"{url}/probe", {}),
        ("PUT", f"{url}/connection", {"base_url": base, "api_key": "sk-test-0123456789"}),
        ("DELETE", f"{url}/connection", None),
        ("PUT", f"{url}/prompts/report", {"text": "Write it."}),
        ("DELETE", f"{url}/prompts/report", None),
    ]
    for who, caller in _callers(token, admin_token).items():
        for method, path, body in calls:
            status = caller.request(method, path, json=body).status_code
            assert status == (401 if who == "nobody" else 403), (who, method, path)

    def key_state():
        body = client.get(url).json()
        return body["api_key_set"], body["api_key_hint"], body["api_key_source"]

    def pi_key():
        with Session(engine()) as s:
            return agent.api_key(s)

    monkeypatch.setattr(settings(), "llm_api_key", "env-key-abcdefgh")
    assert key_state() == (True, "…efgh", "env") and pi_key() == "env-key-abcdefgh"
    secret = "sk-web-0123456789wxyz"
    with caplog.at_level(logging.INFO, logger="scholarmind.agent"):
        r = client.put(f"{url}/connection", json={"base_url": base, "api_key": f"  {secret} "})
    assert r.json() == {
        "api_key_set": True,
        "api_key_hint": "…wxyz",
        "api_key_source": "web",
        "base_url": {"value": base, "source": "web"},
    }
    assert pi_key() == secret  # what the next pi run gets
    with Session(engine()) as s:
        stored = s.get(Setting, "llm_api_key")
        assert secret not in stored.value and stored.updated_by == s.scalar(
            select(User.id).where(User.username == "root")
        )
    assert caplog.messages == [f"root set the model connection to {base} with a new API key"]
    assert secret not in client.get(url).text
    short = client.put(f"{url}/connection", json={"base_url": base, "api_key": "short-key"})
    assert short.json()["api_key_hint"] is None
    for bad in ({}, {"api_key": secret}, {"base_url": base, "api_key": "   "},
                {"base_url": base, "api_key": "k" * 513}):  # fmt: skip
        assert client.put(f"{url}/connection", json=bad).status_code == 422, bad

    # SECRET_KEY changed (which signs everyone out too): the stored key cannot be read, so jobs
    # use the environment's.
    secret_key = settings().secret_key
    monkeypatch.setattr(settings(), "secret_key", "another-secret")
    with caplog.at_level(logging.WARNING, logger="scholarmind.agent"), Session(engine()) as s:
        state = {"api_key_set": True, "api_key_hint": "…efgh", "api_key_source": "env"}
        assert agent.api_key_state(s) == state and pi_key() == "env-key-abcdefgh"
    assert "cannot be read" in caplog.text
    monkeypatch.setattr(settings(), "secret_key", secret_key)
    assert key_state() == (True, None, "web")  # "short-key", readable again
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="scholarmind.agent"):
        r = client.delete(f"{url}/connection")
    assert r.json() == {
        "api_key_set": True,
        "api_key_hint": "…efgh",
        "api_key_source": "env",
        "base_url": {"value": base, "source": "config"},
    }
    assert caplog.messages == [
        "root removed the model connection set on the web; config.yaml's base URL and "
        "LLM_API_KEY again"
    ]
    monkeypatch.setattr(settings(), "llm_api_key", "")
    assert key_state() == (False, None, None)
    removed = [f"{url}/api-key", f"{url}/model/base_url"]
    for method, path in [(method, path) for path in removed for method in ("PUT", "DELETE")]:
        r = client.request(method, path, json={"api_key": secret, "value": base})
        assert r.status_code in (404, 405), (method, path)  # removed: the connection does both

    # Prompts: the default files, an edited text with its own version, back to the default.
    prompts = client.get(url).json()["prompts"]
    keys = ["brief", "report", "assess", "topic-review", "chat", "translate"]
    assert [p["key"] for p in prompts] == keys
    assert prompts[4]["version"] == "chat.v1" and "{{LANGUAGE}}" in prompts[4]["text"]
    assert prompts[5]["version"] == "translate.v1" and "{{LANGUAGE}}" in prompts[5]["text"]
    report = prompts[1]
    assert (report["version"], report["modified"]) == ("report.v2", False)
    assert report["text"] == report["default_text"] == agent.default_prompt("report")
    text = report["default_text"] + "\n多写一些。"
    r = client.put(f"{url}/prompts/report", json={"text": text})
    version = "report.v2+" + hashlib.sha256(text.encode()).hexdigest()[:8]
    assert r.json() == report | {"version": version, "text": text, "modified": True}
    assert client.get(url).json()["prompts"][1] == r.json()
    with Session(engine()) as s:
        assert agent.prompt(s, "report") == (version, text)  # what the next report job uses

    default = agent.default_prompt("brief")
    no_vocabulary = {"text": default.replace("{{VOCABULARY}}", "")}
    r = client.put(f"{url}/prompts/brief", json=no_vocabulary)
    assert (
        r.status_code == 422 and r.json()["detail"] == "The brief prompt must keep {{VOCABULARY}}"
    )
    r = client.put(f"{url}/prompts/brief", json={"text": "Tag these papers."})
    assert r.json()["detail"] == "The brief prompt must keep {{VOCABULARY}}, {{LANGUAGE}}"
    assert client.put(f"{url}/prompts/brief", json={"text": default + "\n"}).status_code == 200
    r = client.put(f"{url}/prompts/chat", json={"text": "Answer."})
    assert r.json()["detail"] == "The chat prompt must keep {{LANGUAGE}}"
    r = client.put(f"{url}/prompts/translate", json={"text": "Translate."})
    assert r.json()["detail"] == "The translate prompt must keep {{LANGUAGE}}"
    assert client.put(f"{url}/prompts/report", json={"text": ""}).status_code == 422
    assert client.put(f"{url}/prompts/report", json={"text": "x" * 100_001}).status_code == 422
    assert client.put(f"{url}/prompts/cooking", json={"text": "x"}).status_code == 404
    assert client.delete(f"{url}/prompts/cooking").status_code == 404

    # An analysis made before the edit is shown as before.
    with Session(engine()) as s, s.begin():
        result = {"version": 2, "markdown": "## 方法\n\n旧解读。", "pdf_url": ""}
        s.add(Analysis(paper_id=1, level="report", model="m", prompt_version="report.v2",
                       result=result, usage={}))  # fmt: skip
    detail = TestClient(app, headers={"Authorization": f"Bearer {token}"}).get("/api/papers/1")
    assert detail.json()["report"]["markdown"] == "## 方法\n\n旧解读。"

    r = client.delete(f"{url}/prompts/report")
    assert r.json() == report  # the default again
    assert client.delete(f"{url}/prompts/report").json() == report  # nothing to delete: fine


def test_admin_model(client, session, token, caplog):
    """An admin signed in on the web overrides config.yaml's model names, one by one; nobody
    else can. A brief model set there leaves the report model alone.
    """
    admin_token = _web_admin(session, client)
    url = "/api/admin/agent/model"
    for who, caller in _callers(token, admin_token).items():
        for method, body in (("PUT", {"value": "web-brief"}), ("DELETE", None)):
            status = caller.request(method, f"{url}/brief_model", json=body).status_code
            assert status == (401 if who == "nobody" else 403), (who, method)

    analysis = config().analysis
    from_config = {
        name: {"value": getattr(analysis, name), "source": "config"} for name in agent.MODEL
    }
    assert client.get("/api/admin/agent").json()["model"] == from_config
    with caplog.at_level(logging.INFO, logger="scholarmind.agent"):
        r = client.put(f"{url}/brief_model", json={"value": " web-brief "})
        assert r.json() == {"value": "web-brief", "source": "web"}
        model = client.get("/api/admin/agent").json()["model"]
        assert model["report_model"] == from_config["report_model"]  # still config.yaml's
        assert client.put(f"{url}/report_model", json={"value": "web-report"}).is_success
        with Session(engine()) as s:  # what the next model call uses
            assert agent.model(s) == {
                "base_url": analysis.base_url,
                "brief_model": "web-brief",
                "report_model": "web-report",
            }
            assert s.get(Setting, "model:brief_model").value == "web-brief"  # plain text
        assert client.delete(f"{url}/brief_model").json() == from_config["brief_model"]
        assert client.delete(f"{url}/brief_model").json() == from_config["brief_model"]  # again
    assert [r.getMessage() for r in caplog.records] == [
        "root set brief_model to web-brief",
        "root set report_model to web-report",
        "root removed brief_model web-brief set on the web; config.yaml again",
    ]

    for name, value in [
        ("brief_model", ""),
        ("brief_model", "   "),
        ("report_model", "deepseek flash"),
        ("report_model", "m" * 129),
    ]:
        r = client.put(f"{url}/{name}", json={"value": value})
        assert r.status_code == 422 and name in r.json()["detail"], (name, value)
    assert client.put(f"{url}/report_model", json={}).status_code == 422
    assert client.put(f"{url}/report_model", json={"value": "m" * 128}).is_success
    assert client.put(f"{url}/temperature", json={"value": "1"}).status_code == 404
    assert client.delete(f"{url}/temperature").status_code == 404


def test_admin_connection(client, session, token, monkeypatch):
    """The base URL and API key change together. Given no key, the key in effect stays and goes
    only to the host it is used with: another path there is fine; another host, port or scheme
    needs its own key, so the deployment's key (never shown) cannot be sent anywhere.
    """
    _web_admin(session, client)
    monkeypatch.setattr(settings(), "llm_api_key", "env-key-abcdefgh")
    url, base = "/api/admin/agent/connection", "https://api.example.com/v1"
    assert config().analysis.base_url == base

    def in_effect():
        with Session(engine()) as s:
            return agent.model(s)["base_url"], agent.api_key(s)

    for elsewhere in (
        "https://llm.example.org/v1",
        "https://api.example.com:8443/v1",
        "http://api.example.com/v1",
    ):
        r = client.put(url, json={"base_url": elsewhere})
        assert r.status_code == 422, elsewhere
        assert r.json() == {"detail": "A different host needs its own API key"}
    assert in_effect() == (base, "env-key-abcdefgh")
    r = client.put(url, json={"base_url": " https://API.example.com:443/v2 "})
    assert r.json() == {
        "api_key_set": True,
        "api_key_hint": "…efgh",
        "api_key_source": "env",
        "base_url": {"value": "https://API.example.com:443/v2", "source": "web"},
    }
    assert in_effect() == ("https://API.example.com:443/v2", "env-key-abcdefgh")  # key kept

    llm = {"base_url": "https://llm.example.org/v1", "api_key": "sk-llm-0123456789"}
    r = client.put(url, json=llm)
    web = {"value": llm["base_url"], "source": "web"}
    assert r.json()["base_url"] == web and r.json()["api_key_source"] == "web"
    settings_page = client.get("/api/admin/agent").json()
    assert settings_page["model"]["base_url"] == web and settings_page["api_key_source"] == "web"
    assert in_effect() == (llm["base_url"], llm["api_key"])
    assert client.put(url, json={"base_url": base}).status_code == 422  # nor the web's key
    for bad in (
        "llm.example.org/v1",  # no scheme
        "ftp://llm.example.org",
        "https://",  # no host
        "http://[::1",
        "https://llm.example.org:port/v1",
        "https://x.org/" + "p" * 2040,
    ):
        r = client.put(url, json={"base_url": bad, "api_key": "sk-other-0123456789"})
        assert r.status_code == 422 and r.json()["detail"].startswith("base_url must be"), bad
    assert in_effect() == (llm["base_url"], llm["api_key"])  # nothing half written

    r = client.delete(url)
    assert r.json()["base_url"] == {"value": base, "source": "config"}
    assert r.json()["api_key_source"] == "env" and in_effect() == (base, "env-key-abcdefgh")


def test_probe(client, session, token, monkeypatch, caplog):
    """The models the provider lists, sorted, with the base URL and key given or in effect. A
    failure is a 502 saying what happened, never what the provider answered; the key is never
    shown or logged.
    """
    _web_admin(session, client)
    monkeypatch.setattr(settings(), "llm_api_key", "env-key-abcdefgh")
    url, models = "/api/admin/agent/probe", "https://api.example.com/v1/models"
    asked, upstream = [], {}
    listed = json.dumps({"object": "list", "data": [{"id": "m-b"}, {"id": "m-a"}]}).encode()

    class Opener:  # what agent._direct does with the provider, faked
        def open(self, request, timeout):
            asked.append((request.full_url, request.get_header("Authorization"), timeout))
            answer = upstream["answer"]
            if isinstance(answer, BaseException):
                raise answer
            return io.BytesIO(answer) if isinstance(answer, bytes) else answer

    def probe(**body):
        asked.clear()
        return client.post(url, json=body)

    monkeypatch.setattr(agent, "_direct", Opener())
    upstream["answer"] = listed
    caplog.set_level(logging.DEBUG)
    r = probe()
    assert r.status_code == 200 and r.json() == {"models": ["m-a", "m-b"]}
    assert asked == [(models, "Bearer env-key-abcdefgh", 15)]  # what is in effect
    assert probe(base_url="https://api.example.com/v2").json()["models"] == ["m-a", "m-b"]
    assert asked == [("https://api.example.com/v2/models", "Bearer env-key-abcdefgh", 15)]
    other = {"base_url": "https://llm.example.org/v1/", "api_key": "sk-llm-0123456789"}
    assert probe(**other).status_code == 200
    assert asked == [("https://llm.example.org/v1/models", "Bearer sk-llm-0123456789", 15)]
    r = probe(base_url="https://llm.example.org/v1")
    assert r.status_code == 422 and r.json() == {"detail": "A different host needs its own API key"}
    assert asked == []  # the key went nowhere
    assert probe(base_url="file:///etc").status_code == 422 and asked == []

    secret = b"secret-page"
    unauthorized = urllib.error.HTTPError(
        models, 401, "Unauthorized", email.message.Message(), io.BytesIO(secret)
    )
    clock = [0.0]

    class Trickle(io.BytesIO):
        def read1(self, size=-1):
            clock[0] += 5
            return super().read1(1)

    for answer, detail in [
        (unauthorized, f"401 from {models}"),
        (
            urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")),
            f"Could not reach {models}: [Errno 61] Connection refused",
        ),
        (b"<html>" + secret + b"</html>", f"{models} did not answer with a model list"),
        (b'{"models": ["' + secret + b'"]}', f"{models} did not answer with a model list"),
        (b'{"data": [{"id": 7}]}', f"{models} did not answer with a model list"),
        (
            b'{"data": [], "pad": "' + b"x" * 2**20 + b'"}',
            f"{models} did not answer with a model list",
        ),
        (http.client.BadStatusLine(secret.decode()), f"{models} did not answer with a model list"),
        (Trickle(listed), f"{models} took longer than 15 s"),
        (urllib.error.URLError(TimeoutError("timed out")), f"{models} took longer than 15 s"),
    ]:
        upstream["answer"], clock[0] = answer, 0.0
        fake_time = SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: None)
        monkeypatch.setattr(agent, "time", fake_time)
        monkeypatch.setattr(arxiv, "time", fake_time)
        r = probe()
        assert (r.status_code, r.json()) == (502, {"detail": detail}), answer
        assert secret.decode() not in r.text

    monkeypatch.setattr(settings(), "llm_api_key", "")
    upstream["answer"] = listed
    r = probe()
    assert r.status_code == 409 and r.json() == {"detail": "No API key is set"} and asked == []
    assert "env-key-abcdefgh" not in caplog.text and "sk-llm-0123456789" not in caplog.text


def test_first_admin(client, session):
    """A new deployment's first account is an admin made on the web page, signed in at once.
    Once there is any account, nobody can make one this way.
    """
    url = "/api/setup/admin"
    assert client.get("/api/setup/status").json() == {"needs_admin": True}
    new = {"username": "root", "password": "pw-root-123", "email": None}
    for bad in [{"username": "ro ot"}, {"password": "short"}, {"email": "root@lab"}]:
        assert client.post(url, json=new | bad).status_code == 422, bad
    r = client.post(url, json=new | {"username": " root "})
    assert r.status_code == 200 and r.json() == {"username": "root", "is_admin": True}
    assert "httponly" in r.headers["set-cookie"].lower()
    me = client.get("/api/me").json()
    assert (me["username"], me["email"], me["is_admin"]) == ("root", None, True)
    assert client.get("/api/admin/setup").status_code == 200  # an admin signed in on the web
    assert client.get("/api/setup/status").json() == {"needs_admin": False}

    other = TestClient(app)
    eve = {"username": "eve", "password": "pw-eve-1234", "email": "eve@lab.org"}
    r = other.post(url, json=eve)
    assert r.status_code == 409 and "set-cookie" not in r.headers
    assert other.get("/api/me").status_code == 401
    assert session.scalars(select(User.username)).all() == ["root"]


def test_only_one_first_admin(session):
    """Two first admins at the same moment: the second waits for the first, then finds it."""
    body = {"username": "ben", "password": "pw-ben-1234"}
    with ThreadPoolExecutor(1) as pool, Session(engine()) as first, first.begin():
        assert auth.add_first_admin(first, "ann", "pw-ann-1234").username == "ann"
        second = pool.submit(TestClient(app).post, "/api/setup/admin", json=body)
        time.sleep(0.5)
        assert not second.done()  # waits for Ann's
    assert second.result().status_code == 409
    assert session.scalars(select(User.username)).all() == ["ann"]


def test_admin_setup(client, session, token):
    """An admin marks the first-run setup done, once and for all; the web app leads admins
    through it while it is not and the library is empty. Nobody else sees or marks it.
    """
    admin_token = _web_admin(session, client)
    for who, caller in _callers(token, admin_token).items():
        expected = 401 if who == "nobody" else 403
        assert caller.get("/api/admin/setup").status_code == expected, who
        assert caller.put("/api/admin/setup/done").status_code == expected, who
    assert client.get("/api/admin/setup").json() == {"done": False, "papers": 3}
    for _ in range(2):  # again changes nothing
        assert client.put("/api/admin/setup/done").status_code == 204
    assert client.get("/api/admin/setup").json() == {"done": True, "papers": 3}


def test_admin_arxiv(client, session, token, monkeypatch):
    """An admin sets the arXiv categories the sync harvests instead of config.yaml's, until
    they remove them, and queues a sync now; nobody else can, not even with the admin's token.
    """
    admin_token = _web_admin(session, client)
    url, harvested = "/api/admin/arxiv", []

    def harvest(category, since, until):
        harvested.append(category)
        yield [], date(2026, 11, 22)

    monkeypatch.setattr(arxiv, "harvest", harvest)
    monkeypatch.setattr(jobs, "enqueue_sync", lambda: SimpleNamespace(id="job-1"))
    calls = [("GET", url, None), ("PUT", url, {"categories": ["cs.LG"]}), ("DELETE", url, None)]
    for who, caller in _callers(token, admin_token).items():
        expected = 401 if who == "nobody" else 403
        for method, path, body in [*calls, ("POST", "/api/admin/sync", None)]:
            assert caller.request(method, path, json=body).status_code == expected, (who, method)

    configured = {"categories": ["cs.CV", "cs.RO"], "source": "config"}
    assert client.get(url).json() == configured
    chosen = [" cs.LG ", "math.OC", "hep-th", "cs.LG", "cond-mat.mes-hall"]
    r = client.put(url, json={"categories": chosen})
    web = ["cs.LG", "math.OC", "hep-th", "cond-mat.mes-hall"]  # stripped, repeats dropped
    assert r.status_code == 200 and r.json() == {"categories": web, "source": "web"}
    assert client.get(url).json() == r.json()
    assert list(ingest.sync_arxiv()) == web and harvested == web
    many = sorted(arxiv.CATEGORIES)[:21]
    for bad in [[], many, ["cs.XY"], ["foo"], ["cs"], ["cs.cv"], ["CS.CV"], [""], ["x" * 65]]:
        assert client.put(url, json={"categories": bad}).status_code == 422, bad
    r = client.put(url, json={"categories": ["cs.CV", "cs.XY"]})  # well formed, but no such
    assert "cs.XY" in r.json()["detail"] and "cs.CV or hep-th" in r.json()["detail"]
    assert client.get(url).json()["categories"] == web

    r = client.delete(url)
    assert r.status_code == 200 and r.json() == configured
    harvested.clear()
    assert list(ingest.sync_arxiv()) == ["cs.CV", "cs.RO"] == harvested

    r = client.post("/api/admin/sync")
    assert r.status_code == 202 and r.json() == {"job": "job-1"}


def test_admin_venues(client, session, token, monkeypatch, capsys):
    """An admin sees the conferences with the editions imported, and queues the others from a
    year on as import-venues does, after a dry run that only estimates; nobody else can.
    """
    admin_token = _web_admin(session, client)
    queued = []
    job = SimpleNamespace(id="job-1")
    monkeypatch.setattr(jobs, "enqueue_import", lambda *a, **k: queued.append((a, k)) or job)
    year = date.today().year
    iccv_held = config().venues["ICCV"].model_copy(update={"held_in": year})  # every 2 years
    monkeypatch.setitem(config().venues, "ICCV", iccv_held)
    with Session(engine()) as s, s.begin():
        for imported in (year - 2, year - 4):
            s.add(SyncState(source=f"venue:ICCV:{imported}", cursor=date.today()))
    url, plan = "/api/admin/venues", {"venues": ["ICCV", "CVPR"], "since": year - 2}
    for who, caller in _callers(token, admin_token).items():
        expected = 401 if who == "nobody" else 403
        assert caller.get(url).status_code == expected, who
        dry = caller.post(f"{url}/import", json=plan | {"dry_run": True})
        assert dry.status_code == expected, who

    listed = client.get(url).json()
    assert [v["name"] for v in listed] == list(config().venues)
    cvpr, iccv = (config().venues[name].papers_per_edition for name in ("CVPR", "ICCV"))
    found, years = next(v for v in listed if v["name"] == "ICCV"), [year - 2, year - 4]
    assert found == {"name": "ICCV", "every": 2, "papers_per_edition": iccv, "years": years}
    assert [v["years"] for v in listed if v["name"] != "ICCV"] == [[]] * (len(listed) - 1)

    # config.yaml's order, newest first, without ICCV's imported edition and its year off
    editions = [("CVPR", year), ("ICCV", year), ("CVPR", year - 1), ("CVPR", year - 2)]
    papers = 3 * cvpr + iccv
    r = client.post(f"{url}/import", json=plan | {"dry_run": True})
    as_json = [list(edition) for edition in editions]
    expected = {"editions": as_json, "papers": papers, "tokens": papers * 1000, "queued": False}
    assert r.status_code == 200 and r.json() == expected and not queued
    r = client.post(f"{url}/import", json=plan | {"briefs": False})
    assert r.json() == expected | {"tokens": 0, "queued": True}
    assert queued == [(editions[0], {"briefs": False, "then": editions[1:]})]

    queued.clear()
    empty = {"editions": [], "papers": 0, "tokens": 0, "queued": False}
    assert client.post(f"{url}/import", json={"venues": [], "since": year}).json() == empty
    unknown = [{"venues": ["SIGGRAPH"]}, {"venues": ["iccv"]}]  # names as GET lists them
    for bad in [*unknown, {"since": 1999}, {"since": year + 1}]:
        assert client.post(f"{url}/import", json=plan | bad).status_code == 422, bad
    assert not queued

    # the command line plans and queues the same way
    cli.import_venues(SimpleNamespace(venue=["cvpr"], since=year - 1, until=None, no_briefs=False))
    assert queued == [(("CVPR", year), {"briefs": True, "then": [("CVPR", year - 1)]})]
    assert "Queued 2 editions" in capsys.readouterr().out


def test_create_user_command(session, monkeypatch, capsys):
    """The command line keeps creating accounts, with an email or without, and a token."""

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["scholarmind", "create-user", *argv])
        monkeypatch.setattr(sys, "stdin", io.StringIO("pw-long-enough\n"))
        cli.main()

    run("gina", "--email", " Gina@Lab.org", "--admin")
    assert "shown only once" in capsys.readouterr().out
    run("hank")
    with pytest.raises(SystemExit, match="this email"):
        run("gina2", "--email", "gina@lab.org")
    with pytest.raises(SystemExit, match="this username"):
        run("gina")
    with pytest.raises(SystemExit, match="Not an email"):
        run("ivy", "--email", "ivy at lab")
    with Session(engine()) as s:
        rows = s.execute(select(User.username, User.email, User.is_admin).order_by(User.id)).all()
        assert rows == [("gina", "gina@lab.org", True), ("hank", None, False)]
        assert s.scalar(select(func.count()).select_from(Token)) == 2


def test_reset_token_command(token, monkeypatch, capsys):
    """The command adds a named token; the member's others keep working."""
    monkeypatch.setattr(sys, "argv", ["scholarmind", "reset-token", "alice", "--name", "laptop"])
    cli.main()
    value = capsys.readouterr().out.split()[-1]
    for each in (value, token):
        bearer = {"Authorization": f"Bearer {each}"}
        assert TestClient(app).get("/api/me", headers=bearer).status_code == 200
    with Session(engine()) as s:
        assert sorted(s.scalars(select(Token.name))) == ["laptop", "test"]


def test_set_password_command(session, monkeypatch, capsys):
    session.add(User(username="carol", password_hash=auth.hash_password("old-password")))
    session.commit()
    cookie = auth.make_session_cookie(session.scalar(select(User)))

    def run(stdin, *argv):
        monkeypatch.setattr(sys, "argv", ["scholarmind", *argv])
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        cli.main()

    run("new-password\n", "set-password", "carol")
    assert "signed out" in capsys.readouterr().out
    session.expire_all()
    assert auth.authenticate(session, "carol", "new-password")
    assert not auth.authenticate(session, "carol", "old-password")
    assert auth.read_session_cookie(session, cookie) is None
    with pytest.raises(SystemExit, match="at least 8 characters"):
        run("short\n", "set-password", "carol")
    with pytest.raises(SystemExit, match="No such user"):
        run("new-password\n", "set-password", "dave")


def _tag(s, paper_id, kind, value):
    s.add(Tag(paper_id=paper_id, kind=kind, value=value, source="ai"))


def _topic(s, name):
    return s.scalar(select(Topic.id).where(Topic.name == name))


def _link(s, paper_id, topic_name):
    s.add(PaperTopic(paper_id=paper_id, topic_id=_topic(s, topic_name), source="ai"))


@pytest.fixture
def labelled(token, vocabulary):
    """Papers 1 (grasping), 2 (segmentation), 3 (odometry) from the token fixture labelled,
    and an out-of-scope paper 4.
    """
    with Session(engine()) as s, s.begin():
        upsert_papers(s, [_paper("2611.00004", "Bandits with knapsacks", ["cs.LG"], 21)])
        for paper_id, kind, value in [
            (1, "domain", "robotics"),
            (1, "keyword", "diffusion policy"),
            (2, "domain", "computer-vision"),
            (3, "domain", "computer-vision"),
            (3, "domain", "robotics"),
            (4, "domain", "out-of-scope"),
        ]:
            _tag(s, paper_id, kind, value)
        for paper_id, name in [
            (1, "Manipulation"),
            (2, "Segmentation"),
            (3, "Visual SLAM"),
            (3, "Robot Perception"),
        ]:
            _link(s, paper_id, name)
        for paper_id, paper_type in [(1, "method"), (2, "dataset"), (3, "method"), (4, "theory")]:
            s.get(Paper, paper_id).paper_type = paper_type
        s.get(Paper, 1).summary = "扩散策略抓取。"
    return token


def _innovations(pairs):
    with Session(engine()) as s, s.begin():
        for paper_id, innovation in pairs:
            s.get(Paper, paper_id).innovation = innovation


def test_multi_select(client, labelled):
    """Several values of one filter mean any of them; filters still combine with AND."""
    client.headers["Authorization"] = f"Bearer {labelled}"
    _innovations([(1, "机制创新"), (2, "模块组合"), (3, "方法迁移")])
    with Session(engine()) as s:
        manipulation, segmentation = _topic(s, "Manipulation"), _topic(s, "Segmentation")

    def ids(**params):
        r = client.get("/api/papers", params=params)
        assert r.status_code == 200, r.text
        return sorted(p["id"] for p in r.json()["items"])

    assert ids(domain="robotics") == ids(domain=["robotics"]) == [1, 3]  # one value as before
    assert ids(domain=["robotics", "computer-vision"]) == [1, 2, 3]
    assert ids(domain=["robotics", "computer-vision"], paper_type="method") == [1, 3]
    assert ids(topic=[manipulation, segmentation]) == [1, 2]
    assert ids(topic=[manipulation, segmentation], domain="computer-vision") == [2]
    assert ids(category=["cs.CV", "cs.LG"]) == [1, 2, 3]  # 4 (cs.LG) is out of scope
    assert ids(innovation=["机制创新", "方法迁移"]) == [1, 3]
    assert ids(paper_type=["dataset", "theory"]) == [2]
    # out-of-scope among the domains brings in out-of-scope papers, as it does alone
    assert ids(domain=["computer-vision", "out-of-scope"], paper_type=["dataset", "theory"]) == [
        2,
        4,
    ]
    assert ids(domain=["robotics", "out-of-scope"]) == [1, 3, 4]
    assert ids(category="cs.LG", include_out_of_scope="true") == [1, 4]
    with Session(engine()) as s, s.begin():
        s.execute(update(Paper).where(Paper.id.in_([1, 2, 3])).values(added_on=date(2026, 11, 20)))
    assert ids(added="latest", domain=["robotics", "computer-vision"], paper_type="method") == [
        1,
        3,
    ]
    for bad in (
        {"paper_type": ["method", "poem"]},
        {"innovation": ["机制创新", "x"]},
        {"topic": [manipulation, "x"]},
    ):
        assert client.get("/api/papers", params=bad).status_code == 422
        assert client.get("/api/papers/facets", params=bad).status_code == 422


def test_facets(client, labelled):
    """Each group counts under every filter but its own; total is the list's."""
    assert client.get("/api/papers/facets").status_code == 401
    client.headers["Authorization"] = f"Bearer {labelled}"
    _innovations([(1, "机制创新"), (3, "机制创新"), (2, "模块组合")])

    def facets(**params):
        r = client.get("/api/papers/facets", params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        listed = client.get("/api/papers", params=params).json()["total"]
        assert body["total"] == listed, params
        topics = [(t["name"], t["papers"]) for t in body.pop("topic")]
        assert body.pop("collection") == []  # none made here: see test_collections
        groups = {
            k: [(v["value"], v["papers"]) for v in vs] for k, vs in body.items() if k != "total"
        }
        return body["total"], groups, topics

    total, groups, topics = facets()
    assert total == 3  # paper 4 is out of scope
    assert groups == {
        # out-of-scope papers count for their domain value: choosing it would add them
        "domain": [("computer-vision", 2), ("robotics", 2), ("out-of-scope", 1)],
        "paper_type": [("method", 2), ("dataset", 1)],
        "innovation": [("机制创新", 2), ("模块组合", 1)],
        "category": [("cs.CV", 2), ("cs.RO", 2), ("cs.LG", 1)],
        "source": [("arXiv", 3)],
        "year": [(2026, 3)],
    }
    assert sorted(topics) == [
        ("Manipulation", 1),
        ("Robot Perception", 1),
        ("Segmentation", 1),
        ("Visual SLAM", 1),
    ]
    first = client.get("/api/papers/facets").json()["topic"][0]
    assert set(first) == {"id", "name", "zh", "domain", "papers"}

    total, groups, topics = facets(domain="robotics")
    assert total == 2
    assert groups["domain"] == [("computer-vision", 2), ("robotics", 2), ("out-of-scope", 1)]
    assert groups["paper_type"] == [("method", 2)]  # within the domain chosen
    assert groups["category"] == [("cs.RO", 2), ("cs.CV", 1), ("cs.LG", 1)]
    assert sorted(topics) == [("Manipulation", 1), ("Robot Perception", 1), ("Visual SLAM", 1)]

    total, groups, _ = facets(domain="robotics", paper_type="dataset")
    assert total == 0
    assert groups["paper_type"] == [("method", 2)]  # what robotics has to offer
    assert groups["domain"] == [("computer-vision", 1)]  # where the datasets are
    assert groups["category"] == groups["innovation"] == []

    total, groups, _ = facets(domain=["robotics", "computer-vision"], paper_type="method")
    assert total == 2 and groups["paper_type"] == [("method", 2), ("dataset", 1)]
    total, groups, _ = facets(domain="out-of-scope")
    assert total == 1 and groups["paper_type"] == [("theory", 1)]
    total, groups, _ = facets(include_out_of_scope="true")
    assert total == 4 and groups["paper_type"] == [("method", 2), ("dataset", 1), ("theory", 1)]

    client.put("/api/papers/3/star")
    total, groups, _ = facets(starred="true")
    assert total == 1 and groups["domain"] == [("computer-vision", 1), ("robotics", 1)]
    total, groups, _ = facets(q="segment")
    assert total == 1 and groups["paper_type"] == [("dataset", 1)]
    with Session(engine()) as s, s.begin():
        s.execute(update(Paper).where(Paper.id.in_([1, 2])).values(added_on=date(2026, 11, 20)))
    total, groups, _ = facets(added="latest", innovation="机制创新")
    assert total == 1 and groups["innovation"] == [("机制创新", 1), ("模块组合", 1)]
    for params in ({"category": "cs.RO", "paper_type": "method"}, {"topic": 999}, {"q": "x y z"}):
        facets(**params)  # totals match the list's


def test_label_filters(client, labelled):
    client.headers["Authorization"] = f"Bearer {labelled}"
    with Session(engine()) as s:
        slam, manipulation = _topic(s, "Visual SLAM"), _topic(s, "Manipulation")
        perception = _topic(s, "Robot Perception")

    def ids(**params):
        r = client.get("/api/papers", params=params)
        assert r.status_code == 200, r.text
        return sorted(p["id"] for p in r.json()["items"])

    assert ids() == [1, 2, 3]  # out of scope is hidden by default
    assert ids(include_out_of_scope=True) == [1, 2, 3, 4]
    assert ids(domain="out-of-scope") == [4]
    assert ids(domain="robotics") == [1, 3]
    assert ids(domain="robotics", category="cs.CV") == [3]
    assert ids(topic=slam) == [3]
    assert ids(topic=manipulation, domain="computer-vision") == []
    assert ids(paper_type="method") == [1, 3]
    assert ids(paper_type="theory") == [] and ids(paper_type="theory", include_out_of_scope=1) == [
        4
    ]
    assert ids(keyword="diffusion policy") == [1]
    assert client.get("/api/papers", params={"paper_type": "position"}).status_code == 422

    detail = client.get("/api/papers/3").json()
    assert detail["paper_type"] == "method"
    assert detail["topics"] == [
        {"id": slam, "name": "Visual SLAM", "zh": "视觉 SLAM 与里程计", "source": "ai"},
        {
            "id": perception,
            "name": "Robot Perception",
            "zh": "机器人感知与状态估计",
            "source": "ai",
        },
    ]
    assert [(t["kind"], t["value"]) for t in detail["tags"]] == [
        ("domain", "computer-vision"),
        ("domain", "robotics"),
    ]
    listed = client.get("/api/papers", params={"domain": "robotics"}).json()["items"]
    assert {p["id"]: [t["name"] for t in p["topics"]] for p in listed} == {
        1: ["Manipulation"],
        3: ["Visual SLAM", "Robot Perception"],
    }
    assert client.get("/api/tags", params={"kind": "domain"}).json() == [
        {"kind": "domain", "value": "computer-vision", "papers": 2},
        {"kind": "domain", "value": "robotics", "papers": 2},
        {"kind": "domain", "value": "out-of-scope", "papers": 1},
    ]


def test_manual_labels_survive_briefs(client, labelled):
    client.headers["Authorization"] = f"Bearer {labelled}"
    with Session(engine()) as s:
        manipulation, learning = _topic(s, "Manipulation"), _topic(s, "Robot Learning")

    r = client.delete(f"/api/papers/1/topics/{manipulation}")
    assert r.status_code == 200 and r.json() == []
    r = client.post("/api/papers/1/topics", json={"topic_id": learning})
    assert [(t["name"], t["source"]) for t in r.json()] == [("Robot Learning", "human")]
    assert client.put("/api/papers/1/type", json={"paper_type": "system"}).json() == {
        "paper_type": "system"
    }
    r = client.post("/api/papers/1/tags", json={"kind": "domain", "value": "embodied-ai"})
    assert ("domain", "embodied-ai", "human") in [
        (t["kind"], t["value"], t["source"]) for t in r.json()
    ]
    with Session(engine()) as s:
        link = s.scalar(select(PaperTopic).where(PaperTopic.topic_id == learning))
        assert link.user_id == s.scalar(select(User.id).where(User.username == "alice"))

    assert client.post("/api/papers/1/topics", json={"topic_id": 99999}).status_code == 422
    assert client.post("/api/papers/99/topics", json={"topic_id": learning}).status_code == 404
    assert client.delete(f"/api/papers/1/topics/{manipulation}").status_code == 404
    assert client.put("/api/papers/1/type", json={"paper_type": "essay"}).status_code == 422
    assert client.put("/api/papers/99/type", json={"paper_type": "system"}).status_code == 404
    bad = {"kind": "domain", "value": "cooking"}
    assert client.post("/api/papers/1/tags", json=bad).status_code == 422  # not a domain
    assert client.post("/api/papers/1/tags", json={"kind": "task", "value": "x"}).status_code == 422

    # A later automatic brief keeps the member's version of paper 1 and replaces paper 2's.
    item = {"domains": ["computer-vision"], "topics": ["Image Generation"], "new_topic": None}
    item |= {"type": "survey", "keywords": ["new"], "summary": "新"}
    with Session(engine()) as s, s.begin():
        papers = s.scalars(select(Paper).where(Paper.id.in_([1, 2]))).all()
        brief.save(
            s,
            papers,
            {"1": item, "2": item},
            {key: 0 for key in pi.USAGE_KEYS},
            "brief.v2",
            "deepseek-flash",
        )
    after = client.get("/api/papers/1").json()
    assert after["summary"] == "新" and after["paper_type"] == "system"
    assert [t["name"] for t in after["topics"]] == ["Robot Learning"]
    assert [(t["kind"], t["value"]) for t in after["tags"]] == [
        ("domain", "robotics"),
        ("keyword", "diffusion policy"),
        ("domain", "embodied-ai"),
    ]
    untouched = client.get("/api/papers/2").json()  # never edited: AI labels replaced
    assert untouched["paper_type"] == "survey"
    assert [t["name"] for t in untouched["topics"]] == ["Image Generation"]
    assert [(t["kind"], t["value"]) for t in untouched["tags"]] == [
        ("domain", "computer-vision"),
        ("keyword", "new"),
    ]


def test_vocabulary_tree(client, labelled):
    client.headers["Authorization"] = f"Bearer {labelled}"
    with Session(engine()) as s, s.begin():
        created = Topic(domain="robotics", name="Micro Robotics", definition="d", source="model")
        s.add(created)
        s.flush()
        s.add(PaperTopic(paper_id=3, topic_id=created.id, source="ai"))
    body = client.get("/api/vocabulary").json()
    assert body["paper_types"][0] == {"value": "method", "zh": "方法"}
    assert len(body["paper_types"]) == 7
    domains = body["domains"]
    assert [d["domain"] for d in domains] == list(config().vocabulary)
    assert domains[-1] == {
        "domain": "out-of-scope",
        "zh": config().vocabulary["out-of-scope"].zh,
        "topics": [],
    }
    assert sum(len(d["topics"]) for d in domains) == 89  # 88 starting topics and the created one
    robotics = {t["name"]: t for t in domains[2]["topics"]}
    assert robotics["Manipulation"] | {"id": 0} == {
        "id": 0,
        "name": "Manipulation",
        "zh": "操作（抓取、灵巧手、双臂）",
        "definition": config().vocabulary["robotics"].topics[0].definition,
        "aliases": ["grasping", "dexterous manipulation", "bimanual manipulation"],
        "created": False,
        "papers": 1,
    }
    assert robotics["Micro Robotics"]["created"] and robotics["Micro Robotics"]["papers"] == 1
    assert robotics["Robot Perception"]["papers"] == 1 and robotics["Navigation"]["papers"] == 0


def test_admin_topic_endpoints(client, labelled):
    with Session(engine()) as s, s.begin():
        admin = User(
            username="root", password_hash=auth.hash_password("pw-root-123"), is_admin=True
        )
        s.add(admin)
        _, admin_token = auth.new_token(s, admin, "test")
        created = Topic(domain="computer-vision", name="Gaussian Splatting Rendering",
                        definition="Rendering with Gaussians.", aliases=["GS rendering"],
                        source="model")  # fmt: skip
        s.add(created)
        s.flush()
        created_id, gaussians = created.id, _topic(s, "3D Gaussian Splatting")
        s.add(PaperTopic(paper_id=2, topic_id=created_id, source="ai"))
        s.add(PaperTopic(paper_id=3, topic_id=created_id, source="ai"))
        s.add(PaperTopic(paper_id=3, topic_id=gaussians, source="ai"))
    member = {"Authorization": f"Bearer {labelled}"}
    merge = {"into": gaussians, "reason": "the same direction"}
    assert client.get("/api/admin/topics", headers=member).status_code == 403
    assert (
        client.post(f"/api/admin/topics/{created_id}/merge", json=merge, headers=member).status_code
        == 403
    )
    assert client.get("/api/admin/topic-merges", headers=member).status_code == 403

    client.headers["Authorization"] = f"Bearer {admin_token}"
    listed = client.get("/api/admin/topics").json()
    assert [(t["id"], t["name"], t["papers"]) for t in listed] == [
        (created_id, "Gaussian Splatting Rendering", 2)
    ]
    seed = {"into": created_id}
    assert client.post(f"/api/admin/topics/{gaussians}/merge", json=seed).status_code == 422
    itself = {"into": created_id}
    assert client.post(f"/api/admin/topics/{created_id}/merge", json=itself).status_code == 422
    assert client.post("/api/admin/topics/99999/merge", json=merge).status_code == 422
    r = client.post(f"/api/admin/topics/{created_id}/merge", json=merge)
    assert r.status_code == 200, r.text
    record = r.json()
    assert (record["source_id"], record["target_id"], record["target_name"]) == (
        created_id,
        gaussians,
        "3D Gaussian Splatting",
    )
    assert (record["merged_by"], record["reason"], record["papers"]) == (
        "root",
        "the same direction",
        1,
    )
    assert record["source"]["aliases"] == ["GS rendering"]
    assert client.get("/api/admin/topic-merges").json() == [record]
    assert client.get("/api/admin/topics").json() == []
    assert [t["name"] for t in client.get("/api/papers/2").json()["topics"]] == [
        "Segmentation",
        "3D Gaussian Splatting",
    ]
    assert [t["name"] for t in client.get("/api/papers/3").json()["topics"]] == [
        "Visual SLAM",
        "Robot Perception",
        "3D Gaussian Splatting",
    ]


def test_me_and_categories(client, token):
    assert client.get("/api/me").status_code == 401
    client.headers["Authorization"] = f"Bearer {token}"
    me = client.get("/api/me").json()
    with Session(engine()) as s:
        joined = s.scalar(select(User.created_at).where(User.username == "alice"))
    assert datetime.fromisoformat(me.pop("created_at")) == joined  # ISO 8601 with offset
    assert me == {"username": "alice", "email": None, "is_admin": False, "avatar": None}
    assert client.get("/api/categories").json() == [
        {"category": "cs.CV", "papers": 2},
        {"category": "cs.RO", "papers": 2},
        {"category": "cs.LG", "papers": 1},  # cross-listed only
    ]


def test_frontend(client):
    page = client.get("/")
    assert page.status_code == 200 and "<title>ScholarMind</title>" in page.text
    assert page.headers["cache-control"] == "no-cache"
    deep = client.get("/papers/123?tab=report")  # the app's own route, after a reload
    assert deep.text == page.text and deep.headers["cache-control"] == "no-cache"
    asset = client.get("/assets/index-1a2b3c.js")
    assert asset.text == 'console.log("app")\n'
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/assets/index-old.js").status_code == 404  # never the page as a script
    assert client.get("/robots.txt").headers["cache-control"] == "no-cache"
    for method, path in [
        ("GET", "/api/nope"),
        ("POST", "/api/nope"),
        ("GET", "/api"),
        ("GET", "/mcp/nope"),
    ]:
        r = client.request(method, path)
        assert (r.status_code, r.json()) == (404, {"detail": "Not Found"})
    assert client.post("/mcp").status_code == 401  # the MCP endpoint itself wants a token
    assert client.get("/api/health").json() == {"status": "ok"}  # API routes come first


def test_report_endpoints(client, token, monkeypatch):
    client.headers["Authorization"] = f"Bearer {token}"
    enqueued = []
    monkeypatch.setattr(jobs, "enqueue_reports", lambda ids: enqueued.extend(ids))
    detail = client.get("/api/papers/1").json()
    assert detail["report"] is None and detail["report_status"] is None
    assert detail["report_progress"] is None and detail["report_error"] is None

    url = "/api/papers/1/report"
    assert (client.post(url).status_code, client.post(url).json()) == (
        202,
        {"report_status": "queued"},
    )
    assert enqueued == [1]  # the second request found it queued and added no job
    detail = client.get("/api/papers/1").json()
    assert detail["report_status"] == "queued" and detail["report_error"] is None
    progress = detail["report_progress"]
    assert datetime.fromisoformat(progress["since"]) > datetime.now(UTC) - timedelta(minutes=1)
    assert progress == {
        "stage": "queued",
        "since": progress["since"],
        "ahead": 0,
        "eta_seconds": None,
        "typical_seconds": None,
    }
    listed = client.get("/api/papers").json()["items"]
    assert {p["id"]: p["report_status"] for p in listed} == {1: "queued", 2: None, 3: None}

    with Session(engine()) as s, s.begin():
        s.get(Paper, 1).report_status = "reported"
        markdown = "## 方法\n\n[[图:Fig. 2]]\n\n## 存疑之处\n\n无。"
        result = {
            "version": 2,
            "markdown": markdown,
            "pdf_url": "https://arxiv.org/pdf/2611.00001v1",
        }
        analysis = Analysis(paper_id=1, level="report", model="m", prompt_version="report.v2",
                            result=result, usage={"totalTokens": 1})  # fmt: skip
        s.add(analysis)
        s.flush()
        figure = Figure(analysis_id=analysis.id, kind="figure", label="Fig. 2",
                        caption="Fig. 2: Overview.", page=3, image=b"\x89PNG fake", width=640,
                        height=480)  # fmt: skip
        s.add(figure)
        s.flush()
        figure_id = figure.id
    got = client.get("/api/papers/1").json()["report"]
    assert got == {
        "version": 2,
        "markdown": markdown,
        "figures": [
            {
                "label": "Fig. 2",
                "kind": "figure",
                "page": 3,
                "caption": "Fig. 2: Overview.",
                "url": f"/api/papers/1/figures/{figure_id}",
                "width": 640,
                "height": 480,
            }
        ],
        "model": "m",
        "created_at": got["created_at"],
    }
    image = client.get(got["figures"][0]["url"])
    assert image.content == b"\x89PNG fake" and image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert client.get(f"/api/papers/2/figures/{figure_id}").status_code == 404  # not its paper
    assert client.get("/api/papers/1").json()["report_progress"] is None
    assert client.get(got["figures"][0]["url"], headers={"Authorization": ""}).status_code == 401
    assert client.post(url).status_code == 409  # has a report
    assert client.post(url, params={"force": "true"}).json() == {"report_status": "queued"}
    assert enqueued == [1, 1]
    assert client.post("/api/papers/999/report").status_code == 404
    with Session(engine()) as s, s.begin():
        s.get(Paper, 2).pdf_url = None
    assert client.post("/api/papers/2/report").status_code == 422
    r = client.post("/api/papers/3/tags", json={"kind": "method", "value": "visual odometry"})
    assert ("method", "visual odometry") in [(t["kind"], t["value"]) for t in r.json()]


def test_report_progress_and_error(client, token, monkeypatch):
    """A polled paper costs the same statements however many reports are queued, and never asks
    Redis; a failed report says why.
    """
    client.headers["Authorization"] = f"Bearer {token}"
    monkeypatch.setattr(jobs, "redis", lambda: pytest.fail("the page must not ask Redis"))
    monkeypatch.setattr(jobs, "enqueue_reports", lambda ids: None)
    statements = []

    def count(*args):
        statements.append(args[2])

    def cost(paper_id):
        statements.clear()
        progress = client.get(f"/api/papers/{paper_id}").json()["report_progress"]
        return progress["ahead"], len(statements)

    event.listen(engine(), "before_cursor_execute", count)
    try:
        client.post("/api/papers/3/report")
        alone = cost(3)
        client.post("/api/papers/1/report")
        client.post("/api/papers/2/report")
        assert cost(2) == (2, alone[1]) and alone[0] == 0  # papers 3 and 1 before it
    finally:
        event.remove(engine(), "before_cursor_execute", count)

    with Session(engine()) as s, s.begin():
        paper = s.get(Paper, 1)
        paper.report_status = "report_failed"
        paper.report_error = {"code": "no_text", "detail": "the PDF has no text layer"}
    detail = client.get("/api/papers/1").json()
    assert detail["report_progress"] is None
    assert detail["report_error"] == {"code": "no_text", "detail": "the PDF has no text layer"}
    with Session(engine()) as s, s.begin():
        s.get(Paper, 2).report_status = "report_failed"  # failed before reasons were kept
    assert client.get("/api/papers/2").json()["report_error"] is None
    assert client.post("/api/papers/1/report").json() == {"report_status": "queued"}
    assert client.get("/api/papers/1").json()["report_error"] is None  # cleared


def _events(response) -> list[tuple[str, dict]]:
    """The server-sent events of an answer: (event, data)."""
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        name, data = block.split("\n")
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


@pytest.fixture
def asked(token, monkeypatch):
    """Paper 1 with its text stored and a reading; pi.stream faked: the answers to give (text,
    or an exception raised after one delta), and what each call was given.
    """
    pages = ["We propose a planner.", "It wins on the benchmark by 3 points."]
    with Session(engine()) as s, s.begin():
        s.add(PaperText(paper_id=1, pdf_url="https://arxiv.org/pdf/2611.00001v1", pages=pages))
        s.add(Analysis(paper_id=1, level="report", model="m", prompt_version="report.v2",
                       result={"version": 2, "markdown": "## 方法\n\n一个规划器。", "pdf_url": ""},
                       usage={}))  # fmt: skip
    answers, calls = [], []

    def stream(model, system_prompt, prompt, timeout, *, stdin="", endpoint=None):
        calls.append(SimpleNamespace(model=model, system=system_prompt, prompt=prompt,
                                     timeout=timeout, stdin=stdin))  # fmt: skip
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            yield "half an answer"
            raise answer
        yield answer[:3]
        yield answer[3:]
        return pi.Result(answer, {"totalTokens": 70, "turns": 1})

    monkeypatch.setattr(pi, "stream", stream)
    return SimpleNamespace(answers=answers, calls=calls)


def test_chat(client, token, asked):
    """A conversation with the agent about a paper: streamed, stored when complete, the model
    given the paper, its reading, the conversation and the quote; private; cleared.
    """
    assert client.get("/api/papers/1/chat").status_code == 401
    client.headers["Authorization"] = f"Bearer {token}"
    url = "/api/papers/1/chat"
    assert client.get(url).json() == {"messages": []}
    asked.answers.append("它赢了 3 个点 [p.2]。")
    events = _events(client.post(url, json={"content": " 效果如何？ ", "quote": " wins "}))
    assert [name for name, _ in events] == ["delta", "delta", "done"]
    assert "".join(data["text"] for name, data in events[:2]) == "它赢了 3 个点 [p.2]。"
    done = events[2][1]
    assert done["user"] | {"id": 0, "created_at": ""} == {
        "id": 0,
        "role": "user",
        "content": "效果如何？",
        "quote": "wins",
        "created_at": "",
    }
    assert (done["assistant"]["role"], done["assistant"]["quote"]) == ("assistant", None)
    assert client.get(url).json() == {"messages": [done["user"], done["assistant"]]}

    call = asked.calls[0]
    assert (call.model, call.timeout) == (config().analysis.report_model, chat.PI_TIMEOUT)
    assert "=== PAGE 2 ===\nIt wins on the benchmark by 3 points." in call.stdin
    assert "<reading>\n## 方法\n\n一个规划器。\n</reading>" in call.stdin
    assert "<conversation>" not in call.stdin  # the first question
    assert call.prompt == "<quote>\nwins\n</quote>\n<question>\n效果如何？\n</question>\n"
    language = config().analysis.language
    assert f"Answer in {language}." in call.system and "{{LANGUAGE}}" not in call.system
    with Session(engine()) as s:
        stored = s.scalars(select(ChatMessage).order_by(ChatMessage.id)).all()
    assert (stored[1].model, stored[1].prompt_version) == (call.model, "chat.v1")
    assert stored[1].usage["totalTokens"] == 70 and stored[1].usage["seconds"] >= 0

    # The second question gets the first exchange; past HISTORY messages, only the newest.
    asked.answers.append("第二个回答。")
    _events(client.post(url, json={"content": "第二个问题"}))
    assert "<conversation>\n<user>\n<quote>\nwins\n</quote>\n<question>\n效果如何？" in (
        asked.calls[1].stdin
    )
    assert "<assistant>\n它赢了 3 个点 [p.2]。\n</assistant>" in asked.calls[1].stdin
    with Session(engine()) as s, s.begin():
        alice = s.scalar(select(User.id).where(User.username == "alice"))
        for n in range(30):
            role = "user" if n % 2 == 0 else "assistant"
            s.add(ChatMessage(user_id=alice, paper_id=1, role=role, content=f"old {n:02}"))
    asked.answers.append("第三个回答。")
    _events(client.post(url, json={"content": "第三个问题"}))
    given = asked.calls[2].stdin
    assert given.count("<user>") + given.count("<assistant>") == chat.HISTORY
    assert "old 10" in given and "old 09" not in given and "第二个问题" not in given

    # A prompt edited on the web is used and recorded.
    with Session(engine()) as s, s.begin():
        agent.set_prompt(s, "chat", "Only {{LANGUAGE}}.", s.get(User, alice))
    asked.answers.append("好。")
    _events(client.post(url, json={"content": "再问"}))
    assert asked.calls[3].system == f"Only {language}."
    version = "chat.v1+" + hashlib.sha256(b"Only {{LANGUAGE}}.").hexdigest()[:8]
    newest = client.get(url).json()["messages"][-1]
    with Session(engine()) as s:
        assert s.get(ChatMessage, newest["id"]).prompt_version == version

    # Private, and cleared
    with Session(engine()) as s, s.begin():
        bob = User(username="bob", password_hash=auth.hash_password("pw-bob-123"))
        s.add(bob)
        _, bob_token = auth.new_token(s, bob, "test")
    as_bob = {"Authorization": f"Bearer {bob_token}"}
    assert client.get(url, headers=as_bob).json() == {"messages": []}
    asked.answers.append("给 Bob。")
    _events(client.post(url, json={"content": "Bob 的问题"}, headers=as_bob))
    assert client.delete(url).status_code == 204
    assert client.get(url).json() == {"messages": []}
    assert len(client.get(url, headers=as_bob).json()["messages"]) == 2
    assert client.get("/api/papers/99/chat").status_code == 404
    assert client.delete("/api/papers/99/chat").status_code == 404


def test_chat_refusals_and_failures(client, token, asked, monkeypatch):
    """Nothing is stored for a failed answer, and the next one goes through; one answer at a
    time per member and paper, WRITING in the process; papers without text say so.
    """
    client.headers["Authorization"] = f"Bearer {token}"
    url = "/api/papers/1/chat"
    asked.answers += [pi.PiError("stopReason error: 500 Internal Server Error"),
                      pi.Incomplete("stopReason length", {}), "好。"]  # fmt: skip
    events = _events(client.post(url, json={"content": "问"}))
    assert events == [
        ("delta", {"text": "half an answer"}),
        ("error", {"detail": "No answer: stopReason error: 500 Internal Server Error"}),
    ]
    assert _events(client.post(url, json={"content": "问"}))[-1] == (
        "error",
        {"detail": "No answer: stopReason length"},
    )
    assert client.get(url).json() == {"messages": []}  # nothing stored
    assert _events(client.post(url, json={"content": "问"}))[-1][0] == "done"  # not stuck

    with Session(engine()) as s:
        alice = s.scalar(select(User.id).where(User.username == "alice"))
    with engine().connect() as other:  # an answer to alice on paper 1 being written
        other.execute(select(func.pg_advisory_lock(alice, 1)))
        r = client.post(url, json={"content": "问"})
        assert (r.status_code, r.json()) == (409, {"detail": "An answer is still being written"})
        other.execute(select(func.pg_advisory_unlock(alice, 1)))
    for _ in range(chat.WRITING):  # as many answers as allowed being written elsewhere
        assert chat._writing.acquire(blocking=False)
    try:
        r = client.post(url, json={"content": "问"})
        assert r.status_code == 503
        assert r.json() == {"detail": "The agent is busy, try again in a moment"}
    finally:
        for _ in range(chat.WRITING):
            chat._writing.release()
    asked.answers.append("又好了。")
    assert _events(client.post(url, json={"content": "问"}))[-1][0] == "done"

    for body in [{"content": ""}, {"content": "x" * 4001}, {"content": "x", "quote": "q" * 2001}]:
        assert client.post(url, json=body).status_code == 422
    asked.answers.append("最长的问题也行。")
    longest = client.post(url, json={"content": "x" * 4000, "quote": "q" * 2000})
    assert _events(longest)[-1][0] == "done"
    assert client.post("/api/papers/99/chat", json={"content": "问"}).status_code == 404
    with Session(engine()) as s, s.begin():
        s.get(Paper, 2).pdf_url = None
        s.add(PaperText(paper_id=3, pdf_url="https://arxiv.org/pdf/2611.00003v1", pages=[" "]))
    r = client.post("/api/papers/2/chat", json={"content": "问"})
    assert (r.status_code, r.json()) == (409, {"detail": "Paper has no PDF link"})
    r = client.post("/api/papers/3/chat", json={"content": "问"})
    assert (r.status_code, r.json()) == (409, {"detail": "The paper's PDF has no text layer"})
    assert len(asked.calls) == 5  # the refused questions never reached the model
    assert chat._writing.acquire(blocking=False) and chat._writing.acquire(blocking=False)
    chat._writing.release()
    chat._writing.release()


ABSTRACT, ABSTRACT_ZH = (
    "We propose a planner. It wins by $3$ points.",
    "我们提出一个规划器。它以 $3$ 分取胜。",
)


@pytest.fixture
def translating(token, monkeypatch):
    """Paper 1 with an abstract of one paragraph; pi.run faked: the answers to give (text, an
    exception to raise, or a function that gives one), and what each call was given.
    """
    with Session(engine()) as s, s.begin():
        s.get(Paper, 1).abstract = ABSTRACT
    answers, calls = [], []

    def run(model, system_prompt, prompt, timeout, *, stdin="", endpoint=None):
        calls.append(SimpleNamespace(model=model, system=system_prompt, prompt=prompt,
                                     timeout=timeout, stdin=stdin))  # fmt: skip
        answer = answers.pop(0)
        answer = answer() if callable(answer) else answer
        if isinstance(answer, Exception):
            raise answer
        return pi.Result(answer, {key: 10 for key in pi.USAGE_KEYS} | {"turns": 1})

    monkeypatch.setattr(pi, "run", run)
    return SimpleNamespace(answers=answers, calls=calls)


def test_abstract_translation(client, token, translating):
    """Made the first time it is asked for, kept, and tied to the abstract it translates; an
    edited prompt is used.
    """
    url = "/api/papers/1/abstract-zh"
    assert client.post(url).status_code == 401
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.get("/api/papers/1").json()["abstract_zh"] is None
    translating.answers.append(f" {ABSTRACT_ZH}\n")
    assert client.post(url).json() == {"abstract_zh": ABSTRACT_ZH}
    assert client.post(url).json() == {"abstract_zh": ABSTRACT_ZH}  # stored: no call
    assert len(translating.calls) == 1
    assert client.get("/api/papers/1").json()["abstract_zh"] == ABSTRACT_ZH
    call, language = translating.calls[0], config().analysis.language
    assert (call.model, call.timeout) == (config().analysis.brief_model, translate.PI_TIMEOUT)
    assert call.stdin == f"<abstract>\n{ABSTRACT}\n</abstract>\n"
    assert f"into {language} " in call.system and "{{LANGUAGE}}" not in call.system
    with Session(engine()) as s:
        made = s.scalar(select(Analysis).where(Analysis.level == "translation"))
    assert (made.model, made.prompt_version) == (call.model, "translate.v1")
    assert made.usage["totalTokens"] == 10 and made.usage["seconds"] >= 0

    # A new abstract (a new arXiv version): the old translation is not shown, a new one made.
    with Session(engine()) as s, s.begin():
        s.get(Paper, 1).abstract = "We propose a better planner."
    assert client.get("/api/papers/1").json()["abstract_zh"] is None
    translating.answers.append("我们提出一个更好的规划器。")
    assert client.post(url).json() == {"abstract_zh": "我们提出一个更好的规划器。"}
    assert len(translating.calls) == 2

    with Session(engine()) as s, s.begin():
        agent.set_prompt(s, "translate", "Into {{LANGUAGE}}, faithfully.", s.scalar(select(User)))
        s.get(Paper, 1).abstract = ABSTRACT  # translated before: stored, no call
    assert client.post(url).json() == {"abstract_zh": ABSTRACT_ZH}
    with Session(engine()) as s, s.begin():
        s.get(Paper, 1).abstract = "A third abstract."
    translating.answers.append("第三个摘要。")
    assert client.post(url).json() == {"abstract_zh": "第三个摘要。"}
    assert translating.calls[2].system == f"Into {language}, faithfully."
    with Session(engine()) as s:
        version = s.scalar(select(Analysis.prompt_version).order_by(Analysis.id.desc()).limit(1))
    assert version.startswith("translate.v1+")


def test_abstract_translation_refusals(client, token, translating, monkeypatch):
    client.headers["Authorization"] = f"Bearer {token}"
    url = "/api/papers/1/abstract-zh"
    # Unusable twice (empty, then two paragraphs for one), or a failed call then cut off: 502.
    translating.answers += ["  ", "第一段。\n\n第二段。"]
    r = client.post(url)
    assert (r.status_code, r.json()["detail"]) == (502, "No translation: 2 paragraphs for 1")
    translating.answers += [pi.PiError("timed out after 60s"), pi.Incomplete("length", {})]
    assert client.post(url).json()["detail"] == "No translation: cut off (length)"
    with Session(engine()) as s:
        assert s.scalar(select(func.count()).select_from(Analysis)) == 0  # nothing stored
    assert client.post("/api/papers/999/abstract-zh").status_code == 404
    r = client.post("/api/papers/3/abstract-zh")
    assert (r.status_code, r.json()["detail"]) == (409, "Paper has no abstract")

    # Both translations being written, neither done within WAIT: 503, no call.
    monkeypatch.setattr(translate, "WAIT", 0)
    for _ in range(translate.WRITING):
        translate._writing.acquire()
    try:
        r = client.post(url)
    finally:
        for _ in range(translate.WRITING):
            translate._writing.release()
    assert (r.status_code, r.json()["detail"]) == (503, "The agent is busy, try again in a moment")
    assert len(translating.calls) == 4


def test_abstract_translation_on_a_busy_provider(client, token, translating, monkeypatch):
    """A 429 (pi gave up after its own retries): a short pause, then the call again."""
    slept = []
    monkeypatch.setattr(pi, "time", SimpleNamespace(sleep=slept.append))
    client.headers["Authorization"] = f"Bearer {token}"
    busy = pi.Busy('stopReason error: 429: {"message":"Upstream rate limit exceeded"}')
    translating.answers += [busy, ABSTRACT_ZH]
    assert client.post("/api/papers/1/abstract-zh").json() == {"abstract_zh": ABSTRACT_ZH}
    assert len(translating.calls) == 2 and len(slept) == 1 and 2 <= slept[0] <= 5


def test_abstract_translation_once_per_paper(token, translating):
    """Two requests for one paper at once: one call; the second waits and returns its result."""
    started, go = threading.Event(), threading.Event()

    def slow():
        started.set()
        go.wait(5)
        return ABSTRACT_ZH

    translating.answers.append(slow)  # a second call would find no answer
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(translate.translation, 1)
        started.wait(5)
        second = pool.submit(translate.translation, 1)
        time.sleep(0.2)  # waiting for the first
        go.set()
        assert first.result() == second.result() == ABSTRACT_ZH
    assert len(translating.calls) == 1


def test_notes(client, token):
    """Notes and excerpts on a paper, and one free block per member and paper; only their
    author sees or changes them.
    """
    client.headers["Authorization"] = f"Bearer {token}"
    url = "/api/papers/1/notes"
    r = client.post(url, json={"content": "  读第 3 页 "})
    assert r.status_code == 201
    note = r.json()
    assert note == {
        "id": note["id"],
        "paper_id": 1,
        "content": "读第 3 页",
        "quote": None,
        "anchor": None,
        "created_at": note["created_at"],
        "updated_at": note["created_at"],
    }
    pdf, section = {"source": "pdf", "page": 3}, {"source": "report", "section": "方法" * 100}
    highlight = client.post(url, json={"content": "", "quote": " a planner ", "anchor": pdf})
    assert highlight.status_code == 201  # an excerpt without a comment
    highlight = highlight.json()
    assert (highlight["content"], highlight["quote"], highlight["anchor"]) == ("", "a planner", pdf)
    excerpt = client.post(url, json={"content": "why?", "quote": "q" * 2000, "anchor": section})
    assert excerpt.json()["anchor"] == section
    for body in [
        {"content": "  "},  # a note without a quote needs content
        {"content": "", "quote": " "},
        {"content": "x" * 20_001},
        {"content": "x", "quote": "q" * 2001},
        {"content": "x", "quote": "q", "anchor": {"source": "pdf", "page": 0}},
        {"content": "x", "quote": "q", "anchor": {"source": "report", "section": "s" * 201}},
        {"content": "x", "quote": "q", "anchor": {"source": "web", "page": 1}},
        {"content": "x", "quote": "q", "anchor": {"source": "pdf", "page": 1, "x": 1}},
    ]:
        assert client.post(url, json=body).status_code == 422, body
    assert client.post(url, json={"content": "  "}).json()["detail"] == (
        "A note without a quote needs content"
    )
    assert client.post("/api/papers/99/notes", json={"content": "x"}).status_code == 404
    assert client.get("/api/papers/99/notes").status_code == 404
    got = client.get(url).json()
    assert got["page"] == {"content": "", "updated_at": None}
    assert [n["content"] for n in got["notes"]] == ["why?", "", "读第 3 页"]  # newest first

    # The free block: saved whole, one row, not among the notes; blank removes it.
    page_url = f"{url}/page"
    first = client.put(page_url, json={"content": "# 想法\n\n第一段\n"}).json()
    assert first["content"] == "# 想法\n\n第一段\n"  # as typed
    second = client.put(page_url, json={"content": "x" * 50_000}).json()
    assert second["updated_at"] > first["updated_at"]
    got = client.get(url).json()
    assert got["page"] == second and len(got["notes"]) == 3
    with Session(engine()) as s:
        assert s.scalar(select(func.count()).where(Note.kind == "page")) == 1
    assert client.put(page_url, json={"content": "x" * 50_001}).status_code == 422
    assert client.put("/api/papers/99/notes/page", json={"content": "x"}).status_code == 404
    mine = client.get("/api/notes").json()["items"]  # free blocks too, with every field
    assert [(n["kind"], n["quote"], n["anchor"]) for n in mine[:3]] == [
        ("page", None, None),
        ("note", "q" * 2000, section),
        ("note", "a planner", pdf),
    ]
    assert mine[0]["updated_at"] == second["updated_at"] and mine[0]["paper_title"]

    # Editing a note's content
    edited = client.patch(f"/api/notes/{note['id']}", json={"content": " 读第 4 页 "}).json()
    assert edited == note | {"content": "读第 4 页", "updated_at": edited["updated_at"]}
    assert edited["updated_at"] > note["updated_at"]
    assert client.patch(f"/api/notes/{note['id']}", json={"content": ""}).status_code == 422
    assert client.patch(f"/api/notes/{highlight['id']}", json={"content": " "}).is_success
    assert client.patch(f"/api/notes/{mine[0]['id']}", json={"content": "x"}).json() == {
        "detail": "The free block is set with PUT /api/papers/{id}/notes/page"
    }
    assert client.patch("/api/notes/999", json={"content": "x"}).status_code == 404

    with Session(engine()) as s, s.begin():
        bob = User(username="bob", password_hash=auth.hash_password("pw-bob-123"))
        s.add(bob)
        _, bob_token = auth.new_token(s, bob, "test")
    as_bob = {"Authorization": f"Bearer {bob_token}"}
    assert client.get(url, headers=as_bob).json() == {  # private
        "page": {"content": "", "updated_at": None},
        "notes": [],
    }
    bobs = client.put(page_url, json={"content": "Bob's"}, headers=as_bob).json()
    assert bobs["content"] == "Bob's" and client.get(url).json()["page"] == second  # his own
    edit = client.patch(f"/api/notes/{note['id']}", json={"content": "x"}, headers=as_bob)
    assert edit.status_code == 404
    assert client.delete(f"/api/notes/{note['id']}", headers=as_bob).status_code == 404
    assert client.delete(f"/api/notes/{note['id']}").json() == {"status": "ok"}
    assert [n["content"] for n in client.get(url).json()["notes"]] == ["why?", ""]
    assert client.put(page_url, json={"content": " \n"}).json() == {
        "content": "",
        "updated_at": None,
    }
    assert client.get(url).json()["page"] == {"content": "", "updated_at": None}
    assert client.get(url, headers=as_bob).json()["page"]["content"] == "Bob's"


def test_stars(client, token):
    star = "/api/papers/{}/star".format
    assert client.put(star(1)).status_code == 401 and client.delete(star(1)).status_code == 401
    client.headers["Authorization"] = f"Bearer {token}"
    with Session(engine()) as s, s.begin():
        upsert_papers(
            s, [_paper("2611.00004", "Diffusion, diffusion", ["cs.CV"], 21, "Diffusion.")]
        )
        bob = User(username="bob", password_hash=auth.hash_password("pw-bob-123"))
        s.add(bob)
        s.flush()
        bob_cookie = auth.make_session_cookie(bob)
    as_bob = TestClient(app)  # signed in in a browser, not with a token
    as_bob.cookies.set(auth.COOKIE, bob_cookie)

    def starred(c=client, **params):
        body = c.get("/api/papers", params={"starred": "true"} | params).json()
        return body["total"], [p["id"] for p in body["items"]]

    def flags(c=client):
        return {p["id"]: p["starred"] for p in c.get("/api/papers").json()["items"]}

    assert flags() == {1: False, 2: False, 3: False, 4: False} and starred() == (0, [])
    for paper_id in [4, 1, 3]:
        assert client.put(star(paper_id)).json() == {"starred": True}
    assert client.put(star(4)).json() == {"starred": True}  # again: keeps its first time
    assert flags() == {1: True, 2: False, 3: True, 4: True}
    when = {p["id"]: p["starred_at"] for p in client.get("/api/papers").json()["items"]}
    assert when[2] is None and when[4] < when[1] < when[3]  # starring again kept 4's time
    assert client.get("/api/papers/3").json()["starred_at"] == when[3]
    assert starred() == (3, [3, 1, 4])  # most recently starred first
    assert starred(category="cs.RO") == (2, [3, 1])
    assert starred(q="diffusion") == (2, [4, 1])  # best match first when searching
    assert client.get("/api/papers/3").json()["starred"] is True
    assert client.get("/api/papers/2").json()["starred"] is False

    # Bob sees none of Alice's stars, and Alice none of his.
    assert flags(as_bob) == {1: False, 2: False, 3: False, 4: False}
    assert as_bob.get("/api/papers/3").json()["starred_at"] is None  # Alice's, not his
    assert starred(as_bob) == (0, []) and as_bob.get("/api/papers/3").json()["starred"] is False
    assert as_bob.put(star(2)).json() == {"starred": True}
    assert starred(as_bob) == (1, [2]) and starred() == (3, [3, 1, 4])

    assert client.delete(star(1)).json() == {"starred": False}
    assert client.delete(star(1)).json() == {"starred": False}  # again: nothing to undo
    assert client.get("/api/papers/1").json()["starred_at"] is None
    assert client.delete(star(2)).json() == {"starred": False}  # never starred by Alice
    assert starred() == (2, [3, 4]) and starred(as_bob) == (1, [2])
    assert client.put(star(1)).json() == {"starred": True}
    assert starred() == (3, [1, 3, 4])  # starred anew: now the latest
    for method in (client.put, client.delete):
        assert method(star(999)).status_code == 404

    # Deleting a paper or a user deletes their stars.
    with Session(engine()) as s, s.begin():
        s.execute(delete(Paper).where(Paper.id == 3))
        s.execute(delete(User).where(User.username == "bob"))
    with Session(engine()) as s:
        assert s.scalars(select(UserPaper.paper_id).order_by(UserPaper.paper_id)).all() == [1, 4]
    assert starred() == (2, [1, 4])


def test_added(client, token, vocabulary):
    """Papers are listed by the arXiv day their latest version came out, new papers and new
    versions of older ones, whether they are briefed yet or not. Out-of-scope papers are left
    out of the list and of the counts unless included; papers not tagged yet are in scope.
    """
    client.headers["Authorization"] = f"Bearer {token}"
    with Session(engine()) as s, s.begin():
        new = [_paper(f"2611.0000{n}", f"Paper {n}", ["cs.CV"], 10) for n in (4, 5, 6, 7)]
        upsert_papers(s, new)
        for paper_id, added_on, version, status in [
            (1, date(2026, 11, 20), 1, "briefed"),
            (2, date(2026, 11, 20), 3, "new"),  # a new version of an older paper
            (3, date(2026, 11, 20), 1, "brief_failed"),
            (4, date(2026, 11, 18), 2, "briefing"),
            (5, None, 1, "briefed"),  # only its metadata changed, or not from arXiv
            (6, date(2026, 11, 20), 1, "brief_failed"),  # out of scope, tagged by hand
            (7, date(2026, 11, 21), 1, "briefed"),  # out of scope: the only paper of its day
        ]:
            values = {"added_on": added_on, "arxiv_version": version, "status": status}
            s.execute(update(Paper).where(Paper.id == paper_id).values(values))
        for paper_id in (6, 7):
            _tag(s, paper_id, "domain", "out-of-scope")
        _tag(s, 1, "domain", "robotics")
        _link(s, 1, "3D Gaussian Splatting")
        s.get(Paper, 1).paper_type = "method"
        s.get(Paper, 3).paper_type = "dataset"

    def added(day, **params):
        body = client.get("/api/papers", params={"added": day} | params).json()
        ids = sorted(p["id"] for p in body["items"])
        return body["added_day"], ids, (body["pending"], body["failed"])

    day = ("2026-11-20", [1, 2, 3], (1, 1))
    assert added("2026-11-20") == added("latest") == day
    assert added("latest", tz="Pacific/Kiritimati") == day  # arXiv's day, not the browser's
    assert added("2026-11-18") == ("2026-11-18", [4], (1, 0))
    assert added("2026-11-19") == ("2026-11-19", [], (0, 0))
    # The counts are the whole day's, whatever the filters but the scope.
    assert added("latest", added_kind="new") == ("2026-11-20", [1, 3], (1, 1))
    assert added("latest", added_kind="updated") == ("2026-11-20", [2], (1, 1))
    assert added("latest", category="cs.RO") == ("2026-11-20", [1, 3], (1, 1))
    assert added("latest", paper_type="dataset") == ("2026-11-20", [3], (1, 1))
    topic = client.get("/api/papers/1").json()["topics"][0]["id"]
    assert added("latest", topic=topic, domain="robotics") == ("2026-11-20", [1], (1, 1))
    every = {"include_out_of_scope": "true"}
    assert added("latest", **every) == ("2026-11-21", [7], (0, 0))
    assert added("2026-11-20", **every) == ("2026-11-20", [1, 2, 3, 6], (1, 2))
    client.put("/api/papers/3/star")
    assert added("latest", starred="true")[1] == [3]
    for bad in ({"added": "yesterday"}, {"added_kind": "old"}):
        assert client.get("/api/papers", params=bad).status_code == 422

    plain = client.get("/api/papers").json()
    assert plain["added_day"] is plain["pending"] is plain["failed"] is None
    assert {p["id"]: (p["added_on"], p["added_kind"]) for p in plain["items"]} == {
        1: ("2026-11-20", "new"),
        2: ("2026-11-20", "updated"),
        3: ("2026-11-20", "new"),
        4: ("2026-11-18", "updated"),
        5: (None, None),
    }  # 6 and 7 are out of scope
    assert datetime.fromisoformat(client.get("/api/papers/1").json()["created_at"]).tzinfo

    def days(**params):
        return client.get("/api/papers/added-days", params=params).json()

    assert days() == [
        {"day": "2026-11-20", "papers": 3, "new": 2, "updated": 1, "pending": 1, "failed": 1},
        {"day": "2026-11-18", "papers": 1, "new": 0, "updated": 1, "pending": 1, "failed": 0},
    ]
    assert days(include_out_of_scope="true", limit=2) == [
        {"day": "2026-11-21", "papers": 1, "new": 1, "updated": 0, "pending": 0, "failed": 0},
        {"day": "2026-11-20", "papers": 4, "new": 3, "updated": 1, "pending": 1, "failed": 2},
    ]
    assert [d["day"] for d in days(limit=1)] == ["2026-11-20"]
    assert client.get("/api/papers/added-days", params={"limit": 0}).status_code == 422

    with Session(engine()) as s, s.begin():  # no day ever, as in a library of imports
        s.execute(update(Paper).values(added_on=None))
    assert added("latest") == (None, [], (0, 0)) and days() == []


def test_collections(client, token):
    """Members group their starred papers; a collection is its owner's alone."""
    assert client.get("/api/collections").status_code == 401
    client.headers["Authorization"] = f"Bearer {token}"
    with Session(engine()) as s, s.begin():
        bob = User(username="bob", password_hash=auth.hash_password("pw-bob-123"))
        s.add(bob)
        _, bob_token = auth.new_token(s, bob, "test")
    as_bob = TestClient(app)
    as_bob.headers["Authorization"] = f"Bearer {bob_token}"

    r = client.post("/api/collections", json={"name": "Reading"})
    assert r.status_code == 201 and r.json()["name"] == "Reading" and r.json()["papers"] == 0
    reading = r.json()["id"]
    assert client.post("/api/collections", json={"name": " Reading "}).status_code == 409
    for bad in ("  ", "x" * 101):
        assert client.post("/api/collections", json={"name": bad}).status_code == 422
    mapping = client.post("/api/collections", json={"name": "SLAM"}).json()["id"]
    url = "/api/collections/{}".format
    assert client.patch(url(mapping), json={"name": "Reading"}).status_code == 409
    assert client.patch(url(mapping), json={"name": " Mapping "}).json()["name"] == "Mapping"
    assert client.patch(url(reading), json={"name": "Reading"}).status_code == 200  # its own

    paper = "/api/collections/{}/papers/{}".format
    assert not client.get("/api/papers/1").json()["starred"]
    assert client.put(paper(reading, 1)).json() == {"collections": [reading]}  # starred too
    assert client.put(paper(reading, 1)).json() == {"collections": [reading]}  # again: the same
    assert client.put(paper(mapping, 1)).json() == {"collections": [reading, mapping]}
    assert client.put(paper(mapping, 3)).json() == {"collections": [mapping]}
    assert client.put(paper(mapping, 999)).status_code == 404

    def items(c=client, **params):
        r = c.get("/api/papers", params=params)
        assert r.status_code == 200, r.text
        return {p["id"]: (p["starred"], p["collections"]) for p in r.json()["items"]}

    assert items() == {1: (True, [reading, mapping]), 2: (False, []), 3: (True, [mapping])}
    assert client.get("/api/papers/1").json()["collections"] == [reading, mapping]
    assert list(items(collection=reading)) == [1]
    assert sorted(items(collection=[reading, mapping])) == [1, 3]  # either
    assert list(items(collection=mapping, category="cs.LG")) == [1]
    assert list(items(collection=mapping, q="odometry")) == [3]
    assert sorted(items(collection=mapping, starred="true")) == [1, 3]
    assert [(c["name"], c["papers"]) for c in client.get("/api/collections").json()] == [
        ("Reading", 1),
        ("Mapping", 2),
    ]

    def groups(**params):
        body = client.get("/api/papers/facets", params=params).json()
        return body["total"], [(c["id"], c["name"], c["papers"]) for c in body["collection"]]

    assert groups() == (3, [(mapping, "Mapping", 2), (reading, "Reading", 1)])
    # choosing one leaves the group's own counts as they were
    assert groups(collection=reading) == (1, [(mapping, "Mapping", 2), (reading, "Reading", 1)])
    assert groups(category="cs.CV") == (2, [(mapping, "Mapping", 1)])

    # Bob sees none of it, cannot touch it, and cannot tell it exists.
    assert as_bob.get("/api/collections").json() == []
    assert items(as_bob)[1] == (False, [])
    for method, path in [
        ("PATCH", url(reading)),
        ("DELETE", url(reading)),
        ("PUT", paper(reading, 2)),
        ("DELETE", paper(reading, 1)),
        ("GET", f"/api/papers?collection={reading}"),
        ("GET", f"/api/papers/facets?collection={reading}"),
        ("GET", "/api/papers?collection=999999"),
    ]:
        r = as_bob.request(method, path, json={"name": "x"} if method == "PATCH" else None)
        assert r.status_code == 404, (method, path)
    theirs = as_bob.post("/api/collections", json={"name": "Reading"}).json()["id"]
    assert as_bob.put(paper(theirs, 1)).json() == {"collections": [theirs]}  # names are per member
    assert client.get("/api/papers", params={"collection": [reading, theirs]}).status_code == 404
    assert items()[1] == (True, [reading, mapping])  # not Bob's
    assert groups() == (3, [(mapping, "Mapping", 2), (reading, "Reading", 1)])
    assert items()[2] == (False, [])  # Bob's attempt put nothing in

    # Taking out keeps the star; unstarring takes it out of every collection.
    assert client.delete(paper(reading, 1)).json() == {"collections": [mapping]}
    assert client.delete(paper(reading, 1)).json() == {"collections": [mapping]}  # again
    assert client.put(paper(reading, 1)).json() == {"collections": [reading, mapping]}
    client.delete("/api/papers/1/star")
    assert items() == {1: (False, []), 2: (False, []), 3: (True, [mapping])}
    assert items(as_bob)[1] == (True, [theirs])  # Bob's star and collection stay
    assert [c["papers"] for c in client.get("/api/collections").json()] == [0, 1]
    # Deleting a collection leaves its papers starred.
    assert client.delete(url(mapping)).json() == {"status": "ok"}
    assert items()[3] == (True, [])
    assert [c["name"] for c in client.get("/api/collections").json()] == ["Reading"]
    assert client.delete(url(mapping)).status_code == 404

    # A page costs as many statements with one paper as with all, collections and all.
    for paper_id in (1, 2, 3):
        client.put(paper(reading, paper_id))
    statements = []

    def count(*args):
        statements.append(args[2])

    def cost(page_size):
        statements.clear()
        page = client.get("/api/papers", params={"page_size": page_size}).json()["items"]
        assert len(page) == page_size and all(p["collections"] == [reading] for p in page)
        return len(statements)

    event.listen(engine(), "before_cursor_execute", count)
    try:
        assert cost(1) == cost(3)
    finally:
        event.remove(engine(), "before_cursor_execute", count)


def test_covers(client, token):
    """A paper's cover is a figure (never a table) its newest report cites: the one its
    assessment named as the method overview, else the first whose caption reads like one,
    else the first. Fetched for a whole page at once.
    """
    client.headers["Authorization"] = f"Bearer {token}"
    with Session(engine()) as s, s.begin():
        upsert_papers(s, [_paper("2611.00004", "No report", ["cs.CV"], 21)])
        upsert_papers(s, [_paper("2611.00005", "Marked", ["cs.CV"], 22)])

        def report(paper_id, *cited):  # figures saved in the order cited, as report.save does
            result = {"version": 2, "markdown": "", "pdf_url": ""}
            analysis = Analysis(paper_id=paper_id, level="report", model="m",
                                prompt_version="report.v2", result=result, usage={})  # fmt: skip
            s.add(analysis)
            s.flush()
            figures = [
                Figure(
                    analysis_id=analysis.id,
                    kind=kind,
                    label=label,
                    caption=more[0] if more else label,
                    page=1,
                    image=b"png",
                    width=width,
                    height=height,
                    cover=len(more) > 1 and more[1],  # (caption, cover) may follow the size
                )  # fmt: skip
                for kind, label, width, height, *more in cited
            ]
            s.add_all(figures)
            s.flush()
            return [f.id for f in figures]

        report(1, ("figure", "Fig. 9", 10, 10, "Fig. 9", True))  # superseded, marked
        cited = [("table", "TABLE I", 800, 300), ("figure", "Fig. 3", 640, 480, "Results."),
                 ("figure", "Fig. 1", 320, 240, "Fig. 1: Overview of the PIPELINE.")]  # fmt: skip
        _, _, fig1 = report(1, *cited)  # the caption wins over citing order
        report(2, ("table", "Table 1", 800, 300, "Overview", True))  # tables only, even marked
        report(3, ("figure", "Fig. 1", 1, 1))
        report(3)  # the newest cites nothing
        cited = [("figure", "Fig. 1", 5, 5, "Fig. 1: Illustration of our method."),
                 ("figure", "Fig. 4", 7, 7, "Fig. 4: Qualitative results.", True)]  # fmt: skip
        _, fig4 = report(5, *cited)  # the assessment's mark wins over captions

    covers = {p["id"]: p["cover"] for p in client.get("/api/papers").json()["items"]}
    url = f"/api/papers/1/figures/{fig1}"
    assert covers == {
        1: {"url": url, "width": 320, "height": 240, "label": "Fig. 1"},
        2: None,
        3: None,
        4: None,
        5: {"url": f"/api/papers/5/figures/{fig4}", "width": 7, "height": 7, "label": "Fig. 4"},
    }
    assert client.get("/api/papers/1").json()["cover"] == covers[1]
    assert client.get("/api/papers/4").json()["cover"] is None
    assert client.get(url).content == b"png"

    # A page costs as many statements with one paper as with all of them, stars and all.
    for paper in client.get("/api/papers").json()["items"]:
        client.put(f"/api/papers/{paper['id']}/star")
    with Session(engine()) as s, s.begin():
        report(2, ("figure", "Fig. 2", 5, 5))
        report(4, ("figure", "Fig. 1", 5, 5))
    statements = []

    def count(*args):
        statements.append(args[2])

    def cost(page_size):
        statements.clear()
        items = client.get("/api/papers", params={"page_size": page_size}).json()["items"]
        assert len(items) == page_size and all(p["cover"] for p in items if p["id"] != 3)
        assert all(p["starred_at"] for p in items)
        return len(statements)

    event.listen(engine(), "before_cursor_execute", count)
    try:
        assert cost(1) == cost(4)
    finally:
        event.remove(engine(), "before_cursor_execute", count)


class _Upstream(io.BytesIO):
    def __init__(self, body: bytes, content_type: str):
        super().__init__(body)
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(body))


def test_pdf_proxy(client, token, monkeypatch):
    opened = []

    def open_pdf(url):
        opened.append(url)
        return _Upstream(b"%PDF-1.4 fake" * 10_000, "application/pdf")

    monkeypatch.setattr(arxiv, "open_pdf", open_pdf)
    monkeypatch.setattr(arxiv, "REQUEST_INTERVAL", 0)
    assert client.get("/api/papers/1/pdf").status_code == 401
    client.headers["Authorization"] = f"Bearer {token}"
    r = client.get("/api/papers/1/pdf")
    assert r.status_code == 200 and r.content == b"%PDF-1.4 fake" * 10_000
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "private, max-age=86400" and r.headers["etag"]
    assert r.headers["content-disposition"] == 'inline; filename="2611.00001v1.pdf"'
    assert opened == ["https://arxiv.org/pdf/2611.00001v1"]  # the stored link, nothing else
    assert arxiv_lock_free()  # let go before the response, not after it

    def no_lock():
        raise AssertionError("a revalidation must not wait for arXiv")

    monkeypatch.setattr(ingest, "arxiv_lock", no_lock)
    again = client.get("/api/papers/1/pdf", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and len(opened) == 1  # the browser's copy is still good
    monkeypatch.undo()
    monkeypatch.setattr(arxiv, "REQUEST_INTERVAL", 0)

    assert client.get("/api/papers/99/pdf").status_code == 404
    with Session(engine()) as s, s.begin():
        s.get(Paper, 3).pdf_url = None
    assert client.get("/api/papers/3/pdf").status_code == 404
    github = _Upstream(b"%PDF-1.5 pmlr", "application/octet-stream")  # how PMLR's PDFs come
    monkeypatch.setattr(arxiv, "open_pdf", lambda url: github)
    assert client.get("/api/papers/2/pdf").content == b"%PDF-1.5 pmlr"


def test_pdf_proxy_failures_let_go_of_the_lock(client, token, monkeypatch):
    client.headers["Authorization"] = f"Bearer {token}"
    monkeypatch.setattr(arxiv, "REQUEST_INTERVAL", 0)

    def unreachable(url):
        raise OSError("connection refused")

    for upstream in (
        lambda url: _Upstream(b"<html>", "text/html"),  # an error page
        unreachable,
        lambda url: _Upstream(b"%PDF" + b"x" * 100, "application/pdf"),  # over the limit
    ):
        monkeypatch.setattr(arxiv, "open_pdf", upstream)
        monkeypatch.setattr(arxiv, "MAX_PDF", 50)
        r = client.get("/api/papers/1/pdf")
        assert r.status_code == 502 and r.json()["detail"].startswith("Could not fetch the PDF")
        assert arxiv_lock_free()


def test_pdf_download_has_a_deadline(client, token, monkeypatch):
    """A server trickling bytes, each read well within the socket timeout: the download stops
    PDF_DEADLINE seconds after it started (a fake clock, 10 s a chunk), the relay answers 502
    and the lock is free. One that is done in time is served.
    """
    client.headers["Authorization"] = f"Bearer {token}"
    clock = [0.0]

    class Trickle(_Upstream):
        def __init__(self, chunks):
            super().__init__(b"", "application/pdf")
            self.chunks = chunks

        def read1(self, size=-1):
            clock[0] += 10
            return next(self.chunks, b"")

    fake_time = SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: None)
    monkeypatch.setattr(arxiv, "time", fake_time)
    monkeypatch.setattr(arxiv, "open_pdf", lambda url: Trickle(iter([b"%PDF"] + [b"x"] * 7)))
    r = client.get("/api/papers/1/pdf")
    assert r.status_code == 200 and r.content == b"%PDF" + b"x" * 7  # 80 s
    endless = iter(lambda: b"x", None)
    monkeypatch.setattr(arxiv, "open_pdf", lambda url: Trickle(endless))
    r = client.get("/api/papers/1/pdf")
    assert r.status_code == 502 and arxiv_lock_free()
    assert r.json() == {
        "detail": "Could not fetch the PDF: "
        "https://arxiv.org/pdf/2611.00001v1 took longer than 90 s to download"
    }


def test_pdf_proxy_falls_back_to_arxiv(client, token, monkeypatch):
    """A proceedings site that answers with a bot check instead of the PDF: the relay serves
    the paper's arXiv version; a paper without one gets a 502 (the stored link may still open
    in a browser). A link that works, or one on arXiv already, is the only one asked.
    """
    client.headers["Authorization"] = f"Bearer {token}"
    rss = "https://www.roboticsproceedings.org/rss22/p001.pdf"
    asked, down = [], {rss}

    def open_pdf(url):
        asked.append(url)
        if url in down:
            return _Upstream(b"<title>One moment, please...</title>", "text/html")
        return _Upstream(b"%PDF-1.4 " + url.encode(), "application/pdf")

    def get(paper_id):
        asked.clear()
        return client.get(f"/api/papers/{paper_id}/pdf")

    monkeypatch.setattr(arxiv, "open_pdf", open_pdf)
    monkeypatch.setattr(arxiv, "REQUEST_INTERVAL", 0)
    with Session(engine()) as s, s.begin():
        s.get(Paper, 1).pdf_url = rss  # also arXiv 2611.00001v1
        s.get(Paper, 2).pdf_url, s.get(Paper, 2).arxiv_id = rss, None  # only there
        s.get(Paper, 3).pdf_url = "https://proceedings.mlr.press/v1/p.pdf"
    r = get(1)
    assert r.status_code == 200 and r.content == b"%PDF-1.4 https://arxiv.org/pdf/2611.00001v1"
    assert asked == [rss, "https://arxiv.org/pdf/2611.00001v1"] and arxiv_lock_free()
    r = get(2)
    assert r.status_code == 502 and asked == [rss] and arxiv_lock_free()
    assert r.json() == {
        "detail": f"Could not fetch the PDF: {rss} answered with text/html, not a PDF"
    }
    assert get(3).status_code == 200 and asked == ["https://proceedings.mlr.press/v1/p.pdf"]
    with Session(engine()) as s, s.begin():
        s.get(Paper, 3).pdf_url = "https://arxiv.org/pdf/2611.00003v1"
    down.add("https://arxiv.org/pdf/2611.00003v1")
    assert get(3).status_code == 502 and asked == ["https://arxiv.org/pdf/2611.00003v1"]


def test_pdf_proxy_does_not_wait_for_the_client(token, monkeypatch):
    """The lock is let go before the response goes out, whether the browser reads it slowly,
    goes away or never starts. A streamed response let go of it only once its body was read
    to the end, so a browser that stopped reading kept it taken.
    """
    monkeypatch.setattr(arxiv, "REQUEST_INTERVAL", 0)
    monkeypatch.setattr(arxiv, "open_pdf", lambda url: _Upstream(b"%PDF-1.4", "application/pdf"))
    request = Request({"type": "http", "method": "GET", "headers": []})
    with Session(engine()) as session:
        response = api.paper_pdf(1, request, session)  # held, as the server holds it
        assert arxiv_lock_free()  # while nobody has read a byte of it
        assert response.body == b"%PDF-1.4"


def _assess(paper_id, innovation):
    with Session(engine()) as s, s.begin():
        result = {"type": innovation, "reason": f"{innovation}的理由。"}
        s.add(Analysis(paper_id=paper_id, level="assessment", model="m",
                       prompt_version="assess.v3", result=result, usage={}))  # fmt: skip
        s.get(Paper, paper_id).innovation = innovation


def test_innovation(client, token):
    client.headers["Authorization"] = f"Bearer {token}"
    detail = client.get("/api/papers/2").json()
    assert detail["innovation"] is None and detail["innovation_reason"] is None
    _assess(1, "机制创新")  # replaced by the next one
    _assess(1, "模块组合")
    _assess(3, "系统集成")
    detail = client.get("/api/papers/1").json()
    assert detail["innovation"] == "模块组合" and detail["innovation_reason"] == "模块组合的理由。"

    def ids(**params):
        items = client.get("/api/papers", params=params).json()["items"]
        return [(p["id"], p["innovation"]) for p in items]

    assert ids() == [(3, "系统集成"), (2, None), (1, "模块组合")]
    assert ids(innovation="系统集成") == [(3, "系统集成")]
    assert ids(innovation="模块组合", category="cs.RO") == [(1, "模块组合")]
    assert ids(innovation="模块组合", category="cs.CV", q="segmentation") == []
    assert ids(innovation="机制创新") == []  # only the newest assessment counts
    assert client.get("/api/papers", params={"innovation": "渐进"}).status_code == 422


def test_sources_and_years(client, token):
    with Session(engine()) as s, s.begin():
        upsert_papers(
            s,
            [
                {"source": "CVPR", "year": 2025, "track": "main", "venue_key": "CVPR:/p"}
                | {"title": "Old conference paper", "authors": "Ann Lee"},
                {"source": "ICRA", "year": 2024, "track": "main", "venue_key": "ICRA:W1"}
                | {"title": "Robot paper", "authors": "Bo Chen", "categories": ["cs.RO"]},
            ],
        )
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.get("/api/sources").json() == [
        {"source": "arXiv", "year": 2026, "papers": 3},
        {"source": "CVPR", "year": 2025, "papers": 1},
        {"source": "ICRA", "year": 2024, "papers": 1},
    ]

    def titles(**params):
        return [p["title"] for p in client.get("/api/papers", params=params).json()["items"]]

    assert titles(source="CVPR") == ["Old conference paper"]
    assert titles(source=["CVPR", "ICRA"]) == ["Robot paper", "Old conference paper"]  # or
    assert titles(year=[2024, 2025]) == ["Robot paper", "Old conference paper"]
    assert titles(source=["arXiv", "ICRA"], year=2024) == ["Robot paper"]  # and
    assert len(titles(source="arXiv", year=2026)) == 3 and titles(year=2023) == []
    assert titles(category="cs.RO", source="ICRA") == ["Robot paper"]
    assert titles(source="Nature") == []  # not a source: nothing, not an error
    assert client.get("/api/papers", params={"year": "twenty"}).status_code == 422
    item = client.get("/api/papers", params={"source": "CVPR"}).json()["items"][0]
    assert (item["source"], item["year"], item["track"]) == ("CVPR", 2025, "main")

    def facets(**params):
        found = client.get("/api/papers/facets", params=params).json()
        return found["total"], found["source"], found["year"]

    every_source = [
        {"value": "arXiv", "papers": 3},  # most papers first
        {"value": "CVPR", "papers": 1},
        {"value": "ICRA", "papers": 1},
    ]
    # each group counts under every filter but its own
    assert facets(source="CVPR") == (1, every_source, [{"value": 2025, "papers": 1}])
    assert facets(year=[2025, 2024]) == (
        2,
        [{"value": "CVPR", "papers": 1}, {"value": "ICRA", "papers": 1}],
        [
            {"value": 2026, "papers": 3},  # newest first
            {"value": 2025, "papers": 1},
            {"value": 2024, "papers": 1},
        ],
    )
