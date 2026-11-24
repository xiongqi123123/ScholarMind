import email.message
import hashlib
import io
import sys
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import rq
from conftest import DATA
from PIL import Image
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from scholarmind import agent, arxiv, assess, cli, figures, jobs, pi, queries, report
from scholarmind.config import config, settings
from scholarmind.db import (
    Analysis,
    Figure,
    Paper,
    PaperText,
    PaperTopic,
    Tag,
    Topic,
    User,
    engine,
)

# A hand-written two-page PDF (Helvetica, not embedded): page 1 breaks "long-horizon" across
# a line, which pdfium marks with \x02.
PDF = (DATA / "paper.pdf").read_bytes()
PAGES = [
    "We propose a longhorizon planner that is very robust to sensor noise.\n"
    "Our code is at https://github.com/example/planner.",
    "Results show a large gain on the benchmark across all splits.",
]
# Two pages of body text with a figure (a vector box) captioned "Fig. 1: ..." under it on
# page 1 and a table captioned "TABLE I: ..." over it on page 2.
FIGURES_PDF = (DATA / "figures.pdf").read_bytes()


def test_extract_pages():
    assert report.extract_pages(PDF) == PAGES
    assert report.paper_txt(PAGES).startswith("=== PAGE 1 ===\nWe propose a longhorizon")
    assert "\n=== PAGE 2 ===\nResults show" in report.paper_txt(PAGES)


def test_crop_figures():
    items = report.crop_figures(FIGURES_PDF)
    assert [(i["kind"], i["label"], i["page"], i["caption"]) for i in items] == [
        (
            "figure",
            "Fig. 1",
            1,
            "Fig. 1: Overview of the planner. A box stands in for the diagram.",
        ),
        ("table", "TABLE I", 2, "TABLE I: Accuracy of three methods on two benchmarks."),
    ]
    figure, table = (Image.open(io.BytesIO(i["image"])) for i in items)
    assert figure.format == table.format == "PNG"
    assert [(i["width"], i["height"]) for i in items] == [figure.size, table.size]
    assert abs(figure.width / figure.height - 270 / 150) < 0.1  # the 270 x 150 box, not the page
    assert 4 < table.width / table.height < 6  # the rows and rules under the caption
    assert report.crop_figures(PDF) == []  # no captions
    assert report.crop_figures(b"not a pdf") == []  # broken: no figures, but no failure


def test_paper_input(monkeypatch):
    pages = ["intro " * 50, "method " * 50, "end\nReferences\n[1] A. Author.", "appendix " * 50]
    assert report.paper_input(pages) == (report.paper_txt(pages), "以上是论文全文。")
    monkeypatch.setattr(report, "MAX_PAPER_CHARS", len(report.paper_txt(pages)) - 1)
    text, message = report.paper_input(pages)  # the references and what follows go
    assert text == report.paper_txt(pages[:2] + ["end\n"]) and "前 3 页" in message
    monkeypatch.setattr(report, "MAX_PAPER_CHARS", len(report.paper_txt(pages[:1])))
    text, message = report.paper_input(pages)  # then the last pages
    assert text == report.paper_txt(pages[:1]) and "前 1 页" in message


def _markdown(sections=7, last="存疑之处", body="这一节讲论文的方法和实验。" * 40, markers=None):
    parts = [f"## 第{n}部分\n\n{body}" for n in range(1, sections)] + [f"## {last}\n\n{body}"]
    parts[1] += "\n\n" + ("[[图:Fig. 1]]\n\n[[表:TABLE I]]" if markers is None else markers)
    return "\n\n".join(parts)


