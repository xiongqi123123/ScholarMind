"""Conference imports, offline: requests are answered from trimmed real responses saved
2026-11-23 in data/venues (the AAAI work in issue 44 had its issue changed for the test).
"""

import email.message
import email.utils
import http.client
import io
import json
import logging
import ssl
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import DATA
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from scholarmind import arxiv, jobs, venues
from scholarmind.config import Config, config
from scholarmind.db import Paper, SyncState, engine

V = DATA / "venues"
OPENALEX = json.loads((V / "openalex.json").read_text())
CVPR_LIST = "https://openaccess.thecvf.com/CVPR2026?day=all"
CVPR_PAPERS = "https://openaccess.thecvf.com/content/CVPR2026/html/*"


def _site(monkeypatch, pages: dict) -> list[str]:
    """Answer requests from `pages`: URL (or prefix ending in *) -> body, or an exception to
    raise. Anything else is a 404. Returns the URLs requested.
    """
    requested = []

    def get(url, interval):
        requested.append(url)
        for key, body in pages.items():
            if url == key or (key.endswith("*") and url.startswith(key[:-1])):
                if isinstance(body, Exception):
                    raise body
                return body if isinstance(body, bytes) else body.encode()
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    monkeypatch.setattr(venues, "_get", get)
    return requested


def _flaky(monkeypatch, *answers) -> tuple[list[str], list[float]]:
    """urlopen gives these answers in turn (bytes, a reader, or an exception to raise); returns
    the URLs requested and the pauses taken (recorded, not slept).
    """
    requested, slept, answers = [], [], list(answers)

    def urlopen(request, timeout):
        requested.append(request.full_url)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(answer) if isinstance(answer, bytes) else answer

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(venues.time, "sleep", slept.append)
    return requested, slept


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://x.org/p", code, "error", headers, None)


class _CutOff(io.BytesIO):
    """An answer whose connection is lost halfway through the body."""

    def read(self, *args):
        raise http.client.IncompleteRead(b"<html>half")


def test_get_retries_transient_failures(monkeypatch, caplog):
    """A flaky way out (the proxy) costs pauses, not the edition: network errors, a connection
    lost mid-answer, 429 and 5xx are tried again after growing pauses, Retry-After honoured
    when it asks for longer (up to 10 minutes); the site's own interval follows every request.
    """
    url = "https://proceedings.iclr.cc/paper_files/paper/2026/hash/x-Abstract-Conference.html"
    eof = urllib.error.URLError(ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING]"))
    in_a_while = email.utils.format_datetime(
        datetime.now(UTC) + timedelta(seconds=200), usegmt=True
    )
    requested, slept = _flaky(
        monkeypatch,
        eof,
        _CutOff(),
        _http_error(429, "90"),  # asks for 90 s: the 120 s backoff is longer
        _http_error(503, in_a_while),  # an HTTP date, shorter than 240 s: the backoff wins
        b"<html>page</html>",
    )
    with caplog.at_level(logging.WARNING, logger="scholarmind.venues"):
        assert venues._get(url, 2) == b"<html>page</html>"
    assert requested == [url] * 5
    assert slept == [2, 30, 2, 60, 2, 120, 2, 240, 2]
    first = caplog.records[0].getMessage()
    assert url in first and "attempt 1" in first and "UNEXPECTED_EOF" in first and "30 s" in first
    assert len(caplog.records) == 4

    # Retry-After wins when it asks for longer than the backoff, up to 10 minutes.
    for retry_after, wait in (("90", 90), (in_a_while, 200), ("3600", 600), ("soon", 30)):
        _, slept = _flaky(monkeypatch, _http_error(429, retry_after), b"ok")
        assert venues._get(url, 1) == b"ok"
        assert slept[0] == 1 and abs(slept[1] - wait) < 5 and slept[2] == 1, retry_after
    _, slept = _flaky(monkeypatch, _http_error(500, "90"), b"ok")  # only 429 and 503 ask
    assert venues._get(url, 1) == b"ok" and slept == [1, 30, 1]


