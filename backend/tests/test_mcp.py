"""MCP over real HTTP: the app runs in uvicorn (lifespan included), the SDK's client connects
with a personal token, exactly as an agent would.
"""

import asyncio
import json
import threading
import time
from datetime import UTC, date, datetime

import httpx2
import pytest
import uvicorn
from conftest import DATA
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from scholarmind import arxiv, auth, mcp_server, queries
from scholarmind.api import app
from scholarmind.db import (
    Analysis,
    Figure,
    Paper,
    PaperText,
    PaperTopic,
    Tag,
    Token,
    Topic,
    User,
    engine,
)

TOOLS = {"search_papers", "get_paper", "read_paper", "save_note", "list_notes", "delete_note"}
REPORT = "## 方法\n\n[[图:Fig. 2]]\n\n## 存疑之处\n\n无。"
INNOVATION = {"type": "模块组合", "reason": "组合了建图和规划。"}


@pytest.fixture(scope="session")
def base_url(schema):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    server.should_exit = True
    thread.join(5)


def _user(name: str) -> str:
    with Session(engine()) as s, s.begin():
        user = User(username=name, password_hash=auth.hash_password(f"pw-{name}-123"))
        s.add(user)
        return auth.new_token(s, user, "test")[1]


def _mcp(base_url: str, token: str, scenario):
    """Run `await scenario(client)` with an MCP client connected as the token's owner."""

    async def main():
        headers = {"Authorization": f"Bearer {token}"}
        timeout = httpx2.Timeout(30.0, read=300.0)
        async with httpx2.AsyncClient(headers=headers, timeout=timeout) as http:
            async with Client(streamable_http_client(f"{base_url}/mcp", http_client=http)) as c:
                return await scenario(c)

    return asyncio.run(main())


def _text(result) -> str:
    return result.content[0].text