def test_validate():
    both, good = {"figure", "table"}, _markdown()
    assert report.validate(good, both) == good
    assert report.validate(f"```markdown\n{good}\n```\n", both) == good
    assert report.validate(_markdown(last="8. 存疑之处"), both)  # numbered headings
    assert report.validate(_markdown(last="八、存疑之处"), both)
    level_1 = _markdown().replace("## ", "# ") + "\n\n## 小节"  # sections at level 1
    assert report.validate(level_1, both) == _markdown() + "\n\n### 小节"
    assert report.validate("# 标题\n\n" + good, both) == "# 标题\n\n" + good  # a title stays
    assert report.validate(_markdown(markers="[[图:Fig. 1]]"), {"figure"})  # a paper without tables
    assert report.validate(_markdown(markers=""), set())  # nothing was cropped


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (" \n", "empty"),
        (_markdown(sections=4), "4 sections"),
        (_markdown(sections=11), "11 sections"),
        (_markdown(last="讨论"), "last section is '讨论'"),
        (_markdown(last="一致性存疑之处"), "last section is '一致性存疑之处'"),
        (_markdown(body="太短。"), "Chinese characters"),
        (_markdown(body="很长的正文。" * 600), "Chinese characters"),
        (_markdown(markers="[[图:Fig. 1]]"), "no marker for a table"),
        (_markdown(markers="Fig. 1 and TABLE I"), "no marker for a figure or table"),
    ],
)
def test_validate_rejects(text, error):
    with pytest.raises(report.ReportInvalid, match=error):
        report.validate(text, {"figure", "table"})


def test_label_key():
    figure_2 = [
        "Fig. 2",
        "Figure 2",
        "Fig 2",
        "FIG. 2",
        "Fig. 2(a)",
        "Figs. 2",
        " 2 ",
        "图2",
        "图 2",
    ]
    assert {report.label_key("figure", label) for label in figure_2} == {("figure", "2")}
    table_2 = [
        ("table", "TABLE II"),
        ("table", "Table 2"),
        ("table", "Tab. 2"),
        ("figure", "Table 2"),
        ("figure", "表2"),  # TABLE II, written in Chinese and Arabic numerals
        ("table", "表 2"),
        ("table", "表Ⅱ"),  # a Roman numeral character
        ("table", "表２"),  # a full-width digit
        ("table", "Ⅱ"),
    ]
    assert {report.label_key(kind, label) for kind, label in table_2} == {("table", "2")}
    assert report.label_key("table", "TABLE XIX") == ("table", "19")
    assert report.label_key("table", "Table S1") == ("table", "S1")
    assert report.label_key("figure", "Fig. 12") != report.label_key("figure", "Fig. 1")


def _item(kind, label, page):
    item = {"kind": kind, "label": label, "caption": f"{label}: x", "page": page}
    return item | {"image": b"png", "width": 640, "height": 480}


def test_match():
    items = [
        _item("figure", "Fig. 2", 3),
        _item("table", "TABLE II", 5),
        _item("figure", "Fig. 3", 4),
        _item("figure", "Fig. 2", 9),  # the same label again (an appendix): the first counts
    ]
    markdown = (
        "a\n[[图:Figure 2]]\nb\n[[表:Table 2]]\nc\n[[图：Fig 2]]\n[[图:Fig. 9]]\n[[表: Fig. 3 ]]"
    )
    text, cited, missing = report.match(markdown, items)
    assert text == (
        "a\n[[图:Fig. 2]]\nb\n[[表:TABLE II]]\nc\n[[图:Fig. 2]]\n*（未能从 PDF 中裁出 Fig. 9）*\n"
        "[[图:Fig. 3]]"
    )
    assert cited == items[:3] and missing == ["Fig. 9"]


def _add_paper(**fields) -> int:
    paper = Paper(title="Planner", title_norm="planner", pdf_url="https://arxiv.org/pdf/1v1")
    with Session(engine()) as s, s.begin():
        s.add(paper)
        for key, value in fields.items():
            setattr(paper, key, value)
        s.flush()
        return paper.id


def _status(session, paper_id):
    session.expire_all()
    return session.get(Paper, paper_id).report_status


def _error(session, paper_id):
    session.expire_all()
    return session.get(Paper, paper_id).report_error


def _progress(paper_id):
    with Session(engine()) as s:
        return report.progress(s, s.get(Paper, paper_id))


def _claim(paper_id):
    with Session(engine()) as s, s.begin():
        return report.claim(s, paper_id)


def test_claim_once(session):
    paper_id = _add_paper(report_status="queued")
    with ThreadPoolExecutor(8) as pool:
        assert sorted(pool.map(_claim, [paper_id] * 8)) == [False] * 7 + [True]
    assert _status(session, paper_id) == "reporting"
    with Session(engine()) as s, s.begin():  # its worker died two hours ago
        old = datetime.now(UTC) - timedelta(hours=2)
        s.execute(update(Paper).values(report_claimed_at=old))
    assert _claim(paper_id) and not _claim(paper_id)


