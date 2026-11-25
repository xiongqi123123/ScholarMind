"""Conference papers: one edition at a time from four kinds of source (OpenAlex, CVF open
access, the NeurIPS/ICLR proceedings sites, PMLR) into the same deduplicating ingest as arXiv,
and the weekly check for new editions. Only main-conference papers are kept; config.VENUES
has each venue's source and filters, docs/design/conference-sources.md the reasons.

Sources and their terms (checked 2026-11-22): CVF, ecva.net, papers.nips.cc,
proceedings.iclr.cc and proceedings.mlr.press have no robots.txt rule against this; OJS
(AAAI) only disallows /cache/. OpenAlex is used without a key or e-mail: a free daily budget
shared per IP, one credit per listing page, and no cursor paging, so pages are numbered.
"""

import email.utils
import html
import http.client
import json
import logging
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from itertools import batched

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import arxiv, ingest
from .config import VenueConfig, config
from .db import Paper, SyncState, engine

log = logging.getLogger(__name__)

USER_AGENT = "ScholarMind/0.1"
OPENALEX_INTERVAL = 1  # seconds after each request
SITE_INTERVAL = 2  # proceedings sites: CVPR is about 4,000 pages, a little over two hours
BATCH = 50  # papers per transaction: an interrupted import resumes after the last one
MIN_COVERAGE = 0.5  # a batch of 20+ papers where fewer have an abstract (authors, PDF) fails
MAX_MISSING = 0.05  # share of an edition's paper pages that may be gone (404) before it fails
REFILL = timedelta(weeks=6)  # an edition is imported again weekly for this long after it appears
ARXIV_BATCH = 100  # IDs per arXiv API request
# Pauses before attempts 2-5 of a request: the way out (the proxy) can be flaky for minutes.
RETRY_PAUSES = (30, 60, 120, 240)
MAX_RETRY_AFTER = 600  # seconds: the most a Retry-After is waited
BRIEF_TOKENS = 1000  # a paper's brief, about: for estimates before an import

ARXIV_LINK = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})|10\.48550/arxiv\.(\d{4}\.\d{4,5})"
)


class NotPublished(Exception):
    """The source does not list this edition (yet)."""


class ImportFailed(Exception):
    """The source answered, but with far less than expected: most likely the site changed."""


def _retry_after(error: Exception) -> float:
    """Seconds a 429 or 503 asks to wait (Retry-After: seconds or an HTTP date), else 0."""
    if not isinstance(error, urllib.error.HTTPError) or error.code not in (429, 503):
        return 0
    value = (error.headers or {}).get("Retry-After") or ""
    try:
        return float(value)
    except ValueError:
        try:
            return (email.utils.parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError):
            return 0


def _get(url: str, interval: float) -> bytes:
    """GET with a pause after every request. Network errors, timeouts, a connection lost
    mid-answer, 429 and 5xx are tried again after RETRY_PAUSES, or after Retry-After when it
    asks for longer; other 4xx are not. When the attempts run out the last error is raised.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt, pause in enumerate((*RETRY_PAUSES, None), 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        # OSError: URLError, HTTPError, timeouts; HTTPException: IncompleteRead
        except (OSError, http.client.HTTPException) as e:
            client_error = isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429
            if client_error or pause is None:
                raise
            wait = min(max(pause, _retry_after(e)), MAX_RETRY_AFTER)
            log.warning("%s: attempt %d failed (%s), trying again in %d s", url, attempt, e, wait)
        finally:
            time.sleep(interval)
        time.sleep(wait)


def _listing(url: str) -> str:
    try:
        return _get(url, SITE_INTERVAL).decode()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotPublished(f"{url} does not exist yet") from None
        raise


def _text(fragment: str) -> str:
    """Plain text of an HTML fragment."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _arxiv_id(urls) -> str | None:
    """The arXiv ID these links point to; None if none, or several (then it is unclear)."""
    ids = {a or b for url in urls if url for a, b in ARXIV_LINK.findall(url.lower())}
    return ids.pop() if len(ids) == 1 else None


def _date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).replace(tzinfo=UTC) if value else None


def _record(name: str, year: int, key: str, title: str, authors: str, **fields) -> dict:
    return {
        "source": name,
        "year": year,
        "track": "main",
        "venue_key": f"{name}:{key}",
        "title": title,
        "authors": authors,
    } | fields