def test_get_gives_up(monkeypatch):
    """403, 404 and other 4xx are answers, not hiccups: raised at once. Five failures in a row
    raise the last error, as before."""
    for code in (403, 404, 410):
        requested, slept = _flaky(monkeypatch, _http_error(code), b"never")
        with pytest.raises(urllib.error.HTTPError) as raised:
            venues._get("https://x.org/p", 2)
        assert raised.value.code == code and len(requested) == 1 and slept == [2]
    timeout = TimeoutError("timed out")
    requested, slept = _flaky(monkeypatch, *[timeout] * 5)
    with pytest.raises(TimeoutError):
        venues._get("https://x.org/p", 2)
    assert len(requested) == 5 and slept == [2, 30, 2, 60, 2, 120, 2, 240, 2]


def _openalex(monkeypatch, name: str, count: int | None = None) -> list[str]:
    page = OPENALEX[name]
    page = page | {"meta": {"count": count or len(page["results"])}}
    archive = (V / "ojs_archive.html").read_text()
    return _site(
        monkeypatch,
        {
            "https://api.openalex.org/works?*": json.dumps(page),
            "https://ojs.aaai.org/index.php/AAAI/issue/archive": archive,
        },
    )


def _listing(name: str, year: int, limit: int | None = None):
    venue = config().venues[name]
    return venues.LISTINGS[venue.adapter](name, venue, year, limit)


def _expect_few(monkeypatch, name: str, **changes):
    """The fixtures hold a few papers, not a whole edition."""
    venue = config().venues[name].model_copy(update={"min_papers": 1} | changes)
    monkeypatch.setitem(config().venues, name, venue)


def test_config_overrides_defaults():
    base = {
        "arxiv": {"categories": ["cs.CV"]},
        "vocabulary": {"domains": ["d"], "tasks": ["t"]},
        "analysis": {"base_url": "http://x", "brief_model": "m"},
    }
    assert set(Config.model_validate(base).venues) == set(venues.config().venues)
    custom = Config.model_validate(
        base
        | {
            "venues": {
                "CVPR": {"min_papers": 3000},
                "ICML": None,
                "CoRL": {"adapter": "pmlr", "title": "Proceedings of CoRL {year}"}
                | {"min_papers": 100},
            }
        }
    ).venues
    assert custom["CVPR"].min_papers == 3000 and custom["CVPR"].adapter == "cvf"
    assert "ICML" not in custom and custom["CoRL"].title.startswith("Proceedings of CoRL")
    with pytest.raises(ValueError, match="needs source"):
        Config.model_validate(base | {"venues": {"X": {"adapter": "openalex", "min_papers": 1}}})


def test_openalex_acl(monkeypatch):
    _openalex(monkeypatch, "ACL")
    records, listed = _listing("ACL", 2026)
    assert listed == len(records) == 2  # the industry track paper is not main conference
    first = records[0]
    assert first["track"] == "long" and first["source"] == "ACL" and first["year"] == 2026
    assert first["venue_key"].startswith("ACL:W") and first["doi"].startswith("10.18653/v1/")
    assert first["pdf_url"] == f"https://aclanthology.org/{first['doi'][len('10.18653/v1/') :]}.pdf"
    assert first["arxiv_id"] and first["abstract"].count(" ") == 24  # 25 words kept in the fixture
    assert first["authors"].count(", ") == 3 and first["published_at"].tzinfo == UTC


def test_openalex_rss_aaai_mm(monkeypatch):
    _openalex(monkeypatch, "RSS")
    with_abstract, without = _listing("RSS", 2026)[0]
    number = with_abstract["doi"].rsplit(".", 1)[1]
    assert with_abstract["pdf_url"] == f"https://www.roboticsproceedings.org/rss22/p{number}.pdf"
    assert with_abstract["abstract"] and without["abstract"] is None

    requested = _openalex(monkeypatch, "AAAI")
    records, _ = _listing("AAAI", 2026)
    assert [r["doi"].split(".")[2] for r in records] == ["v40i1"]  # not the special track
    assert requested[1:] == [  # the archive, until a page lists nothing more
        "https://ojs.aaai.org/index.php/AAAI/issue/archive",
        "https://ojs.aaai.org/index.php/AAAI/issue/archive/2",
    ]

    _openalex(monkeypatch, "ACM MM")
    (paper,) = _listing("ACM MM", 2026)[0]  # the 5-page doctoral symposium paper is not
    assert paper["pdf_url"] is None and paper["arxiv_id"]  # ACM's PDF is not downloadable


