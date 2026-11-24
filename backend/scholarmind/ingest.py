"""Deduplicating ingest shared by every source, and the arXiv sync built on it."""

import logging
import re
import unicodedata
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import arxiv
from .config import config
from .db import Paper, SyncState, engine

log = logging.getLogger(__name__)

# PostgreSQL advisory lock keys (arbitrary constants, scoped to this database).
INGEST_LOCK = 7_301_001
ARXIV_LOCK = 7_301_002
FIRST_SYNC_DAYS = 3  # how far back the very first sync of a category looks


def normalize_title(title: str) -> str:
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", title).lower()).strip()


# A conference paper and its arXiv copy are one record. The conference version wins for what
# was published, arXiv for its own versioning. The PDF is the conference's when it has an open
# one, else arXiv's; but a paper with a report keeps the PDF the report was written from (its
# figures and pages are that version's) until the report is redone.
VENUE_FIELDS = {"title", "title_norm", "abstract", "authors", "doi", "source", "year", "track"}
ARXIV_FIELDS = {"arxiv_id", "arxiv_version", "primary_category", "categories", "comment"}
ARXIV_FIELDS |= {"journal_ref", "published_at", "updated_at"}
ARXIV_PDF = "https://arxiv.org/pdf/"
REPORTED = ("queued", "reporting", "reported")  # report_status of a paper that has or gets one


def _surnames(authors: str) -> set[str]:
    """Last names, lowercase ASCII: "Jörg Müller (MIT), A. Smith and B. Lee" -> muller smith lee"""
    plain = unicodedata.normalize("NFKD", re.sub(r"\([^)]*\)", "", authors))
    names = re.split(r",|\band\b", plain.encode("ascii", "ignore").decode().lower())
    return {words[-1] for words in map(re.compile("[a-z]+").findall, names) if words}


def same_authors(a: str, b: str) -> bool:
    """At least half of the shorter author list appears in the other, by last name."""
    x, y = _surnames(a), _surnames(b)
    return bool(x and y) and 2 * len(x & y) >= min(len(x), len(y))


def _find(session: Session, record: dict) -> Paper | None:
    """The stored paper this record is, or None. Merging is conservative, because a wrong
    merge loses a paper while a missed one only leaves a duplicate:

    1. The same proceedings entry (venue_key): a re-import.
    2. The same arXiv ID, except that a proceedings entry never takes over an arXiv paper
       that is another entry already or shares too few authors (a wrong link on the
       conference site): the link is dropped from the record instead.
    3. The same DOI, unless both carry an arXiv ID (they differ, or step 2 had matched).
    4. The same normalized title, only if no other paper has it, they are not two
       proceedings entries, do not both carry an arXiv ID, and share enough authors
       (same_authors), so short generic titles do not merge different papers.
    """
    key, arxiv_id = record.get("venue_key"), record.get("arxiv_id")
    holder = arxiv_id and session.scalar(select(Paper).where(Paper.arxiv_id == arxiv_id))
    if key and (found := session.scalar(select(Paper).where(Paper.venue_key == key))):
        if holder and holder is not found:
            del record["arxiv_id"]  # refused when the entry was first imported
        return found
    if holder:
        if not key or (
            holder.venue_key is None and same_authors(holder.authors, record.get("authors") or "")
        ):
            return holder
        log.warning("%s: not merged with arXiv %s, authors or entry differ", key, arxiv_id)
        del record["arxiv_id"]
        arxiv_id = None
    if doi := record.get("doi"):
        query = select(Paper).where(Paper.doi == doi).order_by(Paper.id).limit(1)
        if arxiv_id:
            query = query.where(Paper.arxiv_id.is_(None))
        if found := session.scalar(query):
            return found
    same_title = select(Paper).where(Paper.title_norm == record["title_norm"]).limit(2)
    candidates = session.scalars(same_title).all()
    if len(candidates) == 1:
        found = candidates[0]
        if (
            not (arxiv_id and found.arxiv_id)
            and not (key and found.venue_key)
            and same_authors(found.authors, record.get("authors") or "")
        ):
            return found
    return None