def _page_url(venue: VenueConfig, record: dict) -> str:
    return urllib.parse.urljoin(venue.url, record["venue_key"].split(":", 1)[1])


# --- OpenAlex: ICRA, IROS, RSS, ACL, EMNLP, AAAI, ACM MM ---------------------------------------

OPENALEX = "https://api.openalex.org/works"
PAPER_TYPES = {"conference-paper", "article", "review", "letter"}  # not paratext, abstracts
OPEN_PDF_HOSTS = ("ojs.aaai.org",)  # ACM's PDFs sit behind Cloudflare, IEEE's are closed
FIELDS = "id,doi,title,type,publication_date,biblio,authorships,locations,primary_location"


def _openalex_listing(name: str, venue: VenueConfig, year: int, limit: int | None):
    query = {
        "filter": f"primary_location.source.id:{venue.source},publication_year:{year}",
        "select": FIELDS + ",abstract_inverted_index",
        "sort": "doi",  # stable order, so numbered pages neither skip nor repeat works
        "per_page": 200,
    }
    seen, records, main_issues = 0, [], None
    # ponytail: numbered pages stop at 10,000 works per query; no edition is near that.
    for page in range(1, 51):
        url = f"{OPENALEX}?{urllib.parse.urlencode(query | {'page': page})}"
        body = json.loads(_get(url, OPENALEX_INTERVAL))
        works, total = body["results"], body["meta"]["count"]
        if total == 0:
            raise NotPublished(f"OpenAlex has no {year} works in {venue.source} yet")
        if venue.issue_archive and main_issues is None:
            main_issues = _ojs_main_issues(venue.issue_archive, works)
        records += [r for w in works if (r := _openalex_record(name, venue, year, w, main_issues))]
        seen += len(works)
        if not works or seen >= total or (limit and len(records) >= limit):
            break
    return records, total if limit else len(records)


def _openalex_record(name, venue, year, work, main_issues) -> dict | None:
    doi = (work["doi"] or "").removeprefix("https://doi.org/").lower() or None
    biblio = work["biblio"]
    if work["type"] not in PAPER_TYPES or not work["authorships"] or not work["title"]:
        return None
    track = "main"
    if venue.doi:
        match = re.match(venue.doi.replace("{year}", str(year)), doi or "")
        if not match:
            return None
        track = match.groupdict().get("track") or track
    if main_issues is not None and (biblio["volume"], biblio["issue"]) not in main_issues:
        return None
    if venue.min_pages and _pages(biblio) < venue.min_pages:
        return None
    index = work["abstract_inverted_index"] or {}
    words = sorted((at, word) for word, positions in index.items() for at in positions)
    authors = (a["author"]["display_name"] or a["raw_author_name"] for a in work["authorships"])
    links = [location["landing_page_url"] for location in work["locations"]]
    return _record(
        name,
        year,
        work["id"].removeprefix("https://openalex.org/"),
        _text(work["title"]),
        ", ".join(filter(None, authors)),
        track=track,
        abstract=" ".join(word for _, word in words) or None,
        doi=doi,
        pdf_url=_open_pdf(work, doi),
        arxiv_id=_arxiv_id(links),
        published_at=_date(work["publication_date"]),
    )


def _pages(biblio: dict) -> int:
    try:
        return int(biblio["last_page"]) - int(biblio["first_page"]) + 1
    except (TypeError, ValueError):
        return 0


def _open_pdf(work: dict, doi: str | None) -> str | None:
    """The proceedings' own PDF if it is openly downloadable."""
    # RSS: OpenAlex's "PDF" is the DOI, which resolves to an HTML page; volume I was 2005.
    if rss := re.fullmatch(r"10\.15607/rss\.(\d{4})\.[ivxlc]+\.(\d+)", doi or ""):
        return f"https://www.roboticsproceedings.org/rss{int(rss[1]) - 2004}/p{rss[2]}.pdf"
    if acl := re.fullmatch(r"10\.18653/(?:v1/)?(.+)", doi or ""):  # every Anthology paper is open
        return f"https://aclanthology.org/{acl[1]}.pdf"
    url = (work["primary_location"] or {}).get("pdf_url") or ""
    return url if (urllib.parse.urlsplit(url).hostname or "").endswith(OPEN_PDF_HOSTS) else None