def test_queue_automatic(session, vocabulary, monkeypatch):
    analysis = config().analysis
    auto = analysis.report_auto.model_copy(
        update={"domains": ["robotics"], "topics": ["vla"], "keywords": ["Diffusion Policy"]}
    )
    monkeypatch.setattr(analysis, "report_auto", auto)
    monkeypatch.setattr(analysis, "report_daily_limit", 3)
    briefed = {"status": "briefed"}
    papers = {
        "robotics": _add_paper(**briefed),
        "other topic": _add_paper(**briefed),
        "keyword": _add_paper(**briefed),
        "topic": _add_paper(**briefed),
        "not briefed": _add_paper(),
        "reported": _add_paper(**briefed, report_status="reported"),
        "no pdf": _add_paper(**briefed, pdf_url=None),
        "robotics 2": _add_paper(**briefed),
    }
    labels = {
        "robotics": ("domain", "robotics"),
        "other topic": ("topic", "Navigation"),
        "keyword": ("keyword", "diffusion policy"),
        "topic": ("topic", "Vision-Language-Action Models"),  # "vla" is its alias
        "not briefed": ("domain", "robotics"),
        "reported": ("domain", "robotics"),
        "no pdf": ("domain", "robotics"),
        "robotics 2": ("domain", "robotics"),
    }
    with Session(engine()) as s, s.begin():
        for name, (kind, value) in labels.items():
            if kind == "topic":
                topic_id = s.scalar(select(Topic.id).where(Topic.name == value))
                s.add(PaperTopic(paper_id=papers[name], topic_id=topic_id, source="ai"))
            else:
                s.add(Tag(paper_id=papers[name], kind=kind, value=value, source="ai"))

    def queue():
        with Session(engine()) as s, s.begin():
            return report.queue_automatic(s)

    first = [papers["robotics 2"], papers["topic"], papers["keyword"]]  # newest first, up to 3
    assert sorted(queue()) == sorted(first)
    assert queue() == []  # today's limit is used up
    with Session(engine()) as s, s.begin():  # a manual report does not count
        report.request(s, papers["other topic"])
    with Session(engine()) as s, s.begin():  # the next day
        yesterday = datetime.now(UTC) - timedelta(days=1)
        s.execute(update(Paper).where(Paper.id.in_(first)).values(report_queued_at=yesterday))
    assert queue() == [papers["robotics"]]  # the only matching paper left
    statuses = dict(session.execute(select(Paper.id, Paper.report_trigger)).all())
    assert statuses[papers["robotics"]] == "auto" and statuses[papers["other topic"]] == "manual"
    assert statuses[papers["not briefed"]] is None and statuses[papers["no pdf"]] is None


def _fake_pi(*outputs):
    calls = []
    system = (settings().prompts_dir / "report.v2.md").read_text(encoding="utf-8")

    def run(model, system_prompt, prompt, timeout, *, stdin="", **_):
        calls.append((stdin, prompt))
        assert model == config().analysis.report_model and system_prompt == system
        output = outputs[len(calls) - 1]
        if isinstance(output, Exception):
            raise output
        return pi.Result(output, {key: 1000 for key in pi.USAGE_KEYS} | {"turns": 1})

    return run, calls


@pytest.fixture
def downloads(monkeypatch):
    fetched = []
    monkeypatch.setattr(arxiv, "fetch_pdf", lambda url: fetched.append(url) or FIGURES_PDF)
    return fetched


@pytest.fixture(autouse=True)
def assessed(monkeypatch):
    """The assessment that follows a saved report (test_assess.py tests it)."""
    papers = []
    monkeypatch.setattr(
        assess, "assess_paper", lambda paper_id: papers.append(paper_id) or {"status": "assessed"}
    )
    return papers