def test_tools_and_tokens(session, base_url):
    token = _user("alice")
    tools = _mcp(base_url, token, lambda c: c.list_tools())
    assert {t.name for t in tools.tools} == TOOLS
    search = next(t for t in tools.tools if t.name == "search_papers")
    assert "robotics" in json.dumps(search.input_schema)  # the vocabulary is the enum
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    for headers in ({}, {"Authorization": "Bearer sm_wrong"}, {"Authorization": token}):
        r = httpx2.post(f"{base_url}/mcp", json=message, headers=headers)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    with Session(engine()) as s, s.begin():  # the token is in the tokens table, marked used
        assert s.scalar(
            select(Token.last_used_at).where(Token.token_hash == auth.hash_token(token))
        )
        s.execute(update(Token).values(expires_at=func.now()))
    r = httpx2.post(f"{base_url}/mcp", json=message, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401  # expired


def _library():
    """Paper 1 with labels, a report and stored text; paper 2 with nothing but a PDF link;
    paper 3 out of scope. Needs the vocabulary fixture.
    """
    with Session(engine()) as s, s.begin():
        day = datetime(2026, 11, 20, 12, tzinfo=UTC)
        s.add_all(
            [
                Paper(
                    title="Gaussian Splatting for Robots",
                    title_norm="gaussian splatting for robots",
                    abstract="We map scenes with 3D gaussian splatting.",
                    authors="Ann A, Bob B, Cy C, Di D",
                    categories=["cs.RO", "cs.CV"],
                    published_at=day,
                    year=2026,
                    pdf_url="https://arxiv.org/pdf/2611.00001v1",
                    summary="简报总结。",
                    status="briefed",
                    report_status="reported",
                    paper_type="method",
                ),
                Paper(
                    title="Planner",
                    title_norm="planner",
                    published_at=day,
                    source="CVPR",
                    year=2026,
                    track="main",
                    pdf_url="https://arxiv.org/pdf/2611.00002v1",
                ),
                Paper(title="Bandits", title_norm="bandits", paper_type="theory"),
            ]
        )
        s.flush()
        gaussians = s.scalar(select(Topic.id).where(Topic.name == "3D Gaussian Splatting"))
        s.add_all(
            [
                Tag(paper_id=1, kind="domain", value="robotics", source="ai"),
                Tag(paper_id=3, kind="domain", value="out-of-scope", source="ai"),
                PaperTopic(paper_id=1, topic_id=gaussians, source="ai"),
                Tag(paper_id=1, kind="method", value="3D gaussian splatting", source="ai"),
                Analysis(
                    paper_id=1,
                    level="report",
                    model="m",
                    prompt_version="report.v2",
                    result={"version": 2, "markdown": REPORT, "pdf_url": "https://arxiv.org/x"},
                    usage={},
                ),
                PaperText(
                    paper_id=1,
                    pdf_url="https://arxiv.org/pdf/2611.00001v1",
                    pages=[f"text of page {n}" for n in range(1, 13)],
                ),
            ]
        )
        s.flush()
        s.add(Figure(analysis_id=1, kind="figure", label="Fig. 2", caption="Fig. 2: Map.", page=2,
                     image=b"png", width=640, height=480))  # fmt: skip
        s.add(Analysis(paper_id=1, level="assessment", model="m", prompt_version="assess.v3",
                       result=INNOVATION, usage={}))  # fmt: skip
        s.get(Paper, 1).innovation = "模块组合"


def test_search_get_and_read(session, vocabulary, base_url, monkeypatch):
    _library()
    fetched = []
    pdf = (DATA / "paper.pdf").read_bytes()
    monkeypatch.setattr(arxiv, "fetch_pdf", lambda url: fetched.append(url) or pdf)
    token = _user("alice")

    async def scenario(c):
        search = await c.call_tool("search_papers", {"query": "gaussian splatting"})
        filtered = await c.call_tool("search_papers", {"domain": "robotics", "page_size": 1})
        paper = await c.call_tool("get_paper", {"paper_id": 1})
        first = await c.call_tool("read_paper", {"paper_id": 1})
        rest = await c.call_tool("read_paper", {"paper_id": 1, "first_page": 11})
        fresh = await c.call_tool("read_paper", {"paper_id": 2, "last_page": 1})
        missing = await c.call_tool("get_paper", {"paper_id": 99})
        bad = await c.call_tool("search_papers", {"domain": "cooking"})
        labels = [
            await c.call_tool("search_papers", args)
            for args in (
                {"topic": "gaussian splatting"},  # an alias
                {"topic": "Cooking"},
                {"paper_type": "theory"},
                {"paper_type": "theory", "include_out_of_scope": True},
                {"domain": "out-of-scope"},
                {"paper_type": "essay"},
                {},
            )
        ]
        combined = await c.call_tool(
            "search_papers", {"innovation": "模块组合", "domain": "robotics"}
        )
        other = await c.call_tool("search_papers", {"innovation": "机制创新"})
        venue = [
            await c.call_tool(tool, args)
            for tool, args in (
                ("search_papers", {"source": "CVPR", "year": 2026}),
                ("get_paper", {"paper_id": 2}),
                ("search_papers", {"source": "Nature"}),
            )
        ]
        results = search, filtered, paper, first, rest, fresh, missing, bad
        return results, labels, combined, other, venue

    results, labels, combined, other, venue = _mcp(base_url, token, scenario)
    search, filtered, paper, first, rest, fresh, missing, bad = results
    found = json.loads(_text(search))
    assert found["total"] == 1 and found["pages"] == 1
    assert found["papers"][0] == {
        "id": 1,
        "title": "Gaussian Splatting for Robots",
        "authors": "Ann A, Bob B, Cy C et al.",
        "source": "arXiv",
        "year": 2026,
        "date": "2026-11-20",
        "categories": ["cs.RO", "cs.CV"],
        "tags": {"domain": ["robotics"], "method": ["3D gaussian splatting"]},
        "topics": ["3D Gaussian Splatting"],
        "type": "method",
        "summary": "简报总结。",
        "has_report": True,
        "starred": False,
        "innovation": "模块组合",
    }
    assert [p["id"] for p in json.loads(_text(combined))["papers"]] == [1]
    assert json.loads(_text(other))["total"] == 0
    assert json.loads(_text(filtered))["papers"][0]["id"] == 1
    detail = json.loads(_text(paper))
    assert detail["brief_summary"] == "简报总结。" and detail["report_status"] == "reported"
    assert detail["report"]["markdown"] == REPORT and detail["report"]["model"] == "m"
    figure = {"label": "Fig. 2", "kind": "figure", "page": 2, "caption": "Fig. 2: Map."}
    assert detail["report"]["figures"] == [figure]  # no image link: that is for people
    assert detail["paper_type"] == "method"
    assert [(t["name"], t["zh"]) for t in detail["topics"]] == [
        ("3D Gaussian Splatting", "三维高斯泼溅")
    ]
    assert detail["innovation"] == INNOVATION
    assert "=== PAGE 10 ===" in _text(first) and "=== PAGE 11 ===" not in _text(first)
    assert _text(first).endswith("Pages 11-12 not shown: call again with first_page=11.")
    assert "=== PAGE 12 ===" in _text(rest) and _text(rest).endswith("End of paper.")
    assert "longhorizon" in _text(fresh) and fetched == ["https://arxiv.org/pdf/2611.00002v1"]
    assert missing.is_error and "No paper with id 99" in _text(missing)
    assert bad.is_error  # not in the vocabulary
    by_topic, no_topic, theory, theory_all, out, bad_type, everything = labels
    assert [p["id"] for p in json.loads(_text(by_topic))["papers"]] == [1]
    assert no_topic.is_error and "No topic named 'Cooking'" in _text(no_topic)
    assert json.loads(_text(theory))["total"] == 0  # out of scope is hidden by default
    assert [p["id"] for p in json.loads(_text(theory_all))["papers"]] == [3]
    assert [p["id"] for p in json.loads(_text(out))["papers"]] == [3]
    assert bad_type.is_error
    assert sorted(p["id"] for p in json.loads(_text(everything))["papers"]) == [1, 2]
    by_source, venue_paper, bad_source = venue
    assert [(p["id"], p["source"]) for p in json.loads(_text(by_source))["papers"]] == [(2, "CVPR")]
    detail = json.loads(_text(venue_paper))
    assert (detail["source"], detail["year"], detail["track"]) == ("CVPR", 2026, "main")
    assert bad_source.is_error  # sources are arXiv and the configured venues


def test_stars_and_new_papers(session, vocabulary, base_url):
    _library()  # paper 3 is out of scope
    alice, bob = _user("alice"), _user("bob")
    with Session(engine()) as s, s.begin():
        s.execute(update(Paper).values(added_on=date(2026, 11, 20), arxiv_version=1))
        s.get(Paper, 2).arxiv_version = 2  # a new version of an older paper
        queries.star(s, s.scalar(select(User.id).where(User.username == "alice")), 2, True)

    async def scenario(c):
        calls = [
            {},
            {"starred": True},
            {"added": "latest"},
            {"added": "latest", "added_kind": "new"},
            {"added": "latest", "include_out_of_scope": True},
            {"added": "2026-11-19"},
        ]
        return [json.loads(_text(await c.call_tool("search_papers", args))) for args in calls]

    every, starred, latest, new, with_out, other_day = _mcp(base_url, alice, scenario)
    assert {p["id"]: p["starred"] for p in every["papers"]} == {1: False, 2: True}
    assert [p["id"] for p in starred["papers"]] == [2] and "added_day" not in starred
    assert latest["added_day"] == "2026-11-20" and latest["total"] == 2
    assert [p["id"] for p in new["papers"]] == [1]
    assert with_out["total"] == 3
    assert other_day["added_day"] == "2026-11-19" and other_day["total"] == 0
    bobs = _mcp(base_url, bob, lambda c: c.call_tool("search_papers", {"starred": True}))
    assert json.loads(_text(bobs))["total"] == 0


def test_notes_are_private(session, vocabulary, base_url):
    _library()
    alice, bob = _user("alice"), _user("bob")

    async def write(c):
        on_paper = await c.call_tool("save_note", {"content": "读第 3 页", "paper_id": 1})
        loose = await c.call_tool("save_note", {"content": "idea"})
        nowhere = await c.call_tool("save_note", {"content": "x", "paper_id": 99})
        return json.loads(_text(on_paper)), json.loads(_text(loose)), nowhere

    on_paper, loose, nowhere = _mcp(base_url, alice, write)
    assert on_paper["paper_id"] == 1 and loose["paper_id"] is None and nowhere.is_error

    async def snoop(c):
        mine = await c.call_tool("list_notes", {})
        steal = await c.call_tool("delete_note", {"note_id": on_paper["id"]})
        return json.loads(_text(mine)), steal

    bobs, steal = _mcp(base_url, bob, snoop)
    assert bobs == {"total": 0, "page": 1, "notes": []} and steal.is_error

    async def read_and_delete(c):
        on_one = await c.call_tool("list_notes", {"paper_id": 1})
        deleted = await c.call_tool("delete_note", {"note_id": on_paper["id"]})
        left = await c.call_tool("list_notes", {})
        return json.loads(_text(on_one)), _text(deleted), json.loads(_text(left))

    on_one, deleted, left = _mcp(base_url, alice, read_and_delete)
    assert [(n["content"], n["paper_title"]) for n in on_one["notes"]] == [
        ("读第 3 页", "Gaussian Splatting for Robots")
    ]
    assert deleted == f"Deleted note {on_paper['id']}."
    assert [n["content"] for n in left["notes"]] == ["idea"]

    with Session(engine()) as s, s.begin():  # an excerpt and a free block made on the web
        user_id = s.scalar(select(User.id).where(User.username == "alice"))
        queries.add_note(s, user_id, "", 1, "a robust planner", {"source": "pdf", "page": 2})
        queries.set_page(s, user_id, 1, "# 想法")
    on_one = _mcp(base_url, alice, lambda c: c.call_tool("list_notes", {"paper_id": 1}))
    assert [(n["kind"], n["quote"], n["content"]) for n in json.loads(_text(on_one))["notes"]] == [
        ("page", None, "# 想法"),
        ("note", "a robust planner", ""),
    ]
    assert mcp_server.MAX_PAGES == 10  # read_paper's documented limit