def _ojs_main_issues(archive: str, works: list[dict]) -> set[tuple[str, str]]:
    """(volume, issue) of the "Technical Track" issues in an OJS archive. AAAI splits its main
    track into numbered technical-track issues; special tracks, IAAI, EAAI and student
    abstracts are other issues of the same volume. The archive lists newest first.
    """
    volumes = {int(w["biblio"]["volume"]) for w in works if w["biblio"]["volume"]}
    issue = r'class="title"[^>]*>\s*([^<]*?)\s*</a>\s*<div class="series">\s*Vol\. (\d+) No\. (\d+)'
    main = set()
    for page in range(1, 20):
        url = archive if page == 1 else f"{archive}/{page}"
        try:
            found = re.findall(issue, _get(url, SITE_INTERVAL).decode())
        except urllib.error.HTTPError as e:
            if e.code != 404:  # past the last page
                raise
            found = []
        if not found:
            break
        main |= {(volume, n) for title, volume, n in found if "Technical Track" in title}
        if not volumes or min(int(volume) for _, volume, _ in found) < min(volumes):
            break
    return main


# --- CVF open access (CVPR, ICCV) and ECVA (ECCV): same page layout -----------------------------


def _cvf_listing(name: str, venue: VenueConfig, year: int, limit: int | None):
    url = venue.url.replace("{year}", str(year))
    entries = _listing(url).split('<dt class="ptitle">')[1:]
    if not entries:
        raise ImportFailed(f"no papers found on {url}: the page layout may have changed")
    path = venue.path.replace("{year}", str(year))
    records = []
    for entry in entries:
        link = re.search(r"""<a href=["']?([^"'\s>]+)["']?\s*>(.*?)</a>""", entry, re.S)
        page = urllib.parse.urljoin(url, link[1]) if link else ""
        if path not in page:
            continue  # another edition (ecva.net lists them all on one page)
        authors = re.findall(r'name="query_author" value="([^"]*)"', entry)
        if not authors:  # ecva.net: plain text, corresponding authors starred
            listed = re.search(r"</dt>\s*<dd>(.*?)</dd>", entry, re.S)
            authors = [_text(listed[1]).replace("*", "")] if listed else []
        pdf = re.search(r"""<a href=["']?([^"'\s>]+\.pdf)["']?>pdf</a>""", entry)
        doi = re.search(r"""link\.springer\.com/chapter/(10\.[^"'\s>]+)""", entry)
        records.append(
            _record(
                name,
                year,
                urllib.parse.urlsplit(page).path,
                _text(link[2]),
                html.unescape(", ".join(authors)),
                pdf_url=urllib.parse.urljoin(url, pdf[1]) if pdf else None,
                doi=doi[1].lower() if doi else None,
                arxiv_id=_arxiv_id(re.findall(r"""href=["']?([^"'\s>]+)""", entry)),
            )
        )
    if not records:
        raise NotPublished(f"{url} lists no papers under {path} yet")
    return records, len(records)


def _cvf_detail(venue: VenueConfig, record: dict) -> dict:
    page = _get(_page_url(venue, record), SITE_INTERVAL).decode()
    abstract = re.search(r'<div id="abstract">(.*?)</div>', page, re.S)
    return record | {"abstract": (_text(abstract[1]).strip('"') or None) if abstract else None}


# --- NeurIPS and ICLR proceedings: the same site software --------------------------------------


def _proceedings_listing(name: str, venue: VenueConfig, year: int, limit: int | None):
    url = venue.url.replace("{year}", str(year))
    item = r'<li class="([\w-]+)"[^>]*>\s*<div class="paper-content">(.*?)</li>'
    items = re.findall(item, _listing(url), re.S)
    if not items:
        raise ImportFailed(f"no papers found on {url}: the page layout may have changed")
    records = []
    for track, body in items:
        link = re.search(r'<a title="paper title" href="([^"]+)">(.*?)</a>', body, re.S)
        authors = re.search(r'<span class="paper-authors">(.*?)</span>', body, re.S)
        if track == "conference" and link:  # not datasets & benchmarks, creative AI, ...
            records.append(_record(name, year, link[1], _text(link[2]), _text(authors[1])))
    if not records:
        tracks = dict(Counter(track for track, _ in items))
        raise NotPublished(f"{url} lists no main-track papers yet, only {tracks}")
    return records, len(records)