def test_report_paper(session, monkeypatch, downloads, assessed):
    paper_id = _add_paper(report_status="queued")
    markers = "[[图:Figure 1]]\n\n[[表:TABLE I]]\n\n[[图:Fig. 7]]"
    run, calls = _fake_pi("## 只有一节", _markdown(markers=markers))  # the retry passes
    monkeypatch.setattr(pi, "run", run)
    result = report.report_paper(paper_id)
    assert result["status"] == "reported" and result["tokens"] == 2000
    assert result["assessment"] == "assessed" and assessed == [paper_id]  # after the save
    assert (result["figures"], result["markers"], result["missing"]) == (2, 3, ["Fig. 7"])
    pages = report.extract_pages(FIGURES_PDF)
    assert calls[0] == (report.paper_txt(pages), "以上是论文全文。")
    assert downloads == ["https://arxiv.org/pdf/1v1"] and _status(session, paper_id) == "reported"
    analysis = session.scalar(select(Analysis))
    assert (analysis.level, analysis.prompt_version) == ("report", "report.v2")
    assert analysis.model == config().analysis.report_model and analysis.usage["seconds"] >= 0
    assert analysis.result["version"] == 2
    assert analysis.result["pdf_url"] == "https://arxiv.org/pdf/1v1"
    markdown = analysis.result["markdown"]
    assert "[[图:Fig. 1]]" in markdown and "*（未能从 PDF 中裁出 Fig. 7）*" in markdown
    figures = session.scalars(select(Figure).order_by(Figure.id)).all()  # only those cited
    assert [(f.kind, f.label, f.page) for f in figures] == [
        ("figure", "Fig. 1", 1),
        ("table", "TABLE I", 2),
    ]
    assert figures[0].image.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(figures[0].image)).size == (figures[0].width, figures[0].height)

    # Redone: a new report with figures of its own, the old one kept (append-only).
    assert report.report_paper(paper_id)["status"] == "skipped"  # not queued
    with Session(engine()) as s, s.begin():
        assert report.request(s, paper_id) == ("reported", False)
        assert report.request(s, paper_id, force=True) == ("queued", True)
    monkeypatch.setattr(pi, "run", _fake_pi(_markdown(markers="[[表:Table 1]]\n[[图:Fig. 1]]"))[0])
    assert report.report_paper(paper_id)["status"] == "reported" and len(downloads) == 2
    assert session.scalar(select(func.count()).select_from(Analysis)) == 2
    assert session.scalar(select(func.count()).select_from(Figure)) == 4
    assert session.scalar(select(func.count()).select_from(PaperText)) == 1  # once per version
    latest = queries.latest_report(session, paper_id)
    assert latest["markdown"].count("[[表:TABLE I]]") == 1
    assert [f["label"] for f in latest["figures"]] == ["TABLE I", "Fig. 1"]  # in citing order
    assert latest["figures"][1]["width"] == figures[0].width > 0
    assert latest["figures"][0]["url"] == f"/api/papers/{paper_id}/figures/{figures[-1].id + 1}"


@pytest.mark.parametrize(
    ("outputs", "status", "error"),
    [
        (
            ("no headings", _markdown(last="讨论")),
            "report_failed",
            {"code": "invalid_answer", "detail": "last section is '讨论', not '存疑之处'"},
        ),
        (
            (pi.Incomplete("stopReason length", {"totalTokens": 5}), ""),
            "report_failed",
            {"code": "invalid_answer", "detail": "empty answer"},  # the last attempt decides
        ),
        (
            ("no headings", pi.Incomplete("stopReason length", {"totalTokens": 5})),
            "report_failed",
            {"code": "cut_off", "detail": "stopReason length"},
        ),
        (("no headings", pi.PiError("stopReason error")), "queued", None),  # the retry: no answer
    ],
)
def test_report_paper_failures(session, monkeypatch, downloads, assessed, outputs, status, error):
    paper_id = _add_paper(report_status="queued", report_trigger="auto")
    run, calls = _fake_pi(*outputs)
    monkeypatch.setattr(pi, "run", run)
    if status == "queued":
        with pytest.raises(pi.PiError):  # the job fails, so RQ records it
            report.report_paper(paper_id)
    else:
        assert report.report_paper(paper_id)["status"] == "report_failed"
    assert len(calls) == 2 and _status(session, paper_id) == status
    assert _error(session, paper_id) == error
    assert session.scalar(select(func.count()).select_from(Analysis)) == 0 and assessed == []
    if error:  # asked for again: no reason until it fails again
        with Session(engine()) as s, s.begin():
            assert report.request(s, paper_id) == ("queued", True)
        assert _error(session, paper_id) is None


