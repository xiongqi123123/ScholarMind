"""Command line: accounts, sync, conference imports, briefs, topics, reports, worker and cron
processes."""

import argparse
import getpass
import logging
import re
import sys
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session


def _read_password() -> str:
    from .auth import MIN_PASSWORD

    if sys.stdin.isatty():
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Again: "):
            sys.exit("Passwords do not match")
    else:
        password = sys.stdin.readline().rstrip("\n")
    if len(password) < MIN_PASSWORD:
        sys.exit(f"Password must have at least {MIN_PASSWORD} characters")
    return password


def create_user(args):
    from . import auth
    from .db import engine

    email = args.email.strip().lower() if args.email else None
    if email and (len(email) > 254 or not re.fullmatch(auth.EMAIL, email)):
        sys.exit(f"Not an email address: {args.email}")
    password = _read_password()
    with Session(engine()) as session, session.begin():
        try:
            user = auth.add_user(session, args.username, password, email, args.admin)
        except auth.Taken as e:
            sys.exit(f"Another account has this {e.args[0]}")
        _, token = auth.new_token(session, user, "默认")
    print(f"Created {'admin' if args.admin else 'member'} {args.username}")
    print(f"Personal token (shown only once): {token}")


def set_password(args):
    from . import auth
    from .db import User, engine

    password = _read_password()
    with Session(engine()) as session, session.begin():
        user = session.scalar(select(User).where(User.username == args.username))
        if user is None:
            sys.exit(f"No such user: {args.username}")
        user.password_hash = auth.hash_password(password)
    print(f"Password of {args.username} set; their sessions are signed out")


def reset_token(args):
    from . import auth
    from .db import User, engine

    with Session(engine()) as session, session.begin():
        user = session.scalar(select(User).where(User.username == args.username))
        if user is None:
            sys.exit(f"No such user: {args.username}")
        _, token = auth.new_token(session, user, args.name)
    print(f"Personal token {args.name!r} (shown only once): {token}")


def sync(args):
    from . import jobs

    since = args.since.isoformat() if args.since else None
    until = args.until.isoformat() if args.until else None
    if args.enqueue:
        print(f"Enqueued job {jobs.enqueue_sync(since, until).id} on {jobs.FETCH}")
        return
    for category, (inserted, updated) in jobs.sync_job(since, until).items():
        print(f"{category}: {inserted} new, {updated} updated")


def _venue(text: str) -> str:
    from .config import config

    names = {name.lower(): name for name in config().venues}
    if (name := names.get(text.lower())) is None:
        sys.exit(f"No venue {text!r} in config.yaml: {', '.join(config().venues)}")
    return name


def import_venue(args):
    from . import jobs

    name = _venue(args.venue)
    briefs = not args.no_briefs
    if args.enqueue:
        job = jobs.enqueue_import(name, args.year, args.limit, briefs)
        print(f"Enqueued job {job.id} on {jobs.FETCH}")
        return
    result = jobs.import_job(name, args.year, args.limit, briefs, whole=True)
    print(f"{name} {args.year}: {result}")


def import_venues(args):
    from . import jobs, venues

    names = [_venue(v) for v in args.venue]
    plan = venues.editions(args.since, args.until or date.today().year, names)
    if not plan:
        print("Every edition asked for is imported already")
        return
    job = jobs.enqueue_import(*plan[0], briefs=not args.no_briefs, then=plan[1:])
    print(f"Queued {len(plan)} editions on {jobs.FETCH}, one after another (job {job.id}):")
    print(", ".join(f"{name} {year}" for name, year in plan))


def release_held(args):
    from . import venues

    released = venues.release_held(args.venue and _venue(args.venue))
    print(f"{released} papers released; the next daily sync queues their briefs")


def check_venues(args):
    from . import jobs, venues

    for name, year in venues.due_editions():
        if args.enqueue:
            print(f"{name} {year}: enqueued job {jobs.enqueue_import(name, year).id}")
        else:
            print(f"{name} {year}: {jobs.import_job(name, year, whole=True)}")


