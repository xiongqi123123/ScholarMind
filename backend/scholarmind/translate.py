"""A faithful translation of a paper's abstract into the analysis language, made the first time
someone opens the paper and kept (most of the library is never opened, so none is made ahead).
pi answers in the api process like chat, bounded apart from it: WRITING translations at once in
the process, a request waiting up to WAIT for one of them; one per paper at a time, so a second
request for the paper waits for the first and returns what it stored; PI_TIMEOUT a call.

A translation is an Analysis (level "translation") that keeps the hash of the abstract it
translates, so one of an abstract since changed (a new arXiv version, a conference record merged
in) is never returned, whoever changed it, and the next open makes a new one.
"""

import hashlib
import logging
import re
import threading
import time
import weakref

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import agent, pi
from .chat import Refused
from .config import config
from .db import Analysis, Paper, engine

log = logging.getLogger(__name__)

PI_TIMEOUT = 60
WRITING = 2  # translations being written at once in this process
WAIT = 15  # seconds a request waits for one of them to finish
PROMPT = "Translate the abstract now."
_writing = threading.BoundedSemaphore(WRITING)
_papers = weakref.WeakValueDictionary()  # paper id: the lock held while its translation is made
_papers_guard = threading.Lock()


class Unusable(Exception):
    pass


def source(abstract: str) -> str:
    """What a translation keeps of the abstract it translates."""
    return hashlib.sha256(abstract.encode()).hexdigest()[:16]


def stored(session: Session, paper_id: int, abstract: str) -> str | None:
    """The translation of this abstract of the paper, if one was made: one indexed lookup."""
    return session.scalar(
        select(Analysis.result["text"].astext)
        .where(
            Analysis.paper_id == paper_id,
            Analysis.level == "translation",
            Analysis.result["source"].astext == source(abstract),
        )
        .order_by(Analysis.id.desc())
        .limit(1)
    )


def _paper_lock(paper_id: int) -> threading.Lock:
    with _papers_guard:
        lock = _papers.get(paper_id)
        if lock is None:
            lock = _papers[paper_id] = threading.Lock()
        return lock


def _paragraphs(text: str) -> int:
    return len(re.split(r"\n\s*\n", text.strip()))


def translation(paper_id: int) -> str:
    """The paper's abstract translated: the stored translation, or one made now and stored.
    Refused: 404, 409 (no abstract), 503 (no translation finished within WAIT), 502 (the call
    failed or its answer was unusable, twice; nothing stored).
    """
    with Session(engine()) as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            raise Refused(404, "Paper not found")
        abstract = paper.abstract
        if not abstract.strip():
            raise Refused(409, "Paper has no abstract")
        if text := stored(session, paper_id, abstract):
            return text
    with _paper_lock(paper_id):
        with Session(engine()) as session:
            if text := stored(session, paper_id, abstract):  # made while this request waited
                return text
            version, system = agent.prompt(session, "translate")
            endpoint = agent.model(session)
        if not _writing.acquire(timeout=WAIT):
            raise Refused(503, "The agent is busy, try again in a moment")
        try:
            system = system.replace("{{LANGUAGE}}", config().analysis.language)
            text, usage = _translate(abstract, system, endpoint)
        finally:
            _writing.release()
        with Session(engine()) as session, session.begin():
            session.add(
                Analysis(
                    paper_id=paper_id,
                    level="translation",
                    model=endpoint["brief_model"],
                    prompt_version=version,
                    result={"text": text, "source": source(abstract)},
                    usage=usage,
                )
            )
    log.info("translated the abstract of paper %d: %s", paper_id, usage)
    return text


def _translate(abstract: str, system: str, endpoint: dict) -> tuple[str, dict]:
    """The translation and its usage (with seconds); one retry, then Refused 502."""
    started, stdin = time.monotonic(), f"<abstract>\n{abstract}\n</abstract>\n"
    backoff = pi.Backoff.member_call()  # a busy provider: a short pause, then again
    for _ in range(2):
        try:
            model = endpoint["brief_model"]
            result = backoff.run(model, system, PROMPT, PI_TIMEOUT, stdin=stdin, endpoint=endpoint)
            text = result.text.strip()
            if not text:
                raise Unusable("an empty answer")
            if (got := _paragraphs(text)) != (want := _paragraphs(abstract)):
                raise Unusable(f"{got} paragraphs for {want}")
            return text, result.usage | {"seconds": round(time.monotonic() - started)}
        except (pi.PiError, pi.Incomplete, Unusable) as e:
            reason = f"cut off ({e})" if isinstance(e, pi.Incomplete) else str(e)
            log.warning("translation failed: %s", reason)
    raise Refused(502, f"No translation: {reason}")