def test_openalex_limit_and_not_published(monkeypatch):
    _openalex(monkeypatch, "ACL", count=2641)
    records, listed = _listing("ACL", 2026, limit=1)
    assert listed == 2641 and len(records) == 2  # the whole page: limit counts main papers
    _site(monkeypatch, {"https://api.openalex.org/works?*": '{"meta":{"count":0},"results":[]}'})
    with pytest.raises(venues.NotPublished):
        _listing("ICRA", 2027)


def test_cvf(monkeypatch):
    _site(
        monkeypatch,
        {
            CVPR_LIST: (V / "cvf_list.html").read_bytes(),
            CVPR_PAPERS: (V / "cvf_paper.html").read_bytes(),
        },
    )
    records, listed = _listing("CVPR", 2026)
    assert listed == 3 and [bool(r["arxiv_id"]) for r in records] == [True, True, False]
    first = records[0]  # DirectFisheye-GS
    assert first["arxiv_id"] == "2604.00648" and first["authors"].startswith("Zhengxian Yang, ")
    assert first["pdf_url"].startswith("https://openaccess.thecvf.com/content/CVPR2026/papers/")
    assert first["venue_key"].startswith("CVPR:/content/CVPR2026/html/Yang_DirectFisheye-GS")
    detail = venues._cvf_detail(config().venues["CVPR"], first)
    assert detail["abstract"].startswith("3D Gaussian Splatting (3DGS) has enabled")
    with pytest.raises(venues.NotPublished):
        _listing("CVPR", 2027)  # 404
    _site(monkeypatch, {CVPR_LIST: "<html>maintenance</html>"})
    with pytest.raises(venues.ImportFailed):
        _listing("CVPR", 2026)


def test_ecva(monkeypatch):
    page = "https://www.ecva.net/papers/eccv_2024/papers_ECCV/html/*"
    listing = (V / "ecva_list.html").read_bytes()
    _site(
        monkeypatch,
        {"https://www.ecva.net/papers.php": listing, page: (V / "ecva_paper.html").read_bytes()},
    )
    records, listed = _listing("ECCV", 2024)
    assert listed == 2  # not the 2022 papers on the same page
    first = records[0]
    assert first["authors"] == "Jacopo Bonato, Marco Cotogni, Luigi Sabetta"
    assert first["doi"] == "10.1007/978-3-031-73232-4_1" and first["arxiv_id"] is None
    assert first["pdf_url"] == "https://www.ecva.net/papers/eccv_2024/papers_ECCV/papers/00004.pdf"
    abstract = venues._cvf_detail(config().venues["ECCV"], first)["abstract"]
    assert abstract.startswith("In this paper, we introduce") and not abstract.endswith('"')
    with pytest.raises(venues.NotPublished, match="eccv_2026"):
        _listing("ECCV", 2026)


def test_proceedings(monkeypatch):
    base = "https://papers.nips.cc/paper_files/paper/"
    _site(
        monkeypatch,
        {
            base + "2024": (V / "proceedings_list.html").read_bytes(),
            base + "2025": (V / "proceedings_2025.html").read_bytes(),
            base + "2024/hash/*": (V / "proceedings_paper.html").read_bytes(),
        },
    )
    records, listed = _listing("NeurIPS", 2024)
    assert listed == 2  # not the datasets & benchmarks paper
    first = venues._proceedings_detail(config().venues["NeurIPS"], records[0])
    assert first["title"].startswith("MicroAdam") and first["doi"] == "10.52202/079017-0001"
    assert first["pdf_url"].endswith("-Paper-Conference.pdf") and first["abstract"].startswith(
        "We propose a new variant of the Adam optimizer"
    )
    assert first["published_at"] == datetime(2024, 12, 16, tzinfo=UTC)
    with pytest.raises(venues.NotPublished, match="creative_ai_track"):
        _listing("NeurIPS", 2025)  # the index lists only the creative AI track so far
    with pytest.raises(venues.NotPublished):
        _listing("NeurIPS", 2026)


