"""REST API and the built web app. Everything under /api except health and login requires a
session cookie or a personal token.
"""

import hashlib
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import (
    AliasPath,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import (
    agent,
    assess,
    auth,
    brief,
    chat,
    jobs,
    mcp_server,
    queries,
    report,
    topics,
    translate,
    venues,
)
from .config import config, settings
from .db import Note, Paper, User, engine, get_session

config()  # fail at startup on a missing or broken config.yaml

DbSession = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(auth.current_user)]
SessionUser = Annotated[User, Depends(auth.session_user)]  # signed in, not by token
TagKind = Literal["domain", "keyword", "method", "dataset"]
PaperType = Literal[tuple(brief.PAPER_TYPES)]
AddedKind = Literal["new", "updated"]
UTC = ZoneInfo("UTC")


class Credentials(BaseModel):
    username: str = Field(max_length=150)
    password: str = Field(max_length=1024)


class PasswordChange(BaseModel):
    old_password: str = Field(max_length=1024)
    new_password: str = Field(min_length=auth.MIN_PASSWORD, max_length=1024)


class Me(BaseModel):
    username: str
    email: str | None
    is_admin: bool
    created_at: datetime
    avatar: str | None


class MeIn(BaseModel):
    # which figures and colours exist is the frontend's business
    avatar: Annotated[str, StringConstraints(pattern=r"^[a-z]+-[a-z]+$", max_length=32)] | None


class UserIn(BaseModel):
    username: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"
        ),
    ]
    email: Annotated[
        str,
        StringConstraints(strip_whitespace=True, to_lower=True, max_length=254, pattern=auth.EMAIL),
    ]
    password: str = Field(min_length=auth.MIN_PASSWORD, max_length=1024)
    is_admin: bool = False


class RoleIn(BaseModel):
    is_admin: bool


class PasswordIn(BaseModel):
    password: str = Field(min_length=auth.MIN_PASSWORD, max_length=1024)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None
    is_admin: bool
    created_at: datetime


ApiKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
BaseUrl = Annotated[str, Field(max_length=4096)]  # checked by agent.connection


class ProbeIn(BaseModel):
    base_url: BaseUrl | None = None  # None: the one in effect
    api_key: ApiKey | None = None  # None: the one in effect, on its own host only


class ProbeOut(BaseModel):
    models: list[str]


class ConnectionIn(BaseModel):
    base_url: BaseUrl
    api_key: ApiKey | None = None  # None: the key in effect stays, on its own host only


class ApiKeyState(BaseModel):
    api_key_set: bool
    api_key_hint: str | None  # "…" and the last 4 characters, for keys of 12 or more
    api_key_source: Literal["web", "env"] | None


class PromptIn(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)


class PromptOut(BaseModel):
    key: str
    version: str  # recorded with what the prompt makes
    text: str
    default_text: str
    modified: bool


class ModelIn(BaseModel):
    value: str = Field(max_length=4096)  # checked by agent.set_model


class ModelSetting(BaseModel):
    value: str
    source: Literal["web", "config"]


class ModelSettings(BaseModel):
    base_url: ModelSetting
    brief_model: ModelSetting
    report_model: ModelSetting


class ConnectionState(ApiKeyState):
    base_url: ModelSetting


class AgentSettings(ApiKeyState):
    model: ModelSettings
    prompts: list[PromptOut]


class TokenIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    expires_at: AwareDatetime | None = None  # None: never

    @field_validator("expires_at")
    @classmethod
    def _future(cls, value: datetime | None) -> datetime | None:
        if value is not None and value <= datetime.now(UTC):
            raise ValueError("must be in the future")
        return value


class TokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None


class NewToken(BaseModel):
    token: TokenOut
    value: str  # shown this once


NoteText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=20_000)]


class PdfAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["pdf"]
    page: Annotated[int, Field(ge=1)]


class ReportAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["report"]
    section: Annotated[str, Field(max_length=200)]


class NoteIn(BaseModel):
    content: NoteText  # may be empty with a quote (a highlight)
    quote: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    # where the quote is, kept as given for the client to go back to
    anchor: Annotated[PdfAnchor | ReportAnchor, Field(discriminator="source")] | None = None


class NoteEdit(BaseModel):
    content: NoteText


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    paper_id: int | None
    content: str
    quote: str | None
    anchor: dict | None
    created_at: datetime
    updated_at: datetime


class MyNote(NoteOut):
    kind: Literal["note", "page"]  # page: the free block on the paper
    paper_title: str | None  # None when on no paper


class PageIn(BaseModel):
    content: Annotated[str, Field(max_length=50_000)]  # as typed; blank removes it


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    content: str
    updated_at: datetime | None


class PaperNotes(BaseModel):
    page: PageOut  # the free block, "" and None when there is none
    notes: list[NoteOut]  # newest first, without the free block


class NotePage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[MyNote]


