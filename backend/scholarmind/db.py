"""Engine, session and tables."""

from datetime import date, datetime
from functools import cache

from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    case,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    column_property,
    mapped_column,
    relationship,
)

from .config import settings


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    email: Mapped[str | None] = mapped_column(Text, unique=True)  # stripped, lower-case
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    avatar: Mapped[str | None] = mapped_column(Text)  # "<figure>-<colour>"; None: the default


class Token(Base):
    """A member's personal token, one per agent or machine; only its SHA-256 is stored."""

    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime | None]  # None: never
    last_used_at: Mapped[datetime | None]  # to the minute

    user: Mapped[User] = relationship()


class Setting(Base):
    """What an admin overrides on the web (agent.py); no row: the default holds."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    arxiv_id: Mapped[str | None] = mapped_column(Text, unique=True)  # without version
    arxiv_version: Mapped[int | None]
    doi: Mapped[str | None] = mapped_column(Text, index=True)  # lowercase
    title: Mapped[str] = mapped_column(Text)
    title_norm: Mapped[str] = mapped_column(Text, index=True)
    abstract: Mapped[str] = mapped_column(Text, server_default="")
    authors: Mapped[str] = mapped_column(Text, server_default="")
    primary_category: Mapped[str | None] = mapped_column(Text)
    categories: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    comment: Mapped[str | None] = mapped_column(Text)
    journal_ref: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)  # versioned, e.g. .../pdf/2401.12345v2
    published_at: Mapped[datetime | None]  # first version
    updated_at: Mapped[datetime | None]  # latest version
    # The arXiv day its latest version was announced: the OAI datestamp of the record that brought
    # it (arxiv.parse_list_records). Changes of metadata only (a DOI, categories) leave it, so the
    # paper stays among that day's new papers ("每日新增"). None for papers not from arXiv.
    added_on: Mapped[date | None] = mapped_column(index=True)
    # arXiv, or the conference whose proceedings have the paper (then its year and track; else
    # the year of the first version). venue_key identifies the proceedings entry: "<venue>:<id
    # or path at the source>", the same on every import.
    source: Mapped[str] = mapped_column(Text, server_default="arXiv")
    year: Mapped[int | None]
    track: Mapped[str | None] = mapped_column(Text)  # main; ACL: long, short
    venue_key: Mapped[str | None] = mapped_column(Text, unique=True)
    # new -> briefing (claimed by a worker) -> briefed, or brief_failed (invalid on its own)
    status: Mapped[str] = mapped_column(Text, server_default="new", index=True)
    claimed_at: Mapped[datetime | None]  # when a worker took it; stale claims are taken again
    summary: Mapped[str | None] = mapped_column(Text)  # from the latest brief
    tags_edited: Mapped[bool] = mapped_column(server_default="false")  # AI leaves tags alone
    paper_type: Mapped[str | None] = mapped_column(Text, index=True)  # brief.PAPER_TYPES
    # Full report, separate from the brief: None -> queued -> reporting -> reported, or
    # report_failed after a retry
    report_status: Mapped[str | None] = mapped_column(Text, index=True)
    report_trigger: Mapped[str | None] = mapped_column(Text)  # auto, manual
    report_queued_at: Mapped[datetime | None]  # counts automatic reports against the daily limit
    report_claimed_at: Mapped[datetime | None]
    # The step a running report is in (report.STAGES) and since when, set as the worker enters
    # it; why the last one failed: {"code": one of report.ERRORS, "detail"}
    report_stage: Mapped[str | None] = mapped_column(Text)
    report_stage_at: Mapped[datetime | None]
    report_error: Mapped[dict | None] = mapped_column(JSONB)
    # One of assess.INNOVATIONS, from the newest assessment (assess.py), for filtering
    innovation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', title || ' ' || abstract)", persisted=True),
    )
    tags: Mapped[list["Tag"]] = relationship(order_by="Tag.id")
    topics: Mapped[list["PaperTopic"]] = relationship(order_by="PaperTopic.id")

    __table_args__ = (
        Index("ix_papers_search", "search", postgresql_using="gin"),
        Index("ix_papers_categories", "categories", postgresql_using="gin"),
        Index("ix_papers_published_at", "published_at"),
    )


# On added_on, either its first version came out (new) or a new version of it (updated).
Paper.added_kind = column_property(
    case((Paper.added_on.is_(None), None), (Paper.arxiv_version == 1, "new"), else_="updated")
)


# The list's order, newest first, also within a source and year or a year: at 100k papers a
# page took 0.2-1.4 s of sorting without these, 4-25 ms with them.
_newest = (Paper.published_at.desc().nulls_last(), Paper.id.desc())
Index("ix_papers_newest", *_newest)
Index("ix_papers_source_year", Paper.source, Paper.year, *_newest)
Index("ix_papers_year", Paper.year, *_newest)


class PaperText(Base):
    """Text extracted from one PDF version of a paper, page by page (pages[0] is page 1)."""

    __tablename__ = "paper_texts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    pdf_url: Mapped[str] = mapped_column(Text)  # versioned: the version the pages come from
    pages: Mapped[list[str]] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (UniqueConstraint("paper_id", "pdf_url"),)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)  # brief: domain, keyword; method, dataset only by hand
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)  # ai, human
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("paper_id", "kind", "value"),
        Index("ix_tags_kind_value", "kind", "value"),
    )


class Topic(Base):
    """A research topic under a domain. Papers link to its id, so a rename or a merge never
    touches paper tags. No two topics share a name or alias after config.topic_key().
    """

    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    domain: Mapped[str] = mapped_column(Text)  # a vocabulary domain, never out-of-scope
    name: Mapped[str] = mapped_column(Text, unique=True)  # canonical, English
    zh: Mapped[str] = mapped_column(Text, server_default="")
    definition: Mapped[str] = mapped_column(Text, server_default="")  # one sentence
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    source: Mapped[str] = mapped_column(Text)  # seed (config's starting vocabulary), model
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PaperTopic(Base):
    __tablename__ = "paper_topics"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)  # keeps the order
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(Text)  # ai, human
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    topic: Mapped[Topic] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("paper_id", "topic_id"),)


class TopicMerge(Base):
    """A created topic merged into another (and deleted): who, when, why, and what it was."""

    __tablename__ = "topic_merges"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger)  # gone: no foreign key
    source: Mapped[dict] = mapped_column(JSONB)  # its domain, name, zh, definition, aliases
    target_id: Mapped[int] = mapped_column(BigInteger)  # may be merged away later itself
    target_name: Mapped[str] = mapped_column(Text)
    merged_by: Mapped[str] = mapped_column(Text)  # an admin's username, or "review"
    reason: Mapped[str] = mapped_column(Text)
    papers: Mapped[int]  # paper links moved over (those the target had already are dropped)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Analysis(Base):
    """Append-only: every brief or report a model produced, never updated."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    # brief (tier 1), report (tier 2, 论文解读), assessment (its innovation type)
    level: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)  # prompt file name, e.g. brief.v1
    result: Mapped[dict] = mapped_column(JSONB)
    # Token usage, plus seconds for reports. A brief is one call for a whole batch, so each
    # paper gets an equal share.
    usage: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # the newest reports, for how long a reading takes (report.typical_seconds), among the briefs
    __table_args__ = (
        Index("ix_analyses_reports", "id", postgresql_where=text("level = 'report'")),
    )