def test_pmlr(monkeypatch):
    _site(
        monkeypatch,
        {
            "https://proceedings.mlr.press/": (V / "pmlr_index.html").read_bytes(),
            "https://proceedings.mlr.press/v306/assets/bib/bibliography.bib": (
                V / "pmlr.bib"
            ).read_bytes(),
        },
    )
    records, listed = _listing("ICML", 2026)
    assert listed == 3
    assert records[0]["title"].startswith("TUR-DPO: Topology-")
    assert records[0]["authors"].startswith("Abdulhady Abas, Fatemeh Daneshfar")
    assert records[0]["pdf_url"].startswith("https://raw.githubusercontent.com/mlresearch/v306/")
    assert records[0]["venue_key"] == "ICML:/v306/abas26a.html" and records[0]["abstract"]
    assert "Martín Costa" in records[2]["authors"] and "Jakub Łącki" in records[2]["authors"]
    with pytest.raises(venues.NotPublished):
        _listing("ICML", 2027)


def _cvpr(monkeypatch, failing: str | None = None) -> list[str]:
    pages = {
        CVPR_LIST: (V / "cvf_list.html").read_bytes(),
        CVPR_PAPERS: (V / "cvf_paper.html").read_bytes(),
    }
    if failing:  # put first: matched before the prefix
        pages = {failing: urllib.error.URLError("connection reset")} | pages
    return _site(monkeypatch, pages)


def _arxiv_lookup(monkeypatch) -> list[list[str]]:
    """arXiv answers with the fixture's metadata under the requested IDs."""
    looked_up, sample = [], arxiv.parse_atom((V / "arxiv_api.xml").read_bytes())

    def fetch_ids(ids):
        looked_up.append(ids)
        return [sample[0] | {"arxiv_id": i, "title": "arXiv title"} for i in ids]

    monkeypatch.setattr(arxiv, "fetch_ids", fetch_ids)
    return looked_up


def _count(session, **where) -> int:
    query = select(func.count()).select_from(Paper).filter_by(**where)
    return session.scalar(query)


def test_import_resumes_and_repeats(session, monkeypatch):
    _expect_few(monkeypatch, "CVPR")
    monkeypatch.setattr(venues, "BATCH", 2)
    looked_up = _arxiv_lookup(monkeypatch)
    third = "https://openaccess.thecvf.com/content/CVPR2026/html/Xiao_Generalizable"
    _cvpr(monkeypatch, failing=third + "*")
    with pytest.raises(OSError):
        venues.import_venue("CVPR", 2026)  # the third paper's page fails
    assert _count(session, source="CVPR") == 2  # the first batch is kept

    requested = _cvpr(monkeypatch)
    result = venues.import_venue("CVPR", 2026)
    assert result == {
        "listed": 3,
        "inserted": 1,
        "updated": 0,
        "skipped": 2,
        "missing": 0,
        "arxiv": 2,
        "done": True,
    }
    assert [u.startswith(third) for u in requested[1:]] == [True]  # only the missing page
    assert len(looked_up) == 1 and "2604.00648" in looked_up[0]  # both linked, in one request

    requested = _cvpr(monkeypatch)
    again = venues.import_venue("CVPR", 2026)
    assert again == {
        "listed": 3,
        "inserted": 0,
        "updated": 0,
        "skipped": 3,
        "missing": 0,
        "arxiv": 0,
        "done": True,
    }
    assert requested == [CVPR_LIST] and _count(session) == 3
    linked = session.scalars(select(Paper).where(Paper.arxiv_id.is_not(None))).all()
    assert len(linked) == 2
    for paper in linked:  # arXiv's metadata, the conference's title and PDF
        assert paper.arxiv_version and paper.categories and paper.title != "arXiv title"
        assert paper.pdf_url.startswith("https://openaccess.thecvf.com/")
    assert session.get(SyncState, "venue:CVPR:2026").cursor == date.today()


