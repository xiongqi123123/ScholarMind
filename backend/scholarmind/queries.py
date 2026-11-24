"""Paper queries shared by REST now and MCP later."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from sqlalchemy import ColumnElement, and_, delete, func, select, text, true, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer, selectinload

from . import topics
from .config import OUT_OF_SCOPE, config
from .db import (
    Analysis,
    Collection,
    CollectionPaper,
    Figure,
    Note,
    Paper,
    PaperText,
    PaperTopic,
    Tag,
    Topic,
    TopicMerge,
    UserPaper,
)


class NotInVocabulary(Exception):
    pass


class NameTaken(Exception):
    pass


def paper_filters(
    *,
    category: Sequence[str] = (),
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
    domain: Sequence[str] = (),
    topic: Sequence[int] = (),
    paper_type: Sequence[str] = (),
    keyword: str | None = None,
    include_out_of_scope: bool = False,
    added: date | None = None,
    added_kind: str | None = None,
    starred_by: int | None = None,
    innovation: Sequence[str] = (),
    source: Sequence[str] = (),
    year: Sequence[int] = (),
    collection: Sequence[int] = (),
    tz: tzinfo = UTC,
) -> dict[str, ColumnElement[bool]]:
    """The conditions of a search, by the parameter they come from (facets leave one out).
    Several values of category (any of a paper's categories), domain, topic (ids), paper_type,
    innovation, source (arXiv or a conference), year or collection (ids, the caller's: the API
    checks) mean any of them; the parameters combine with AND. Dates are first-version days in
    `tz`, inclusive; added and added_kind are Paper.added_on and added_kind; keyword is an
    exact tag value; q searches title and abstract. Out-of-scope papers are left out unless
    included or asked for as a domain, which makes it part of the domain condition.
    """
    where = {}
    if category:
        where["category"] = Paper.categories.overlap(list(category))
    domains = [_tagged("domain", *domain)] if domain else []
    if not include_out_of_scope and OUT_OF_SCOPE not in domain:
        domains.append(~_tagged("domain", OUT_OF_SCOPE))
    if domains:
        where["domain"] = and_(*domains)
    if keyword:
        where["keyword"] = _tagged("keyword", keyword)
    if topic:
        linked = select(PaperTopic.id).where(PaperTopic.paper_id == Paper.id)
        where["topic"] = linked.where(PaperTopic.topic_id.in_(topic)).exists()
    if paper_type:
        where["paper_type"] = Paper.paper_type.in_(paper_type)
    if innovation:
        where["innovation"] = Paper.innovation.in_(innovation)
    if source:
        where["source"] = Paper.source.in_(source)
    if year:
        where["year"] = Paper.year.in_(year)
    if collection:
        kept = select(CollectionPaper.paper_id).where(CollectionPaper.paper_id == Paper.id)
        where["collection"] = kept.where(CollectionPaper.collection_id.in_(collection)).exists()
    if date_from:
        where["date_from"] = Paper.published_at >= datetime.combine(date_from, time(), tz)
    if date_to:
        end = datetime.combine(date_to + timedelta(1), time(), tz)  # next midnight, DST-safe
        where["date_to"] = Paper.published_at < end
    if added:
        where["added"] = Paper.added_on == added
    if added_kind:
        where["added_kind"] = Paper.added_kind == added_kind
    if starred_by is not None:
        where["starred"] = _starred(starred_by).exists()
    if q:
        where["q"] = Paper.search.op("@@")(func.websearch_to_tsquery("english", q))
    return where


def _starred(user_id: int):
    """The star of the outer query's paper by the user."""
    return select(UserPaper.starred_at).where(
        UserPaper.paper_id == Paper.id,
        UserPaper.user_id == user_id,
        UserPaper.starred_at.is_not(None),
    )


def search_papers(
    session: Session, *, page: int = 1, page_size: int = 20, **filters
) -> tuple[int, list[Paper]]:
    """The papers paper_filters(**filters) selects: (total, page of papers with their tags and
    topics), best match first when searching, else most recently starred first with
    starred_by, else newest.
    """
    query = select(Paper).where(*paper_filters(**filters).values())
    order = [Paper.published_at.desc().nulls_last(), Paper.id.desc()]
    if filters.get("starred_by") is not None:
        order = [_starred(filters["starred_by"]).scalar_subquery().desc(), Paper.id.desc()]
    if q := filters.get("q"):
        order.insert(0, func.ts_rank(Paper.search, func.websearch_to_tsquery("english", q)).desc())
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    papers = session.scalars(
        query.options(selectinload(Paper.tags), selectinload(Paper.topics))
        .order_by(*order)
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    return total, papers


def facets(session: Session, user_id: int, **filters) -> dict:
    """The number of papers paper_filters(**filters) selects, and for each of domain, topic,
    paper_type, innovation, category, source, year and collection (the user's) its values with
    their numbers of papers under every filter but its own (so a value tells what choosing it
    as well would add), most first (years newest first), those with none left out.
    """
    where = paper_filters(**filters)

    def others(group: str):
        return [c for g, c in where.items() if g != group]

    def counts(group: str, key, *joins):
        """(key..., number of papers) under every condition but the group's own."""
        papers = func.count().label("papers")
        query = select(*key, papers).select_from(Paper)
        for target, on in joins:
            query = query.join(target, on)
        query = query.where(*others(group)).group_by(*key)
        return session.execute(query.order_by(papers.desc(), *key)).all()

    def values(group, key, *joins):
        return [{"value": v, "papers": n} for v, n in counts(group, [key], *joins) if v]

    domain = (Tag, (Tag.paper_id == Paper.id) & (Tag.kind == "domain"))
    linked = (PaperTopic, PaperTopic.paper_id == Paper.id)
    # Categories by distinct list first: far fewer lists than papers (a tenth on real data), so
    # half the time of unnesting every paper's at 100,000 papers.
    lists = select(Paper.categories, func.count().label("n")).where(*others("category"))
    lists = lists.group_by(Paper.categories).subquery()
    each = func.unnest(lists.c.categories).table_valued("value").render_derived().lateral()
    papers = func.sum(lists.c.n)
    category = select(each.c.value, papers).select_from(lists).join(each, true())
    category = category.group_by(each.c.value).order_by(papers.desc(), each.c.value)
    topics = counts("topic", [Topic.id, Topic.name, Topic.zh, Topic.domain], linked,
                    (Topic, Topic.id == PaperTopic.topic_id))  # fmt: skip
    mine = (Collection.id == CollectionPaper.collection_id) & (Collection.user_id == user_id)
    kept = (CollectionPaper, CollectionPaper.paper_id == Paper.id)
    collections = counts("collection", [Collection.id, Collection.name], kept, (Collection, mine))
    return {
        "total": session.scalar(select(func.count()).select_from(Paper).where(*where.values())),
        "domain": values("domain", Tag.value, domain),
        "topic": [dict(t._mapping) for t in topics],
        "paper_type": values("paper_type", Paper.paper_type),
        "innovation": values("innovation", Paper.innovation),
        "category": [{"value": v, "papers": int(n)} for v, n in session.execute(category)],
        "source": values("source", Paper.source),
        "year": sorted(values("year", Paper.year), key=lambda c: c["value"], reverse=True),
        "collection": [dict(c._mapping) for c in collections],
    }


def _tagged(kind: str, *values: str):
    """Whether the outer query's paper has a tag of the kind with any of the values."""
    return (
        select(Tag.id)
        .where(Tag.paper_id == Paper.id, Tag.kind == kind, Tag.value.in_(values))
        .exists()
    )


def _added(include_out_of_scope: bool):
    """Papers with an arXiv day, out-of-scope ones only if included, as search_papers lists."""
    added = Paper.added_on.is_not(None)
    return added if include_out_of_scope else added & ~_tagged("domain", OUT_OF_SCOPE)


def latest_added_day(session: Session, include_out_of_scope: bool = False) -> date | None:
    """The last arXiv day papers were added on, None if none ever were."""
    # Not max(): with the scope's anti-join PostgreSQL would read every tag for it (0.3 s at
    # 350,000 papers); walking the index back stops at the first paper in scope.
    last = select(Paper.added_on).where(_added(include_out_of_scope))
    return session.scalar(last.order_by(Paper.added_on.desc()).limit(1))


def _counts():
    """How many papers, how many new and updated (Paper.added_kind), how many still wait for
    their brief and how many failed it.
    """
    count = func.count()
    waiting = Paper.status.not_in(["briefed", "brief_failed"])
    return (
        count.label("papers"),
        count.filter(Paper.added_kind == "new").label("new"),
        count.filter(Paper.added_kind == "updated").label("updated"),
        count.filter(waiting).label("pending"),
        count.filter(Paper.status == "brief_failed").label("failed"),
    )


def added_days(session: Session, limit: int = 30, include_out_of_scope: bool = False):
    """Rows (day, then the counts of _counts) for the last `limit` arXiv days papers were
    added on, newest first.
    """
    # Walking the index back stops after `limit` days, but the scope check makes PostgreSQL
    # plan parallel workers that read on past them (0.13 s at 350,000 papers, against 0.03 s
    # alone). SET LOCAL ends with the transaction.
    session.execute(text("SET LOCAL max_parallel_workers_per_gather = 0"))
    query = select(Paper.added_on.label("day"), *_counts()).where(_added(include_out_of_scope))
    query = query.group_by(Paper.added_on).order_by(Paper.added_on.desc()).limit(limit)
    return session.execute(query).all()


def added_on(session: Session, day: date, include_out_of_scope: bool = False):
    """The counts of _counts for the papers added on an arXiv day."""
    query = select(*_counts()).where(_added(include_out_of_scope), Paper.added_on == day)
    return session.execute(query).one()


def starred(
    session: Session, user_id: int, paper_ids: list[int]
) -> dict[int, tuple[datetime, list[int]]]:
    """{paper id: (when the user starred it, ids of the user's collections it is in)} for those
    of `paper_ids` the user starred; a paper in a collection always is.
    """
    mine = CollectionPaper.collection_id.in_(
        select(Collection.id).where(Collection.user_id == user_id)
    )
    collections = func.array_agg(CollectionPaper.collection_id).filter(
        CollectionPaper.collection_id.is_not(None)
    )
    query = (
        select(UserPaper.paper_id, UserPaper.starred_at, collections)
        .outerjoin(CollectionPaper, (CollectionPaper.paper_id == UserPaper.paper_id) & mine)
        .where(
            UserPaper.user_id == user_id,
            UserPaper.paper_id.in_(paper_ids),
            UserPaper.starred_at.is_not(None),
        )
        .group_by(UserPaper.paper_id, UserPaper.starred_at)
    )
    return {paper: (at, sorted(ids or [])) for paper, at, ids in session.execute(query)}


def star(session: Session, user_id: int, paper_id: int, on: bool) -> bool:
    """Star or unstar a paper for the user; doing it twice changes nothing (a star keeps its
    first time). False when there is no such paper.
    """
    if session.get(Paper, paper_id) is None:
        return False
    if on:
        row = insert(UserPaper).values(user_id=user_id, paper_id=paper_id, starred_at=func.now())
        kept = {"starred_at": func.coalesce(UserPaper.starred_at, func.now())}
        session.execute(
            row.on_conflict_do_update(index_elements=["user_id", "paper_id"], set_=kept)
        )
    else:
        mark = (UserPaper.user_id == user_id) & (UserPaper.paper_id == paper_id)
        session.execute(update(UserPaper).where(mark).values(starred_at=None))
        mine = select(Collection.id).where(Collection.user_id == user_id)
        session.execute(  # collections hold starred papers only
            delete(CollectionPaper).where(
                CollectionPaper.paper_id == paper_id, CollectionPaper.collection_id.in_(mine)
            )
        )
    return True


def _papers_in(collection_id: int):
    count = select(func.count()).where(CollectionPaper.collection_id == collection_id)
    return count.scalar_subquery()


def collections(session: Session, user_id: int) -> list[tuple[Collection, int]]:
    """(collection, number of papers) for each of the user's collections, oldest first."""
    query = select(Collection, _papers_in(Collection.id)).where(Collection.user_id == user_id)
    return session.execute(query.order_by(Collection.created_at, Collection.id)).all()


def own_collections(session: Session, user_id: int, collection_ids: Sequence[int]) -> bool:
    """Whether all of `collection_ids` are the user's."""
    ids = set(collection_ids)
    mine = select(func.count()).where(Collection.id.in_(ids), Collection.user_id == user_id)
    return session.scalar(mine) == len(ids)


def _collection(session: Session, user_id: int, collection_id: int) -> Collection | None:
    collection = session.get(Collection, collection_id)
    return collection if collection and collection.user_id == user_id else None


def add_collection(session: Session, user_id: int, name: str) -> Collection:
    """A new collection of the user's; NameTaken if the user has one of that name."""
    collection = Collection(user_id=user_id, name=name)
    try:
        with session.begin_nested():
            session.add(collection)
    except IntegrityError:
        raise NameTaken(name) from None
    session.refresh(collection)  # created_at comes from the database
    return collection


def rename_collection(
    session: Session, user_id: int, collection_id: int, name: str
) -> tuple[Collection, int] | None:
    """(collection, number of papers) after renaming, None if it is not the user's; NameTaken
    if the user has another of that name.
    """
    collection = _collection(session, user_id, collection_id)
    if collection is None:
        return None
    try:
        with session.begin_nested():
            collection.name = name
    except IntegrityError:
        raise NameTaken(name) from None
    return collection, session.scalar(select(_papers_in(collection_id)))


def delete_collection(session: Session, user_id: int, collection_id: int) -> bool:
    """Its papers stay starred. False if it is not the user's."""
    gone = delete(Collection).where(Collection.id == collection_id, Collection.user_id == user_id)
    return session.execute(gone).rowcount > 0


def collect(
    session: Session, user_id: int, collection_id: int, paper_id: int, on: bool
) -> list[int] | None:
    """Put a paper in one of the user's collections, starring it if need be, or take it out;
    doing either twice changes nothing. Returns the ids of the user's collections the paper is
    in, None if the collection is not the user's or there is no such paper.
    """
    if _collection(session, user_id, collection_id) is None or not session.get(Paper, paper_id):
        return None
    pair = {"collection_id": collection_id, "paper_id": paper_id}
    if on:
        star(session, user_id, paper_id, True)
        session.execute(insert(CollectionPaper).values(pair).on_conflict_do_nothing())
    else:
        session.execute(delete(CollectionPaper).filter_by(**pair))
    return starred(session, user_id, [paper_id]).get(paper_id, (None, []))[1]


def get_paper(session: Session, paper_id: int) -> Paper | None:
    options = [selectinload(Paper.tags), selectinload(Paper.topics)]
    return session.get(Paper, paper_id, options=options)


def latest_report(session: Session, paper_id: int) -> dict | None:
    """The newest full report (论文解读): {"version", "markdown", "figures", "model",
    "created_at"}. The markdown cites figures and tables with markers like [[图:Fig. 2]] or
    [[表:TABLE I]]; "figures" has those cited, each {"label" (as in its marker), "kind",
    "page", "caption", "url", "width", "height" (of the image, in pixels)}.
    """
    analysis = session.scalar(
        select(Analysis)
        .where(Analysis.paper_id == paper_id, Analysis.level == "report")
        .order_by(Analysis.id.desc())
        .limit(1)
    )
    if analysis is None:
        return None
    cited = session.scalars(
        select(Figure)
        .options(defer(Figure.image))
        .where(Figure.analysis_id == analysis.id)
        .order_by(Figure.id)
    )
    figures = [
        {
            "label": f.label,
            "kind": f.kind,
            "page": f.page,
            "caption": f.caption,
            "url": f"/api/papers/{paper_id}/figures/{f.id}",
            "width": f.width,
            "height": f.height,
        }
        for f in cited
    ]
    return {
        "version": analysis.result["version"],
        "markdown": analysis.result["markdown"],
        "figures": figures,
        "model": analysis.model,
        "created_at": analysis.created_at,
    }


# Captions of method figures. Not "the proposed": results compare against the proposed method.
OVERVIEW = (
    r"overview|framework|architecture|pipeline|schematic"
    r"|illustration of (our|the) (method|approach)"
)


def covers(session: Session, paper_ids: list[int]) -> dict[int, dict]:
    """{paper id: {"url", "width", "height", "label"}} of the cover of each of `paper_ids`
    whose newest report cites a figure (never a table): the figure its assessment named as the
    method overview (Figure.cover), else the first cited one whose caption reads like a method
    figure (OVERVIEW), else the first cited one. report.save stores a report's figures in the
    order it first cites them, so that is the lowest id.
    """
    newest = (
        select(func.max(Analysis.id))
        .where(Analysis.paper_id.in_(paper_ids), Analysis.level == "report")
        .group_by(Analysis.paper_id)
    )
    first = (
        select(Analysis.paper_id, Figure.id, Figure.width, Figure.height, Figure.label)
        .join(Figure, Figure.analysis_id == Analysis.id)
        .where(Analysis.id.in_(newest), Figure.kind == "figure")
        .ext(distinct_on(Analysis.paper_id))
        .order_by(
            Analysis.paper_id,
            Figure.cover.desc(),
            Figure.caption.regexp_match(OVERVIEW, "i").desc(),
            Figure.id,
        )
    )
    return {
        paper_id: {
            "url": f"/api/papers/{paper_id}/figures/{figure_id}",
            "width": width,
            "height": height,
            "label": label,
        }
        for paper_id, figure_id, width, height, label in session.execute(first)
    }


def with_report(session: Session, paper_ids: list[int]) -> set[int]:
    """Those of `paper_ids` that have a full report."""
    reported = select(Analysis.paper_id).where(
        Analysis.paper_id.in_(paper_ids), Analysis.level == "report"
    )
    return set(session.scalars(reported))


def latest_innovation(session: Session, paper_id: int) -> dict | None:
    """The newest assessment of the paper's innovation: {"type", "reason", "overview"}."""
    return session.scalar(
        select(Analysis.result)
        .where(Analysis.paper_id == paper_id, Analysis.level == "assessment")
        .order_by(Analysis.id.desc())
        .limit(1)
    )


def figure_image(session: Session, paper_id: int, figure_id: int) -> bytes | None:
    """The PNG of a figure a report of this paper cites."""
    return session.scalar(
        select(Figure.image)
        .join(Analysis)
        .where(Figure.id == figure_id, Analysis.paper_id == paper_id)
    )


def read_pages(
    session: Session, paper_id: int, first: int = 1, last: int | None = None
) -> tuple[str, list[tuple[int, str]]] | None:
    """Pages first..last (1-based, inclusive) of the newest extracted full text, as
    (pdf_url of that version, [(page number, text)]); None if no text was extracted yet.
    """
    text = session.scalar(
        select(PaperText)
        .where(PaperText.paper_id == paper_id)
        .order_by(PaperText.id.desc())
        .limit(1)
    )
    if text is None:
        return None
    last = min(last or len(text.pages), len(text.pages))
    return text.pdf_url, [(n, text.pages[n - 1]) for n in range(max(first, 1), last + 1)]


def tag_counts(session: Session, kind: str | None = None) -> list[tuple[str, str, int]]:
    """(kind, value, number of papers) for every tag in use, most used first within a kind."""
    # ponytail: returns every keyword, which grows without bound; add a limit when it matters.
    papers = func.count().label("papers")
    query = select(Tag.kind, Tag.value, papers).group_by(Tag.kind, Tag.value)
    if kind:
        query = query.where(Tag.kind == kind)
    return session.execute(query.order_by(Tag.kind, papers.desc(), Tag.value)).all()


def category_counts(session: Session) -> list[tuple[str, int]]:
    """(category, number of papers) for every arXiv category in use, cross-lists included,
    most papers first.
    """
    categories = select(func.unnest(Paper.categories).label("category")).subquery()
    papers = func.count().label("papers")
    query = select(categories.c.category, papers).group_by(categories.c.category)
    return session.execute(query.order_by(papers.desc(), categories.c.category)).all()


def _mark_edited(session: Session, paper_id: int) -> bool:
    """Flag the paper so AI tagging leaves its tags alone. The row lock this takes orders the
    edit against a brief being saved for the same paper (which locks it too).
    """
    marked = update(Paper).where(Paper.id == paper_id).values(tags_edited=True)
    return session.execute(marked).rowcount > 0


def _tags(session: Session, paper_id: int) -> list[Tag]:
    return session.scalars(select(Tag).where(Tag.paper_id == paper_id).order_by(Tag.id)).all()


def add_tag(
    session: Session, paper_id: int, kind: str, value: str, user_id: int
) -> list[Tag] | None:
    """A member adds a tag; returns the paper's tags, None if there is no such paper.
    Domains must come from the vocabulary (NotInVocabulary otherwise).
    """
    if kind == "domain" and value not in config().vocabulary:
        raise NotInVocabulary(f"{value!r} is not a domain")
    if not _mark_edited(session, paper_id):
        return None
    tag = dict(paper_id=paper_id, kind=kind, value=value, source="human", user_id=user_id)
    session.execute(insert(Tag).values(tag).on_conflict_do_nothing())
    return _tags(session, paper_id)


def delete_tag(session: Session, paper_id: int, tag_id: int) -> list[Tag] | None:
    """A member removes a tag; returns the paper's tags, None if there is no such tag."""
    if not _mark_edited(session, paper_id):
        return None
    if not session.execute(delete(Tag).where(Tag.id == tag_id, Tag.paper_id == paper_id)).rowcount:
        return None
    return _tags(session, paper_id)


def _paper_topics(session: Session, paper_id: int) -> list[PaperTopic]:
    query = select(PaperTopic).where(PaperTopic.paper_id == paper_id).order_by(PaperTopic.id)
    return session.scalars(query).all()


def add_topic(
    session: Session, paper_id: int, topic_id: int, user_id: int
) -> list[PaperTopic] | None:
    """A member links a topic; returns the paper's topics, None if there is no such paper
    (NotInVocabulary if there is no such topic).
    """
    topics.lock(session)  # before the paper row; a merge cannot move the topic meanwhile
    if session.get(Topic, topic_id) is None:
        raise NotInVocabulary(f"no topic {topic_id}")
    if not _mark_edited(session, paper_id):
        return None
    link = dict(paper_id=paper_id, topic_id=topic_id, source="human", user_id=user_id)
    session.execute(insert(PaperTopic).values(link).on_conflict_do_nothing())
    return _paper_topics(session, paper_id)


def delete_topic(session: Session, paper_id: int, topic_id: int) -> list[PaperTopic] | None:
    """A member unlinks a topic; returns the paper's topics, None if it was not linked."""
    if not _mark_edited(session, paper_id):
        return None
    unlink = delete(PaperTopic).where(
        PaperTopic.paper_id == paper_id, PaperTopic.topic_id == topic_id
    )
    if not session.execute(unlink).rowcount:
        return None
    return _paper_topics(session, paper_id)


def set_paper_type(session: Session, paper_id: int, paper_type: str) -> bool:
    """A member sets the paper type; False if there is no such paper."""
    if not _mark_edited(session, paper_id):
        return False
    session.execute(update(Paper).where(Paper.id == paper_id).values(paper_type=paper_type))
    return True


def vocabulary(session: Session) -> list[dict]:
    """Every domain with its topics, each with its number of papers and whether a brief
    created it (rather than the starting vocabulary).
    """
    counts = topics.paper_counts(session)
    tree = {d: {"domain": d, "zh": v.zh, "topics": []} for d, v in config().vocabulary.items()}
    for topic in session.scalars(select(Topic).order_by(Topic.id)):
        if topic.domain in tree:
            tree[topic.domain]["topics"].append(
                {
                    "id": topic.id,
                    "name": topic.name,
                    "zh": topic.zh,
                    "definition": topic.definition,
                    "aliases": topic.aliases,
                    "created": topic.source == "model",
                    "papers": counts.get(topic.id, 0),
                }
            )
    return list(tree.values())


def created_topics(session: Session) -> list[tuple[Topic, int]]:
    """(topic, number of papers) for every topic a brief created, newest first."""
    counts = topics.paper_counts(session)
    created = select(Topic).where(Topic.source == "model").order_by(Topic.id.desc())
    return [(topic, counts.get(topic.id, 0)) for topic in session.scalars(created)]


def topic_merges(session: Session) -> list[TopicMerge]:
    return session.scalars(select(TopicMerge).order_by(TopicMerge.id.desc())).all()


def add_note(
    session: Session,
    user_id: int,
    content: str,
    paper_id: int | None = None,
    quote: str | None = None,
    anchor: dict | None = None,
):
    """Returns the new note, or None when `paper_id` names no paper."""
    if paper_id is not None and session.get(Paper, paper_id) is None:
        return None
    note = Note(user_id=user_id, paper_id=paper_id, content=content, quote=quote, anchor=anchor)
    session.add(note)
    session.flush()
    session.refresh(note)  # created_at comes from the database
    return note


def list_notes(
    session: Session, user_id: int, paper_id: int | None = None, page: int = 1, page_size: int = 20
) -> tuple[int, list[tuple[Note, str | None]]]:
    """The user's own notes, newest first, free blocks too, optionally only those on one paper.
    Returns (total, page of (note, title of its paper)).
    """
    query = select(Note, Paper.title).outerjoin(Paper).where(Note.user_id == user_id)
    if paper_id is not None:
        query = query.where(Note.paper_id == paper_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.execute(
        query.order_by(Note.id.desc()).limit(page_size).offset((page - 1) * page_size)
    ).all()
    return total, [tuple(row) for row in rows]


def set_page(session: Session, user_id: int, paper_id: int, content: str) -> Note | None:
    """The user's free block on the paper, written whole; blank text removes it (None)."""
    if not content.strip():
        session.execute(
            delete(Note).where(
                Note.user_id == user_id, Note.paper_id == paper_id, Note.kind == "page"
            )
        )
        return None
    row = dict(user_id=user_id, paper_id=paper_id, kind="page", content=content)
    page = (
        insert(Note)
        .values(row)
        .on_conflict_do_update(
            index_elements=["user_id", "paper_id"],
            index_where=text("kind = 'page'"),
            set_=dict(content=content, updated_at=func.now()),
        )
        .returning(Note)
    )
    return session.scalars(page).one()


def delete_note(session: Session, user_id: int, note_id: int) -> bool:
    """Only the author can delete a note; False when it is not theirs or does not exist."""
    deleted = delete(Note).where(Note.id == note_id, Note.user_id == user_id)
    return session.execute(deleted).rowcount > 0