class Figure(Base):
    """A figure or table cropped from the PDF that a report cites; never changed (a redone
    report gets new rows), so its image can be cached for good.
    """

    __tablename__ = "figures"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(Text)  # figure, table
    label: Mapped[str] = mapped_column(Text)  # as the caption writes it: "Fig. 2", "TABLE I"
    caption: Mapped[str] = mapped_column(Text)
    page: Mapped[int]
    image: Mapped[bytes] = mapped_column(LargeBinary)  # PNG
    width: Mapped[int]  # of the image, in pixels
    height: Mapped[int]
    # the method overview the paper's assessment named (assess.mark_cover), its card's cover
    cover: Mapped[bool] = mapped_column(server_default="false")


class Note(Base):
    """A member's private note, on a paper or on nothing. Only its author ever sees it. A note
    (kind "note") may quote the paper, with where the quote is (anchor, as the client gave it);
    "page" is the one free block of text per member and paper.
    """

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    paper_id: Mapped[int | None] = mapped_column(ForeignKey("papers.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(Text, server_default="note")  # note, page
    content: Mapped[str] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text)
    anchor: Mapped[dict | None] = mapped_column(JSONB)  # {"source": "pdf", "page"} or "report"
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_notes_user_id_paper_id", "user_id", "paper_id"),
        Index(
            "ix_notes_page",
            "user_id",
            "paper_id",
            unique=True,
            postgresql_where=text("kind = 'page'"),
        ),
    )


class ChatMessage(Base):
    """A member's conversation with the agent about a paper, one per member and paper, kept
    until they clear it. An answer is stored with its question once it is complete; it records
    the model, prompt and token use as analyses do.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(Text)  # user, assistant
    content: Mapped[str] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text)  # what the question is about, from the paper
    model: Mapped[str | None] = mapped_column(Text)  # the assistant's
    prompt_version: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (Index("ix_chat_messages_user_id_paper_id", "user_id", "paper_id", "id"),)


class UserPaper(Base):
    """One member's own marks on one paper, which nobody else sees. Only the star so far."""

    __tablename__ = "user_papers"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    starred_at: Mapped[datetime | None]  # None: not starred


class Collection(Base):
    """A member's own group of starred papers, which nobody else sees."""

    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)  # stripped
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "name"),)


class CollectionPaper(Base):
    """A paper in a collection: always one the collection's owner starred (queries.collect
    stars it, queries.star takes it out of the collections when unstarred).
    """

    __tablename__ = "collection_papers"

    collection_id: Mapped[int] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class SyncState(Base):
    __tablename__ = "sync_state"

    source: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. arxiv:cs.CV
    cursor: Mapped[date]  # next sync harvests from this day (inclusive)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


@cache
def engine():
    return create_engine(settings().sqlalchemy_url, pool_pre_ping=True)


def get_session():
    """FastAPI dependency: one transaction per request."""
    with Session(engine()) as session, session.begin():
        yield session
