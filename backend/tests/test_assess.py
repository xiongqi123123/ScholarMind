import json
import sys

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scholarmind import assess, cli, pi, report
from scholarmind.config import config, settings
from scholarmind.db import Analysis, Figure, Paper, PaperText, engine


def _answer(type="模块组合", reason="把 FSM 和 VLA 组合起来。", **changes) -> str:
    return json.dumps({"type": type, "reason": reason} | changes, ensure_ascii=False)


def test_validate():
    expected = {"type": "系统集成", "reason": "x", "overview": "Fig. 2"}
    answer = _answer("系统集成", "x", overview=" Fig. 2 ", extra=1)
    assert assess.validate("```json\n" + answer + "\n```") == expected
    for overview in (None, "", 2, ["Fig. 2"]):  # never fails the answer
        assert assess.validate(_answer(overview=overview))["overview"] is None
    assert assess.validate(_answer())["overview"] is None  # missing


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("不是 JSON", "not an innovation type"),
        (_answer(type="渐进改进"), "type"),
        (_answer(reason=""), "reason"),
        (json.dumps({"type": "机制创新"}), "reason"),
    ],
)
def test_validate_rejects(text, error):
    with pytest.raises(assess.AssessmentInvalid, match=error):
        assess.validate(text)


def _report(s, paper_id, *cited) -> list[int]:
    """A report citing (kind, label) figures, in that order; returns the figure ids."""
    result = {"version": 2, "markdown": "## 存疑之处"}
    analysis = Analysis(paper_id=paper_id, level="report", model="m",
                        prompt_version="report.v2", result=result, usage={})  # fmt: skip
    s.add(analysis)
    s.flush()
    figures = [Figure(analysis_id=analysis.id, kind=kind, label=label, caption=label, page=1,
                      image=b"png", width=1, height=1) for kind, label in cited]  # fmt: skip
    s.add_all(figures)
    s.flush()
    return [f.id for f in figures]


def _paper(reported=True) -> int:
    with Session(engine()) as s, s.begin():
        paper = Paper(title="Planner", title_norm="planner", pdf_url="https://arxiv.org/pdf/1v1")
        s.add(paper)
        s.flush()
        if reported:
            paper.report_status = "reported"
            s.add(PaperText(paper_id=paper.id, pdf_url=paper.pdf_url, pages=["one", "two"]))
            _report(s, paper.id, ("figure", "Fig. 1"), ("table", "TABLE II"), ("figure", "Fig. 2"))
        return paper.id


def _fake_pi(*outputs):
    calls = []
    system = (settings().prompts_dir / "assess.v3.md").read_text(encoding="utf-8")

    def run(model, system_prompt, prompt, timeout, *, stdin="", **_):
        calls.append((stdin, prompt))
        assert model == config().analysis.report_model and system_prompt == system
        output = outputs[len(calls) - 1]
        if isinstance(output, Exception):
            raise output
        return pi.Result(output, {key: 100 for key in pi.USAGE_KEYS} | {"turns": 1})

    return run, calls


def _innovation(session, paper_id):
    session.expire_all()
    return session.get(Paper, paper_id).innovation


def _covers(session) -> list[str]:
    session.expire_all()
    return session.scalars(select(Figure.label).where(Figure.cover).order_by(Figure.id)).all()


def test_assess_paper(session, monkeypatch):
    paper_id = _paper()
    run, calls = _fake_pi("{}", _answer("机制创新", overview="Figure 2"))  # the retry passes
    monkeypatch.setattr(pi, "run", run)
    assert assess.assess_paper(paper_id) == {
        "paper": paper_id,
        "status": "assessed",
        "tokens": 200,
        "innovation": "机制创新",
        "overview": "Figure 2",
        "cover": "Fig. 2",  # matched however the label is written
    }
    assert calls[0] == (report.paper_txt(["one", "two"]), "以上是论文全文。")
    analysis = session.scalar(select(Analysis).where(Analysis.level == "assessment"))
    assert analysis.prompt_version == "assess.v3"
    assert analysis.model == config().analysis.report_model
    assert analysis.result == {
        "type": "机制创新",
        "reason": "把 FSM 和 VLA 组合起来。",
        "overview": "Figure 2",
    }
    assert analysis.usage["totalTokens"] == 100 and analysis.usage["seconds"] >= 0
    assert _innovation(session, paper_id) == "机制创新" and _covers(session) == ["Fig. 2"]

    # A failed assessment never raises and keeps the last one.
    monkeypatch.setattr(pi, "run", _fake_pi("not json", pi.PiError("stopReason error"))[0])
    failed = assess.assess_paper(paper_id)
    assert failed["status"] == "failed" and len(failed["errors"]) == 2
    assert _innovation(session, paper_id) == "机制创新" and _covers(session) == ["Fig. 2"]

    # Without an overview the innovation type is saved all the same and the cover goes.
    monkeypatch.setattr(pi, "run", _fake_pi(_answer("系统集成", overview=None))[0])
    assessed = assess.assess_paper(paper_id)
    assert (assessed["status"], assessed["innovation"], assessed["cover"]) == (
        "assessed",
        "系统集成",
        None,
    )
    assert _innovation(session, paper_id) == "系统集成" and _covers(session) == []
    count = select(func.count()).where(Analysis.level == "assessment")
    assert session.scalar(count) == 2  # appended, never replaced


def test_mark_cover(session):
    paper_id = _paper()

    def mark(label):
        with Session(engine()) as s, s.begin():
            marked = assess.mark_cover(s, paper_id, label)
        return marked, _covers(session)

    assert mark("Fig 1") == ("Fig. 1", ["Fig. 1"])
    assert mark("Figure 2") == ("Fig. 2", ["Fig. 2"])  # moves, never two
    assert mark("Fig. 7") == (None, [])  # not cited: the caption rule decides
    assert mark("TABLE II") == (None, [])  # a table is never a cover
    assert mark("Table 2") == (None, [])
    with Session(engine()) as s, s.begin():  # a newer report: only its figures count
        newer = _report(s, paper_id, ("figure", "Fig. 2"))
    assert mark("Fig. 2") == ("Fig. 2", ["Fig. 2"])
    assert session.scalar(select(Figure.id).where(Figure.cover)) == newer[0]


def test_assess_command(session, monkeypatch, capsys):
    first, second, unreported = _paper(), _paper(), _paper(reported=False)
    done = []
    monkeypatch.setattr(assess, "assess_paper", lambda paper_id: done.append(paper_id) or "ok")
    monkeypatch.setattr(sys, "argv", ["scholarmind", "assess"])
    cli.main()  # every reported paper not assessed yet
    assert done == [first, second]
    with Session(engine()) as s, s.begin():
        s.add(Analysis(paper_id=first, level="assessment", model="m",
                       prompt_version="assess.v3", result={}, usage={}))  # fmt: skip
    done.clear()
    cli.main()
    assert done == [second]
    done.clear()
    capsys.readouterr()
    argv = ["scholarmind", "assess", str(first), str(unreported), "999"]
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()  # named papers are assessed again
    assert done == [first]
    assert capsys.readouterr().out.splitlines() == [
        f"{first}: ok",
        f"{unreported}: no such reported paper",
        "999: no such reported paper",
    ]