def _keep(paper: Paper, field: str, from_venue: bool) -> bool:
    """Whether the stored value of `field` wins over the incoming record's."""
    if from_venue:
        if field == "pdf_url":
            return paper.report_status in REPORTED
        return field in ARXIV_FIELDS and getattr(paper, field) is not None
    if paper.venue_key is None:
        return False
    if field == "pdf_url":
        return paper.pdf_url is not None and not paper.pdf_url.startswith(ARXIV_PDF)
    return field in VENUE_FIELDS


def upsert_papers(session: Session, records: list[dict]) -> tuple[int, int]:
    """Insert or update papers; returns (inserted, updated). Safe to repeat and to run in parallel.

    Records from proceedings carry venue_key; the others are arXiv's. Fields that are None in
    a record leave the stored value untouched; see _find for matching and _keep for which
    side wins when a conference paper and its arXiv copy meet.
    """
    # ponytail: one global lock serializes all ingest transactions so find-then-insert cannot
    # race. Switch to per-key locks if ingest throughput ever matters.
    session.execute(select(func.pg_advisory_xact_lock(INGEST_LOCK)))
    inserted = updated = 0
    for record in records:
        record = {**record, "title_norm": normalize_title(record["title"])}
        from_venue = bool(record.get("venue_key"))
        if not from_venue and record.get("published_at"):
            record.setdefault("year", record["published_at"].year)
        paper = _find(session, record)
        if paper is None:
            session.add(Paper(**{k: v for k, v in record.items() if v is not None}))
            session.flush()
            inserted += 1
            continue
        version = record.get("arxiv_version")
        if version and paper.arxiv_version and version < paper.arxiv_version:
            continue  # stale copy of a paper we already hold a newer version of
        for key, value in record.items():
            if value is not None and not _keep(paper, key, from_venue):
                setattr(paper, key, value)
        updated += session.is_modified(paper)
    return inserted, updated


@contextmanager
def arxiv_lock():
    """Hold a session-level advisory lock so only one process talks to arXiv at a time.
    Everything that requests arXiv (sync, PDF downloads) takes it, and sleeps REQUEST_INTERVAL
    after each request before letting go. Taken per request, so none waits behind a whole
    harvest.
    """
    with engine().connect() as connection:
        connection.execute(select(func.pg_advisory_lock(ARXIV_LOCK)))
        connection.commit()
        try:
            yield
        finally:
            connection.execute(select(func.pg_advisory_unlock(ARXIV_LOCK)))
            connection.commit()


def sync_arxiv(since: date | None = None, until: date | None = None) -> dict[str, tuple[int, int]]:
    """Harvest every configured category; returns {category: (inserted, updated)}.

    Without arguments this continues from sync_state. `since`/`until` select records by the
    day they were last modified on arXiv; a run with `until` is a backfill and leaves
    sync_state alone.
    """
    result = {}
    for category in config().arxiv.categories:
        source = f"arxiv:{category}"
        with Session(engine()) as session:
            state = session.get(SyncState, source)
        start = since or (
            state.cursor if state else datetime.now(UTC).date() - timedelta(FIRST_SYNC_DAYS)
        )
        inserted = updated = 0
        server_date = None
        pages = arxiv.harvest(category, start, until)
        while True:
            with arxiv_lock():  # one page: a PDF download can come in between two
                page = next(pages, None)
            if page is None:
                break
            papers, page_date = page
            server_date = server_date or page_date
            with Session(engine()) as session, session.begin():
                added, changed = upsert_papers(session, papers)
            inserted += added
            updated += changed
        if until is None:
            # ponytail: the cursor only moves after a fully successful harvest, so a failed
            # run needs no retry logic: the next run starts from the same day again.
            with Session(engine()) as session, session.begin():
                state = session.get(SyncState, source)
                if state is None:
                    session.add(SyncState(source=source, cursor=server_date))
                else:
                    state.cursor = max(state.cursor, server_date)
        log.info("%s from %s: %d new, %d updated", source, start, inserted, updated)
        result[category] = (inserted, updated)
    return result