class ChatIn(BaseModel):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    quote: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None


class Chat(BaseModel):
    messages: list[chat.Message]  # oldest first


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    value: str
    source: str  # ai, human


class TagIn(BaseModel):
    kind: TagKind
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class TopicRef(BaseModel):
    """A topic linked to a paper."""

    model_config = ConfigDict(from_attributes=True, validate_by_name=True)

    id: int = Field(validation_alias="topic_id")
    name: str = Field(validation_alias=AliasPath("topic", "name"))
    zh: str = Field(validation_alias=AliasPath("topic", "zh"))
    source: str  # ai, human


class TopicIn(BaseModel):
    topic_id: int


class PaperTypeIn(BaseModel):
    paper_type: PaperType


class CreatedTopic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    name: str
    zh: str
    definition: str
    aliases: list[str]
    created_at: datetime
    papers: int = 0


class MergeIn(BaseModel):
    into: int  # the topic it duplicates
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""


class MergeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    source: dict  # what the merged topic was: domain, name, zh, definition, aliases
    target_id: int
    target_name: str
    merged_by: str  # an admin's username, or "review" (the weekly job)
    reason: str
    papers: int  # paper links moved over
    created_at: datetime


class TagCount(BaseModel):
    kind: str
    value: str
    papers: int


class CategoryCount(BaseModel):
    category: str
    papers: int


class AddedDay(BaseModel):
    day: date  # arXiv's
    papers: int
    new: int
    updated: int
    pending: int  # not briefed yet (status new or briefing)
    failed: int  # brief_failed


class ValueCount(BaseModel):
    value: str
    papers: int


class YearCount(BaseModel):
    value: int
    papers: int


class TopicCount(BaseModel):
    id: int
    name: str
    zh: str
    domain: str
    papers: int


class CollectionIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class CollectionOut(BaseModel):
    id: int
    name: str
    papers: int


class Facets(BaseModel):
    total: int  # under every filter, as GET /api/papers
    # each under every filter but its own, most papers first
    domain: list[ValueCount]
    topic: list[TopicCount]
    paper_type: list[ValueCount]
    innovation: list[ValueCount]
    category: list[ValueCount]
    source: list[ValueCount]  # arXiv or a conference
    year: list[YearCount]  # newest first
    collection: list[CollectionOut]  # the caller's


class Cover(BaseModel):
    url: str  # /api/papers/{id}/figures/{figure_id}
    width: int  # of the image, in pixels
    height: int
    label: str  # as the paper writes it, e.g. "Fig. 2"


class SourceCount(BaseModel):
    source: str  # arXiv or a conference
    year: int | None
    papers: int


class PaperOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    arxiv_id: str | None
    arxiv_version: int | None
    doi: str | None
    title: str
    abstract: str
    authors: str
    primary_category: str | None
    categories: list[str]
    comment: str | None
    journal_ref: str | None
    pdf_url: str | None
    published_at: datetime | None
    updated_at: datetime | None
    created_at: datetime  # when it entered the library
    added_on: date | None  # the arXiv day its latest version came out, see db.Paper
    added_kind: AddedKind | None  # new paper or new version that day
    source: str  # arXiv, or the conference whose proceedings have the paper
    year: int | None  # the conference's, else the first arXiv version's
    track: str | None  # conference papers: main; ACL: long, short
    status: str
    summary: str | None
    tags: list[TagOut]  # domain and keyword from the brief, method and dataset from the report
    topics: list[TopicRef]
    paper_type: str | None  # see /api/vocabulary
    report_status: str | None  # queued, reporting, reported, report_failed
    starred: bool = False  # by the caller; set by _out
    starred_at: datetime | None = None  # when the caller starred it; by _out
    collections: list[int] = []  # ids of the caller's collections it is in; by _out
    cover: Cover | None = None  # the first figure its report cites (queries.covers); by _out
    innovation: str | None  # from the newest assessment, one of assess.INNOVATIONS


class ReportProgress(BaseModel):
    """A queued or running report, see report.progress."""

    stage: Literal[("queued", *report.STAGES)]
    since: datetime | None  # when the stage began; queued: when it was queued
    ahead: int | None  # queued manual reports before it, 0: it is next; else None
    eta_seconds: int | None  # rough time to its report; None when nothing gives one
    typical_seconds: int | None  # how long a report takes once a worker has it


class ReportError(BaseModel):
    code: Literal[report.ERRORS]  # see report.ERRORS
    detail: str  # in English, for a code a client does not know


class PaperDetail(PaperOut):
    report: dict | None  # newest full report (论文解读), see queries.latest_report
    innovation_reason: str | None  # why the newest assessment chose `innovation`
    report_progress: ReportProgress | None  # while report_status is queued or reporting
    report_error: ReportError | None  # with report_failed; None for failures before 0015
    abstract_zh: str | None  # the stored translation of the current abstract (translate)


class PaperPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[PaperOut]
    # With `added`: the day it meant (None if no paper was ever added) and how many of that
    # day's papers are not briefed yet or failed to be (as in AddedDay), whatever the filters
    # but include_out_of_scope.
    added_day: date | None = None
    pending: int | None = None
    failed: int | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A mounted MCP app's own lifespan never runs: the host app has to start its sessions.
    async with mcp_server.mcp.session_manager.run():
        yield


app = FastAPI(
    title="ScholarMind", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)
public = APIRouter(prefix="/api")
private = APIRouter(prefix="/api", dependencies=[Depends(auth.current_user)])


@public.get("/health")
def health(session: DbSession):
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@public.post("/login")
def login(credentials: Credentials, response: Response, session: DbSession):
    user = auth.authenticate(session, credentials.username, credentials.password)
    if user is None:
        raise HTTPException(401, "Invalid username or password")
    _start_session(response, user)
    return {"username": user.username, "is_admin": user.is_admin}


def _start_session(response: Response, user: User):
    response.set_cookie(
        auth.COOKIE,
        auth.make_session_cookie(user),
        max_age=auth.SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings().cookie_secure,
    )


@public.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"status": "ok"}


@private.get("/me", response_model=Me)
def me(user: CurrentUser):
    return {
        "username": user.username,
        "email": user.email,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
        "avatar": user.avatar,
    }


@private.patch("/me", response_model=Me)
def update_me(body: MeIn, user: CurrentUser):
    """Choose a preset avatar; null restores the default."""
    user.avatar = body.avatar
    return me(user)


@private.post("/me/password")
def change_password(change: PasswordChange, response: Response, user: SessionUser):
    """Ends every session of the user, including this one, which gets a fresh cookie.
    Personal tokens stay valid.
    """
    if not auth.verify_password(change.old_password, user.password_hash):
        raise HTTPException(400, "Old password is incorrect")  # not 401: still signed in
    user.password_hash = auth.hash_password(change.new_password)
    _start_session(response, user)
    return {"status": "ok"}