def _meta(page: str, name: str) -> str | None:
    match = re.search(rf'<meta name="{name}" content="([^"]*)"', page)
    return html.unescape(match[1]) if match and match[1] else None


def _proceedings_detail(venue: VenueConfig, record: dict) -> dict:
    page = _get(_page_url(venue, record), SITE_INTERVAL).decode()
    abstract = re.search(r'<p class="paper-abstract">(.*?)</section>', page, re.S)
    doi = _meta(page, "citation_doi")
    return record | {
        "abstract": (_text(abstract[1]) or None) if abstract else None,
        "doi": doi.lower() if doi else None,
        "pdf_url": _meta(page, "citation_pdf_url"),
        "published_at": _date(_meta(page, "citation_publication_date")),
    }


# --- PMLR: ICML ---------------------------------------------------------------------------------

PMLR = "https://proceedings.mlr.press/"


def _pmlr_listing(name: str, venue: VenueConfig, year: int, limit: int | None):
    """The volume's bibliography.bib has every paper with its abstract: one request."""
    volumes = re.findall(r'<a href="(v\d+)"><b>Volume \d+</b></a>\s*([^<]*?)\s*<', _listing(PMLR))
    if not volumes:
        raise ImportFailed(f"no volumes found on {PMLR}: the page layout may have changed")
    title = venue.title.replace("{year}", str(year))
    volume = next((v for v, t in volumes if t == title), None)
    if volume is None:
        raise NotPublished(f"{PMLR} has no volume titled {title!r} yet")
    bib = _get(f"{PMLR}{volume}/assets/bib/bibliography.bib", SITE_INTERVAL).decode()
    records = [_pmlr_record(name, year, entry) for entry in re.split(r"^@", bib, flags=re.M)]
    records = [r for r in records if r]
    return records, len(records)


def _pmlr_record(name: str, year: int, entry: str) -> dict | None:
    if not entry.startswith("InProceedings{"):
        return None  # the volume's own entry
    # Machine-written: one field per line, "  name = {value},".
    fields = dict(re.findall(r"^\s*(\w+)\s*=\s*\{(.*)\},?\s*$", entry, re.M))
    authors = (" ".join(reversed(a.split(", ", 1))) for a in fields["author"].split(" and "))
    return _record(
        name,
        year,
        urllib.parse.urlsplit(fields["url"]).path,
        _latex(fields["title"]),
        _latex(", ".join(authors)),
        abstract=_text(fields.get("abstract", "")) or None,
        pdf_url=fields.get("pdf"),
    )


_ACCENTS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303"}
_ACCENTS |= {
    "=": "\u0304",
    ".": "\u0307",
    "u": "\u0306",
    "v": "\u030c",
    "c": "\u0327",
    "k": "\u0328",
}


_LETTERS = {"i": "i", "l": "ł", "L": "Ł", "o": "ø", "O": "Ø", "ss": "ß"}
_LETTERS |= {"aa": "å", "AA": "Å", "ae": "æ", "AE": "Æ"}


def _latex(value: str) -> str:
    """Braces off, the usual accent commands to Unicode: {\\"u} -> ü, M{\\'e}zard -> Mézard,
    \\l -> ł, \\'{\\i} -> í.
    """
    value = re.sub(r"\\(ss|aa|AA|ae|AE|[ilLoO])(?![A-Za-z])", lambda m: _LETTERS[m[1]], value)
    value = re.sub(
        r"""\\([\"'`^~=.]|[uvck](?=[ {]))\s*\{?([A-Za-z])\}?""",
        lambda m: m[2] + _ACCENTS[m[1]],
        value,
    )
    return unicodedata.normalize("NFC", " ".join(re.sub(r"[{}]", "", value).split()))


# --- import -------------------------------------------------------------------------------------

LISTINGS = {
    "openalex": _openalex_listing,
    "cvf": _cvf_listing,
    "proceedings": _proceedings_listing,
    "pmlr": _pmlr_listing,
}
DETAILS = {"cvf": _cvf_detail, "proceedings": _proceedings_detail}  # a request per paper


