"""What an admin changes on the web, kept in the settings table: about the agent, the connection
(the base URL with its API key, the key encrypted with a key derived from SECRET_KEY), the model
names and the prompts; besides, the arXiv categories to sync and whether the first-run setup is
done. Without a row the defaults hold: LLM_API_KEY, config.yaml's analysis and arxiv sections
and the files in prompts/. Read at every use, never cached, so a change reaches the next job in
every worker process.
"""

import base64
import hashlib
import http.client
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import arxiv
from .config import config, settings
from .db import Setting, User

log = logging.getLogger(__name__)

API_KEY = "llm_api_key"
NAMES = ("brief_model", "report_model")  # set one by one; base_url only with the connection
MODEL = ("base_url", *NAMES)  # of config.yaml's analysis; rows model:<name>
PROBE_SECONDS = 15  # a whole model list request, connect to last byte
PROBE_MAX = 2**20
# The prompts by key, each with its default: prompts/<version>.md. An edited text records the
# version <default>+<first 8 hex of its SHA-256> with what it makes.
PROMPTS = {
    "brief": "brief.v2",
    "report": "report.v2",
    "assess": "assess.v3",
    "topic-review": "topic-review.v1",
    "chat": "chat.v1",
    "translate": "translate.v1",
}
# what the code fills in: brief.system_prompt, chat.ask, translate.translation
PLACEHOLDERS = {
    "brief": ("{{VOCABULARY}}", "{{LANGUAGE}}"),
    "chat": ("{{LANGUAGE}}",),
    "translate": ("{{LANGUAGE}}",),
}
ARXIV = "arxiv:categories"  # a JSON list
MAX_CATEGORIES = 20
SETUP_DONE = "setup:done"


class MissingPlaceholders(ValueError):
    pass


class InvalidSetting(ValueError):
    pass


class ProbeFailed(Exception):
    """The provider gave no model list; the message says what happened, never what it said."""


def _fernet() -> Fernet:
    secret = hashlib.sha256(b"scholarmind settings\0" + settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(secret))


def _put(session: Session, key: str, value: str, admin: User):
    row = dict(key=key, value=value, updated_by=admin.id)
    changed = dict(value=value, updated_by=admin.id, updated_at=func.now())
    session.execute(
        insert(Setting).values(row).on_conflict_do_update(index_elements=["key"], set_=changed)
    )


def _web_api_key(session: Session) -> str | None:
    """The key set on the web; None if there is none, or it cannot be read (SECRET_KEY changed)."""
    row = session.get(Setting, API_KEY)
    if row is None:
        return None
    try:
        return _fernet().decrypt(row.value.encode()).decode()
    except InvalidToken:
        log.warning("the model API key set on the web cannot be read (SECRET_KEY changed?); "
                    "using LLM_API_KEY")  # fmt: skip
        return None


def api_key(session: Session) -> str:
    """The model API key pi gets: the web's if one is set, else LLM_API_KEY."""
    return _web_api_key(session) or settings().llm_api_key


def api_key_state(session: Session) -> dict:
    """Whether there is a key and where it comes from, with its last 4 characters if it is long
    enough for that to give nothing away. Never the key itself.
    """
    web = _web_api_key(session)
    key, source = (web, "web") if web else (settings().llm_api_key, "env")
    return {
        "api_key_set": bool(key),
        "api_key_hint": "…" + key[-4:] if len(key) >= 12 else None,
        "api_key_source": source if key else None,
    }


def _web_model(session: Session) -> dict[str, str]:
    rows = select(Setting.key, Setting.value).where(Setting.key.in_([f"model:{n}" for n in MODEL]))
    return {key.removeprefix("model:"): value for key, value in session.execute(rows)}


def model(session: Session) -> dict[str, str]:
    """base_url, brief_model and report_model as a job uses them now: each the web's if an admin
    set it, else config.yaml's (where report_model defaults to brief_model; a brief_model set on
    the web leaves report_model alone).
    """
    web, analysis = _web_model(session), config().analysis
    return {name: web.get(name) or getattr(analysis, name) for name in MODEL}