def test_manual_report_fails_on_a_failed_call(session, monkeypatch, downloads, assessed):
    """A member's report fails with why when the model call fails, so they can ask again,
    instead of waiting queued for the daily sync like an automatic one.
    """
    paper_id = _add_paper(report_status="queued", report_trigger="manual")
    run, calls = _fake_pi("no headings", pi.PiError("stopReason error"))
    monkeypatch.setattr(pi, "run", run)
    result = report.report_paper(paper_id)
    assert (result["status"], len(calls)) == ("report_failed", 2)
    assert _status(session, paper_id) == "report_failed"
    assert _error(session, paper_id) == {"code": "model_error", "detail": "stopReason error"}


def test_reports_on_a_busy_provider(session, monkeypatch, downloads, assessed):
    """A 429: a member's report tries again after a short pause; an automatic one waits 30-90 s
    before its retry.
    """
    slept = []
    monkeypatch.setattr(pi, "time", SimpleNamespace(sleep=slept.append))
    busy = pi.Busy('stopReason error: 429: {"message":"Upstream rate limit exceeded"}')
    manual = _add_paper(report_status="queued", report_trigger="manual")
    run, calls = _fake_pi(busy, _markdown())
    monkeypatch.setattr(pi, "run", run)
    assert report.report_paper(manual)["status"] == "reported" and len(calls) == 2
    assert len(slept) == 1 and 2 <= slept[0] <= 5

    slept.clear()
    automatic = _add_paper(report_status="queued", report_trigger="auto")
    run, calls = _fake_pi(busy, _markdown())
    monkeypatch.setattr(pi, "run", run)
    assert report.report_paper(automatic)["status"] == "reported" and len(calls) == 2
    assert len(slept) == 1 and 30 <= slept[0] <= 90


def test_report_paper_without_text(session, monkeypatch, downloads):
    monkeypatch.setattr(pi, "run", lambda *a, **kw: pytest.fail("pi must not run"))
    no_link = _add_paper(report_status="queued", pdf_url=None)  # queued before the link went
    assert report.report_paper(no_link)["errors"] == ["no PDF link"]
    assert _error(session, no_link) == {"code": "no_pdf", "detail": "no PDF link"}
    monkeypatch.setattr(report, "extract_pages", lambda pdf: ["", " \n"])  # scanned pages
    scanned = _add_paper(report_status="queued")
    assert report.report_paper(scanned)["status"] == "report_failed"
    assert _error(session, scanned) == {"code": "no_text", "detail": "the PDF has no text layer"}


def test_report_records_its_stages(session, monkeypatch):
    """Each step of the job is in the database as it starts, and the paper's progress says so."""
    paper_id = _add_paper(report_status="queued")
    seen = []

    def look():
        seen.append(_progress(paper_id))

    run = _fake_pi(_markdown())[0]
    extract, save = figures.extract, report.save
    monkeypatch.setattr(arxiv, "fetch_pdf", lambda url: look() or FIGURES_PDF)
    monkeypatch.setattr(figures, "extract", lambda pdf: look() or extract(pdf))
    monkeypatch.setattr(pi, "run", lambda *a, **kw: look() or run(*a, **kw))
    monkeypatch.setattr(report, "save", lambda *a: look() or save(*a))
    assert report.report_paper(paper_id)["status"] == "reported"
    assert [p["stage"] for p in seen] == ["pdf", "figures", "writing", "checking"]
    assert all(p["ahead"] is None and p["since"] for p in seen)
    assert seen[0]["since"] <= seen[1]["since"] <= seen[2]["since"] <= seen[3]["since"]
    assert _progress(paper_id) is None  # reported