def _detailed(name, year, venue, detail, record, missing: list[str], most: float) -> dict | None:
    """The record completed from its page; None if the page is gone (404), logged and added to
    `missing`. A paper listed whose page and PDF are gone was seen (CVPR 2024), as was a
    listing that links a wrong file name. More than `most` missing fails the import.
    """
    try:
        return detail(venue, record)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    missing.append(url := _page_url(venue, record))
    log.warning("%s %d: skipped %r, its page is gone: %s", name, year, record["title"], url)
    if len(missing) > most:
        raise ImportFailed(f"{name} {year}: {len(missing)} paper pages gone: the site changed?")
    return None


def _check(name: str, year: int, records: list[dict], pdf: bool):
    """Fail on a batch that lacks what every paper of the source has: a changed page layout
    otherwise loses abstracts silently.
    """
    if len(records) < 20:
        return
    for field in ("abstract", "authors") + (("pdf_url",) if pdf else ()):
        share = sum(bool(r.get(field)) for r in records) / len(records)
        if share < MIN_COVERAGE:
            raise ImportFailed(
                f"{name} {year}: only {share:.0%} of {len(records)} papers have {field}: "
                "the page layout may have changed"
            )


def import_venue(name: str, year: int, limit: int | None = None, until: float | None = None):
    """Import one edition's main-conference papers into the library; returns counts, and done:
    False when it stopped at `until` (time.monotonic()) with papers left.

    Safe to repeat: a paper already imported from a source that takes a request per paper is
    not fetched again, so an interrupted import continues where it stopped. Only those sources
    take long (hours), so only they stop at `until`. `limit` takes only the first papers, to
    try a source. A paper whose page is gone (404) is skipped and counted as missing; it is
    tried again by the next import. NotPublished if the source does not list the edition,
    ImportFailed if it lists far fewer papers than expected (VenueConfig.min_papers), a
    batch lacks abstracts, or more than MAX_MISSING of the edition's pages are gone.
    Afterwards papers linked to arXiv get their arXiv metadata, and the arXiv PDF where the
    proceedings have no open one.
    """
    venue = config().venues[name]
    records, listed = LISTINGS[venue.adapter](name, venue, year, limit)
    if not records:
        raise ImportFailed(f"{name} {year}: none of {listed} listed papers passed the filters")
    if listed < venue.min_papers:
        raise ImportFailed(
            f"{name} {year}: {listed} main-conference papers listed, expected at least "
            f"{venue.min_papers}: incomplete proceedings, or the site changed"
        )
    if venue.exclude_title:
        records = [r for r in records if not re.search(venue.exclude_title, r["title"])]
    records, skipped, missing = records[:limit], 0, []
    most = MAX_MISSING * len(records)
    detail = DETAILS.get(venue.adapter)
    if detail:
        with Session(engine()) as session:
            done = select(Paper.venue_key).where(Paper.source == name, Paper.year == year)
            done = set(session.scalars(done))
        todo = [r for r in records if r["venue_key"] not in done]
        records, skipped = todo, len(records) - len(todo)
    inserted = updated = 0
    batches = list(batched(records, BATCH))
    for number, batch in enumerate(batches, 1):
        if detail:
            batch = [
                d for r in batch if (d := _detailed(name, year, venue, detail, r, missing, most))
            ]
        _check(name, year, batch, pdf=venue.adapter != "openalex")
        with Session(engine()) as session, session.begin():
            added, changed = ingest.upsert_papers(session, batch)
        inserted, updated = inserted + added, updated + changed
        log.info("%s %d: %d new, %d updated so far", name, year, inserted, updated)
        if detail and until and time.monotonic() > until and number < len(batches):
            result = dict(listed=listed, inserted=inserted, updated=updated, skipped=skipped)
            return result | {"missing": len(missing), "arxiv": 0, "done": False}
    if limit is None:  # imported to the end: the day it first was, for top-ups (due_editions)
        with Session(engine()) as session, session.begin():
            seen = dict(source=f"venue:{name}:{year}", cursor=date.today())
            session.execute(insert(SyncState).values(seen).on_conflict_do_nothing())
    linked = _fill_from_arxiv(name, year)
    result = dict(listed=listed, inserted=inserted, updated=updated, skipped=skipped)
    result |= {"missing": len(missing)}
    log.info("%s %d: %s, %d linked to arXiv", name, year, result, linked)
    return result | {"arxiv": linked, "done": True}


