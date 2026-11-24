"""Tier-1 briefs: a batch of abstracts goes to pi (no tools) and comes back as 1-2 domains,
1-3 topics from the vocabulary (or one new topic), a paper type, keywords and a one-paragraph
summary per paper.
"""

import json
import logging
import re
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from . import agent, pi, topics
from .config import OUT_OF_SCOPE, config, topic_key
from .db import Analysis, Paper, PaperTopic, Tag, Topic, engine

log = logging.getLogger(__name__)

PI_TIMEOUT = 300  # one batch takes ~25 s
JOB_TIMEOUT = 2 * PI_TIMEOUT + pi.BULK_MAX_WAIT + 120  # an attempt, its retry, a busy provider
STALE_CLAIM = timedelta(hours=1)  # a claim this old belongs to a dead worker: take it again
PROMPT = "Return the JSON object now."
GIVE_UP = 3  # papers of a batch invalid on their own: give up on the rest of it
# A JSON escape, or a backslash that starts none (LaTeX such as \alpha copied from an abstract).
ESCAPE = re.compile(r'\\(["\\/bfnrt]|u[0-9a-fA-F]{4})?')
KINDS = ("domain", "keyword")  # the tag kinds a brief sets
PAPER_TYPES = {
    "method": "方法",
    "dataset": "数据集",
    "benchmark": "基准",
    "survey": "综述",
    "theory": "理论",
    "empirical": "实证分析",
    "system": "系统",
}


class BriefInvalid(Exception):
    excerpt: str | None = None  # the answer around where it stops being JSON


