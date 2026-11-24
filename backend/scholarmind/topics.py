"""Research topics, the vocabulary under each domain. The starting topics come from the config
and are synced in (added and updated, never deleted); briefs create more when nothing fits
(source "model"); a weekly review merges a created topic that turns out to duplicate another.

Every write to the vocabulary holds one advisory lock, so a name never gets two topics, not
even when two workers create it at once. Lock order: this lock before any paper row.
"""

import json
import logging

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from . import agent, pi
from .config import config, topic_key
from .db import Paper, PaperTopic, Topic, TopicMerge, engine

log = logging.getLogger(__name__)

LOCK = 7_301_004  # advisory lock key: writes to the vocabulary
REVIEW_TITLES = 5  # paper titles shown per created topic
PI_TIMEOUT = 300
REVIEW_TIMEOUT = 2 * PI_TIMEOUT + 120  # an attempt and its retry, plus the merges


class MergeRefused(Exception):
    pass


class ReviewInvalid(Exception):
    pass


def lock(session: Session):
    """Hold the vocabulary until the transaction ends. Take it before locking paper rows."""
    session.execute(select(func.pg_advisory_xact_lock(LOCK)))


def keys(session: Session) -> dict[str, Topic]:
    """{topic_key of every name and alias: its topic}."""
    known = {}
    for topic in session.scalars(select(Topic).order_by(Topic.id)):
        for name in (topic.name, *topic.aliases):
            known.setdefault(topic_key(name), topic)
    return known


def find(session: Session, name: str) -> Topic | None:
    """The topic with this name or alias (compared by topic_key)."""
    return keys(session).get(topic_key(name))


def _new_names(names, known: dict[str, Topic]) -> list[str]:
    """Those of `names` no topic has yet, one per key."""
    fresh = {}
    for name in names:
        if topic_key(name) not in known:
            fresh.setdefault(topic_key(name), name.strip())
    return list(fresh.values())


def sync(session: Session) -> tuple[int, int]:
    """Bring the config's starting topics into the database; returns (added, updated). A
    topic is found by its name, or renamed when one of its aliases is a topic's name. Its
    domain, name, zh and definition follow the config, its aliases only grow, and a topic the
    config no longer lists stays. A name that is another topic's alias is skipped.
    """
    lock(session)
    known = keys(session)
    added = updated = 0
    for domain, entry in config().vocabulary.items():
        for seed in entry.topics:
            topic = known.get(topic_key(seed.name))
            if topic is None:  # renamed: the old name is among the aliases now
                old = [(known.get(topic_key(a)), topic_key(a)) for a in seed.aliases]
                topic = next((t for t, k in old if t and topic_key(t.name) == k), None)
            elif topic_key(topic.name) != topic_key(seed.name):
                log.warning("vocabulary: %r is an alias of %r, not added", seed.name, topic.name)
                continue
            if topic is None:
                topic = Topic(domain=domain, name=seed.name, aliases=[], source="seed")
                session.add(topic)
                added += 1
            values = {
                "domain": domain,
                "name": seed.name,
                "zh": seed.zh or topic.zh or "",
                "definition": seed.definition or topic.definition or "",
                "source": "seed",
            }
            own, aliases = {topic_key(n) for n in (seed.name, *topic.aliases)}, []
            for alias in seed.aliases:  # those neither this topic nor another one has
                if topic_key(alias) not in own and known.get(topic_key(alias), topic) is topic:
                    own.add(topic_key(alias))
                    aliases.append(alias.strip())
            if topic.id is not None:
                if not aliases and all(getattr(topic, k) == v for k, v in values.items()):
                    continue
                updated += 1
            for k, v in values.items():
                setattr(topic, k, v)
            topic.aliases = [*topic.aliases, *aliases]
            session.flush()
            for name in (topic.name, *aliases):
                known.setdefault(topic_key(name), topic)
    return added, updated


def create(session: Session, known: dict[str, Topic], new: dict) -> Topic:
    """The topic named new["name"]: an existing one with that name or alias, else a new
    created topic. The caller holds lock() and passes keys(), which this keeps up to date.
    """
    if topic := known.get(topic_key(new["name"])):
        return topic
    topic = Topic(
        domain=new["domain"],
        name=new["name"].strip(),
        zh=(new.get("zh") or "").strip(),
        definition=new["definition"].strip(),
        aliases=[],
        source="model",
    )
    session.add(topic)
    session.flush()
    known[topic_key(topic.name)] = topic
    log.info("created topic %d %r under %s", topic.id, topic.name, topic.domain)
    return topic


def merge(session: Session, source_id: int, target_id: int, merged_by: str, reason: str):
    """Merge a created topic into another: its paper links move over (a paper the target has
    already keeps one), its name and aliases become the target's aliases, so naming it later
    lands on the target, and a TopicMerge records it. Starting topics never merge away.
    """
    lock(session)
    source, target = session.get(Topic, source_id), session.get(Topic, target_id)
    if source is None or target is None:
        raise MergeRefused(f"no topic {source_id if source is None else target_id}")
    if source.source != "model":
        raise MergeRefused(f"{source.name!r} is from the starting vocabulary")
    if source.id == target.id:
        raise MergeRefused("a topic cannot merge into itself")
    linked = select(PaperTopic.paper_id).where(PaperTopic.topic_id == target.id)
    moved = session.execute(
        update(PaperTopic)
        .where(PaperTopic.topic_id == source.id, PaperTopic.paper_id.not_in(linked))
        .values(topic_id=target.id)
        .execution_options(synchronize_session=False)
    ).rowcount
    session.execute(delete(PaperTopic).where(PaperTopic.topic_id == source.id))
    own = {topic_key(name): target for name in (target.name, *target.aliases)}
    target.aliases = [*target.aliases, *_new_names((source.name, *source.aliases), own)]
    record = TopicMerge(
        source_id=source.id,
        source={
            "domain": source.domain,
            "name": source.name,
            "zh": source.zh,
            "definition": source.definition,
            "aliases": source.aliases,
        },
        target_id=target.id,
        target_name=target.name,
        merged_by=merged_by,
        reason=reason,
        papers=moved,
    )
    session.add(record)
    session.delete(source)
    session.flush()
    log.info("merged topic %d %r into %d %r", source.id, source.name, target.id, target.name)
    return record