def run_briefs(args):
    from . import agent, brief, jobs
    from .config import config
    from .db import engine

    if args.redo:
        with Session(engine()) as session, session.begin():
            redone, (version, _) = brief.redo(session, args.limit), agent.prompt(session, "brief")
            print(f"{redone} papers to brief again with {version}")
    if args.enqueue:
        print(f"Enqueued {jobs.enqueue_briefs(args.limit)} brief jobs on {jobs.ANALYZE}")
        return
    size, done = config().analysis.brief_batch_size, 0
    while args.limit is None or done < args.limit:
        result = jobs.brief_job(min(size, args.limit - done) if args.limit else None)
        if not result["papers"]:
            break
        done += result["papers"]
        print(f"{result['papers']} papers {result['status']}, {result['tokens']} tokens")
    print(f"Done: {done} papers")


def run_topics(args):
    from . import topics
    from .db import engine

    if args.action == "sync":
        with Session(engine()) as session, session.begin():
            added, updated = topics.sync(session)
        print(f"Starting vocabulary: {added} topics added, {updated} updated")
        return
    result = topics.review()
    for topic, name, target, target_name, reason in result["merged"]:
        print(f"merged [{topic}] {name} into [{target}] {target_name}: {reason}")
    for item, why in result["rejected"]:
        print(f"rejected {item}: {why}")
    print(f"Reviewed {result['created']} created topics, {len(result['merged'])} merged")


def run_reports(args):
    from . import jobs, report
    from .db import Paper, engine

    for key in args.papers:  # database id or arXiv id
        with Session(engine()) as session, session.begin():
            match = Paper.id == int(key) if key.isdecimal() else Paper.arxiv_id == key
            paper_id = session.scalar(select(Paper.id).where(match)) or 0
            status, enqueue = report.request(session, paper_id, args.force)
        if not enqueue:
            hint = ", --force redoes it" if status == "reported" else ""
            print(f"{key}: {status or 'no such paper'}{hint}")
        elif args.enqueue:
            jobs.enqueue_reports([paper_id])
            print(f"{key}: enqueued on {jobs.MANUAL}")
        else:
            print(f"{key}: {report.report_paper(paper_id)}")


def run_assessments(args):
    from . import assess
    from .db import Analysis, Paper, engine

    reported = select(Analysis.paper_id).where(Analysis.level == "report")
    with Session(engine()) as session:
        if args.papers:  # database id or arXiv id
            keys = args.papers
            match = [Paper.id == int(k) if k.isdecimal() else Paper.arxiv_id == k for k in keys]
            ids = [session.scalar(select(Paper.id).where(m, Paper.id.in_(reported))) for m in match]
        else:  # every reported paper not assessed yet
            assessed = select(Analysis.paper_id).where(Analysis.level == "assessment")
            query = reported.where(Analysis.paper_id.not_in(assessed)).distinct()
            ids = session.scalars(query.order_by(Analysis.paper_id)).all()
            keys = [str(paper_id) for paper_id in ids]
    for key, paper_id in zip(keys, ids, strict=True):
        print(f"{key}: {assess.assess_paper(paper_id) if paper_id else 'no such reported paper'}")


def worker(args):
    from rq import SimpleWorker, Worker

    from . import jobs
    from .config import config

    config()  # fail at startup on a missing or broken config.yaml
    # macOS aborts forked children that touch system frameworks (urllib proxy lookup, libpq),
    # so jobs run in the worker process there. Linux keeps the default fork-per-job worker.
    worker_class = SimpleWorker if sys.platform == "darwin" else Worker
    # An analyze worker takes the reports members asked for first: RQ tries queues in order.
    queues = []
    for name in args.queues:
        queues += [jobs.MANUAL, name] if name == jobs.ANALYZE else [name]
    worker_class(queues, connection=jobs.redis()).work()