def admin_user(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Admins only")
    return user


AdminUser = Annotated[User, Depends(admin_user)]


def web_admin(user: SessionUser) -> User:
    """An admin signed in on the web page: a personal token never will do, not even an admin's,
    so a leaked token cannot create accounts or change the model key.
    """
    if not user.is_admin:
        raise HTTPException(403, "Admins only")
    return user


WebAdmin = Annotated[User, Depends(web_admin)]


@private.get("/admin/users", response_model=list[UserOut])
def list_users(session: DbSession, admin: WebAdmin):
    return auth.users(session)


@private.post("/admin/users", response_model=UserOut, status_code=201)
def create_user(body: UserIn, session: DbSession, admin: WebAdmin):
    """An account, a member's unless is_admin, without a token."""
    try:
        user = auth.add_user(session, body.username, body.password, body.email, body.is_admin)
    except auth.Taken as e:
        raise HTTPException(409, f"Another account has this {e.args[0]}") from None
    session.refresh(user)  # created_at comes from the database
    if user.is_admin:
        auth.log.info("%s created the admin %s", admin.username, user.username)
    return user


@private.patch("/admin/users/{user_id}", response_model=UserOut)
def set_role(user_id: int, body: RoleIn, session: DbSession, admin: WebAdmin):
    """Promote or demote; a demoted admin loses the admin pages at once."""
    try:
        user = auth.set_admin(session, admin, user_id, body.is_admin)
    except auth.Refused as e:
        raise HTTPException(409, str(e)) from None
    if user is None:
        raise HTTPException(404, "No such account")
    return user


@private.delete("/admin/users/{user_id}", status_code=204)
def delete_user(user_id: int, session: DbSession, admin: WebAdmin):
    """Its sessions and tokens stop working at once."""
    try:
        deleted = auth.delete_user(session, admin, user_id)
    except auth.Refused as e:
        raise HTTPException(409, str(e)) from None
    if not deleted:
        raise HTTPException(404, "No such account")


@private.put("/admin/users/{user_id}/password", status_code=204)
def reset_password(user_id: int, body: PasswordIn, session: DbSession, admin: WebAdmin):
    """Signs the account out everywhere, as any password change does; its tokens stay."""
    try:
        done = auth.reset_password(session, admin, user_id, body.password)
    except auth.Refused as e:
        raise HTTPException(409, str(e)) from None
    if not done:
        raise HTTPException(404, "No such account")


@private.get("/admin/agent", response_model=AgentSettings)
def agent_settings(session: DbSession, admin: WebAdmin):
    """The model API key (whether there is one, where from; never the key) and the prompts."""
    prompts = [agent.prompt_state(session, key) for key in agent.PROMPTS]
    model = agent.model_state(session)
    return agent.api_key_state(session) | {"model": model, "prompts": prompts}


@private.post("/admin/agent/probe", response_model=ProbeOut)
def probe(body: ProbeIn, session: DbSession, admin: WebAdmin):
    """The models the provider lists with this base URL and key, to pick from. 502 if there is
    no list, saying why but never what the provider answered.
    """
    try:
        base_url, key = agent.connection(session, body.base_url, body.api_key)
    except agent.InvalidSetting as e:
        raise HTTPException(422, str(e)) from None
    if not key:
        raise HTTPException(409, "No API key is set")
    try:
        return {"models": agent.list_models(base_url, key)}
    except agent.ProbeFailed as e:
        raise HTTPException(502, str(e)) from None


def _connection_state(session: Session) -> dict:
    return agent.api_key_state(session) | {"base_url": agent.model_state(session)["base_url"]}


@private.put("/admin/agent/connection", response_model=ConnectionState)
def set_connection(body: ConnectionIn, session: DbSession, admin: WebAdmin):
    """The base URL and its key, together (the key stored encrypted); the next job uses them,
    in every worker.
    """
    try:
        agent.set_connection(session, body.base_url, body.api_key, admin)
    except agent.InvalidSetting as e:
        raise HTTPException(422, str(e)) from None
    return _connection_state(session)


@private.delete("/admin/agent/connection", response_model=ConnectionState)
def reset_connection(session: DbSession, admin: WebAdmin):
    """config.yaml's base URL and LLM_API_KEY again."""
    agent.reset_connection(session, admin)
    return _connection_state(session)


def _model_name(name: str) -> str:
    if name not in agent.NAMES:
        raise HTTPException(404, "No such model setting")
    return name


ModelName = Annotated[str, Depends(_model_name)]


@private.put("/admin/agent/model/{name}", response_model=ModelSetting)
def set_model(name: ModelName, body: ModelIn, session: DbSession, admin: WebAdmin):
    """Overrides config.yaml from the next model call on, in every worker."""
    try:
        agent.set_model(session, name, body.value, admin)
    except agent.InvalidSetting as e:
        raise HTTPException(422, str(e)) from None
    return agent.model_state(session)[name]


@private.delete("/admin/agent/model/{name}", response_model=ModelSetting)
def reset_model(name: ModelName, session: DbSession, admin: WebAdmin):
    """config.yaml's value again."""
    agent.reset_model(session, name, admin)
    return agent.model_state(session)[name]


def _prompt_key(key: str) -> str:
    if key not in agent.PROMPTS:
        raise HTTPException(404, "No such prompt")
    return key


PromptKey = Annotated[str, Depends(_prompt_key)]


@private.put("/admin/agent/prompts/{key}", response_model=PromptOut)
def set_prompt(key: PromptKey, body: PromptIn, session: DbSession, admin: WebAdmin):
    """Only what is generated afterwards uses it; nothing is redone."""
    try:
        agent.set_prompt(session, key, body.text, admin)
    except agent.MissingPlaceholders as e:
        raise HTTPException(422, str(e)) from None
    return agent.prompt_state(session, key)


@private.delete("/admin/agent/prompts/{key}", response_model=PromptOut)
def reset_prompt(key: PromptKey, session: DbSession, admin: WebAdmin):
    """The default prompt (its file) again."""
    agent.reset_prompt(session, key)
    return agent.prompt_state(session, key)


@private.get("/me/tokens", response_model=list[TokenOut])
def list_tokens(session: DbSession, user: SessionUser):
    return auth.tokens(session, user.id)


@private.post("/me/tokens", response_model=NewToken, status_code=201)
def create_token(body: TokenIn, session: DbSession, user: SessionUser):
    """A new personal token for an agent or machine. Its value is shown this once; only its
    hash is kept.
    """
    token, value = auth.new_token(session, user, body.name, body.expires_at)
    return {"token": token, "value": value}


@private.delete("/me/tokens/{token_id}", status_code=204)
def revoke_token(token_id: int, session: DbSession, user: SessionUser):
    """The token stops working at once."""
    if not auth.revoke_token(session, user.id, token_id):
        raise HTTPException(404, "No such token")


@private.get("/vocabulary")
def vocabulary(session: DbSession):
    """Domains with their topics (number of papers, created by a brief or not), and the paper
    types. Out-of-scope is a domain without topics.
    """
    types = [{"value": value, "zh": zh} for value, zh in brief.PAPER_TYPES.items()]
    return {"domains": queries.vocabulary(session), "paper_types": types}


@private.get("/categories", response_model=list[CategoryCount])
def category_counts(session: DbSession):
    """Every arXiv category in use (cross-lists included) with its number of papers."""
    return [{"category": c, "papers": n} for c, n in queries.category_counts(session)]


def _out(session: Session, user: User, papers: list[Paper]) -> list[dict]:
    """The papers with what is per caller or kept elsewhere: one query each for the page."""
    ids = [p.id for p in papers]
    starred, covers = queries.starred(session, user.id, ids), queries.covers(session, ids)
    out = []
    for paper in papers:
        at, collections = starred.get(paper.id, (None, []))
        mine = {"starred": at is not None, "starred_at": at, "collections": collections}
        out.append(
            PaperOut.model_validate(paper).model_dump() | mine | {"cover": covers.get(paper.id)}
        )
    return out


NO_DAY = date.min  # an arXiv day without papers: what added=latest is when none was ever added


def paper_filters(
    session: DbSession,
    user: CurrentUser,
    category: Annotated[list[str], Query()] = (),
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
    domain: Annotated[list[str], Query()] = (),
    topic: Annotated[list[int], Query()] = (),  # topic ids
    paper_type: Annotated[list[PaperType], Query()] = (),
    keyword: str | None = None,
    include_out_of_scope: bool = False,  # implied by domain=out-of-scope
    added: date | Literal["latest"] | None = None,  # an arXiv day, see db.Paper.added_on
    added_kind: AddedKind | None = None,
    starred: bool = False,  # only yours, most recently starred first
    innovation: Annotated[list[Literal[assess.INNOVATIONS]], Query()] = (),
    source: Annotated[list[str], Query()] = (),  # arXiv or a conference name
    year: Annotated[list[int], Query()] = (),  # a conference's, else the first version's
    collection: Annotated[list[int], Query()] = (),  # ids of your collections
    tz: ZoneInfo = UTC,  # IANA name; date_from/date_to are days in this zone
) -> dict:
    """The filters of GET /api/papers and /api/papers/facets, as queries.paper_filters takes
    them. Repeat category, domain, topic, paper_type, innovation, source, year or collection
    for any of several values. A collection that is not yours is not found, like one that does
    not exist.
    """
    if collection and not queries.own_collections(session, user.id, collection):
        raise HTTPException(404, "Collection not found")
    if added == "latest":
        added = queries.latest_added_day(session, include_out_of_scope) or NO_DAY
    return {
        "category": category,
        "date_from": date_from,
        "date_to": date_to,
        "q": q,
        "domain": domain,
        "topic": topic,
        "paper_type": paper_type,
        "keyword": keyword,
        "include_out_of_scope": include_out_of_scope,
        "added": added,
        "added_kind": added_kind,
        "starred_by": user.id if starred else None,
        "innovation": innovation,
        "source": source,
        "year": year,
        "collection": collection,
        "tz": tz,
    }


Filters = Annotated[dict, Depends(paper_filters)]


@private.get("/sources", response_model=list[SourceCount])
def source_counts(session: DbSession):
    """Papers per source (arXiv or a conference) and year, for filter bars."""
    return [{"source": s, "year": y, "papers": n} for s, y, n in venues.source_counts(session)]


@private.get("/papers", response_model=PaperPage)
def list_papers(
    session: DbSession,
    user: CurrentUser,
    filters: Filters,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    total, items = queries.search_papers(session, page=page, page_size=page_size, **filters)
    found = {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": _out(session, user, items),
    }
    if day := filters["added"]:
        counts = queries.added_on(session, day, filters["include_out_of_scope"])
        added_day = None if day == NO_DAY else day
        found |= {"added_day": added_day, "pending": counts.pending, "failed": counts.failed}
    return found


@private.get("/papers/facets", response_model=Facets)  # before /papers/{paper_id}
def facets(session: DbSession, user: CurrentUser, filters: Filters):
    """For a filter bar: how many papers the filters select, and the values of domain, topic,
    paper_type, innovation, category, source, year and collection (yours) with their numbers of
    papers under every filter but their own.
    """
    return queries.facets(session, user.id, **filters)


@private.get("/papers/added-days", response_model=list[AddedDay])  # before /papers/{paper_id}
def added_days(
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=365)] = 30,
    include_out_of_scope: bool = False,
):
    """The last arXiv days papers were added on, newest first, with how many: new, updated,
    not briefed yet, failed to be. Out-of-scope papers count only if included, as in the list.
    """
    return [day._asdict() for day in queries.added_days(session, limit, include_out_of_scope)]