def _many(n: int) -> tuple[str, list[str]]:
    """A CVF listing of n copies of the fixture's first paper, each with its own page, and the
    URLs of those pages.
    """
    entry = (V / "cvf_list.html").read_text().split('<dt class="ptitle">')[1]
    href = entry.split('href="')[1].split('"')[0]
    hrefs = [href.replace(".html", f"_{i}.html") for i in range(n)]
    listing = "".join('<dt class="ptitle">' + entry.replace(href, h) for h in hrefs)
    return listing, ["https://openaccess.thecvf.com" + h for h in hrefs]


def test_import_skips_a_page_that_is_gone(session, monkeypatch, caplog):
    """Seen on CVPR 2024: a listed paper whose page (and PDF) answers 404. The edition goes on
    without it, counts it as missing and is done; the next import asks for that page again.
    """
    _expect_few(monkeypatch, "CVPR")
    _arxiv_lookup(monkeypatch)
    listing, urls = _many(25)
    gone = urllib.error.HTTPError(urls[3], 404, "Not Found", None, None)
    paper = (V / "cvf_paper.html").read_bytes()
    _site(monkeypatch, {urls[3]: gone, CVPR_LIST: listing, CVPR_PAPERS: paper})
    result = venues.import_venue("CVPR", 2026)
    assert (result["inserted"], result["missing"], result["done"]) == (24, 1, True)
    assert "CVPR 2026: skipped 'DirectFisheye-GS" in caplog.text and urls[3] in caplog.text
    assert session.get(SyncState, "venue:CVPR:2026")
    requested = _site(monkeypatch, {urls[3]: gone, CVPR_LIST: listing, CVPR_PAPERS: paper})
    again = venues.import_venue("CVPR", 2026)
    assert requested == [CVPR_LIST, urls[3]] and (again["skipped"], again["missing"]) == (24, 1)


def test_import_fails_when_pages_are_gone(session, monkeypatch):
    """Every paper page 404 (the site moved them): the import stops once more than MAX_MISSING
    of the edition is gone, and the edition is not done. The listing's 404 is NotPublished.
    """
    _expect_few(monkeypatch, "CVPR")
    listing, urls = _many(25)
    requested = _site(monkeypatch, {CVPR_LIST: listing})
    with pytest.raises(venues.ImportFailed, match="2 paper pages gone"):
        venues.import_venue("CVPR", 2026)  # 5 % of 25 is 1.25
    assert requested == [CVPR_LIST, *urls[:2]]
    assert _count(session) == 0 and not session.get(SyncState, "venue:CVPR:2026")
    _site(monkeypatch, {})
    with pytest.raises(venues.NotPublished):
        venues.import_venue("CVPR", 2026)


def test_import_checks(session, monkeypatch):
    _cvpr(monkeypatch)
    with pytest.raises(venues.ImportFailed, match="expected at least 1000"):
        venues.import_venue("CVPR", 2026)  # 3 papers listed: the site changed, or is not done

    _expect_few(monkeypatch, "CVPR")
    many, _ = _many(25)
    _site(monkeypatch, {CVPR_LIST: many, CVPR_PAPERS: "<html>no abstract here</html>"})
    with pytest.raises(venues.ImportFailed, match="have abstract"):
        venues.import_venue("CVPR", 2026)
    assert _count(session) == 0


def test_jobs_and_due_editions(session, monkeypatch):
    _site(monkeypatch, {})
    assert jobs.import_job("CVPR", 2027)["status"] == "not_published"

    assert venues.due_editions() == []  # a venue's first edition is imported by hand
    with Session(engine()) as s, s.begin():
        s.add(Paper(title="t", title_norm="t", source="ICCV", year=2025))
        s.add(SyncState(source="venue:ICCV:2025", cursor=date.today() - timedelta(weeks=2)))
    due = venues.due_editions()
    assert ("ICCV", 2025) in due and ("ICCV", 2027) in due  # top up, and the next (every 2)
    later = venues.due_editions(date.today() + timedelta(weeks=5))
    assert ("ICCV", 2025) not in later and ("ICCV", 2027) in later