def claim(session: Session, limit: int) -> list[int]:
    """Mark up to `limit` papers as being briefed and return their ids, newest first.

    One conditional UPDATE whose candidates are locked with SKIP LOCKED, so concurrent workers
    never claim the same paper.
    """
    stale = (Paper.status == "briefing") & (Paper.claimed_at < func.now() - STALE_CLAIM)
    candidates = (
        select(Paper.id)
        .where((Paper.status == "new") | stale)
        .order_by(Paper.id.desc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return session.scalars(
        update(Paper)
        .where(Paper.id.in_(candidates.scalar_subquery()))
        .values(status="briefing", claimed_at=func.now())
        .returning(Paper.id)
        .execution_options(synchronize_session=False)
    ).all()


def system_prompt(template: str, vocabulary: list[Topic]) -> str:
    """The prompt (agent.prompt) with the whole vocabulary: created topics and every alias
    included. agent.PLACEHOLDERS lists what is filled in.
    """
    return template.replace("{{VOCABULARY}}", topics.vocabulary_text(vocabulary)).replace(
        "{{LANGUAGE}}", config().analysis.language
    )


def batch_input(papers: list[Paper]) -> str:
    items = "\n\n".join(
        f'<paper id="{p.id}">\nTitle: {p.title}\nAbstract: {p.abstract}\n</paper>' for p in papers
    )
    return f"Index the following {len(papers)} papers.\n\n{items}\n"


def _strings(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _new_topic(paper_id: str, new) -> dict | None:
    """new_topic if it is complete and under a domain (not out-of-scope), else None (logged)."""
    if new is None:
        return None
    fields = ("name", "definition", "domain")
    if not (
        isinstance(new, dict)
        and all(isinstance(new.get(f), str) and new[f].strip() for f in fields)
    ):
        log.warning("paper %s: dropped incomplete new_topic %r", paper_id, new)
        return None
    if new["domain"] not in config().domains:
        log.warning("paper %s: dropped new_topic %r under %r", paper_id, new["name"], new["domain"])
        return None
    return {f: new[f].strip() for f in fields} | {"zh": str(new.get("zh") or "").strip()}


def _check(paper_id: str, item: dict, known: dict[str, tuple[str, str]]):
    """Check one paper's item and normalize it in place (see parse)."""
    domains = item.get("domains")
    if not _strings(domains):
        raise BriefInvalid(f"paper {paper_id}: domains {domains!r} is not a list")
    kept = []
    for domain in domains:
        if domain in config().vocabulary:
            kept.append(domain)
        elif topic := known.get(topic_key(domain)):  # seen: a topic written as a domain
            log.warning("paper %s: domain %r read as %s", paper_id, domain, topic[1])
            kept.append(topic[1])
        else:
            log.warning("paper %s: dropped domain off-vocabulary: %r", paper_id, domain)
    kept = list(dict.fromkeys(kept))
    if len(kept) > 1 and OUT_OF_SCOPE in kept:  # in a domain, so not out of scope
        kept.remove(OUT_OF_SCOPE)
    item["domains"] = kept[:2]
    if not kept:
        raise BriefInvalid(f"paper {paper_id}: no domains from the vocabulary")
    kind = item.get("type")
    item["type"] = kind.strip().lower() if isinstance(kind, str) else kind
    if item["type"] not in PAPER_TYPES:
        raise BriefInvalid(f"paper {paper_id}: type {kind!r}")
    if item["domains"] == [OUT_OF_SCOPE]:  # no topics for papers outside every domain
        item["topics"], item["new_topic"] = [], None
    else:
        names, new = item.get("topics"), item["new_topic"]
        if not _strings(names):
            raise BriefInvalid(f"paper {paper_id}: topics {names!r} is not a list")
        chosen = {}  # topic: its first spelling, in order
        for name in names:
            if topic := known.get(topic_key(name)):
                chosen.setdefault(topic, name.strip())
        if dropped := [n for n in names if topic_key(n) not in known]:
            log.warning("paper %s: dropped topics off-vocabulary: %s", paper_id, dropped)
        if new:
            chosen.setdefault(known[topic_key(new["name"])], new["name"])
        item["topics"] = list(chosen.values())[:3]
        if not chosen:
            raise BriefInvalid(f"paper {paper_id}: no topics from the vocabulary")
    summary = item.get("summary")
    if not (_strings(item.get("keywords")) and isinstance(summary, str) and summary.strip()):
        raise BriefInvalid(f"paper {paper_id}: keywords or summary missing")


def _loads(text: str):
    """json.loads; when that fails, again with every backslash that starts no JSON escape read
    as a backslash. Quotes left unescaped in a string are not guessed at.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(ESCAPE.sub(lambda m: m[0] if m[1] else r"\\", text))


def parse(text: str, ids: list[str], known: dict[str, tuple[str, str]]) -> dict[str, dict]:
    """Check a brief against its batch and the vocabulary (`known`: {topic_key of every topic
    name and alias: (the topic's name, its domain)}); returns {paper id: item}. Items are
    normalized: values outside the vocabulary dropped (and logged), a topic given as a domain
    read as its domain, at most 2 domains and 3 topics, out-of-scope
    only when alone and then without topics, an incomplete new_topic dropped, the new topic
    among the topics. A topic another paper of the batch creates counts as known. A paper left
    without a domain, a topic (unless out of scope) or a valid type, or without keywords or
    summary, makes the brief invalid.
    """
    try:
        # Tolerate code fences or chatter around the object.
        items = _loads(text[text.find("{") : text.rfind("}") + 1])["papers"]
        by_id = {str(item["id"]): item for item in items}
    except (ValueError, KeyError, TypeError) as e:
        invalid = BriefInvalid(f"not a JSON object with a papers list: {e!r}")
        if isinstance(e, json.JSONDecodeError):
            invalid.excerpt = e.doc[max(e.pos - 100, 0) : e.pos + 100]
        raise invalid from None
    if len(items) != len(ids) or by_id.keys() != set(ids):
        missing, extra = set(ids) - by_id.keys(), by_id.keys() - set(ids)
        raise BriefInvalid(f"{len(items)} items, missing {missing}, extra {extra}")
    created = {}  # topics the batch creates: known to every paper of it
    for paper_id, item in by_id.items():
        new = None if item.get("domains") == [OUT_OF_SCOPE] else item.get("new_topic")
        new = item["new_topic"] = _new_topic(paper_id, new)
        if new:
            created.setdefault(topic_key(new["name"]), (new["name"], new["domain"]))
    for paper_id, item in by_id.items():
        _check(paper_id, item, created | known)
    return by_id


def save(
    session: Session,
    papers: list[Paper],
    by_id: dict[str, dict],
    usage: dict,
    version: str,
    model: str,
):
    """Keep the parsed briefs, made by `model` with the prompt `version`. Topics are looked up
    by name now, under the vocabulary lock (a topic may have been merged since the prompt), and
    a new topic is created unless one with its name or alias exists by now.
    """
    topics.lock(session)
    known = topics.keys(session)
    for paper in papers:  # first, so every paper of the batch can link a topic one creates
        if not paper.tags_edited and (new := by_id[str(paper.id)]["new_topic"]):
            topics.create(session, known, new)
    share = {key: usage[key] // len(papers) for key in pi.USAGE_KEYS} | {"batch": len(papers)}
    for paper in papers:
        item = by_id[str(paper.id)]
        session.add(
            Analysis(
                paper_id=paper.id,
                level="brief",
                model=model,
                prompt_version=version,
                result=item,
                usage=share,
            )
        )
        paper.summary = item["summary"].strip()
        paper.status = "briefed"
        # ponytail: one flag per paper. Once a member edits any tag, AI tagging never touches
        # that paper's tags again. Track edits per tag if partial refreshes are ever wanted.
        if paper.tags_edited:
            continue
        paper.paper_type = item["type"]
        session.execute(
            delete(Tag).where(Tag.paper_id == paper.id, Tag.source == "ai", Tag.kind.in_(KINDS))
        )
        session.execute(
            delete(PaperTopic).where(PaperTopic.paper_id == paper.id, PaperTopic.source == "ai")
        )
        found = [known.get(topic_key(name)) for name in item["topics"]]
        for topic_id in dict.fromkeys(topic.id for topic in found if topic):
            session.add(PaperTopic(paper_id=paper.id, topic_id=topic_id, source="ai"))
        tags = [("domain", v) for v in item["domains"]]
        tags += [("keyword", v.strip()) for v in item["keywords"] if v.strip()]
        for kind, value in dict.fromkeys(tags):
            session.add(Tag(paper_id=paper.id, kind=kind, value=value, source="ai"))


def brief_batch(limit: int | None = None) -> dict:
    """Claim and brief one batch of new papers, retrying a failed attempt once. A batch whose
    answer is still invalid is briefed in halves, down to one paper, each part once, and each
    part is saved when it is briefed; a paper invalid on its own is marked brief_failed (not
    retried automatically). Once GIVE_UP papers failed on their own, every paper not briefed
    yet is marked brief_failed too (a model that cannot write the JSON: 9 calls for a batch of
    20; one paper that breaks every answer costs 10-12, two 20, three 26 at most). When the
    last call itself failed (timeout, relay down), the papers not briefed yet go back to new
    for the next enqueue and the job fails, so RQ records it.
    """
    analysis = config().analysis
    with Session(engine()) as session, session.begin():
        topics.sync(session)  # the configured starting topics, before a prompt shows them
        ids = claim(session, limit or analysis.brief_batch_size)
    if not ids:
        return {"papers": 0}
    with Session(engine()) as session:
        papers = session.scalars(select(Paper).where(Paper.id.in_(ids)).order_by(Paper.id)).all()
        vocabulary = session.scalars(select(Topic).order_by(Topic.id)).all()
        version, template = agent.prompt(session, "brief")
        endpoint = agent.model(session)
        system = system_prompt(template, vocabulary)
        known = {topic_key(n): (t.name, t.domain) for t in vocabulary for n in (t.name, *t.aliases)}
    model = endpoint["brief_model"]
    tokens, errors, briefed, failed = 0, [], [], []
    backoff = pi.Backoff.bulk()
    parts = [(papers, 2)]  # (papers, attempts) to brief, the next one last
    while parts:
        part, attempts = parts.pop()
        part_ids = [p.id for p in part]
        for _ in range(attempts):
            try:
                stdin = batch_input(part)
                result = pi.run(model, system, PROMPT, PI_TIMEOUT, stdin=stdin, endpoint=endpoint)
                tokens += result.usage["totalTokens"]
                by_id = parse(result.text, [str(i) for i in part_ids], known)
                break
            except (pi.PiError, pi.Incomplete, BriefInvalid) as e:
                log.warning("brief of %d papers failed: %s", len(part), e)
                errors.append(str(e))
                error = e
                backoff.failed(e)  # the provider busy: wait, before the next attempt or not
        else:
            if isinstance(error, pi.PiError):
                left = set(ids) - set(briefed)
                with Session(engine()) as session, session.begin():
                    session.execute(update(Paper).where(Paper.id.in_(left)).values(status="new"))
                raise error
            if len(part) > 1:
                half = len(part) // 2
                parts += [(part[half:], 1), (part[:half], 1)]
                continue
            why = str(error)
            if excerpt := getattr(error, "excerpt", None):
                why += f", around it: {excerpt!r}"
            log.warning("paper %d failed to brief on its own: %s", part_ids[0], why)
            failed += part_ids
            if len(failed) == GIVE_UP:
                failed = [i for i in ids if i not in briefed]
                log.warning(
                    "gave up on a batch of %d papers after %d failed on their own: "
                    "%d marked brief_failed",
                    len(ids),
                    GIVE_UP,
                    len(failed),
                )
                break
            continue
        with Session(engine()) as session, session.begin():
            topics.lock(session)  # before the paper rows (the lock order)
            # Row locks order this against a member editing the same paper's tags.
            locked = select(Paper).where(Paper.id.in_(part_ids)).with_for_update()
            save(session, session.scalars(locked).all(), by_id, result.usage, version, model)
        briefed += part_ids
    if failed:
        with Session(engine()) as session, session.begin():
            session.execute(update(Paper).where(Paper.id.in_(failed)).values(status="brief_failed"))
    log.info("briefed %d papers, %d failed, %d tokens", len(briefed), len(failed), tokens)
    status = "briefed" if briefed else "brief_failed"
    done = {"papers": len(ids), "status": status, "tokens": tokens}
    return done | ({"failed": failed, "errors": errors} if failed else {})


def redo(session: Session, limit: int | None = None) -> int:
    """Put briefed (or failed) papers without a brief from the current prompt (its text as
    edited on the web, if it was) back to new, newest first, at most `limit`; returns how many.
    The next briefs tag them again.
    """
    version, _ = agent.prompt(session, "brief")
    current = select(Analysis.id).where(
        Analysis.paper_id == Paper.id, Analysis.level == "brief", Analysis.prompt_version == version
    )
    outdated = (
        select(Paper.id)
        .where(Paper.status.in_(("briefed", "brief_failed")), ~current.exists())
        .order_by(Paper.id.desc())
        .limit(limit)
    )
    reset = update(Paper).where(Paper.id.in_(outdated.scalar_subquery())).values(status="new")
    return session.execute(reset.execution_options(synchronize_session=False)).rowcount