def _fill_from_arxiv(name: str, year: int) -> int:
    """Look up the arXiv versions of this edition's papers that link to one we have not seen;
    the merge in ingest gives them arXiv's metadata, and its PDF where they have none.
    Returns how many were found.
    """
    with Session(engine()) as session:
        missing = select(Paper.arxiv_id).where(
            Paper.source == name,
            Paper.year == year,
            Paper.arxiv_id.is_not(None),
            Paper.arxiv_version.is_(None),
        )
        ids = session.scalars(missing.order_by(Paper.id)).all()
    found = 0
    for chunk in batched(ids, ARXIV_BATCH):
        with ingest.arxiv_lock():  # one request: a PDF download can come in between two
            fetched = arxiv.fetch_ids(list(chunk))
        # Only these IDs: anything else would add a paper instead of completing one.
        records = [r for r in fetched if r["arxiv_id"] in chunk]
        with Session(engine()) as session, session.begin():
            ingest.upsert_papers(session, records)
        found += len(records)
    return found


def editions(since: int, until: int, names: list[str] | None = None) -> list[tuple[str, int]]:
    """(venue, year) for these venues (default: all) from `until` back to `since`, newest
    first, without the editions already imported to the end, nor the years a venue is not held
    (VenueConfig.held: ICCV is held in odd years only).
    """
    with Session(engine()) as session:
        done = set(
            session.scalars(select(SyncState.source).where(SyncState.source.like("venue:%")))
        )
    names = names or list(config().venues)
    return [
        (name, year)
        for year in range(until, since - 1, -1)
        for name in names
        if f"venue:{name}:{year}" not in done and config().venues[name].held(year)
    ]


def imported(session: Session) -> dict[str, list[int]]:
    """The years of each venue's editions imported to the end, newest first."""
    years = {}
    for source in session.scalars(select(SyncState.source).where(SyncState.source.like("venue:%"))):
        name, year = source.removeprefix("venue:").rsplit(":", 1)
        years.setdefault(name, []).append(int(year))
    return {name: sorted(found, reverse=True) for name, found in years.items()}


def estimate(plan: list[tuple[str, int]]) -> int:
    """Papers these editions bring, roughly: VenueConfig.papers_per_edition each, also for an
    edition not published yet.
    """
    return sum(config().venues[name].papers_per_edition for name, _ in plan)


def hold(name: str, year: int) -> int:
    """Keep this edition's papers that wait for a brief out of briefs (status held), also out
    of the ones the daily sync queues, until release_held. Returns how many. Not the ones arXiv
    announced (added_on): they belong to a day's new papers, which wait for their briefs.
    """
    held = update(Paper).where(
        Paper.source == name, Paper.year == year, Paper.status == "new", Paper.added_on.is_(None)
    )
    with Session(engine()) as session, session.begin():
        return session.execute(held.values(status="held")).rowcount


def release_held(name: str | None = None) -> int:
    """Let held papers (of one venue, or all) be briefed again; returns how many."""
    released = update(Paper).where(Paper.status == "held")
    if name:
        released = released.where(Paper.source == name)
    with Session(engine()) as session, session.begin():
        return session.execute(released.values(status="new")).rowcount


def due_editions(today: date | None = None) -> list[tuple[str, int]]:
    """(venue, year) to import this week: each venue's next edition after the latest in the
    library, and that latest again while it is young, as proceedings often fill up over a few
    weeks. A venue without any edition yet gets nothing: its first is imported by hand.
    """
    today = today or date.today()
    due = []
    with Session(engine()) as session:
        for name, venue in config().venues.items():
            latest = session.scalar(select(func.max(Paper.year)).where(Paper.source == name))
            if latest is None:
                continue
            first_seen = session.get(SyncState, f"venue:{name}:{latest}")
            if first_seen and today - first_seen.cursor <= REFILL:
                due.append((name, latest))
            due.append((name, latest + venue.every))
    return due


def source_counts(session: Session) -> list[tuple[str, int | None, int]]:
    """(source, year, number of papers) for every source and year in use: arXiv first, then
    conferences by name, newest year first.
    """
    papers = func.count().label("papers")
    query = select(Paper.source, Paper.year, papers).group_by(Paper.source, Paper.year)
    order = (Paper.source != "arXiv", Paper.source, Paper.year.desc().nulls_last())
    return session.execute(query.order_by(*order)).all()