def test_import_job_slices(session, monkeypatch):
    """A queued import stops after its slice and queues the rest; each slice queues briefs for
    its new papers.
    """
    _expect_few(monkeypatch, "CVPR")
    _cvpr(monkeypatch)
    _arxiv_lookup(monkeypatch)
    monkeypatch.setattr(venues, "BATCH", 1)
    monkeypatch.setattr(jobs, "IMPORT_SLICE", 0)  # every slice is one batch
    queued, briefed = _queues(monkeypatch)
    slices = [jobs.import_job("CVPR", 2026)]
    while not slices[-1]["done"]:
        args, kwargs = queued[-1]
        assert args == ("CVPR", 2026, None, True, None)  # the same import, no editions after it
        slices.append(jobs.import_job(*args[:4], then=args[4]))
    assert [r["inserted"] for r in slices] == [1, 1, 1] and len(queued) == 2
    assert briefed == [1, 1, 1] and slices[-1]["arxiv"] == 2 and _count(session, source="CVPR") == 3

    # by hand: to the end at once; without briefs the papers are held, also from the daily sync
    session.execute(Paper.__table__.delete())
    session.commit()
    queued.clear()
    result = jobs.import_job("CVPR", 2026, briefs=False, whole=True)
    assert result["done"] and result["inserted"] == 3 and not queued and len(briefed) == 3
    assert _count(session, status="held") == 3
    assert venues.release_held("CVPR") == 3 and _count(session, status="new") == 3
    # one arXiv announced (a day's new paper, waiting for its brief) is never held
    session.execute(
        update(Paper).where(Paper.id == min(_ids(session))).values(added_on=date.today())
    )
    session.commit()
    assert venues.hold("CVPR", 2026) == 2 and _count(session, status="new") == 1


def test_editions_one_after_another(session, monkeypatch):
    _site(monkeypatch, {CVPR_LIST: (V / "cvf_list.html").read_bytes()})  # 3 papers, not 1000
    queued, _ = _queues(monkeypatch)
    with Session(engine()) as s, s.begin():
        s.add(SyncState(source="venue:CVPR:2025", cursor=date.today()))  # imported to the end
    plan = venues.editions(2024, 2026, ["CVPR", "ICCV"])
    # newest first; without CVPR 2025, imported, and ICCV 2024 and 2026: it is held in odd years
    assert plan == [("CVPR", 2026), ("ICCV", 2025), ("CVPR", 2024)]

    with pytest.raises(venues.ImportFailed):  # a failed edition still hands on to the next
        jobs.import_job(*plan[0], then=[list(e) for e in plan[1:]])
    assert queued[-1] == (("ICCV", 2025), {"briefs": True, "then": [["CVPR", 2024]]})
    assert jobs.import_job("ICCV", 2027, then=[["CVPR", 2024]])["status"] == "not_published"
    assert queued[-1] == (("CVPR", 2024), {"briefs": True, "then": []})

    # trying a source with a limit does not count as having imported the edition
    _expect_few(monkeypatch, "CVPR")
    _arxiv_lookup(monkeypatch)
    _site(monkeypatch, {CVPR_LIST: (V / "cvf_list.html").read_bytes(), CVPR_PAPERS: b"<html/>"})
    venues.import_venue("CVPR", 2026, limit=1)
    assert ("CVPR", 2026) in venues.editions(2026, 2026, ["CVPR"])


def _queues(monkeypatch) -> tuple[list, list]:
    """Record what would be queued instead of queueing it."""
    queued, briefed = [], []
    monkeypatch.setattr(jobs, "enqueue_import", lambda *a, **k: queued.append((a, k)) or _Job())
    monkeypatch.setattr(jobs, "enqueue_briefs", lambda limit=None: briefed.append(limit) or 1)
    return queued, briefed


def _ids(session) -> list[int]:
    return session.scalars(select(Paper.id)).all()


class _Job:
    id = "job-1"
