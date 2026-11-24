"""The nature of a reported paper's innovation (docs/ARCHITECTURE.md 16.2): one of five types,
with a reason, given by the model after reading the full text (prompts/assess.v3.md). The same
answer names the paper's method overview figure, which becomes its cover (queries.covers).
"""

import json
import logging
import time
from typing import Literal

import pypdfium2 as pdfium
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import agent, pi, report
from .db import Analysis, Figure, Paper, engine

log = logging.getLogger(__name__)

PI_TIMEOUT = 600
INNOVATIONS = ("机制创新", "模块组合", "方法迁移", "系统集成", "新问题或新设定")


class AssessmentInvalid(Exception):
    pass


class Innovation(BaseModel):
    """The JSON the prompt asks for; unknown keys are dropped."""

    type: Literal[INNOVATIONS]
    reason: str = Field(min_length=1)


def validate(text: str) -> dict:
    """{"type", "reason", "overview"}: the overview figure's label as the model wrote it, or
    None when it gave none or something else than a string (that never fails the answer).
    """
    try:
        data = json.loads(text[text.find("{") : text.rfind("}") + 1])  # tolerate fences
        innovation = Innovation.model_validate(data).model_dump()
    except (ValueError, ValidationError) as e:
        raise AssessmentInvalid(f"not an innovation type: {e}") from None
    overview = data.get("overview")
    return innovation | {
        "overview": (overview.strip() or None) if isinstance(overview, str) else None
    }


def mark_cover(session: Session, paper_id: int, label: str | None) -> str | None:
    """Make the figure `label` (matched as report.label_key does; a table never) of the paper's
    newest report its cover, and no other. Returns the label of the figure marked, None when
    the report cites no such figure (it stores only those it cites; covers then fall back to
    captions).
    """
    newest = select(func.max(Analysis.id)).where(
        Analysis.paper_id == paper_id, Analysis.level == "report"
    )
    figures = session.scalars(
        select(Figure)
        .where(Figure.analysis_id == newest.scalar_subquery(), Figure.kind == "figure")
        .order_by(Figure.id)
    ).all()
    key = label and report.label_key("figure", label)
    cover = next((f for f in figures if report.label_key("figure", f.label) == key), None)
    for figure in figures:
        figure.cover = figure is cover
    return cover and cover.label


def assess_paper(paper_id: int) -> dict:
    """Classify a paper's innovation from its stored full text (one retry) and save the result;
    the paper's innovation column follows the newest one. Never raises for a failed call or
    answer: the report it follows stands either way.
    """
    try:
        _, pages = report.paper_pages(paper_id)  # stored by the report
    except (OSError, ValueError, report.ReportInvalid, pdfium.PdfiumError) as e:
        return {"paper": paper_id, "status": "failed", "tokens": 0, "errors": [str(e)]}
    paper_text, prompt = report.paper_input(pages)
    with Session(engine()) as session:
        version, system = agent.prompt(session, "assess")
        endpoint = agent.model(session)
    model = endpoint["report_model"]
    tokens, errors = 0, []
    backoff = pi.Backoff.bulk()
    for _ in range(2):
        start = time.monotonic()
        try:
            result = pi.run(model, system, prompt, PI_TIMEOUT, stdin=paper_text, endpoint=endpoint)
            tokens += result.usage["totalTokens"]
            innovation = validate(result.text)
            break
        except (pi.PiError, pi.Incomplete, AssessmentInvalid) as e:
            tokens += getattr(e, "usage", {}).get("totalTokens", 0)
            log.warning("assessment of paper %d failed: %s", paper_id, e)
            errors.append(str(e))
            backoff.failed(e)
    else:
        return {"paper": paper_id, "status": "failed", "tokens": tokens, "errors": errors}
    usage = result.usage | {"seconds": round(time.monotonic() - start)}
    with Session(engine()) as session, session.begin():
        session.add(
            Analysis(
                paper_id=paper_id,
                level="assessment",
                model=model,
                prompt_version=version,
                result=innovation,
                usage=usage,
            )
        )
        set_type = update(Paper).where(Paper.id == paper_id).values(innovation=innovation["type"])
        session.execute(set_type)
        cover = mark_cover(session, paper_id, innovation["overview"])
    log.info("assessed paper %d: %s, cover %s %s", paper_id, innovation["type"], cover, usage)
    found = {"innovation": innovation["type"], "overview": innovation["overview"], "cover": cover}
    return {"paper": paper_id, "status": "assessed", "tokens": tokens} | found