@private.get("/papers/{paper_id}", response_model=PaperDetail)
def get_paper(paper_id: int, session: DbSession, user: CurrentUser):
    paper = queries.get_paper(session, paper_id)
    if paper is None:
        raise HTTPException(404, "Paper not found")
    reason = (queries.latest_innovation(session, paper_id) or {}).get("reason")
    return _out(session, user, [paper])[0] | {
        "report": queries.latest_report(session, paper_id),
        "innovation_reason": reason,
        "report_progress": report.progress(session, paper),
        "report_error": paper.report_error if paper.report_status == "report_failed" else None,
        "abstract_zh": translate.stored(session, paper_id, paper.abstract),
    }


class AbstractZh(BaseModel):
    abstract_zh: str


@private.post("/papers/{paper_id}/abstract-zh", response_model=AbstractZh)
def translate_abstract(paper_id: int, session: DbSession):
    """The abstract translated into the analysis language: the stored translation, or one made
    now (a few seconds) and stored (translate.translation).
    """
    session.commit()  # signed in: no connection held while the model writes, up to two minutes
    try:
        return {"abstract_zh": translate.translation(paper_id)}
    except chat.Refused as e:
        raise HTTPException(e.status, e.detail) from None


@private.put("/papers/{paper_id}/star")
def star(paper_id: int, session: DbSession, user: CurrentUser):
    """Star a paper for yourself; nobody else sees your stars."""
    if not queries.star(session, user.id, paper_id, True):
        raise HTTPException(404, "Paper not found")
    return {"starred": True}


