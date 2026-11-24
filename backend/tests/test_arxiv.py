import io
import urllib.request
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from conftest import DATA

from scholarmind import arxiv


def test_set_spec():
    assert arxiv.set_spec("cs.CV") == "cs:cs:CV"
    assert arxiv.set_spec("eess.SY") == "eess:eess:SY"
    assert arxiv.set_spec("astro-ph.CO") == "physics:astro-ph:CO"
    assert arxiv.set_spec("hep-th") == "physics:hep-th"


def test_parse_list_records():
    papers, token, server_date = arxiv.parse_list_records(
        (DATA / "arxiv_listrecords.xml").read_bytes()
    )
    assert server_date == date(2026, 11, 22)
    assert token and token.startswith("verb%3DListRecords")
    assert [p["arxiv_id"] for p in papers] == ["2507.06174", "2605.04939", "2611.23364"]

    multi = papers[0]
    assert multi["arxiv_version"] == 8
    assert multi["pdf_url"] == "https://arxiv.org/pdf/2507.06174v8"
    assert multi["primary_category"] == "cs.RO"
    assert multi["categories"] == ["cs.RO", "cs.AI", "cs.SY", "eess.SY"]
    assert multi["doi"] == "10.1109/access.2026.3713604"
    assert multi["journal_ref"] == "IEEE Access, vol. 14, pp. 109407-109428, 2026"
    assert multi["published_at"] == datetime(2025, 7, 8, 16, 54, 34, tzinfo=UTC)
    assert multi["updated_at"] == datetime(2026, 11, 18, 13, 22, 37, tzinfo=UTC)
    assert multi["title"].startswith("Design and Experimental Validation")
    assert "\n" not in multi["abstract"] and multi["authors"].startswith("Koki Yamane")

    single = papers[2]
    assert single["arxiv_version"] == 1 and single["doi"] is None
    assert single["published_at"] == single["updated_at"]

    # All three records are of 2026-11-19: v8 of 11-18, a v1 of May (metadata changed only),
    # a v1 of 11-17. Only the first and the last were announced that day.
    assert [p["added_on"] for p in papers] == [date(2026, 11, 19), None, date(2026, 11, 19)]


def test_new_version_window():
    xml = (DATA / "arxiv_listrecords.xml").read_bytes()

    def added_on(datestamp):
        stamped = xml.replace(b"2026-11-19</datestamp>", datestamp.encode() + b"</datestamp>")
        return [p["added_on"] for p in arxiv.parse_list_records(stamped)[0]]

    # The v1 of 2611.23364 came on 11-17: 7 days before is still a new version, 8 are not.
    assert added_on("2026-11-24")[2] == date(2026, 11, 24)
    assert added_on("2026-11-25") == [date(2026, 11, 25), None, None]


def test_parse_no_records():
    papers, token, server_date = arxiv.parse_list_records(
        (DATA / "arxiv_norecords.xml").read_bytes()
    )
    assert papers == [] and token is None and server_date == date(2026, 11, 22)


def test_parse_atom():
    first, second = arxiv.parse_atom((DATA / "venues" / "arxiv_api.xml").read_bytes())
    assert (first["arxiv_id"], first["arxiv_version"]) == ("2503.01347", 4)
    assert first["pdf_url"] == "https://arxiv.org/pdf/2503.01347v4"
    assert first["published_at"] < first["updated_at"] and first["published_at"].tzinfo
    assert first["categories"][0] == first["primary_category"] and first["authors"].count(", ")
    assert "\n" not in first["abstract"] and second["arxiv_version"] == 1
    assert set(first) == set(
        arxiv.parse_list_records((DATA / "arxiv_listrecords.xml").read_bytes())[0][0]
    )


def test_get_has_a_deadline(monkeypatch):
    """A server trickling a byte at a time, 10 s apart on a fake clock: a request not done in
    DEADLINE seconds is a TimeoutError. The interval is slept after a failure too.
    """
    clock, slept = [0.0], []

    class Trickle(io.BytesIO):
        def read1(self, size=-1):
            clock[0] += 10
            return super().read1(1)

    monkeypatch.setattr(
        arxiv, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=slept.append)
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: Trickle(b"<OAI/>"))
    assert arxiv._get({"verb": "Identify"}) == b"<OAI/>" and clock == [70] and slept == [3]
    clock[0] = 0
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: Trickle(b"x" * 20))
    with pytest.raises(
        TimeoutError, match=r"^https://oaipmh\.arxiv\.org/oai\?verb=Identify took longer than 90 s"
    ):
        arxiv._get({"verb": "Identify"})
    assert slept == [3, 3]
