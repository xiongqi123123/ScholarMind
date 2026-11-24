from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

from conftest import DATA, arxiv_lock_free
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from scholarmind import arxiv, ingest
from scholarmind.db import Note, Paper, PaperTopic, Tag, Topic, User, UserPaper, engine
from scholarmind.ingest import normalize_title, same_authors, upsert_papers


def _sample():
    return arxiv.parse_list_records((DATA / "arxiv_listrecords.xml").read_bytes())[0]


def _ingest(records):
    with Session(engine()) as session, session.begin():
        return upsert_papers(session, records)


def _count(session):
    return session.scalar(select(func.count()).select_from(Paper))


def test_normalize_title():
    assert normalize_title("  Deep  Learning:\nA Survey! ") == "deep learning a survey"
    assert normalize_title("ＢＥＶ-Former") == "bev former"


def test_repeat_ingest_is_idempotent(session):
    assert _ingest(_sample()) == (3, 0)
    assert _ingest(_sample()) == (0, 0)
    assert _count(session) == 3


def test_new_version_updates_in_place(session):
    old = {**_sample()[2], "arxiv_version": 1}
    _ingest([old])
    _ingest([{**old, "arxiv_version": 2, "pdf_url": old["pdf_url"][:-1] + "2"}])
    _ingest([old])  # a stale copy must not roll the version back
    paper = session.scalar(select(Paper))
    assert _count(session) == 1
    assert paper.arxiv_version == 2 and paper.pdf_url.endswith("v2")


def test_added_on(session):
    new = _sample()[2]  # its first version came out on 2026-11-19
    _ingest([new])
    _ingest([{**new, "doi": "10.1/x", "added_on": None}])  # later only a DOI: stays on its day
    paper = session.scalar(select(Paper))
    assert (paper.doi, paper.added_on, paper.added_kind) == ("10.1/x", date(2026, 11, 19), "new")
    _ingest([{**new, "arxiv_version": 2, "added_on": date(2026, 11, 24)}])  # a new version
    session.expire_all()
    assert (paper.added_on, paper.added_kind) == (date(2026, 11, 24), "updated")


def test_match_by_doi_and_title(session):
    with_doi, without_doi = _sample()[:2]
    _ingest([with_doi, without_doi])
    # A conference import (no arXiv ID) that shares the DOI merges into that arXiv record.
    _ingest([{"title": "Another Title", "doi": with_doi["doi"], "comment": "venue"}])
    # One that only shares the title (case and punctuation differ) and authors merges too.
    title, authors = without_doi["title"].upper() + ".", without_doi["authors"]
    _ingest([{"title": title, "authors": authors, "journal_ref": "Conf 2026"}])
    assert _count(session) == 2
    by_id = {p.arxiv_id: p for p in session.scalars(select(Paper))}
    assert by_id[with_doi["arxiv_id"]].comment == "venue"
    assert by_id[without_doi["arxiv_id"]].journal_ref == "Conf 2026"


def test_different_arxiv_ids_never_merge(session):
    first = _sample()[0]
    _ingest([first, {**first, "arxiv_id": "2601.00001"}])
    assert _count(session) == 2


def test_parallel_ingest_no_duplicates(session):
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(_ingest, [_sample() for _ in range(8)]))
    assert _count(session) == 3


def _entry(key="CVPR:/p/1", **fields):
    """A proceedings record (it carries venue_key)."""
    return {"source": "CVPR", "year": 2026, "track": "main", "venue_key": key} | fields


def test_same_authors():
    arxiv_style = "Koki Yamane, Sho Sakaino (Univ. Tsukuba) and Toshiaki Tsuji"
    assert same_authors(arxiv_style, "Koki Yamane, Sho Sakaino, Toshiaki Tsuji, New Person")
    assert same_authors("Jörg Müller, Ann Lee", "Jorg Muller, Bo Chen")  # half of the shorter
    assert not same_authors("Wei Wang, Li Zhang, Hao Chen", "Wei Wang, Yu Liu, Jun Zhao")
    assert not same_authors("", "Wei Wang")