@private.delete("/papers/{paper_id}/star")
def unstar(paper_id: int, session: DbSession, user: CurrentUser):
    if not queries.star(session, user.id, paper_id, False):
        raise HTTPException(404, "Paper not found")
    return {"starred": False}


@private.get("/collections", response_model=list[CollectionOut])
def list_collections(session: DbSession, user: CurrentUser):
    """Your collections, oldest first, with their numbers of papers. Nobody else sees them."""
    rows = queries.collections(session, user.id)
    return [{"id": c.id, "name": c.name, "papers": n} for c, n in rows]


@private.post("/collections", response_model=CollectionOut, status_code=201)
def add_collection(body: CollectionIn, session: DbSession, user: CurrentUser):
    try:
        collection = queries.add_collection(session, user.id, body.name)
    except queries.NameTaken:
        raise HTTPException(409, "You have a collection of that name") from None
    return {"id": collection.id, "name": collection.name, "papers": 0}


@private.patch("/collections/{collection_id}", response_model=CollectionOut)
def rename_collection(
    collection_id: int, body: CollectionIn, session: DbSession, user: CurrentUser
):
    try:
        found = queries.rename_collection(session, user.id, collection_id, body.name)
    except queries.NameTaken:
        raise HTTPException(409, "You have a collection of that name") from None
    if found is None:
        raise HTTPException(404, "Collection not found")
    collection, papers = found
    return {"id": collection.id, "name": collection.name, "papers": papers}


@private.delete("/collections/{collection_id}")
def delete_collection(collection_id: int, session: DbSession, user: CurrentUser):
    """Its papers stay starred."""
    if not queries.delete_collection(session, user.id, collection_id):
        raise HTTPException(404, "Collection not found")
    return {"status": "ok"}


@private.put("/collections/{collection_id}/papers/{paper_id}")
def collect(collection_id: int, paper_id: int, session: DbSession, user: CurrentUser):
    """Put a paper in your collection, starring it if it is not yet. Returns the ids of your
    collections it is in.
    """
    collections = queries.collect(session, user.id, collection_id, paper_id, True)
    if collections is None:
        raise HTTPException(404, "Collection or paper not found")
    return {"collections": collections}


@private.delete("/collections/{collection_id}/papers/{paper_id}")
def uncollect(collection_id: int, paper_id: int, session: DbSession, user: CurrentUser):
    """Take a paper out of your collection; it stays starred."""
    collections = queries.collect(session, user.id, collection_id, paper_id, False)
    if collections is None:
        raise HTTPException(404, "Collection or paper not found")
    return {"collections": collections}


NO_PAGE = {"content": "", "updated_at": None}  # no free block


def _need_content(content: str, quote: str | None):
    if not content and not quote:
        raise HTTPException(422, "A note without a quote needs content")


@private.get("/papers/{paper_id}/notes", response_model=PaperNotes)
def list_notes(paper_id: int, session: DbSession, user: CurrentUser):
    """Your own notes on the paper, newest first, and your free block on it. Nobody else's are
    ever shown.
    """
    if session.get(Paper, paper_id) is None:
        raise HTTPException(404, "Paper not found")
    _, rows = queries.list_notes(session, user.id, paper_id, page_size=1000)  # one paper's
    page = next((note for note, _ in rows if note.kind == "page"), NO_PAGE)
    return {"page": page, "notes": [note for note, _ in rows if note.kind == "note"]}


@private.put("/papers/{paper_id}/notes/page", response_model=PageOut)
def set_page(paper_id: int, page: PageIn, session: DbSession, user: CurrentUser):
    """Your free block on the paper, saved whole (autosave); blank text removes it."""
    if session.get(Paper, paper_id) is None:
        raise HTTPException(404, "Paper not found")
    return queries.set_page(session, user.id, paper_id, page.content) or NO_PAGE


@private.post("/papers/{paper_id}/notes", response_model=NoteOut, status_code=201)
def add_note(paper_id: int, note: NoteIn, session: DbSession, user: CurrentUser):
    """A note, or an excerpt: a quote from the paper with where it is, and a comment that may be
    empty.
    """
    _need_content(note.content, note.quote)
    anchor = note.anchor and note.anchor.model_dump()
    quote = note.quote or None
    created = queries.add_note(session, user.id, note.content, paper_id, quote, anchor)
    if created is None:
        raise HTTPException(404, "Paper not found")
    return created