def test_progress(session):
    """ahead counts the manual reports queued before one, not those running; the estimate
    comes from how long recent reports took, claimed to saved.
    """
    first, second, idle = _add_paper(), _add_paper(), _add_paper()
    for paper_id in (first, second):
        with Session(engine()) as s, s.begin():
            report.request(s, paper_id)
    auto = _add_paper(report_status="queued", report_trigger="auto")
    assert _progress(idle) is None
    queued = _progress(first)
    assert queued == {
        "stage": "queued",
        "since": queued["since"],
        "ahead": 0,
        "eta_seconds": None,  # no report has finished yet
        "typical_seconds": None,
    }
    assert queued["since"] is not None and _progress(second)["ahead"] == 1
    assert _progress(auto) | {"since": None} == {
        "stage": "queued",
        "since": None,
        "ahead": None,  # behind brief batches too
        "eta_seconds": None,
        "typical_seconds": None,
    }

    now = datetime.now(UTC)
    with Session(engine()) as s, s.begin():  # two finished reports: 100 s and 200 s
        for took in (100, 200):
            claimed = now - timedelta(seconds=took)
            done = _add_paper(report_status="reported", report_claimed_at=claimed)
            s.add(Analysis(paper_id=done, level="report", model="m", prompt_version="report.v2",
                           result={}, usage={}, created_at=now))  # fmt: skip
    assert _progress(first)["typical_seconds"] == 150
    assert (_progress(first)["eta_seconds"], _progress(second)["eta_seconds"]) == (150, 300)
    assert _claim(first)  # running: no longer ahead of the second
    running = _progress(first)
    assert (running["stage"], running["ahead"], running["typical_seconds"]) == ("pdf", None, 150)
    assert 140 < running["eta_seconds"] <= 150 and _progress(second)["ahead"] == 0
    with Session(engine()) as s, s.begin():  # past the typical time: no estimate
        s.execute(
            update(Paper)
            .where(Paper.id == first)
            .values(report_claimed_at=now - timedelta(hours=1))
        )
    assert _progress(first)["eta_seconds"] is None


def test_manual_reports_go_first(session, monkeypatch):
    """A report a member asked for goes on the manual queue, which an analyze worker empties
    before analyze, where brief batches and automatic reports wait; queued again too.
    """
    enqueued = []

    class FakeQueue:
        def __init__(self, name, connection):
            self.name = name

        def enqueue(self, func, paper_id, job_timeout):
            enqueued.append((self.name, paper_id))

    monkeypatch.setattr(jobs, "Queue", FakeQueue)
    earlier = datetime.now(UTC) - timedelta(hours=1)
    auto = _add_paper(report_status="queued", report_trigger="auto", report_queued_at=earlier)
    manual = _add_paper()
    with Session(engine()) as s, s.begin():
        report.request(s, manual)
    assert jobs.enqueue_reports([manual]) == 1 and enqueued == [("manual", manual)]
    enqueued.clear()
    assert jobs.enqueue_reports() == 2  # every queued report, as the daily sync does
    assert enqueued == [("analyze", auto), ("manual", manual)]  # in the order they were queued

    workers = []

    class FakeWorker:
        def __init__(self, queues, connection):
            workers.append(queues)

        def work(self):
            pass

    monkeypatch.setattr(rq, "Worker", FakeWorker)
    monkeypatch.setattr(rq, "SimpleWorker", FakeWorker)
    monkeypatch.setattr(sys, "argv", ["scholarmind", "worker", "analyze"])
    cli.main()  # what deploy/run.sh starts
    assert workers == [["manual", "analyze"]]  # RQ tries them in this order (by default)


def test_report_uses_the_prompt_edited_on_the_web(session, monkeypatch, downloads, assessed):
    """The report job reads the prompt and the model endpoint when it runs and records which
    text and model made the report.
    """
    session.add(User(username="root", password_hash="x", is_admin=True))
    session.commit()
    edited = "写一篇论文解读，引用图表。\n"
    with Session(engine()) as s, s.begin():
        admin = s.scalar(select(User))
        agent.set_prompt(s, "report", edited, admin)
        agent.set_model(s, "report_model", "web-report", admin)
        agent.set_connection(s, "https://llm.example.org/v1", "web-key-0123456789", admin)
    calls = []

    def run(model, system_prompt, prompt, timeout, *, stdin="", endpoint=None):
        calls.append((model, system_prompt, endpoint))
        return pi.Result(_markdown(), {key: 1000 for key in pi.USAGE_KEYS} | {"turns": 1})

    monkeypatch.setattr(pi, "run", run)
    assert report.report_paper(_add_paper(report_status="queued"))["status"] == "reported"
    endpoint = {"base_url": "https://llm.example.org/v1", "report_model": "web-report"}
    endpoint |= {"brief_model": config().analysis.brief_model}
    assert calls == [("web-report", edited, endpoint)]
    version = "report.v2+" + hashlib.sha256(edited.encode()).hexdigest()[:8]
    made = session.execute(select(Analysis.prompt_version, Analysis.model)).one()
    assert tuple(made) == (version, "web-report")