def test_conference_version_merges_into_arxiv(session):
    paper = _sample()[0]  # 2507.06174v8, its own DOI
    _ingest([paper])
    conference = _entry(
        arxiv_id=paper["arxiv_id"],
        title="Conference Title",
        abstract="The conference abstract.",
        authors=paper["authors"],
        doi="10.1109/conf.2026.1",
        pdf_url="https://openaccess.thecvf.com/content/CVPR2026/papers/x.pdf",
        published_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert _ingest([conference]) == (0, 1)
    stored = session.scalar(select(Paper))
    assert (stored.title, stored.abstract, stored.doi) == (
        "Conference Title",
        "The conference abstract.",
        "10.1109/conf.2026.1",
    )
    assert (stored.source, stored.year, stored.venue_key) == ("CVPR", 2026, "CVPR:/p/1")
    assert stored.pdf_url == conference["pdf_url"]  # open: the conference's PDF
    assert stored.arxiv_version == 8 and stored.published_at == paper["published_at"]

    # arXiv's next version: its own fields move on, the published ones stay
    newer = paper | {"arxiv_version": 9, "title": "arXiv v9", "pdf_url": "https://arxiv.org/pdf/x"}
    _ingest([newer])
    session.refresh(stored)
    assert stored.arxiv_version == 9 and stored.title == "Conference Title"
    assert stored.pdf_url == conference["pdf_url"] and stored.year == 2026
    assert _count(session) == 1


def test_closed_proceedings_keep_the_arxiv_pdf(session):
    paper = _sample()[0]
    _ingest([paper])
    _ingest([_entry(arxiv_id=paper["arxiv_id"], title="T", authors=paper["authors"])])
    newer = paper | {"arxiv_version": 9, "pdf_url": "https://arxiv.org/pdf/2507.06174v9"}
    _ingest([newer])
    stored = session.scalar(select(Paper))
    assert stored.pdf_url.endswith("v9") and stored.source == "CVPR" and stored.abstract


def test_conservative_merges(session):
    a, b, c = _sample()
    _ingest([a, b, c])
    # Same title, other authors: a different paper with a generic title.
    _ingest([_entry("CVPR:/p/1", title=a["title"], authors="Wei Wang, Li Zhang")])
    # A link to arXiv paper b whose authors share nothing: the link is wrong, not followed,
    # and a re-import does not trip over it.
    wrong = _entry("CVPR:/p/2", arxiv_id=b["arxiv_id"], title="Other", authors="Hao Chen")
    _ingest([wrong])
    _ingest([wrong])
    # Two proceedings entries never merge by title, even with the same authors.
    _ingest([_entry("ICCV:/p/1", source="ICCV", title="Twice", authors="Ann Lee")])
    _ingest([_entry("CVPR:/p/3", title="Twice", authors="Ann Lee")])
    assert _count(session) == 3 + 4
    entries = session.scalars(select(Paper).where(Paper.venue_key.is_not(None))).all()
    assert [p.arxiv_id for p in entries] == [None] * 4
    assert session.scalar(select(Paper).where(Paper.arxiv_id == b["arxiv_id"])).source == "arXiv"


def test_title_shared_by_two_papers_is_not_merged(session):
    first = _sample()[0]
    twin = first | {"arxiv_id": "2601.00001", "doi": None}  # same title and authors
    _ingest([first, twin])
    _ingest([_entry(title=first["title"], authors=first["authors"])])
    assert _count(session) == 3  # which of the two? Unclear, so neither


def test_arxiv_id_of_another_entry_is_not_taken(session):
    first = _entry("ICRA:W1", source="ICRA", arxiv_id="2601.00002", title="A", authors="Ann Lee")
    second = _entry("IROS:W2", source="IROS", arxiv_id="2601.00002", title="B", authors="Ann Lee")
    _ingest([first, second])
    ids = dict(session.execute(select(Paper.venue_key, Paper.arxiv_id)).all())
    assert ids == {"ICRA:W1": "2601.00002", "IROS:W2": None}


def test_reported_paper_keeps_its_pdf(session):
    paper = _sample()[0]
    _ingest([paper])
    session.execute(update(Paper).values(report_status="reported"))
    session.commit()
    open_pdf = "https://openaccess.thecvf.com/content/CVPR2026/papers/x.pdf"
    conference = {"arxiv_id": paper["arxiv_id"], "title": "Conference Title", "pdf_url": open_pdf}
    _ingest([_entry(authors=paper["authors"], **conference)])
    stored = session.scalar(select(Paper))
    session.refresh(stored)
    assert stored.pdf_url == paper["pdf_url"]  # the version its report was written from
    assert (stored.title, stored.source) == ("Conference Title", "CVPR")


def test_merge_keeps_the_row_and_what_hangs_on_it(session):
    """A conference version updates the arXiv paper's row: its stars, topics, tags, notes and
    reports stay, and so does the arXiv day it was announced on (added_on)."""
    paper = _sample()[0] | {"added_on": date(2026, 11, 20)}
    _ingest([paper])
    with Session(engine()) as s, s.begin():
        stored = s.scalar(select(Paper))
        user = User(username="u", password_hash="x")
        topic = Topic(domain="robotics", name="Teleoperation", source="seed")
        s.add_all([user, topic])
        s.flush()
        s.add_all(
            [
                UserPaper(user_id=user.id, paper_id=stored.id, starred_at=func.now()),
                PaperTopic(paper_id=stored.id, topic_id=topic.id, source="human"),
                Tag(paper_id=stored.id, kind="keyword", value="teleop", source="ai"),
                Note(user_id=user.id, paper_id=stored.id, content="read"),
            ]
        )
        paper_id = stored.id
    entry = _entry(arxiv_id=paper["arxiv_id"], title="Conference Title", authors=paper["authors"])
    assert _ingest([entry]) == (0, 1)
    stored = session.scalar(select(Paper))
    assert (stored.id, stored.source, stored.added_on) == (paper_id, "CVPR", date(2026, 11, 20))
    for table in (UserPaper, PaperTopic, Tag, Note):
        assert session.scalar(select(func.count()).select_from(table)) == 1
    # an arXiv lookup after an import (venues._fill_from_arxiv) does not announce it either
    looked_up = arxiv.parse_atom((DATA / "venues" / "arxiv_api.xml").read_bytes())[0]
    _ingest([looked_up | {"arxiv_id": paper["arxiv_id"], "arxiv_version": 9}])
    session.refresh(stored)
    assert stored.arxiv_version == 9 and stored.added_on == date(2026, 11, 20)


def test_conference_papers_are_not_announced(session):
    _ingest([_entry(title="Only at the conference", authors="Ann Lee")])
    assert session.scalar(select(Paper.added_on)) is None


def test_sync_lets_go_of_the_lock_between_pages(session, monkeypatch):
    """The sync holds the arXiv lock for one page at a time, so a PDF download can come in
    between two pages instead of waiting for the whole harvest.
    """
    held, between = [], []

    def harvest(category, since, until):
        for papers in (_sample()[:1], _sample()[1:]):
            held.append(not arxiv_lock_free())  # requesting the page
            yield papers, date(2026, 11, 22)

    def upsert(session, papers):
        between.append(arxiv_lock_free())
        return upsert_papers(session, papers)

    monkeypatch.setattr(arxiv, "harvest", harvest)
    monkeypatch.setattr(ingest, "upsert_papers", upsert)
    assert ingest.sync_arxiv()["cs.CV"] == (3, 0)
    assert held == [True] * 4 and between == [True] * 4  # two pages of cs.CV, two of cs.RO