@private.patch("/notes/{note_id}", response_model=NoteOut)
def edit_note(note_id: int, edit: NoteEdit, session: DbSession, user: CurrentUser):
    """A note's new content; the free block is set with PUT /api/papers/{id}/notes/page."""
    note = session.get(Note, note_id)
    if note is None or note.user_id != user.id:
        raise HTTPException(404, "Note not found")
    if note.kind == "page":
        raise HTTPException(422, "The free block is set with PUT /api/papers/{id}/notes/page")
    _need_content(edit.content, note.quote)
    note.content = edit.content
    session.flush()
    session.refresh(note)  # updated_at comes from the database
    return note


@private.get("/notes", response_model=NotePage)
def my_notes(
    session: DbSession,
    user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """All your own notes, newest first (by when they were written), free blocks too, each with
    the paper it is on. Nobody else's are ever shown.
    """
    total, rows = queries.list_notes(session, user.id, page=page, page_size=page_size)
    items = [
        NoteOut.model_validate(note).model_dump() | {"kind": note.kind, "paper_title": t}
        for note, t in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@private.delete("/notes/{note_id}")
def delete_note(note_id: int, session: DbSession, user: CurrentUser):
    if not queries.delete_note(session, user.id, note_id):
        raise HTTPException(404, "Note not found")
    return {"status": "ok"}


@private.get("/papers/{paper_id}/chat", response_model=Chat)
def get_chat(paper_id: int, session: DbSession, user: CurrentUser):
    """Your conversation with the agent about the paper, oldest first. Nobody else's is shown."""
    if session.get(Paper, paper_id) is None:
        raise HTTPException(404, "Paper not found")
    return {"messages": chat.messages(session, user.id, paper_id)}


@private.post("/papers/{paper_id}/chat")
def ask(paper_id: int, question: ChatIn, user: CurrentUser):
    """Ask the agent about the paper. The answer comes as server-sent events (chat.ask): `delta`
    {"text"} while it is written, then `done` {"user", "assistant"} or `error` {"detail"}.
    """
    try:
        events = chat.ask(user.id, paper_id, question.content, question.quote or None)
    except chat.Refused as e:
        raise HTTPException(e.status, e.detail) from None
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(events, media_type="text/event-stream", headers=headers)


@private.delete("/papers/{paper_id}/chat", status_code=204)
def clear_chat(paper_id: int, session: DbSession, user: CurrentUser):
    if session.get(Paper, paper_id) is None:
        raise HTTPException(404, "Paper not found")
    chat.clear(session, user.id, paper_id)
    return Response(status_code=204)


@private.get("/papers/{paper_id}/pdf")
def paper_pdf(paper_id: int, request: Request, session: DbSession):
    """The paper's PDF, from the link stored for it (never a URL from the request), or arXiv's
    when that gives none (report.fetch_paper_pdf): downloaded into memory under the arXiv lock
    and rate limit, the lock let go, then sent. So the lock is held only while downloading,
    however slow the browser or whenever it goes away. A 502 means no PDF could be had; the
    stored link may still open in a browser (a site's bot check that only a person passes).
    """
    paper = session.get(Paper, paper_id)
    if paper is None or not paper.pdf_url:
        raise HTTPException(404, "No PDF for this paper")
    # The link is versioned, so it names fixed bytes. Browsers keep the PDF for a day, then
    # revalidate: an unchanged link answers 304 without asking arXiv.
    etag = '"' + hashlib.sha256(paper.pdf_url.encode()).hexdigest()[:32] + '"'
    headers = {
        "Cache-Control": "private, max-age=86400",
        "ETag": etag,
        "Content-Disposition": f'inline; filename="{paper.pdf_url.rsplit("/", 1)[-1]}.pdf"',
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    try:
        _, pdf = report.fetch_paper_pdf(paper)  # waits out the request interval before returning
    except (OSError, ValueError) as e:  # ValueError: not a PDF, or larger than arxiv.MAX_PDF
        raise HTTPException(502, f"Could not fetch the PDF: {e}") from None
    return Response(pdf, media_type="application/pdf", headers=headers)


@private.get("/papers/{paper_id}/figures/{figure_id}")
def figure(paper_id: int, figure_id: int, session: DbSession):
    """A figure or table cropped from the paper, as its report cites it (PNG). A figure never
    changes (a redone report gets new ones), so browsers keep it.
    """
    image = queries.figure_image(session, paper_id, figure_id)
    if image is None:
        raise HTTPException(404, "Figure not found")
    headers = {"Cache-Control": "private, max-age=31536000, immutable"}
    return Response(image, media_type="image/png", headers=headers)


@private.post("/papers/{paper_id}/report", status_code=202)
def request_report(paper_id: int, force: bool = False):
    """Queue a full report. Re-running a finished one needs force=true."""
    with Session(engine()) as session, session.begin():  # committed before the job exists
        status, enqueue = report.request(session, paper_id, force)
    if status is None:
        raise HTTPException(404, "Paper not found")
    if status == "no_pdf":
        raise HTTPException(422, "Paper has no PDF link")
    if status == "reported":
        raise HTTPException(409, "Paper already has a report; pass force=true to redo it")
    if enqueue:
        jobs.enqueue_reports([paper_id])
    return {"report_status": status}


@private.post("/papers/{paper_id}/tags", response_model=list[TagOut])
def add_tag(paper_id: int, tag: TagIn, session: DbSession, user: CurrentUser):
    """Manual tags are kept: once a paper's tags are edited, AI tagging leaves them alone."""
    try:
        tags = queries.add_tag(session, paper_id, tag.kind, tag.value, user.id)
    except queries.NotInVocabulary as e:
        raise HTTPException(422, str(e)) from None
    if tags is None:
        raise HTTPException(404, "Paper not found")
    return tags


@private.delete("/papers/{paper_id}/tags/{tag_id}", response_model=list[TagOut])
def delete_tag(paper_id: int, tag_id: int, session: DbSession):
    tags = queries.delete_tag(session, paper_id, tag_id)
    if tags is None:
        raise HTTPException(404, "Tag not found")
    return tags


@private.post("/papers/{paper_id}/topics", response_model=list[TopicRef])
def add_topic(paper_id: int, topic: TopicIn, session: DbSession, user: CurrentUser):
    """Like manual tags, manual topics are kept: AI tagging leaves the paper alone after."""
    try:
        linked = queries.add_topic(session, paper_id, topic.topic_id, user.id)
    except queries.NotInVocabulary as e:
        raise HTTPException(422, str(e)) from None
    if linked is None:
        raise HTTPException(404, "Paper not found")
    return linked


@private.delete("/papers/{paper_id}/topics/{topic_id}", response_model=list[TopicRef])
def delete_topic(paper_id: int, topic_id: int, session: DbSession):
    linked = queries.delete_topic(session, paper_id, topic_id)
    if linked is None:
        raise HTTPException(404, "Topic not linked to this paper")
    return linked


@private.put("/papers/{paper_id}/type")
def set_paper_type(paper_id: int, body: PaperTypeIn, session: DbSession):
    """Kept like manual tags: AI tagging leaves the paper alone after."""
    if not queries.set_paper_type(session, paper_id, body.paper_type):
        raise HTTPException(404, "Paper not found")
    return {"paper_type": body.paper_type}


@private.get("/admin/topics", response_model=list[CreatedTopic])
def created_topics(session: DbSession, admin: AdminUser):
    """Topics briefs created (not from the starting vocabulary), newest first."""
    rows = queries.created_topics(session)
    return [
        CreatedTopic.model_validate(topic).model_copy(update={"papers": n}) for topic, n in rows
    ]


@private.post("/admin/topics/{topic_id}/merge", response_model=MergeOut)
def merge_topic(topic_id: int, body: MergeIn, session: DbSession, admin: AdminUser):
    """Merge a created topic into the topic it duplicates: its papers and names move over."""
    try:
        return topics.merge(session, topic_id, body.into, admin.username, body.reason)
    except topics.MergeRefused as e:
        raise HTTPException(422, str(e)) from None


@private.get("/admin/topic-merges", response_model=list[MergeOut])
def topic_merges(session: DbSession, admin: AdminUser):
    """Every merge, newest first."""
    return queries.topic_merges(session)


@private.get("/tags", response_model=list[TagCount])
def tag_counts(session: DbSession, kind: TagKind | None = None):
    """Every tag in use with its number of papers, for filter bars."""
    return [{"kind": k, "value": v, "papers": n} for k, v, n in queries.tag_counts(session, kind)]


class Frontend(StaticFiles):
    """The single-page app: its files as they are, index.html for any other path so the app's
    own routes work on reload. /api and /mcp paths never fall through to the page.
    """

    async def get_response(self, path: str, scope) -> Response:
        if path.split(os.sep)[0] in ("api", "mcp"):
            raise HTTPException(404)
        hashed = path.startswith("assets" + os.sep)  # file names carry a content hash
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as e:  # what StaticFiles raises
            if e.status_code != 404 or hashed:  # a missing asset is a 404, not the page
                raise
            response = await super().get_response("index.html", scope)
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if hashed else "no-cache"
        )
        return response


app.include_router(public)
app.include_router(private)
app.add_route("/mcp", mcp_server.app)  # exactly /mcp; a Mount would serve /mcp/ only
if settings().frontend_dir.is_dir():
    app.mount("/", Frontend(directory=settings().frontend_dir))  # last: after every API route