def model_state(session: Session) -> dict[str, dict]:
    web, analysis = _web_model(session), config().analysis
    return {
        name: {"value": web[name], "source": "web"}
        if name in web
        else {"value": getattr(analysis, name), "source": "config"}
        for name in MODEL
    }


def set_model(session: Session, name: str, value: str, admin: User):
    """A model name (one of NAMES); InvalidSetting unless it is 1-128 characters without
    whitespace, stripped first.
    """
    value = value.strip()
    if not re.fullmatch(r"\S{1,128}", value):
        raise InvalidSetting(f"{name} must be 1-128 characters without spaces")
    _put(session, f"model:{name}", value, admin)
    log.info("%s set %s to %s", admin.username, name, value)


def reset_model(session: Session, name: str, admin: User):
    removed = session.execute(
        delete(Setting).where(Setting.key == f"model:{name}").returning(Setting.value)
    ).scalar()
    if removed is not None:
        log.info(
            "%s removed %s %s set on the web; config.yaml again", admin.username, name, removed
        )


def _origin(url: str) -> tuple:
    """Scheme, host and port: where a request to this URL goes."""
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, parts.hostname, parts.port or {"http": 80, "https": 443}.get(parts.scheme)


def connection(
    session: Session, base_url: str | None = None, key: str | None = None
) -> tuple[str, str]:
    """(base URL, API key) with what an admin gave, None for what is in effect now. The key in
    effect only goes to the host it is used with: the web never shows it, so a base URL on
    another host needs its own key. InvalidSetting for that, or a base URL that is not an http(s)
    URL with a host and at most 2048 characters (stripped first).
    """
    current = model(session)["base_url"]
    if base_url is None:
        base_url = current
    else:
        base_url = base_url.strip()
        try:
            scheme, host, _ = _origin(base_url)
            ok = scheme in ("http", "https") and bool(host) and len(base_url) <= 2048
        except ValueError:  # a broken IPv6 address, a port that is no number
            ok = False
        if not ok:
            raise InvalidSetting(
                "base_url must be an http or https URL with a host, at most 2048 characters"
            )
    if key is None:
        if _origin(base_url) != _origin(current):
            raise InvalidSetting("A different host needs its own API key")
        key = api_key(session)
    return base_url, key


def set_connection(session: Session, base_url: str, key: str | None, admin: User):
    """The base URL, and the key with it if one is given (else the key in effect stays: only
    on the same host, see connection).
    """
    base_url, _ = connection(session, base_url, key)
    _put(session, "model:base_url", base_url, admin)
    if key is not None:
        _put(session, API_KEY, _fernet().encrypt(key.encode()).decode(), admin)
    with_key = " with a new API key" if key is not None else ""
    log.info("%s set the model connection to %s%s", admin.username, base_url, with_key)


def reset_connection(session: Session, admin: User):
    keys = ["model:base_url", API_KEY]
    if session.execute(delete(Setting).where(Setting.key.in_(keys)).returning(Setting.key)).all():
        log.info("%s removed the model connection set on the web; config.yaml's base URL and "
                 "LLM_API_KEY again", admin.username)  # fmt: skip


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect is an answer (an HTTPError): the key goes nowhere else


