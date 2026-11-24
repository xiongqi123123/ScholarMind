"""Questions to the agent about a paper: one conversation per member and paper, kept until they
clear it. pi answers in the api process, not on the queue (a question must not wait behind
brief batches), from the paper's full text, its latest 论文解读 and the conversation so far,
and the answer is streamed as it is written. Bounded: WRITING answers at once in a process,
one per member and paper (an advisory lock on a connection held for the request, so a crash
or restart frees it), PI_TIMEOUT for one answer.
"""

import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime

import pypdfium2 as pdfium
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Integer, cast, delete, func, select
from sqlalchemy.orm import Session

from . import agent, pi, queries, report
from .config import config
from .db import ChatMessage, Paper, engine

log = logging.getLogger(__name__)

PI_TIMEOUT = 180
WRITING = 2  # answers being written at once in this process
HISTORY = 20  # the newest messages of the conversation the model gets,
HISTORY_CHARS = 40_000  # as many of them as fit in this (the paper takes up to 240k)
_writing = threading.BoundedSemaphore(WRITING)


class Refused(Exception):
    """No answer is started: an HTTP status and what to say."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class Message(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str  # user, assistant
    content: str
    quote: str | None
    created_at: datetime


def messages(session: Session, user_id: int, paper_id: int) -> list[ChatMessage]:
    """The conversation, oldest first."""
    mine = (ChatMessage.user_id == user_id) & (ChatMessage.paper_id == paper_id)
    return session.scalars(select(ChatMessage).where(mine).order_by(ChatMessage.id)).all()


def clear(session: Session, user_id: int, paper_id: int):
    mine = (ChatMessage.user_id == user_id) & (ChatMessage.paper_id == paper_id)
    session.execute(delete(ChatMessage).where(mine))


def _tag(name: str, text: str) -> str:
    return f"<{name}>\n{text}\n</{name}>\n"


def _question(content: str, quote: str | None) -> str:
    return (_tag("quote", quote) if quote else "") + _tag("question", content)


def _context(session: Session, user_id: int, paper_id: int, pages: list[str]) -> str:
    """What the model reads before the question: the paper, its latest reading, and the newest
    of the conversation (HISTORY, HISTORY_CHARS).
    """
    paper, note = report.paper_input(pages)
    parts = [_tag("paper", paper.rstrip()) + note + "\n"]
    if reading := queries.latest_report(session, paper_id):
        parts.append(_tag("reading", reading["markdown"]))
    mine = (ChatMessage.user_id == user_id) & (ChatMessage.paper_id == paper_id)
    newest = select(ChatMessage).where(mine).order_by(ChatMessage.id.desc()).limit(HISTORY)
    kept, size = [], 0
    for message in session.scalars(newest):
        size += len(message.content) + len(message.quote or "")
        if size > HISTORY_CHARS:
            break
        kept.append(message)
    if kept:
        turns = [
            _tag("user", _question(m.content, m.quote))
            if m.role == "user"
            else _tag("assistant", m.content)
            for m in reversed(kept)
        ]
        parts.append(_tag("conversation", "".join(turns)))
    return "\n".join(parts)


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def ask(user_id: int, paper_id: int, content: str, quote: str | None) -> Iterator[str]:
    """Start an answer to a question about the paper and return its server-sent events: `delta`
    {"text"} as it is written, then `done` {"user", "assistant"} (Messages, both stored now)
    or `error` {"detail"} (nothing stored). Refused before it starts: 404, 409 (no PDF link,
    no text, an answer to this member on this paper still being written), 503 (WRITING answers
    being written), 502 (the PDF could not be fetched).
    """
    with Session(engine()) as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            raise Refused(404, "Paper not found")
        if not paper.pdf_url:
            raise Refused(409, "Paper has no PDF link")
    connection = engine().connect()
    # two int keys: a space of advisory locks only this uses (the others are single bigints)
    lock = func.pg_try_advisory_lock(cast(user_id, Integer), cast(paper_id, Integer))
    taken = False
    try:
        if not connection.scalar(select(lock)):
            raise Refused(409, "An answer is still being written")
        connection.commit()  # the lock is the session's: not idle in a transaction for minutes
        if not _writing.acquire(blocking=False):
            raise Refused(503, "The agent is busy, try again in a moment")
        taken = True
        try:
            _, pages = report.paper_pages(paper_id)  # downloaded the first time
        except (OSError, ValueError) as e:
            raise Refused(502, f"Could not fetch the paper's PDF: {e}") from None
        except pdfium.PdfiumError:
            raise Refused(409, "The paper's PDF cannot be read") from None
        if not any(page.strip() for page in pages):
            raise Refused(409, "The paper's PDF has no text layer")
        with Session(engine()) as session:
            stdin = _context(session, user_id, paper_id, pages)
            version, system = agent.prompt(session, "chat")
            endpoint = agent.model(session)
        system = system.replace("{{LANGUAGE}}", config().analysis.language)
        prompt = _question(content, quote)
        written = pi.stream(
            endpoint["report_model"], system, prompt, PI_TIMEOUT, stdin=stdin, endpoint=endpoint
        )
    except BaseException:
        if taken:
            _writing.release()
        connection.invalidate()  # its session, and the lock with it, ends
        connection.close()
        raise

    def release():
        written.close()  # kills pi if it is still writing
        _writing.release()
        connection.invalidate()
        connection.close()

    question = dict(user_id=user_id, paper_id=paper_id, content=content, quote=quote)
    meta = dict(model=endpoint["report_model"], prompt_version=version)
    events = _answer(written, release, question, meta)
    next(events)  # started: whatever ends the response from here on, `release` runs
    return events


def _answer(
    written: Iterator[str], release: Callable[[], None], question: dict, meta: dict
) -> Iterator[str]:
    started = time.monotonic()
    try:
        yield ""
        try:
            while True:
                try:
                    yield _event("delta", {"text": next(written)})
                except StopIteration as end:
                    result = end.value
                    break
            if not result.text.strip():
                raise pi.PiError("an empty answer")
        except (pi.PiError, pi.Incomplete) as e:
            log.warning("answer to user %d on paper %d failed: %s", question["user_id"],
                        question["paper_id"], e)  # fmt: skip
            yield _event("error", {"detail": f"No answer: {e}"})
            return
        usage = result.usage | {"seconds": round(time.monotonic() - started)}
        with Session(engine()) as session, session.begin():
            stored = [
                ChatMessage(role="user", **question),
                ChatMessage(
                    role="assistant",
                    user_id=question["user_id"],
                    paper_id=question["paper_id"],
                    content=result.text,
                    usage=usage,
                    **meta,
                ),
            ]
            session.add_all(stored)
            session.flush()
            for message in stored:
                session.refresh(message)  # created_at comes from the database
            user, assistant = (Message.model_validate(m).model_dump(mode="json") for m in stored)
        log.info("answered user %d on paper %d: %s", question["user_id"], question["paper_id"],
                 usage)  # fmt: skip
        yield _event("done", {"user": user, "assistant": assistant})
    finally:
        release()
