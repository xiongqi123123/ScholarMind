"""Tier-2 full reports, written as a 论文解读: the model reads a paper's whole text in one call,
without tools, and writes a Markdown article (prompts/report.v2.md, docs/ARCHITECTURE.md 16.1)
that cites the paper's figures and tables with markers like [[图:Fig. 2]]. The figures are
cropped from the PDF and the markers matched to them; those cited are kept with the report.
"""

import logging
import re
import statistics
import time
import unicodedata
from datetime import UTC, datetime, timedelta

import pypdfium2 as pdfium
from sqlalchemy import func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import agent, arxiv, figures, ingest, pi, topics
from .config import config, topic_key
from .db import Analysis, Figure, Paper, PaperText, PaperTopic, Tag, engine

log = logging.getLogger(__name__)

VERSION = 2  # of the report format, for clients
PI_TIMEOUT = 900  # verified reports took 1-2 minutes
# two attempts, waiting for arXiv and downloading, then two attempts at classifying the
# innovation (600 s each, assess.PI_TIMEOUT) after the report is saved; both may wait for a
# busy provider
JOB_TIMEOUT = 2 * PI_TIMEOUT + 600 + 2 * 600 + 2 * pi.BULK_MAX_WAIT
STALE_CLAIM = timedelta(hours=1)  # longer than the job takes up to saving the report
AUTO_LOCK = 7_301_003  # advisory lock key: one automatic queueing at a time (daily limit)
# The paper goes into the prompt whole: 40 pages are about 124k characters and 43k tokens. Past
# this (~80k tokens) it would crowd the prompt and the 32k of output out of a 128k context.
MAX_PAPER_CHARS = 240_000
SECTIONS = range(5, 11)  # level-2 headings; the prompt asks for 6 to 9
LENGTH = range(3000, 10_001)  # Chinese characters; the prompt asks for 4500 to 6500
LAST_SECTION = "存疑之处"
MARKER = re.compile(r"\[\[(图|表)\s*[:：]\s*([^\[\]]+?)\s*\]\]")
REFERENCES = re.compile(r"^\s*(?:\d+\.?\s*)?(?:references|bibliography)\s*$", re.I | re.M)
# The steps of report_paper, as report_stage records them: the PDF and its text, cropping its
# figures and tables, the model writing (with the retry), matching the markers and saving.
STAGES = ("pdf", "figures", "writing", "checking")
# Why a report failed, as report_error records it: no PDF link, or no PDF behind it in the end
# (not a PDF, too large); a PDF without text; one pdfium cannot open; an answer that failed the
# checks, or was cut off, the second time; for a manual report only, a download or a model call
# that failed (an automatic one is queued again, see report_paper).
ERRORS = (
    "no_pdf",
    "no_text",
    "pdf_unreadable",
    "invalid_answer",
    "cut_off",
    "network",
    "model_error",
)
TYPICAL_OF = 10  # finished reports whose median duration is the typical one


class ReportInvalid(Exception):
    pass


# --- full text and figures -------------------------------------------------------------------


def extract_pages(pdf: bytes) -> list[str]:
    """Text of every page. pdfium marks a hyphen at a line end with \\x02 and cannot tell a split
    word from a compound, so it is dropped ("long-" + "horizon" -> "longhorizon").
    """
    document = pdfium.PdfDocument(pdf)
    try:
        return [
            document[i]
            .get_textpage()
            .get_text_bounded()
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\x02", "")
            .replace("\x0c", "\n")
            .strip()
            for i in range(len(document))
        ]
    finally:
        document.close()


def _arxiv_pdf(paper: Paper) -> str | None:
    """The paper's PDF on arXiv, if it is there and its stored link is another."""
    if not paper.arxiv_id or (paper.pdf_url or "").startswith(ingest.ARXIV_PDF):
        return None
    version = f"v{paper.arxiv_version}" if paper.arxiv_version else ""  # none: the latest
    return f"{ingest.ARXIV_PDF}{paper.arxiv_id}{version}"


def fetch_paper_pdf(paper: Paper) -> tuple[str, bytes]:
    """(link, PDF) of a paper with a PDF link, downloaded under the arXiv lock: from the stored
    link, or from arXiv when that gives no PDF (a proceedings site answering with a bot check
    or an error) and the paper is on arXiv too. The PDF relay and the full text both use this.
    """
    with ingest.arxiv_lock():
        try:
            return paper.pdf_url, arxiv.fetch_pdf(paper.pdf_url)
        except (OSError, ValueError) as e:
            if not (fallback := _arxiv_pdf(paper)):
                raise
            log.warning("paper %d: %s, so its PDF comes from %s", paper.id, e, fallback)
            return fallback, arxiv.fetch_pdf(fallback)


