"""arXiv adapter: incremental metadata harvest over OAI-PMH (arXivRaw format).

OAI-PMH is what arXiv recommends for keeping a metadata copy up to date
(https://info.arxiv.org/help/bulk_data.html). Records are selected by datestamp, the day
the record was last modified, so a harvest returns new papers and new versions alike.
"""

import math
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import date, datetime
from email.utils import parsedate_to_datetime

BASE_URL = "https://oaipmh.arxiv.org/oai"
API_URL = "https://export.arxiv.org/api/query"  # Atom; looks papers up by ID, 100 at a time
# Terms of use: at most one request every three seconds, one connection at a time
# (https://info.arxiv.org/help/api/tou.html). Every request holds ingest.arxiv_lock().
REQUEST_INTERVAL = 3
# A record whose latest version was submitted at most this many days before its datestamp
# brought that version; an older one only changed metadata (a DOI, a journal reference,
# categories). Submissions come out one to four days later (weekends), holidays add a day or two.
NEW_VERSION_DAYS = 7
MAX_PDF = 100 * 2**20
# Seconds a whole request may take, connect to last byte (a PDF download, an OAI page, an API
# lookup): it holds the arXiv lock. A socket timeout alone bounds each read, not a server that
# keeps trickling bytes. An OAI page of 1.8 MB took 6.5 s (2026-11).
DEADLINE = 90
PDF_TYPES = ("application/pdf", "application/octet-stream")  # the latter: PMLR on GitHub
NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "raw": "http://arxiv.org/OAI/arXivRaw/"}
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
# Archives that are their own OAI group; every other archive is in the physics group.
_OWN_GROUP = {"cs", "econ", "eess", "math", "q-bio", "q-fin", "stat"}
# Every category of https://arxiv.org/category_taxonomy (checked 2026-11-25: 155, aliases such
# as cs.NA = math.NA included). Anything else makes the OAI harvest fail with badArgument.
CATEGORIES = frozenset(
    """
    cs.AI cs.AR cs.CC cs.CE cs.CG cs.CL cs.CR cs.CV cs.CY cs.DB cs.DC cs.DL cs.DM cs.DS cs.ET
    cs.FL cs.GL cs.GR cs.GT cs.HC cs.IR cs.IT cs.LG cs.LO cs.MA cs.MM cs.MS cs.NA cs.NE cs.NI
    cs.OH cs.OS cs.PF cs.PL cs.RO cs.SC cs.SD cs.SE cs.SI cs.SY
    econ.EM econ.GN econ.TH
    eess.AS eess.IV eess.SP eess.SY
    math.AC math.AG math.AP math.AT math.CA math.CO math.CT math.CV math.DG math.DS math.FA
    math.GM math.GN math.GR math.GT math.HO math.IT math.KT math.LO math.MG math.MP math.NA
    math.NT math.OA math.OC math.PR math.QA math.RA math.RT math.SG math.SP math.ST
    astro-ph.CO astro-ph.EP astro-ph.GA astro-ph.HE astro-ph.IM astro-ph.SR
    cond-mat.dis-nn cond-mat.mes-hall cond-mat.mtrl-sci cond-mat.other cond-mat.quant-gas
    cond-mat.soft cond-mat.stat-mech cond-mat.str-el cond-mat.supr-con
    nlin.AO nlin.CD nlin.CG nlin.PS nlin.SI
    physics.acc-ph physics.ao-ph physics.app-ph physics.atm-clus physics.atom-ph physics.bio-ph
    physics.chem-ph physics.class-ph physics.comp-ph physics.data-an physics.ed-ph
    physics.flu-dyn physics.gen-ph physics.geo-ph physics.hist-ph physics.ins-det
    physics.med-ph physics.optics physics.plasm-ph physics.pop-ph physics.soc-ph
    physics.space-ph
    q-bio.BM q-bio.CB q-bio.GN q-bio.MN q-bio.NC q-bio.OT q-bio.PE q-bio.QM q-bio.SC q-bio.TO
    q-fin.CP q-fin.EC q-fin.GN q-fin.MF q-fin.PM q-fin.PR q-fin.RM q-fin.ST q-fin.TR
    stat.AP stat.CO stat.ME stat.ML stat.OT stat.TH
    gr-qc hep-ex hep-lat hep-ph hep-th math-ph nucl-ex nucl-th quant-ph
    """.split()
)


