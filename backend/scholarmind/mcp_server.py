"""MCP at /mcp: the paper library as tools for agents (Claude Code, Codex, pi), a second shell
over the same queries as the REST API. Every request needs a personal token
(`Authorization: Bearer <token>`, made on the web page or by `scholarmind reset-token`); notes
are the caller's own.

Mounting follows the SDK's "Add to an existing app" guide (docs/run/asgi.md, mcp 2.3.0): the
host app's lifespan must enter `mcp.session_manager.run()` (api.py does).
"""

import json
from datetime import date, datetime
from typing import Annotated, Literal

import anyio
import pypdfium2 as pdfium
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy.orm import Session
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from . import assess, auth, brief, queries, report, topics
from .config import config
from .db import Paper, engine

MAX_PAGES = 10  # read_paper: pages per call
MAX_CHARS = 30_000  # read_paper: characters per call
READ_ONLY = ToolAnnotations(read_only_hint=True)
Domain = Literal[tuple(config().vocabulary)]
PaperType = Literal[tuple(brief.PAPER_TYPES)]
Innovation = Literal[assess.INNOVATIONS]
Source = Literal[("arXiv", *config().venues)]

mcp = MCPServer(
    "ScholarMind",
    instructions=(
        "A group's library of arXiv and conference papers. Every paper has 1-2 domains (or "
        "out-of-scope, hidden unless asked for), 1-3 research topics, a paper type, keywords "
        "and a short summary; some have a full report, a close reading of the paper written "
        "in Chinese. Find papers with search_papers, open one with get_paper, read its text "
        "page by page with read_paper. Notes saved with "
        "save_note are private to you. Comparing papers or finding gaps is up to you: these "
        "tools give the facts, with page numbers to cite."
    ),
)


