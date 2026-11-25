"""RQ queues and jobs.

`fetch` talks to data sources and is meant for a single worker; `analyze` can have many.
`manual` has the reports members asked for: an analyze worker takes from it first (cli.worker).
Jobs are idempotent, so a duplicate enqueue or a second worker is harmless.
"""

import logging
import time
from datetime import date
from functools import cache

from redis import Redis
from rq import Queue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import brief, report, venues
from .config import config, settings
from .db import Paper, engine
from .ingest import sync_arxiv

log = logging.getLogger(__name__)

FETCH = "fetch"
ANALYZE = "analyze"
MANUAL = "manual"
SYNC_TIMEOUT = 3600
IMPORT_SLICE = 600  # seconds a queued import works, then it queues the rest behind other jobs
IMPORT_TIMEOUT = 3600  # one slice, the listing and the arXiv lookups at the end


@cache
def redis() -> Redis:
    return Redis.from_url(settings().redis_url)


def sync_job(since: str | None = None, until: str | None = None) -> dict:
    result = sync_arxiv(
        date.fromisoformat(since) if since else None,
        date.fromisoformat(until) if until else None,
    )
    log.info("enqueued %d brief jobs", enqueue_briefs())
    with Session(engine()) as session, session.begin():
        report.queue_automatic(session)  # today's room, for papers that waited past a limit
    log.info("enqueued %d report jobs", enqueue_reports())
    return result


def enqueue_sync(since: str | None = None, until: str | None = None):
    queue = Queue(FETCH, connection=redis())
    return queue.enqueue(sync_job, since, until, job_timeout=SYNC_TIMEOUT)


def import_job(
    name: str,
    year: int,
    limit: int | None = None,
    briefs: bool = True,
    whole: bool = False,
    then: list | None = None,
) -> dict:
    """Import one conference edition (see venues.import_venue). A queued job works for about
    IMPORT_SLICE, then queues the rest at the back of the fetch queue, so the daily sync does
    not wait hours behind CVPR; whole=True works to the end (the command line). New papers
    get briefs queued; with briefs=False they are held instead (venues.hold), so the daily
    sync does not brief them either. An edition the source does not list yet is not a
    failure. `then`: [venue, year] pairs to import afterwards, one at a time, even when this
    one fails (import-venues).
    """
    until = None if whole else time.monotonic() + IMPORT_SLICE
    try:
        result = venues.import_venue(name, year, limit, until)
    except venues.NotPublished as e:
        log.info("%s %d not published: %s", name, year, e)
        result = {"status": "not_published", "detail": str(e), "done": True}
    except Exception:
        if then:
            enqueue_import(*then[0], briefs=briefs, then=then[1:])
        raise
    if not briefs:
        venues.hold(name, year)
    elif result.get("inserted"):
        log.info("enqueued %d brief jobs", enqueue_briefs(result["inserted"]))
    if not result["done"]:
        rest = enqueue_import(name, year, limit, briefs, then)
        log.info("%s %d: the rest continues as job %s", name, year, rest.id)
    elif then:
        enqueue_import(*then[0], briefs=briefs, then=then[1:])
    return result


def enqueue_import(
    name: str, year: int, limit: int | None = None, briefs: bool = True, then: list | None = None
):
    queue = Queue(FETCH, connection=redis())
    return queue.enqueue(
        import_job,
        args=(name, year, limit, briefs),
        kwargs={"then": [list(edition) for edition in then or []]},
        job_timeout=IMPORT_TIMEOUT,
    )


def import_venues(
    since: int,
    until: int,
    names: list[str] | None = None,
    briefs: bool = True,
    dry_run: bool = False,
):
    """The editions of these venues (default: all) from `until` back to `since` not imported
    yet (venues.editions), and the first job of their imports, queued one after another,
    newest first; None if nothing was queued (nothing to import, or dry_run).
    """
    plan = venues.editions(since, until, names)
    if not plan or dry_run:
        return plan, None
    return plan, enqueue_import(*plan[0], briefs=briefs, then=plan[1:])


def venues_job() -> list[tuple[str, int]]:
    """Weekly: queue an import of every edition that is due (venues.due_editions)."""
    due = venues.due_editions()
    for name, year in due:
        enqueue_import(name, year)
    return due


def enqueue_briefs(limit: int | None = None) -> int:
    """Queue one job per batch of papers waiting for a brief, for at most `limit` papers;
    returns the number of jobs. A job briefs whichever papers are new when it runs, so extra
    jobs find nothing to do.
    """
    size = config().analysis.brief_batch_size
    with Session(engine()) as session:
        waiting = session.scalar(select(func.count()).where(Paper.status == "new"))
    papers = min(waiting, limit) if limit else waiting
    batches = [min(size, papers - start) for start in range(0, papers, size)]
    queue = Queue(ANALYZE, connection=redis())
    for batch in batches:
        queue.enqueue(brief_job, batch, job_timeout=brief.JOB_TIMEOUT)
    return len(batches)


def brief_job(limit: int | None = None) -> dict:
    """Brief the next batch of new papers, then queue reports for those matching
    analysis.report_auto.
    """
    result = brief.brief_batch(limit)
    if result.get("status") == "briefed":
        with Session(engine()) as session, session.begin():
            queued = report.queue_automatic(session)
        enqueue_reports(queued)  # after the commit, or a worker could find nothing queued
    return result


def enqueue_reports(paper_ids: list[int] | None = None) -> int:
    """Queue one job per paper, on MANUAL if a member asked for its report, else on ANALYZE,
    in the order they were queued; by default for every report that is queued, or running for
    longer than a worker could (its worker died). Returns the number of jobs. A job whose
    report someone else already took does nothing.
    """
    if paper_ids is None:
        stale = (Paper.report_status == "reporting") & (
            Paper.report_claimed_at < func.now() - report.STALE_CLAIM
        )
        waiting = (Paper.report_status == "queued") | stale
    else:
        waiting = Paper.id.in_(paper_ids)
    with Session(engine()) as session:
        rows = session.execute(
            select(Paper.id, Paper.report_trigger)
            .where(waiting)
            .order_by(Paper.report_queued_at, Paper.id)
        ).all()
    for paper_id, trigger in rows:
        queue = Queue(MANUAL if trigger == "manual" else ANALYZE, connection=redis())
        queue.enqueue(report.report_paper, paper_id, job_timeout=report.JOB_TIMEOUT)
    return len(rows)