def set_spec(category: str) -> str:
    """cs.CV -> cs:cs:CV, astro-ph.CO -> physics:astro-ph:CO, hep-th -> physics:hep-th."""
    archive, _, subject = category.partition(".")
    group = archive if archive in _OWN_GROUP else "physics"
    return ":".join(filter(None, [group, archive, subject]))


def _text(element: ET.Element, tag: str) -> str | None:
    value = element.findtext(f"raw:{tag}", namespaces=NS)
    return " ".join(value.split()) if value and value.strip() else None


def parse_list_records(xml: bytes) -> tuple[list[dict], str | None, date]:
    """Parse a ListRecords response into (papers, resumption token, server date)."""
    root = ET.fromstring(xml)
    server_date = datetime.fromisoformat(root.findtext("oai:responseDate", namespaces=NS)).date()
    error = root.find("oai:error", NS)
    if error is not None:
        if error.get("code") == "noRecordsMatch":
            return [], None, server_date
        raise RuntimeError(f"arXiv OAI-PMH error {error.get('code')}: {error.text}")

    papers = []
    for record in root.iterfind(".//oai:record", NS):
        raw = record.find("oai:metadata/raw:arXivRaw", NS)
        if raw is None:  # a deleted record
            continue
        datestamp = date.fromisoformat(record.findtext("oai:header/oai:datestamp", namespaces=NS))
        arxiv_id = _text(raw, "id")
        versions = raw.findall("raw:version", NS)
        version = int(versions[-1].get("version").lstrip("v"))
        latest = parsedate_to_datetime(_text(versions[-1], "date"))
        new_version = (datestamp - latest.date()).days <= NEW_VERSION_DAYS
        categories = _text(raw, "categories").split()
        doi = _text(raw, "doi")
        papers.append(
            {
                "arxiv_id": arxiv_id,
                "arxiv_version": version,
                "doi": doi.lower() if doi else None,
                "title": _text(raw, "title"),
                "abstract": _text(raw, "abstract") or "",
                "authors": _text(raw, "authors") or "",
                "primary_category": categories[0],
                "categories": categories,
                "comment": _text(raw, "comments"),
                "journal_ref": _text(raw, "journal-ref"),
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}v{version}",
                "published_at": parsedate_to_datetime(_text(versions[0], "date")),
                "updated_at": latest,
                "added_on": datestamp if new_version else None,  # None: left as it was
            }
        )
    token = root.findtext(".//oai:resumptionToken", namespaces=NS)
    return papers, (token.strip() or None) if token else None, server_date


def parse_atom(xml: bytes) -> list[dict]:
    """Parse an arXiv API response into the same records as parse_list_records."""
    papers = []
    for entry in ET.fromstring(xml).iterfind("a:entry", ATOM):
        match = re.search(r"/abs/(.+)v(\d+)$", entry.findtext("a:id", "", ATOM))
        if not match:
            continue  # an error entry, e.g. for an ID arXiv does not know
        arxiv_id, version = match[1], int(match[2])

        def text(tag, entry=entry):
            value = entry.findtext(tag, None, ATOM)
            return " ".join(value.split()) if value and value.strip() else None

        doi = text("arxiv:doi")
        papers.append(
            {
                "arxiv_id": arxiv_id,
                "arxiv_version": version,
                "doi": doi.lower() if doi else None,
                "title": text("a:title"),
                "abstract": text("a:summary") or "",
                "authors": ", ".join(
                    a.findtext("a:name", "", ATOM) for a in entry.iterfind("a:author", ATOM)
                ),
                "primary_category": entry.find("arxiv:primary_category", ATOM).get("term"),
                "categories": [c.get("term") for c in entry.iterfind("a:category", ATOM)],
                "comment": text("arxiv:comment"),
                "journal_ref": text("arxiv:journal_ref"),
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}v{version}",
                "published_at": datetime.fromisoformat(text("a:published")),
                "updated_at": datetime.fromisoformat(text("a:updated")),
                # A lookup is not an announcement: the day stays as the daily sync set it.
                "added_on": None,
            }
        )
    return papers