def _json(value) -> str:
    """Compact JSON for the model to read (the SDK would indent a returned dict)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_iso)


def _iso(value) -> str:
    return value.isoformat(timespec="seconds") if isinstance(value, datetime) else str(value)


def _caller(ctx: Context) -> int:
    with Session(engine()) as session:
        user = auth.bearer_user(session, (ctx.headers or {}).get("authorization"))
    if user is None:
        raise ToolError("Not authenticated")
    return user.id


def _authors(authors: str, limit: int = 3) -> str:
    names = [n.strip().removeprefix("and ") for n in authors.split(",") if n.strip()]
    return ", ".join(names[:limit]) + (" et al." if len(names) > limit else "")


def _tags(paper: Paper) -> dict[str, list[str]]:
    grouped = {}
    for tag in paper.tags:
        grouped.setdefault(tag.kind, []).append(tag.value)
    return grouped


def _topics(paper: Paper) -> list[dict]:
    return [{"id": t.topic_id, "name": t.topic.name, "zh": t.topic.zh} for t in paper.topics]


def _one(value) -> list:
    """A single filter value as the list queries.paper_filters takes."""
    return [] if value is None else [value]


def _day(paper_date) -> str | None:
    return paper_date and paper_date.date().isoformat()


@mcp.tool(annotations=READ_ONLY, structured_output=False)
def search_papers(
    query: Annotated[
        str, Field(description='Words in title or abstract; supports "quotes", OR, -word.')
    ] = "",
    domain: Domain | None = None,
    topic: Annotated[
        str | None,
        Field(description='A research topic by name or alias, e.g. "3D Gaussian Splatting".'),
    ] = None,
    paper_type: PaperType | None = None,
    keyword: Annotated[str | None, Field(description="A keyword tag, exactly as shown.")] = None,
    include_out_of_scope: Annotated[
        bool, Field(description="Also papers outside the library's domains.")
    ] = False,
    category: Annotated[
        str | None, Field(description="arXiv category such as cs.CV; cross-lists count.")
    ] = None,
    source: Annotated[
        Source | None, Field(description="arXiv, or the conference that published the paper.")
    ] = None,
    year: Annotated[
        int | None, Field(description="Conference year, else the year of the first version.")
    ] = None,
    date_from: Annotated[date | None, Field(description="First version on or after (UTC).")] = None,
    date_to: Annotated[date | None, Field(description="First version on or before (UTC).")] = None,
    added: Annotated[
        date | Literal["latest"] | None,
        Field(
            description="Only papers arXiv announced that day, new or in a new version, or "
            "'latest' for its last day with any."
        ),
    ] = None,
    added_kind: Annotated[
        Literal["new", "updated"] | None,
        Field(description="With added: only new papers, or only new versions of older ones."),
    ] = None,
    starred: Annotated[bool, Field(description="Only papers you starred.")] = False,
    innovation: Annotated[
        Innovation | None, Field(description="Nature of the innovation (reported papers).")
    ] = None,
    page: Annotated[int, Field(ge=1)] = 1,
    page_size: Annotated[int, Field(ge=1, le=50)] = 10,
    *,
    ctx: Context,
) -> str:
    """Search the library; filters combine with AND. Results are best match first when there
    is a query, else most recently starred first with starred, else newest first. Each paper
    has its id (for get_paper and read_paper), first authors, source (arXiv or a conference)
    and year, first-version date, tags, topics, paper type, a short summary, has_report (a
    full report is there for get_paper), starred (you starred it) and innovation (the nature
    of its innovation, for reported papers). With added, added_day is the day it meant (null
    if there was none).
    """
    user_id = _caller(ctx)
    with Session(engine()) as session:
        topic_id = None
        if topic:
            found = topics.find(session, topic)
            if found is None:
                raise ToolError(f"No topic named {topic!r}; papers' topics show the names")
            topic_id = found.id
        if added == "latest":
            day = queries.latest_added_day(session, include_out_of_scope)
        else:
            day = added
        if added and day is None:  # latest, but no paper was ever added
            return _json({"total": 0, "page": page, "pages": 0, "papers": [], "added_day": None})
        total, papers = queries.search_papers(
            session,
            q=query.strip() or None,
            domain=_one(domain),
            topic=_one(topic_id),
            paper_type=_one(paper_type),
            keyword=keyword,
            include_out_of_scope=include_out_of_scope,
            category=_one(category),
            source=_one(source),
            year=_one(year),
            date_from=date_from,
            date_to=date_to,
            added=day,
            added_kind=added_kind,
            starred_by=user_id if starred else None,
            innovation=_one(innovation),
            page=page,
            page_size=page_size,
        )
        reported = queries.with_report(session, [p.id for p in papers])
        mine = queries.starred(session, user_id, [p.id for p in papers])
        items = [
            {
                "id": p.id,
                "title": p.title,
                "authors": _authors(p.authors),
                "source": p.source,
                "year": p.year,
                "date": _day(p.published_at),
                "categories": p.categories,
                "tags": _tags(p),
                "topics": [t["name"] for t in _topics(p)],
                "type": p.paper_type,
                "summary": p.summary or p.abstract[:300],
                "has_report": p.id in reported,
                "starred": p.id in mine,
                "innovation": p.innovation,
            }
            for p in papers
        ]
    pages = -(-total // page_size)
    result = {"total": total, "page": page, "pages": pages, "papers": items}
    return _json(result | ({"added_day": day} if added else {}))


@mcp.tool(annotations=READ_ONLY, structured_output=False)
def get_paper(paper_id: int) -> str:
    """One paper: metadata, abstract, all tags, topics, paper type, the brief summary and the
    newest full report (null if none; report_status says whether one is queued). The report
    is Markdown in Chinese; a line like [[图:Fig. 2]] or [[表:TABLE I]] stands for that figure
    or table of the paper, whose caption and page are in report.figures (read_paper shows the
    page). A reported paper also has innovation: {"type", "reason", "overview"}, the nature of
    its innovation (a neutral classification, not a grade), why, and the label of its method
    overview figure (null if it has none).
    """
    with Session(engine()) as session:
        paper = queries.get_paper(session, paper_id)
        if paper is None:
            raise ToolError(f"No paper with id {paper_id}")
        full = queries.latest_report(session, paper_id)
        innovation = queries.latest_innovation(session, paper_id)
        for figure in full["figures"] if full else []:
            for key in ("url", "width", "height"):  # the image is for people
                del figure[key]
        return _json(
            {
                "id": paper.id,
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "authors": paper.authors,
                "source": paper.source,
                "year": paper.year,
                "track": paper.track,
                "published": _day(paper.published_at),
                "updated": _day(paper.updated_at),
                "categories": paper.categories,
                "doi": paper.doi,
                "journal_ref": paper.journal_ref,
                "comment": paper.comment,
                "pdf_url": paper.pdf_url,
                "abstract": paper.abstract,
                "tags": _tags(paper),
                "topics": _topics(paper),
                "paper_type": paper.paper_type,
                "brief_summary": paper.summary,
                "report_status": paper.report_status,
                "report": full,
                "innovation": innovation,
            }
        )


@mcp.tool(annotations=READ_ONLY, structured_output=False)
def read_paper(
    paper_id: int,
    first_page: Annotated[int, Field(ge=1)] = 1,
    last_page: Annotated[int | None, Field(ge=1)] = None,
) -> str:
    """The text of pages first_page..last_page (1-based, inclusive) as extracted from the PDF,
    each page starting with "=== PAGE n ===". One call returns at most 10 pages or about
    30,000 characters; the last line says which pages are left. The first read of a paper
    may take a minute while its PDF is fetched from arXiv.
    """
    with Session(engine()) as session:
        if session.get(Paper, paper_id) is None:
            raise ToolError(f"No paper with id {paper_id}")
        text = queries.read_pages(session, paper_id)
    if text is None:
        # ponytail: fetched on the spot, waiting for the arXiv lock behind a running sync
        # (minutes at worst; the text is kept, so a retry is instant). Add a lock timeout if
        # agents time out on it.
        try:
            report.paper_pages(paper_id)
        except report.ReportInvalid as e:  # no PDF link
            raise ToolError(f"Paper {paper_id} has no full text: {e}") from None
        except (OSError, ValueError, pdfium.PdfiumError) as e:
            raise ToolError(f"Could not fetch or read the PDF ({e}); try again later") from None
        with Session(engine()) as session:
            text = queries.read_pages(session, paper_id)
    pdf_url, pages = text
    total = len(pages)
    if first_page > total:
        raise ToolError(f"Paper {paper_id} has {total} pages")
    last = min(last_page or total, total, first_page + MAX_PAGES - 1)
    blocks, size, shown = [], 0, first_page - 1
    for number, page in pages[first_page - 1 : last]:
        block = f"=== PAGE {number} ===\n{page}\n"
        if blocks and size + len(block) > MAX_CHARS:
            break
        blocks.append(block[:MAX_CHARS])  # a single huge page is cut
        size, shown = size + len(block), number
    head = f"Paper {paper_id}, {pdf_url}, {total} pages. Pages {first_page}-{shown}:\n\n"
    rest = f"Pages {shown + 1}-{total} not shown: call again with first_page={shown + 1}."
    return head + "\n".join(blocks) + ("\n" + rest if shown < total else "\nEnd of paper.")


@mcp.tool(structured_output=False)
def save_note(
    content: Annotated[str, Field(min_length=1, max_length=20_000)],
    paper_id: Annotated[int | None, Field(description="The paper it is about, if any.")] = None,
    *,
    ctx: Context,
) -> str:
    """Save a private note, visible only to you (the owner of the token), optionally on a
    paper. Returns its id.
    """
    user_id = _caller(ctx)
    with Session(engine()) as session, session.begin():
        note = queries.add_note(session, user_id, content, paper_id)
        if note is None:
            raise ToolError(f"No paper with id {paper_id}")
        return _json({"id": note.id, "paper_id": note.paper_id, "created_at": note.created_at})


@mcp.tool(annotations=READ_ONLY, structured_output=False)
def list_notes(
    paper_id: Annotated[int | None, Field(description="Only notes on this paper.")] = None,
    page: Annotated[int, Field(ge=1)] = 1,
    *,
    ctx: Context,
) -> str:
    """Your own notes, newest first, 20 per page: notes (some quoting the paper) and your free
    block on a paper (kind "page").
    """
    user_id = _caller(ctx)
    with Session(engine()) as session:
        total, rows = queries.list_notes(session, user_id, paper_id, page)
    notes = [
        {
            "id": note.id,
            "paper_id": note.paper_id,
            "paper_title": title,
            "kind": note.kind,  # page: the free block on the paper
            "content": note.content,
            "quote": note.quote,
            "created_at": note.created_at,
        }
        for note, title in rows
    ]
    return _json({"total": total, "page": page, "notes": notes})


@mcp.tool(annotations=ToolAnnotations(destructive_hint=True), structured_output=False)
def delete_note(note_id: int, *, ctx: Context) -> str:
    """Delete one of your own notes."""
    user_id = _caller(ctx)
    with Session(engine()) as session, session.begin():
        if not queries.delete_note(session, user_id, note_id):
            raise ToolError(f"You have no note with id {note_id}")
    return f"Deleted note {note_id}."


def _known_token(authorization: str | None) -> bool:
    with Session(engine()) as session:
        return auth.bearer_user(session, authorization) is not None


class RequireToken:
    """ASGI wrapper: only requests with a valid personal token reach the MCP app, others get
    401 before anything is parsed. Tools look the caller up again from the same header.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            authorization = Headers(scope=scope).get("authorization")
            if not await anyio.to_thread.run_sync(_known_token, authorization):
                response = JSONResponse(
                    {"detail": "Not authenticated"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


app = RequireToken(
    mcp.streamable_http_app(
        stateless_http=True,  # clients before protocol 2026-07-28: no session to keep
        json_response=True,  # one JSON body per POST rather than an SSE stream
        # Every request needs a token and the reverse proxy sets Host; the SDK's localhost-only
        # DNS-rebinding check would just answer the real hostname with 421 (docs/run/deploy.md).
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
)