def download(paper_id: int) -> tuple[str, bytes, list[str]]:
    """(pdf_url, PDF, pages) of the paper's current version, pdf_url the link it came from.
    The PDF is kept in memory only; its text is stored once per version (for read_paper).
    """
    with Session(engine()) as session:
        paper = session.get(Paper, paper_id)
    if not paper.pdf_url:
        raise ReportInvalid("no PDF link")
    pdf_url, pdf = fetch_paper_pdf(paper)
    pages = extract_pages(pdf)
    with Session(engine()) as session, session.begin():
        text = dict(paper_id=paper_id, pdf_url=pdf_url, pages=pages)
        session.execute(insert(PaperText).values(text).on_conflict_do_nothing())
    return pdf_url, pdf, pages


def paper_pages(paper_id: int) -> tuple[str, list[str]]:
    """(pdf_url, pages) of the paper's current version, downloaded only the first time."""
    with Session(engine()) as session:
        paper = session.get(Paper, paper_id)
        links = [link for link in (paper.pdf_url, _arxiv_pdf(paper)) if link]
        stored = select(PaperText.pdf_url, PaperText.pages).where(
            PaperText.paper_id == paper_id, PaperText.pdf_url.in_(links)
        )
        found = session.execute(stored.order_by(PaperText.id.desc()).limit(1)).first()
    if found:
        return found.pdf_url, found.pages
    pdf_url, _, pages = download(paper_id)
    return pdf_url, pages


def paper_txt(pages: list[str]) -> str:
    return "\n".join(f"=== PAGE {n} ===\n{text}\n" for n, text in enumerate(pages, 1))


def paper_input(pages: list[str]) -> tuple[str, str]:
    """(paper text, the message that follows it) as the model gets them. A paper longer than
    MAX_PAPER_CHARS loses its reference list and all after it (appendices), then if still too
    long its last pages; the message says so.
    """
    if len(paper_txt(pages)) <= MAX_PAPER_CHARS:
        return paper_txt(pages), "以上是论文全文。"
    for n, page in enumerate(pages):
        if heading := REFERENCES.search(page):
            pages = pages[:n] + [page[: heading.start()]]
            break
    while len(paper_txt(pages)) > MAX_PAPER_CHARS:
        pages = pages[:-1]
    note = f"以上是论文的前 {len(pages)} 页，之后的部分（参考文献、附录等）因篇幅省略。"
    return paper_txt(pages), note


def crop_figures(pdf: bytes) -> list[dict]:
    """The paper's figures and tables (figures.extract). Finding them is heuristic work on
    arbitrary PDFs: if it breaks, the report goes ahead without figures.
    """
    try:
        return figures.extract(pdf)
    except Exception:
        log.exception("cropping figures failed")
        return []


# --- checking --------------------------------------------------------------------------------


def _heading(title: str) -> str:
    """The title without a number: "8. 存疑之处", "八、存疑之处" -> "存疑之处"."""
    return re.sub(r"^(?:[0-9]+|[一二三四五六七八九十]+)(?:[.、．]\s*|\s+)", "", title).strip()


def validate(text: str, kinds: set[str]) -> str:
    """Check a 论文解读 and return it without code fences, its sections at level 2 (the model
    sometimes writes them at level 1). `kinds` are the kinds of item found in the PDF
    ("figure", "table"): the report must cite at least one of each (the method overview and
    the main results table). A cut-off answer never gets here (pi.Incomplete).
    """
    markdown = re.sub(r"^```(?:markdown|md)?\n(.*)\n```$", r"\1", text.strip(), flags=re.S)
    markdown = markdown.strip()
    if not markdown:
        raise ReportInvalid("empty answer")
    if len(re.findall(r"^# ", markdown, re.M)) >= SECTIONS.start:  # sections, not a title
        markdown = re.sub(r"^(#+) ", r"#\1 ", markdown, flags=re.M)  # every heading one down
    sections = [_heading(t) for t in re.findall(r"^## +(.+)$", markdown, re.M)]
    if len(sections) not in SECTIONS:
        expected = f"{SECTIONS.start}-{SECTIONS.stop - 1}"
        raise ReportInvalid(f"{len(sections)} sections, not {expected}: {markdown[:100]!r}...")
    if sections[-1] != LAST_SECTION:
        raise ReportInvalid(f"last section is {sections[-1]!r}, not {LAST_SECTION!r}")
    length = len(re.findall(r"[\u4e00-\u9fff]", markdown))
    if length not in LENGTH:
        raise ReportInvalid(f"{length} Chinese characters, not {LENGTH.start}-{LENGTH.stop - 1}")
    cited = {"figure" if word == "图" else "table" for word, _ in MARKER.findall(markdown)}
    if uncited := kinds - cited:
        raise ReportInvalid(f"no marker for a {' or '.join(sorted(uncited))}")
    return markdown