def fetch_ids(ids: list[str]) -> list[dict]:
    """Current metadata of these papers (at most 100). Hold ingest.arxiv_lock() around this."""
    return parse_atom(_get({"id_list": ",".join(ids), "max_results": len(ids)}, API_URL))


def read_body(
    url: str, response, started: float, limit: float = math.inf, seconds: float = DEADLINE
) -> bytes:
    """The body as it comes in, to its end or past `limit` bytes (more is the caller's to
    refuse). A TimeoutError once `seconds` have gone since `started`, taken before connecting;
    checked between reads, so it can run over by one socket wait.
    """
    body = bytearray()
    while len(body) <= limit:
        if time.monotonic() > started + seconds:
            raise TimeoutError(f"{url} took longer than {seconds} s to download")
        if not (chunk := response.read1(2**16)):  # whatever has come, not a full 64 KiB
            break
        body += chunk
    return bytes(body)


def _get(params: dict, base_url: str = BASE_URL) -> bytes:
    """One request, then REQUEST_INTERVAL of sleep, also after a failure. Hold
    ingest.arxiv_lock() around this.
    """
    url = base_url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "ScholarMind/0.1"})
    started = time.monotonic()
    try:
        # urllib honours HTTP_PROXY / HTTPS_PROXY.
        with urllib.request.urlopen(request, timeout=30) as response:  # each socket wait
            return read_body(url, response, started)
    finally:
        time.sleep(REQUEST_INTERVAL)


def open_pdf(url: str):
    """Start downloading a PDF; returns the response to read from. Hold ingest.arxiv_lock()
    from here until the download is done, then sleep REQUEST_INTERVAL.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "ScholarMind/0.1"})
    return urllib.request.urlopen(request, timeout=30)  # each socket wait; see DEADLINE


def fetch_pdf(url: str) -> bytes:
    """Download a PDF into memory (never to disk). Hold ingest.arxiv_lock() around this.
    A response of another content type (an error page, a site's bot check) is a ValueError,
    like one too large: the link answers, but not with a PDF. One not done in DEADLINE seconds
    is a TimeoutError.
    """
    started = time.monotonic()
    try:
        with open_pdf(url) as response:
            if (kind := response.headers.get_content_type()) not in PDF_TYPES:
                raise ValueError(f"{url} answered with {kind}, not a PDF")
            body = read_body(url, response, started, MAX_PDF)
    finally:
        time.sleep(REQUEST_INTERVAL)
    if len(body) > MAX_PDF:
        raise ValueError(f"{url} is larger than {MAX_PDF} bytes")
    return body


def harvest(
    category: str, since: date, until: date | None = None
) -> Iterator[tuple[list[dict], date]]:
    """Yield (papers, server date) per page for records modified in [since, until]. Each page
    is one request: hold ingest.arxiv_lock() around each next(), not around the loop.
    """
    params = {
        "verb": "ListRecords",
        "metadataPrefix": "arXivRaw",
        "set": set_spec(category),
        "from": since.isoformat(),
    }
    if until:
        params["until"] = until.isoformat()
    while True:
        papers, token, server_date = parse_list_records(_get(params))
        yield papers, server_date
        if not token:
            return
        params = {"verb": "ListRecords", "resumptionToken": token}