def paper_counts(session: Session) -> dict[int, int]:
    query = select(PaperTopic.topic_id, func.count()).group_by(PaperTopic.topic_id)
    return dict(session.execute(query).all())


def vocabulary_text(topics: list[Topic], counts: dict[int, int] | None = None) -> str:
    """The vocabulary for a prompt, domain by domain. With `counts` every topic shows its id,
    whether it was created and its number of papers.
    """
    lines = []
    for domain, entry in config().vocabulary.items():
        lines.append(f"\n{domain} ({entry.zh})")
        for t in (t for t in topics if t.domain == domain):
            about = "; ".join(
                part
                for part in (t.zh, t.aliases and "aliases: " + ", ".join(t.aliases), t.definition)
                if part
            )
            line = f"- {t.name}" + (f" ({about})" if about else "")
            if counts is not None:
                created = " [created]" if t.source == "model" else ""
                line = f"- [{t.id}] {line[2:]}{created}: {counts.get(t.id, 0)} papers"
            lines.append(line)
    return "\n".join(lines).strip()


# --- the weekly review -----------------------------------------------------------------------


def review_input(session: Session) -> tuple[str, set[int], set[int]]:
    """(the review's input, ids of created topics, ids of all topics)."""
    topics = session.scalars(select(Topic).order_by(Topic.id)).all()
    counts = paper_counts(session)
    created = [t for t in topics if t.source == "model"]
    blocks = []
    for t in created:
        titles = session.scalars(
            select(Paper.title)
            .join(PaperTopic, PaperTopic.paper_id == Paper.id)
            .where(PaperTopic.topic_id == t.id)
            .order_by(Paper.id.desc())
            .limit(REVIEW_TITLES)
        ).all()
        blocks.append(
            f"[{t.id}] {t.name}, under {t.domain}\n"
            f"Definition: {t.definition or t.zh}\n"
            f"Aliases: {', '.join(t.aliases) or 'none'}\n"
            f"Papers ({counts.get(t.id, 0)}):\n" + "\n".join(f"- {title}" for title in titles)
        )
    text = (
        f"<vocabulary>\n{vocabulary_text(topics, counts)}\n</vocabulary>\n\n"
        f"<created>\n" + "\n\n".join(blocks) + "\n</created>\n"
    )
    return text, {t.id for t in created}, {t.id for t in topics}


def parse_review(text: str, created: set[int], existing: set[int]) -> tuple[list, list]:
    """(merges as (id, duplicate_of, reason), rejected entries with why). An entry is rejected
    unless its id is a created topic and duplicate_of is null or another existing topic.
    """
    try:
        items = json.loads(text[text.find("{") : text.rfind("}") + 1])["topics"]
        if not isinstance(items, list):
            raise TypeError("topics is not a list")
    except (ValueError, KeyError, TypeError) as e:
        raise ReviewInvalid(f"not a JSON object with a topics list: {e!r}") from None
    merges, rejected = [], []
    for item in items:
        if not isinstance(item, dict):
            rejected.append((item, "not an object"))
            continue
        topic, target, reason = item.get("id"), item.get("duplicate_of"), item.get("reason")
        if topic not in created or isinstance(topic, bool):
            rejected.append((item, "id is not a created topic"))
        elif target is None:
            continue
        elif target not in existing or isinstance(target, bool) or target == topic:
            rejected.append((item, "duplicate_of is not another existing topic"))
        else:
            merges.append((topic, target, reason if isinstance(reason, str) else ""))
    return merges, rejected


def review() -> dict:
    """The weekly job: the model looks at every created topic and says which ones duplicate
    an existing topic; those are merged. A failed attempt (invalid answer or failed call) is
    retried once, then the job fails.
    """
    with Session(engine()) as session, session.begin():
        sync(session)
    with Session(engine()) as session:
        stdin, created, existing = review_input(session)
        _, system = agent.prompt(session, "topic-review")
        endpoint = agent.model(session)
    if not created:
        return {"created": 0, "merged": [], "rejected": []}
    model, tokens = endpoint["brief_model"], 0
    for attempt in range(2):
        try:
            result = pi.run(
                model,
                system,
                "Return the JSON object now.",
                PI_TIMEOUT,
                stdin=stdin,
                endpoint=endpoint,
            )
            tokens += result.usage["totalTokens"]
            merges, rejected = parse_review(result.text, created, existing)
            break
        except (pi.PiError, pi.Incomplete, ReviewInvalid) as e:
            log.warning("topic review attempt %d failed: %s", attempt + 1, e)
            error = e
    else:
        raise error
    merged, moved = [], {}  # moved: topics merged in this run, to their target
    for topic, target, reason in merges:
        while target in moved:  # its target was merged too: follow it
            target = moved[target]
        try:
            with Session(engine()) as session, session.begin():
                record = merge(session, topic, target, "review", reason)
                merged.append((topic, record.source["name"], target, record.target_name, reason))
            moved[topic] = target
        except MergeRefused as e:
            rejected.append(({"id": topic, "duplicate_of": target, "reason": reason}, str(e)))
    for item, why in rejected:
        log.warning("topic review: rejected %s: %s", item, why)
    return {"created": len(created), "merged": merged, "rejected": rejected, "tokens": tokens}