# Straight to the provider, never through HTTPS_PROXY: pi gets no proxy variables (pi.run), so
# a probe that works means jobs will.
_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def list_models(base_url: str, key: str) -> list[str]:
    """The ids of the models the provider lists, sorted: GET {base_url}/models, the
    OpenAI-compatible list. ProbeFailed if there is none in PROBE_SECONDS.
    """
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {key}", "User-Agent": "ScholarMind/0.1"}
    started = time.monotonic()
    try:
        with _direct.open(urllib.request.Request(url, headers=headers), timeout=PROBE_SECONDS) as r:
            body = arxiv.read_body(url, r, started, PROBE_MAX, PROBE_SECONDS)
    except urllib.error.HTTPError as e:
        e.close()
        raise ProbeFailed(f"{e.code} from {url}") from None
    except TimeoutError:
        raise ProbeFailed(f"{url} took longer than {PROBE_SECONDS} s") from None
    except http.client.HTTPException:  # not HTTP: what came back stays unsaid
        raise ProbeFailed(f"{url} did not answer with a model list") from None
    except (OSError, ValueError) as e:  # refused, no such host, TLS: said here, not by the server
        reason = getattr(e, "reason", e)
        if isinstance(reason, TimeoutError):
            raise ProbeFailed(f"{url} took longer than {PROBE_SECONDS} s") from None
        raise ProbeFailed(f"Could not reach {url}: {reason}") from None
    try:
        ids = sorted(m["id"] for m in json.loads(body)["data"])
        ok = len(body) <= PROBE_MAX and all(isinstance(i, str) for i in ids)
    except (ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        raise ProbeFailed(f"{url} did not answer with a model list")
    return ids


def default_prompt(key: str) -> str:
    return (settings().prompts_dir / f"{PROMPTS[key]}.md").read_text(encoding="utf-8")


def prompt(session: Session, key: str) -> tuple[str, str]:
    """(version, text) of a prompt as a job uses it now; the version goes with what it makes."""
    edited = session.get(Setting, f"prompt:{key}")
    if edited is None:
        return PROMPTS[key], default_prompt(key)
    digest = hashlib.sha256(edited.value.encode()).hexdigest()[:8]
    return f"{PROMPTS[key]}+{digest}", edited.value


def prompt_state(session: Session, key: str) -> dict:
    version, text = prompt(session, key)
    default = default_prompt(key)
    return dict(
        key=key, version=version, text=text, default_text=default, modified=version != PROMPTS[key]
    )


def set_prompt(session: Session, key: str, text: str, admin: User):
    """MissingPlaceholders if the text drops one the code fills in."""
    if missing := [p for p in PLACEHOLDERS.get(key, ()) if p not in text]:
        raise MissingPlaceholders(f"The {key} prompt must keep {', '.join(missing)}")
    _put(session, f"prompt:{key}", text, admin)


def reset_prompt(session: Session, key: str):
    session.execute(delete(Setting).where(Setting.key == f"prompt:{key}"))


def arxiv_state(session: Session) -> dict:
    """The arXiv categories the sync harvests: the web's if set, else config.yaml's."""
    row = session.get(Setting, ARXIV)
    if row is None:
        return {"categories": config().arxiv.categories, "source": "config"}
    return {"categories": json.loads(row.value), "source": "web"}


def set_arxiv_categories(session: Session, categories: list[str], admin: User):
    """InvalidSetting unless 1-MAX_CATEGORIES categories arXiv has (arxiv.CATEGORIES), each
    stripped first and repeats dropped: one it does not have would stop every daily sync.
    """
    categories = list(dict.fromkeys(c.strip() for c in categories))
    if not 1 <= len(categories) <= MAX_CATEGORIES:
        raise InvalidSetting(f"Give 1-{MAX_CATEGORIES} arXiv categories")
    if bad := [c for c in categories if c not in arxiv.CATEGORIES]:
        raise InvalidSetting(
            f"Not an arXiv category (like cs.CV or hep-th, see https://arxiv.org/category_taxonomy):"
            f" {', '.join(bad)}"
        )
    _put(session, ARXIV, json.dumps(categories), admin)
    log.info("%s set the arXiv categories to %s", admin.username, ", ".join(categories))


def reset_arxiv_categories(session: Session, admin: User):
    if session.execute(delete(Setting).where(Setting.key == ARXIV).returning(Setting.key)).first():
        log.info(
            "%s removed the arXiv categories set on the web; config.yaml's again", admin.username
        )


def setup_done(session: Session) -> bool:
    return session.get(Setting, SETUP_DONE) is not None


def finish_setup(session: Session, admin: User):
    """Marks the first-run setup done, so the web app no longer leads admins through it."""
    _put(session, SETUP_DONE, "", admin)