def test_report_paper_download_fails(session, monkeypatch):
    paper_id = _add_paper(report_status="queued", report_trigger="auto")

    def unreachable(url):
        raise urllib.error.URLError("proxy down")

    monkeypatch.setattr(arxiv, "fetch_pdf", unreachable)
    with pytest.raises(urllib.error.URLError):
        report.report_paper(paper_id)
    assert _status(session, paper_id) == "queued"  # tried again on the next enqueue
    manual = _add_paper(report_status="queued", report_trigger="manual")
    assert report.report_paper(manual)["errors"] == ["<urlopen error proxy down>"]
    assert _status(session, manual) == "report_failed"  # its member asks again
    assert _error(session, manual) == {"code": "network", "detail": "<urlopen error proxy down>"}
    with Session(engine()) as s, s.begin():
        assert report.request(s, _add_paper(pdf_url=None)) == ("no_pdf", False)
        assert report.request(s, 999) == (None, False)
        assert report.request(s, paper_id) == ("queued", False)  # already waiting


def test_report_command(session, monkeypatch, capsys):
    first, second = _add_paper(arxiv_id="2611.00001"), _add_paper(report_status="reported")
    enqueued = []
    monkeypatch.setattr(jobs, "enqueue_reports", lambda ids: enqueued.extend(ids))
    argv = ["scholarmind", "report", "2611.00001", str(second), "7", "--enqueue"]
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    assert enqueued == [first]
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "2611.00001: enqueued on manual",
        f"{second}: reported, --force redoes it",
        "7: no such paper",
    ]


class _Answer(io.BytesIO):
    """Enough of what urllib's urlopen returns for arxiv.fetch_pdf."""

    def __init__(self, body: bytes, kind: str):
        super().__init__(body)
        self.headers = email.message.Message()
        self.headers["Content-Type"] = kind


def test_pdf_falls_back_to_arxiv(session, monkeypatch):
    """A proceedings site that answers with a bot check instead of the PDF: the full text comes
    from the paper's arXiv version and is kept under that link. Without an arXiv version the
    report fails, rather than being queued again and again.
    """
    rss = "https://www.roboticsproceedings.org/rss22/p001.pdf"
    asked = []

    def open_pdf(url):
        asked.append(url)
        if url.startswith("https://arxiv.org/"):
            return _Answer(FIGURES_PDF, "application/pdf")
        return _Answer(b"<title>One moment, please...</title>", "text/html")

    monkeypatch.setattr(arxiv, "open_pdf", open_pdf)
    monkeypatch.setattr(arxiv, "REQUEST_INTERVAL", 0)
    on_arxiv = _add_paper(pdf_url=rss, arxiv_id="2611.00007", arxiv_version=2)
    pdf_url, pdf, pages = report.download(on_arxiv)
    assert (pdf_url, pdf, asked) == (
        "https://arxiv.org/pdf/2611.00007v2",
        FIGURES_PDF,
        [rss, pdf_url],
    )
    assert report.paper_pages(on_arxiv) == (pdf_url, pages) and len(asked) == 2  # kept
    latest = _add_paper(pdf_url=rss, arxiv_id="2611.00008")  # its version not looked up yet
    assert report.download(latest)[0] == "https://arxiv.org/pdf/2611.00008"

    only_there = _add_paper(pdf_url=rss, report_status="queued")
    monkeypatch.setattr(pi, "run", lambda *a, **kw: pytest.fail("pi must not run"))
    result = report.report_paper(only_there)
    assert result["status"] == "report_failed" and asked[-1] == rss
    assert result["errors"] == [f"{rss} answered with text/html, not a PDF"]
    assert _status(session, only_there) == "report_failed"
    assert _error(session, only_there) == {"code": "no_pdf", "detail": result["errors"][0]}


def test_report_paper_broken_pdf(session, monkeypatch):
    paper_id = _add_paper(report_status="queued")
    monkeypatch.setattr(arxiv, "fetch_pdf", lambda url: b"<html>not a pdf</html>")
    monkeypatch.setattr(pi, "run", lambda *a, **kw: pytest.fail("pi must not run"))
    assert report.report_paper(paper_id)["status"] == "report_failed"
    assert _status(session, paper_id) == "report_failed"
    assert _error(session, paper_id)["code"] == "pdf_unreadable"