def _roman(numeral: str) -> int:
    values = [{"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}[c] for c in numeral.upper()]
    return sum(-v if v < after else v for v, after in zip(values, values[1:] + [0], strict=True))


def label_key(kind: str, label: str) -> tuple[str, str]:
    """(kind, number) of a label, the same for every way of writing it: "Fig. 2", "Figure 2",
    "Fig 2", "Fig. 2(a)" and "图2"; "TABLE II", "Table 2", "表 2", "表Ⅱ" and "表２"; "Table S1".
    A word in the label ("Table 2" in an 图 marker) decides the kind.
    """
    label = unicodedata.normalize("NFKC", label)  # "Ⅱ" -> "II", "２" -> "2"
    if word := re.match(r"\s*(?:(fig|tab)[a-z]*\.?|(图|表))", label, re.I):
        table = (word[1] or "").lower() == "tab" or word[2] == "表"
        kind, label = ("table" if table else "figure"), label[word.end() :]
    number = re.search(r"\b([A-Z]?)(\d+)|\b([IVXLC]+)\b", label, re.I)
    if number is None:
        return kind, label.strip().lower()
    if number[3]:
        return kind, str(_roman(number[3]))
    return kind, number[1].upper() + str(int(number[2]))


def match(markdown: str, items: list[dict]) -> tuple[str, list[dict], list[str]]:
    """Point the markers at the cropped items. A matched marker is rewritten with the item's
    kind and label as the paper writes them (so clients compare labels exactly); one that
    matches nothing becomes a note in its place. Returns (markdown, the items cited, in order of
    first citation, the labels not found).
    """
    found = {}
    for item in items:
        found.setdefault(label_key(item["kind"], item["label"]), item)  # the first of repeats
    cited, missing = {}, []

    def replace(marker: re.Match) -> str:
        word, label = marker[1], marker[2]
        item = found.get(label_key("figure" if word == "图" else "table", label))
        if item is None:
            missing.append(label)
            return f"*（未能从 PDF 中裁出 {label}）*"
        cited.setdefault(id(item), item)
        return f"[[{'图' if item['kind'] == 'figure' else '表'}:{item['label']}]]"

    return MARKER.sub(replace, markdown), list(cited.values()), missing


# --- queueing --------------------------------------------------------------------------------


def request(session: Session, paper_id: int, force: bool = False) -> tuple[str | None, bool]:
    """A member or the command line asks for a report. Returns (status, enqueue): enqueue is
    True when the report was queued now and needs a job. The status is the paper's report
    status, or "no_pdf"; None means no such paper. A finished report is redone only with
    `force`; one already queued or running is left alone.
    """
    paper = session.get(Paper, paper_id, with_for_update=True)
    if paper is None:
        return None, False
    if paper.report_status in ("queued", "reporting") or (
        paper.report_status == "reported" and not force
    ):
        return paper.report_status, False
    if not paper.pdf_url:
        return "no_pdf", False
    paper.report_status, paper.report_trigger = "queued", "manual"
    paper.report_queued_at, paper.report_error = func.now(), None
    return "queued", True


def queue_automatic(session: Session) -> list[int]:
    """Queue reports for briefed papers with a domain, topic or keyword listed in
    analysis.report_auto, newest first, as many as today's limit (local time) leaves room for.
    Papers past the limit wait for a later day. Returns the queued ids.
    """
    analysis = config().analysis
    auto = analysis.report_auto
    tagged, wanted = [], []
    if auto.domains:
        tagged.append((Tag.kind == "domain") & Tag.value.in_(auto.domains))
    if auto.keywords:
        keywords = [k.lower() for k in auto.keywords]
        tagged.append((Tag.kind == "keyword") & func.lower(Tag.value).in_(keywords))
    if tagged:
        wanted.append(Paper.id.in_(select(Tag.paper_id).where(or_(*tagged))))
    known = topics.keys(session) if auto.topics else {}
    if topic_ids := {known[k].id for k in map(topic_key, auto.topics) if k in known}:
        linked = select(PaperTopic.paper_id).where(PaperTopic.topic_id.in_(topic_ids))
        wanted.append(Paper.id.in_(linked))
    if not wanted:
        return []
    session.execute(select(func.pg_advisory_xact_lock(AUTO_LOCK)))
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    today = (Paper.report_trigger == "auto") & (Paper.report_queued_at >= midnight)
    room = analysis.report_daily_limit - session.scalar(select(func.count()).where(today))
    if room <= 0:
        return []
    candidates = (
        select(Paper.id)
        .where(
            Paper.status == "briefed",
            Paper.report_status.is_(None),
            Paper.pdf_url.is_not(None),
            or_(*wanted),
        )
        .order_by(Paper.id.desc())
        .limit(room)
    )
    queued = (
        update(Paper)
        .where(Paper.id.in_(candidates.scalar_subquery()))
        .values(report_status="queued", report_trigger="auto", report_queued_at=func.now())
        .returning(Paper.id)
        .execution_options(synchronize_session=False)
    )
    return session.scalars(queued).all()


def claim(session: Session, paper_id: int) -> bool:
    """Take a queued report (or one a dead worker left) with one conditional UPDATE, so two
    workers never run the same paper.
    """
    stale = (Paper.report_status == "reporting") & (
        Paper.report_claimed_at < func.now() - STALE_CLAIM
    )
    taken = (
        update(Paper)
        .where(Paper.id == paper_id, (Paper.report_status == "queued") | stale)
        .values(
            report_status="reporting",
            report_claimed_at=func.now(),
            report_stage="pdf",
            report_stage_at=func.now(),
        )
        .returning(Paper.id)
    )
    return session.execute(taken).first() is not None


def typical_seconds(session: Session) -> int | None:
    """How long a report takes, claimed to saved: the median of the last TYPICAL_OF; None
    before the first.
    """
    took = (
        select(func.extract("epoch", Analysis.created_at - Paper.report_claimed_at))
        .join(Paper, Paper.id == Analysis.paper_id)
        # a paper's older reports were saved before its last claim
        .where(Analysis.level == "report", Paper.report_status == "reported")
        .where(Paper.report_claimed_at < Analysis.created_at)
        .order_by(Analysis.id.desc())
        .limit(TYPICAL_OF)
    )
    seconds = session.scalars(took).all()
    return round(statistics.median(seconds)) if seconds else None


def progress(session: Session, paper: Paper) -> dict | None:
    """Where a queued or running report is (api.ReportProgress), else None. `ahead` counts
    the manual reports queued before a manual one (the manual queue; jobs.MANUAL), so 0 means
    it is next; for an automatic one, which waits behind brief batches too, it is None, as is
    its estimate. The estimate is the typical duration for each report ahead and this one, as
    if they ran one at a time; while running, the typical duration less the time since the
    claim, None once past it. Two queries at most, none that reads the queues.
    """
    if paper.report_status not in ("queued", "reporting"):
        return None
    typical = typical_seconds(session)
    if paper.report_status == "reporting":
        left = typical and typical - (datetime.now(UTC) - paper.report_claimed_at).total_seconds()
        return {
            "stage": paper.report_stage,
            "since": paper.report_stage_at,
            "ahead": None,
            "eta_seconds": round(left) if left and left > 0 else None,
            "typical_seconds": typical,
        }
    ahead = None
    if paper.report_trigger == "manual":
        before = tuple_(Paper.report_queued_at, Paper.id) < (paper.report_queued_at, paper.id)
        manual = (Paper.report_status == "queued") & (Paper.report_trigger == "manual")
        ahead = session.scalar(select(func.count()).where(manual, before))
    return {
        "stage": "queued",
        "since": paper.report_queued_at,
        "ahead": ahead,
        "eta_seconds": typical * (ahead + 1) if typical and ahead is not None else None,
        "typical_seconds": typical,
    }


# --- running ---------------------------------------------------------------------------------


def save(
    session: Session,
    paper: Paper,
    report: dict,
    cited: list[dict],
    usage: dict,
    version: str,
    model: str,
):
    analysis = Analysis(
        paper_id=paper.id,
        level="report",
        model=model,
        prompt_version=version,
        result=report,
        usage=usage,
    )
    session.add(analysis)
    session.flush()
    for item in cited:
        session.add(Figure(analysis_id=analysis.id, **item))
    paper.report_status = "reported"


def _update(paper_id: int, **values):
    with Session(engine()) as session, session.begin():
        session.execute(update(Paper).where(Paper.id == paper_id).values(**values))


def _stage(paper_id: int, stage: str):
    _update(paper_id, report_stage=stage, report_stage_at=func.now())


def _failed(paper_id: int, code: str, errors: list[str], tokens: int = 0) -> dict:
    """Mark the report failed, with why: `code` (ERRORS) and the last error."""
    _update(
        paper_id, report_status="report_failed", report_error={"code": code, "detail": errors[-1]}
    )
    return {"paper": paper_id, "status": "report_failed", "tokens": tokens, "errors": errors}


def _again(paper_id: int, code: str, error: Exception, errors: list[str], tokens: int = 0) -> dict:
    """A download or model call failed. A manual report fails with why, so its member can ask
    again at once; an automatic one goes back to queued and the job fails (raises), so RQ
    records it and the next enqueue (the daily sync) tries again.
    """
    with Session(engine()) as session:
        manual = session.get(Paper, paper_id).report_trigger == "manual"
    if manual:
        return _failed(paper_id, code, errors, tokens)
    _update(paper_id, report_status="queued")
    raise error


def report_paper(paper_id: int) -> dict:
    """The analyze job: claim the paper's queued report, fetch the PDF, crop its figures, run
    the model (one retry), check, match the markers and save. The last attempt decides a
    failure: an invalid or cut-off answer marks report_failed; a failed model call or download
    too for a manual report, else it goes back to queued (_again). Each step is recorded as it
    starts (STAGES), a failure with why (ERRORS).
    """
    with Session(engine()) as session, session.begin():
        if not claim(session, paper_id):
            return {"paper": paper_id, "status": "skipped"}
    tokens, errors, error = 0, [], None
    try:
        pdf_url, pdf, pages = download(paper_id)
    except OSError as e:  # network: arXiv or the proxy
        return _again(paper_id, "network", e, [str(e)])
    except (ReportInvalid, ValueError) as e:  # no link; not a PDF or too large, also on arXiv
        return _failed(paper_id, "no_pdf", [str(e)])
    except pdfium.PdfiumError as e:
        return _failed(paper_id, "pdf_unreadable", [str(e)])
    if not any(page.strip() for page in pages):
        return _failed(paper_id, "no_text", ["the PDF has no text layer"])
    _stage(paper_id, "figures")
    items = crop_figures(pdf)
    paper, prompt = paper_input(pages)
    with Session(engine()) as session:
        version, system = agent.prompt(session, "report")
        endpoint = agent.model(session)
    model = endpoint["report_model"]
    with Session(engine()) as session:
        manual = session.get(Paper, paper_id).report_trigger == "manual"
    backoff = pi.Backoff.member_call() if manual else pi.Backoff.bulk()
    _stage(paper_id, "writing")
    for _ in range(2):
        start = time.monotonic()
        try:
            result = backoff.run(model, system, prompt, PI_TIMEOUT, stdin=paper, endpoint=endpoint)
            tokens += result.usage["totalTokens"]
            markdown = validate(result.text, {item["kind"] for item in items})
            break
        except (pi.PiError, pi.Incomplete, ReportInvalid) as e:
            tokens += getattr(e, "usage", {}).get("totalTokens", 0)
            log.warning("report of paper %d failed: %s", paper_id, e)
            errors.append(str(e))
            error = e
            backoff.failed(e)
    else:
        if isinstance(error, pi.PiError):
            return _again(paper_id, "model_error", error, errors, tokens)
        code = "cut_off" if isinstance(error, pi.Incomplete) else "invalid_answer"
        return _failed(paper_id, code, errors, tokens)
    _stage(paper_id, "checking")
    markdown, cited, missing = match(markdown, items)
    markers = len(MARKER.findall(markdown)) + len(missing)
    usage = result.usage | {"seconds": round(time.monotonic() - start)}
    report = {"version": VERSION, "markdown": markdown, "pdf_url": pdf_url}
    found = {"figures": len(cited), "markers": markers, "missing": missing}
    with Session(engine()) as session, session.begin():
        save(session, session.get(Paper, paper_id), report | found, cited, usage, version, model)
    log.info("reported paper %d: %s %s", paper_id, usage, found)
    from . import assess  # it uses this module

    assessed = assess.assess_paper(paper_id)  # a failure leaves the report as it is
    reported = {"paper": paper_id, "status": "reported", "tokens": tokens, "usage": usage}
    return reported | found | {"assessment": assessed["status"]}