def cron(args):
    from rq.cron import CronScheduler

    from . import jobs, topics
    from .config import config

    config()  # fail at startup on a missing or broken config.yaml
    # Run exactly one cron process: RQ does not coordinate several, each would enqueue the job
    # (harmless because the job is idempotent, but it wastes arXiv requests).
    scheduler = CronScheduler(connection=jobs.redis())
    scheduler.register(
        jobs.sync_job, queue_name=jobs.FETCH, cron=config().sync.cron, job_timeout=jobs.SYNC_TIMEOUT
    )
    scheduler.register(
        topics.review,
        queue_name=jobs.ANALYZE,
        cron=config().analysis.topic_review_cron,
        job_timeout=topics.REVIEW_TIMEOUT,
    )
    scheduler.register(jobs.venues_job, queue_name=jobs.FETCH, cron=config().sync.venues_cron)
    scheduler.start()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(prog="scholarmind")
    commands = parser.add_subparsers(required=True)

    p = commands.add_parser("create-user", help="create an account (password read from stdin)")
    p.add_argument("username")
    p.add_argument("--email")
    p.add_argument("--admin", action="store_true")
    p.set_defaults(func=create_user)

    p = commands.add_parser("set-password", help="set a user's password (read from stdin)")
    p.add_argument("username")
    p.set_defaults(func=set_password)

    p = commands.add_parser(
        "reset-token", help="give a user a new personal token (the others stay valid)"
    )
    p.add_argument("username")
    p.add_argument("--name", default="默认", help="what the token is for (default: 默认)")
    p.set_defaults(func=reset_token)

    p = commands.add_parser("sync", help="sync arXiv now, continuing from the last sync")
    p.add_argument("--from", dest="since", type=date.fromisoformat, metavar="YYYY-MM-DD")
    p.add_argument("--until", type=date.fromisoformat, metavar="YYYY-MM-DD")
    p.add_argument("--enqueue", action="store_true", help="queue it for a worker instead")
    p.set_defaults(func=sync)

    p = commands.add_parser(
        "import-venue", help="import a conference edition now (rerun to resume or top it up)"
    )
    p.add_argument("venue", help="a venue in config.yaml, e.g. CVPR")
    p.add_argument("year", type=int)
    p.add_argument("--limit", type=int, help="only the first papers, to try a source")
    p.add_argument("--no-briefs", action="store_true", help="hold its papers out of briefs")
    p.add_argument("--enqueue", action="store_true", help="queue it for a worker instead")
    p.set_defaults(func=import_venue)

    p = commands.add_parser(
        "import-venues", help="queue conference editions, newest first (e.g. --since 2022)"
    )
    p.add_argument("--since", type=int, required=True, metavar="YEAR")
    p.add_argument("--until", type=int, metavar="YEAR", help="default: this year")
    p.add_argument("--venue", action="append", default=[], help="only these (repeatable)")
    p.add_argument("--no-briefs", action="store_true", help="hold their papers out of briefs")
    p.set_defaults(func=import_venues)

    p = commands.add_parser("release-held", help="let papers imported with --no-briefs be briefed")
    p.add_argument("--venue", help="only this venue's")
    p.set_defaults(func=release_held)

    p = commands.add_parser(
        "check-venues", help="import the conference editions that are due (cron does it weekly)"
    )
    p.add_argument("--enqueue", action="store_true", help="queue them for a worker instead")
    p.set_defaults(func=check_venues)

    p = commands.add_parser("brief", help="brief new papers now (tags, keywords, summary)")
    p.add_argument("--limit", type=int, help="at most this many papers")
    p.add_argument("--enqueue", action="store_true", help="queue jobs for analyze workers instead")
    p.add_argument(
        "--redo", action="store_true", help="first put papers briefed with an older prompt back"
    )
    p.set_defaults(func=run_briefs)

    p = commands.add_parser("topics", help="sync the starting vocabulary, or review created topics")
    p.add_argument(
        "action", choices=["sync", "review"], help="review: merge duplicates now (calls the model)"
    )
    p.set_defaults(func=run_topics)

    p = commands.add_parser("report", help="write full reports for these papers now")
    p.add_argument("papers", nargs="+", metavar="paper", help="database id or arXiv id")
    p.add_argument("--force", action="store_true", help="redo papers that have a report")
    p.add_argument("--enqueue", action="store_true", help="queue jobs for analyze workers instead")
    p.set_defaults(func=run_reports)

    p = commands.add_parser("assess", help="classify the innovation of reported papers now")
    p.add_argument("papers", nargs="*", metavar="paper", help="default: those not assessed yet")
    p.set_defaults(func=run_assessments)

    p = commands.add_parser("worker", help="run an RQ worker")
    p.add_argument("queues", nargs="+", choices=["fetch", "analyze"])
    p.set_defaults(func=worker)

    p = commands.add_parser("cron", help="run the scheduler: daily sync, weekly venue check")
    p.set_defaults(func=cron)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
