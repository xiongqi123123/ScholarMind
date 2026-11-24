import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from scholarmind import agent, brief, jobs, pi, topics
from scholarmind.config import config, topic_key
from scholarmind.db import Analysis, Paper, PaperTopic, Tag, Topic, User, engine

# {topic_key of every name and alias: (name, domain)} of the built-in vocabulary (the example's)
KNOWN = {
    topic_key(name): (t.name, domain)
    for domain, entry in config().vocabulary.items()
    for t in entry.topics
    for name in (t.name, *t.aliases)
}


def _item(paper_id, **fields):
    item = {
        "id": paper_id,
        "domains": ["computer-vision"],
        "topics": ["3D Gaussian Splatting"],
        "new_topic": None,
        "type": "method",
        "keywords": ["3DGS", "real-time rendering", "densification"],
        "summary": "摘要。",
    }
    return item | fields


def _parse(*items):
    text = json.dumps({"papers": list(items)})
    return brief.parse(text, [str(item["id"]) for item in items], KNOWN)


def test_parse_accepts_and_normalizes(caplog):
    caplog.set_level(logging.WARNING)
    new = {"name": "Polarization Imaging", "zh": "偏振成像", "domain": "computer-vision"}
    by_id = _parse(
        _item(1),
        # Aliases count, spellings are kept, the same topic twice is once, a 4th is cut.
        _item(
            2,
            domains=["robotics", "embodied-ai", "computer-vision"],
            topics=["3dgs", "VLA", "vision language action model", "Manipulation", "Locomotion"],
            type=" Dataset ",
        ),
        # A new topic, also put in topics by itself; off-vocabulary names are dropped.
        _item(
            3,
            topics=["Made Up Topic"],
            new_topic=new | {"definition": "  Vision with polarization."},
        ),
        # Out of scope: alone, and never with topics.
        _item(4, domains=["out-of-scope"], topics=["Manipulation"], new_topic=new),
        # Out of scope next to a domain is dropped.
        _item(5, domains=["out-of-scope", "robotics"], topics=["Manipulation"], type="survey"),
        # An incomplete new topic is dropped, the paper keeps its listed topic.
        _item(6, new_topic={"name": "Foo", "domain": "computer-vision"}),
        _item(7, new_topic=new | {"definition": "x", "domain": "out-of-scope"}),
        _item(8, topics=["polarization imaging"]),  # created by paper 3 of the same batch
        # Seen in the real run: a topic written as a domain, read as the topic's domain.
        _item(9, domains=["remote-sensing"], topics=["Remote Sensing"]),
    )
    assert by_id["1"]["topics"] == ["3D Gaussian Splatting"] and by_id["1"]["new_topic"] is None
    assert by_id["2"]["domains"] == ["robotics", "embodied-ai"]
    assert by_id["2"]["topics"] == ["3dgs", "VLA", "Manipulation"]
    assert by_id["2"]["type"] == "dataset"
    assert by_id["3"]["topics"] == ["Polarization Imaging"]
    assert by_id["3"]["new_topic"] == new | {"definition": "Vision with polarization."}
    assert "dropped topics off-vocabulary: ['Made Up Topic']" in caplog.text
    assert (by_id["4"]["domains"], by_id["4"]["topics"], by_id["4"]["new_topic"]) == (
        ["out-of-scope"],
        [],
        None,
    )
    assert by_id["5"]["domains"] == ["robotics"]
    assert by_id["6"]["new_topic"] is None and by_id["6"]["topics"] == ["3D Gaussian Splatting"]
    assert by_id["7"]["new_topic"] is None
    assert "dropped new_topic 'Polarization Imaging' under 'out-of-scope'" in caplog.text
    assert by_id["8"]["topics"] == ["polarization imaging"] and by_id["8"]["new_topic"] is None
    assert by_id["9"]["domains"] == ["computer-vision"]
    assert "domain 'remote-sensing' read as computer-vision" in caplog.text


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"domains": ["biology"]}, "no domains"),
        ({"domains": "computer-vision"}, "domains"),
        ({"type": "position"}, "type 'position'"),
        ({"type": None}, "type None"),
        ({"topics": ["Made Up Topic"]}, "no topics"),
        ({"topics": None}, "topics None"),
        ({"topics": [], "new_topic": {"name": "X", "domain": "robotics"}}, "no topics"),
        ({"keywords": "a, b"}, "keywords"),
        ({"summary": " "}, "summary"),
    ],
)
def test_parse_rejects_bad_items(fields, error):
    with pytest.raises(brief.BriefInvalid, match=re.escape(error)):
        _parse(_item(1), _item(2, **fields))


def test_parse_rejects_bad_batches():
    text = json.dumps({"papers": [_item(1), _item(2)]})
    assert len(brief.parse(f"```json\n{text}\n```", ["1", "2"], KNOWN)) == 2  # fences are fine
    with pytest.raises(brief.BriefInvalid, match="JSON"):
        brief.parse("I cannot help with that.", ["1"], KNOWN)
    with pytest.raises(brief.BriefInvalid, match="missing {'3'}"):
        brief.parse(text, ["1", "2", "3"], KNOWN)
    with pytest.raises(brief.BriefInvalid, match="JSON"):
        brief.parse('{"papers": ["1"]}', ["1"], KNOWN)
    broken = text.replace('"summary": "', '"summary": "a "quoted" term ', 1)
    with pytest.raises(brief.BriefInvalid, match="delimiter") as raised:
        brief.parse(broken, ["1", "2"], KNOWN)
    excerpt = raised.value.excerpt  # what the model wrote around the error, for the log
    assert len(excerpt) == 200 and '"summary": "a "quoted" term' in excerpt


def test_system_prompt_shows_the_whole_vocabulary(vocabulary, session):
    session.add(
        Topic(
            domain="robotics",
            name="Micro Robotics",
            definition="Robots built from compliant materials.",
            aliases=["micro robots"],
            source="model",
        )
    )
    session.commit()
    vocabulary = session.scalars(select(Topic).order_by(Topic.id)).all()
    prompt = brief.system_prompt(agent.default_prompt("brief"), vocabulary)
    assert "{{" not in prompt and "Simplified Chinese" in prompt
    gaussians = "- 3D Gaussian Splatting (三维高斯泼溅; aliases: 3DGS, Gaussian Splatting; "
    assert gaussians + "Representing and rendering scenes with explicit 3D Gaussian" in prompt
    assert (
        "- Micro Robotics (aliases: micro robots; Robots built from compliant materials.)" in prompt
    )
    assert "\nout-of-scope (范围外" in prompt
    robotics = prompt[prompt.index("\nrobotics (") : prompt.index("\nembodied-ai (")]
    assert "Micro Robotics" in robotics and robotics.count("\n- ") == 20


def _add_papers(n):
    with Session(engine()) as session, session.begin():
        session.add_all(Paper(title=f"Paper {i}", title_norm=f"paper {i}") for i in range(n))


def _claim(limit):
    with Session(engine()) as session, session.begin():
        return brief.claim(session, limit)


def test_claim_never_hands_out_a_paper_twice(session):
    _add_papers(60)
    with ThreadPoolExecutor(6) as pool:
        claims = list(pool.map(_claim, [10] * 8))
    claimed = [i for ids in claims for i in ids]
    assert sorted(claimed) == list(range(1, 61))  # each paper exactly once
    assert _claim(10) == []


def test_claim_takes_back_stale_claims_only(session):
    _add_papers(4)
    now = datetime.now(UTC)
    with Session(engine()) as s, s.begin():
        s.execute(update(Paper).where(Paper.id == 1).values(status="briefed"))
        s.execute(update(Paper).where(Paper.id == 2).values(status="brief_failed"))
        s.execute(
            update(Paper).where(Paper.id == 3).values(status="briefing", claimed_at=now)
        )  # another worker is on it
        s.execute(
            update(Paper)
            .where(Paper.id == 4)
            .values(status="briefing", claimed_at=now - timedelta(hours=2))
        )  # that worker died
    assert _claim(10) == [4]


def _fake_pi(*outputs):
    """Replace pi.run: each call answers with the next output (raised if it is an exception);
    "valid" builds a good brief.
    """
    calls = []

    def run(model, system_prompt, prompt, timeout, *, stdin="", **_):
        calls.append(stdin)
        output = outputs[len(calls) - 1]
        if isinstance(output, Exception):
            raise output
        if output == "valid":
            ids = re.findall(r'<paper id="(\d+)">', stdin)
            item = {"domains": ["robotics"], "topics": ["Manipulation", "grasping"]}
            item |= {"keywords": ["grasping", " diffusion policy "], "summary": "摘要。"}
            output = json.dumps({"papers": [_item(int(i), **item) for i in ids]})
        return pi.Result(output, {key: 100 for key in pi.USAGE_KEYS} | {"turns": 1})

    return run, calls


def test_brief_batch_retries_once_then_saves(session, monkeypatch):
    _add_papers(3)
    run, calls = _fake_pi('{"papers": []}', "valid")
    monkeypatch.setattr(pi, "run", run)
    assert brief.brief_batch() == {"papers": 3, "status": "briefed", "tokens": 200}
    assert len(calls) == 2 and "Index the following 3 papers." in calls[0]
    manipulation = session.scalar(select(Topic.id).where(Topic.name == "Manipulation"))
    assert manipulation  # the batch synced the starting vocabulary first
    for paper in session.scalars(select(Paper)):
        assert paper.status == "briefed" and paper.summary == "摘要。"
        assert paper.paper_type == "method"
        values = sorted((t.kind, t.value, t.source) for t in paper.tags)
        assert values == [
            ("domain", "robotics", "ai"),
            ("keyword", "diffusion policy", "ai"),
            ("keyword", "grasping", "ai"),
        ]
        # "grasping" is an alias of Manipulation: one link
        assert [(t.topic_id, t.source) for t in paper.topics] == [(manipulation, "ai")]
    analysis = session.scalars(select(Analysis)).first()
    assert (analysis.level, analysis.model, analysis.prompt_version) == (
        "brief",
        config().analysis.brief_model,
        "brief.v2",
    )
    assert analysis.usage["totalTokens"] == 33 and analysis.usage["batch"] == 3
    assert brief.brief_batch() == {"papers": 0}  # nothing left: briefed papers are not redone


def test_brief_batch_gives_up_on_invalid_answers(session, monkeypatch):
    _add_papers(2)
    # The batch, its retry, then each paper alone
    run, calls = _fake_pi("garbage", '{"papers": [{"id": 1}]}', "garbage", "garbage")
    monkeypatch.setattr(pi, "run", run)
    result = brief.brief_batch()
    assert result["status"] == "brief_failed" and len(result["errors"]) == 4
    assert result["failed"] == [1, 2]
    assert brief.brief_batch() == {"papers": 0} and len(calls) == 4  # not retried again
    statuses = session.scalars(select(Paper.status)).all()
    assert statuses == ["brief_failed", "brief_failed"]
    assert session.scalar(select(func.count()).select_from(Analysis)) == 0
    assert session.scalar(select(func.count()).select_from(Tag)) == 0


@pytest.mark.parametrize(
    ("outputs", "statuses"),
    [
        ((pi.PiError("timed out"), pi.PiError("stopReason error")), ["new", "new"]),
        (("garbage", pi.PiError("stopReason error")), ["new", "new"]),  # the retry got no answer
        # It got an invalid one: then each paper alone.
        ((pi.PiError("timed out"), "garbage", "garbage", "garbage"), ["brief_failed"] * 2),
        # Halved: the call for the first half fails, the second half goes back too.
        (("garbage", "garbage", pi.PiError("stopReason error")), ["new", "new"]),
        # The first half is briefed and kept, the call for the second fails.
        (("garbage", "garbage", "valid", pi.PiError("stopReason error")), ["briefed", "new"]),
    ],
)
def test_brief_batch_failed_calls_put_papers_back(session, monkeypatch, outputs, statuses):
    _add_papers(2)
    run, calls = _fake_pi(*outputs)
    monkeypatch.setattr(pi, "run", run)
    if "new" in statuses:
        with pytest.raises(pi.PiError, match="stopReason error"):  # the RQ job fails
            brief.brief_batch()
    else:
        assert brief.brief_batch()["status"] == "brief_failed"
    assert len(calls) == len(outputs)
    assert session.scalars(select(Paper.status).order_by(Paper.id)).all() == statuses
    briefs = session.scalar(select(func.count()).select_from(Analysis))
    assert briefs == statuses.count("briefed")


def test_brief_batch_reads_a_stray_backslash_as_itself(session, monkeypatch):
    """LaTeX copied from an abstract into the answer: a backslash that starts no JSON escape is
    read as itself, in the one call.
    """
    _add_papers(1)
    summary = r"用 \alpha、\underline{x}、\\beta 与 \u00e9 表示"  # as the model wrote it
    run, calls = _fake_pi(
        json.dumps({"papers": [_item(1, summary="S")]}).replace('"S"', f'"{summary}"')
    )
    monkeypatch.setattr(pi, "run", run)
    assert brief.brief_batch()["status"] == "briefed" and len(calls) == 1
    assert session.get(Paper, 1).summary == r"用 \alpha、\underline{x}、\beta 与 é 表示"


@pytest.mark.parametrize(("papers", "bad", "expected"), [(4, 3, 6), (20, 1, 10), (20, 20, 12)])
def test_brief_batch_halves_an_invalid_batch(session, monkeypatch, caplog, papers, bad, expected):
    """Any batch with paper `bad` gets an answer that is not JSON (a quote left unescaped): the
    batch fails twice, then its halves are briefed once each, down to that paper alone.
    """
    _add_papers(papers)
    run, calls = _fake_pi(*["valid"] * expected)

    def breaking(model, system, prompt, timeout, *, stdin="", **kwargs):
        result = run(model, system, prompt, timeout, stdin=stdin, **kwargs)
        if f'<paper id="{bad}">' in stdin:
            result.text = result.text.replace('"summary": "', '"summary": "a "quoted" term ', 1)
        return result

    monkeypatch.setattr(pi, "run", breaking)
    result = brief.brief_batch()
    assert (result["status"], result["failed"], len(calls)) == ("briefed", [bad], expected)
    if papers == 4:  # the batch twice, its halves, then the half with paper 3 halved
        assert [re.findall(r'<paper id="(\d+)">', c) for c in calls] == [
            ["1", "2", "3", "4"],
            ["1", "2", "3", "4"],
            ["1", "2"],
            ["3", "4"],
            ["3"],
            ["4"],
        ]
    statuses = dict(session.execute(select(Paper.id, Paper.status)).all())
    assert statuses == {i: "brief_failed" if i == bad else "briefed" for i in range(1, papers + 1)}
    assert session.scalar(select(func.count()).select_from(Analysis)) == papers - 1
    [alone] = [r.getMessage() for r in caplog.records if "on its own" in r.getMessage()]
    assert alone.startswith(f"paper {bad} failed to brief on its own: not a JSON object")
    assert 'around it: \'"new_topic": null, ' in alone and '"summary": "a "quoted" term' in alone


def test_brief_batch_gives_up_on_a_model_that_cannot_write_json(session, monkeypatch, caplog):
    """Every answer invalid: once GIVE_UP papers failed on their own the batch stops and every
    paper of it is brief_failed, as the whole batch was before it was halved.
    """
    _add_papers(20)
    run, calls = _fake_pi(*["garbage"] * 9)
    monkeypatch.setattr(pi, "run", run)
    result = brief.brief_batch()  # no raise: a job that fails would be retried with them
    assert (result["status"], sorted(result["failed"])) == ("brief_failed", list(range(1, 21)))
    sizes = [len(re.findall(r'<paper id="', c)) for c in calls]
    assert sizes == [20, 20, 10, 5, 2, 1, 1, 3, 1]
    assert session.scalars(select(Paper.status).distinct()).all() == ["brief_failed"]
    assert session.scalar(select(func.count()).select_from(Analysis)) == 0
    assert "gave up on a batch of 20 papers after 3 failed on their own: 20 marked" in caplog.text


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    """The waits after a busy provider, not slept."""
    waits = []
    monkeypatch.setattr(pi, "time", SimpleNamespace(sleep=waits.append))
    return waits


BUSY = pi.Busy('stopReason error: 429: {"message":"Upstream rate limit exceeded"}')


def test_brief_batch_waits_for_a_busy_provider(session, monkeypatch, slept):
    """A 429 (after pi's own retries): the job waits 30-90 s, then the retry succeeds."""
    _add_papers(2)
    run, calls = _fake_pi(BUSY, "valid")
    monkeypatch.setattr(pi, "run", run)
    assert brief.brief_batch()["status"] == "briefed" and len(calls) == 2
    assert len(slept) == 1 and 30 <= slept[0] <= 90
    assert session.scalars(select(Paper.status).distinct()).all() == ["briefed"]


@pytest.mark.parametrize(
    ("outputs", "waits"),
    [
        ((BUSY, BUSY), 2),  # before the retry and before giving up
        ((pi.PiError("timed out after 300s"), pi.PiError("timed out after 300s")), 0),
    ],
)
def test_brief_batch_gives_up_on_a_busy_provider(session, monkeypatch, slept, outputs, waits):
    """Busy twice: two waits at most (pi.BULK_MAX_WAIT in all), the papers back to new, the
    job fails. A timeout does not wait.
    """
    _add_papers(2)
    run, calls = _fake_pi(*outputs)
    monkeypatch.setattr(pi, "run", run)
    with pytest.raises(type(outputs[-1])):
        brief.brief_batch()
    assert len(calls) == 2 and len(slept) == waits and sum(slept) <= pi.BULK_MAX_WAIT
    assert session.scalars(select(Paper.status).distinct()).all() == ["new"]
    assert brief.JOB_TIMEOUT >= 2 * brief.PI_TIMEOUT + pi.BULK_MAX_WAIT


def test_sync_enqueues_one_job_per_batch(session, monkeypatch):
    _add_papers(45)
    enqueued = []

    class FakeQueue:
        def __init__(self, name, connection):
            assert name == jobs.ANALYZE

        def enqueue(self, func, size, job_timeout):
            enqueued.append((func, size))

    monkeypatch.setattr(jobs, "Queue", FakeQueue)
    monkeypatch.setattr(jobs, "sync_arxiv", lambda since, until: {"cs.RO": (45, 0)})
    assert jobs.sync_job() == {"cs.RO": (45, 0)}
    assert enqueued == [(jobs.brief_job, 20), (jobs.brief_job, 20), (jobs.brief_job, 5)]
    enqueued.clear()
    assert jobs.enqueue_briefs(limit=30) == 2 and [size for _, size in enqueued] == [20, 10]


def _save(items):
    """brief.save of parsed items, as brief_batch does it."""
    by_id = _parse(*items)
    with Session(engine()) as s, s.begin():
        topics.lock(s)
        ids = [int(i) for i in by_id]
        found = s.scalars(select(Paper).where(Paper.id.in_(ids)).with_for_update()).all()
        brief.save(
            s, found, by_id, {key: 40 for key in pi.USAGE_KEYS}, "brief.v2", "deepseek-flash"
        )


def _links(session, paper_id):
    query = select(Topic.name).join(PaperTopic).where(PaperTopic.paper_id == paper_id)
    return session.scalars(query.order_by(PaperTopic.id)).all()


def test_save_creates_topics_once(vocabulary, session):
    _add_papers(4)
    new = {
        "name": "Polarization Imaging",
        "zh": "偏振成像",
        "definition": "Vision with polarization.",
    }
    new["domain"] = "computer-vision"
    _save(
        [
            _item(1, topics=["Polarization Imaging", "Optical Flow"], new_topic=new),
            _item(
                2, new_topic=new | {"name": "polarization-imaging"}
            ),  # the same, spelt differently
            _item(3, domains=["out-of-scope"], type="theory"),
            _item(4, topics=["Optical Flow"], new_topic=new | {"name": "Scene Flow"}),  # an alias
        ]
    )
    created = session.scalars(select(Topic).where(Topic.source == "model")).all()
    assert [(t.name, t.zh, t.definition, t.domain) for t in created] == [
        ("Polarization Imaging", "偏振成像", "Vision with polarization.", "computer-vision")
    ]
    assert _links(session, 1) == ["Polarization Imaging", "Optical Flow"]
    assert _links(session, 2) == ["3D Gaussian Splatting", "Polarization Imaging"]
    assert _links(session, 3) == [] and _links(session, 4) == ["Optical Flow"]
    paper = session.get(Paper, 3)
    assert paper.paper_type == "theory"
    assert [(t.kind, t.value) for t in paper.tags if t.kind == "domain"] == [
        ("domain", "out-of-scope")
    ]
    # Paper 2 (saved first) links a topic that paper 5 of the same batch creates.
    _add_papers(1)
    micro = {"name": "Micro Robotics", "definition": "d", "domain": "robotics"}
    _save(
        [_item(2, topics=["micro-robotics"]), _item(5, topics=["Micro Robotics"], new_topic=micro)]
    )
    assert _links(session, 2) == ["Micro Robotics"] and _links(session, 5) == ["Micro Robotics"]


def _create(name):
    new = {"name": name, "definition": "d", "domain": "robotics"}
    with Session(engine()) as s, s.begin():
        topics.lock(s)
        return topics.create(s, topics.keys(s), new).id


def test_concurrent_creates_make_one_topic(vocabulary, session):
    names = ["Micro Robotics", "micro robotics", "Micro-Robotics", "MICRO ROBOTIC"] * 4
    with ThreadPoolExecutor(8) as pool:
        ids = set(pool.map(_create, names))
    created = session.scalars(select(Topic.name).where(Topic.source == "model")).all()
    assert len(ids) == 1 and len(created) == 1  # whichever spelling came first
    assert topic_key(created[0]) == "microrobotic"


def test_redo_puts_old_briefs_back(session):
    _add_papers(4)
    with Session(engine()) as s, s.begin():
        s.execute(update(Paper).where(Paper.id < 4).values(status="briefed"))
        for paper_id, version in [
            (1, "brief.v1"),
            (2, "brief.v1"),
            (2, "brief.v2"),
            (3, "brief.v1"),
        ]:
            s.add(
                Analysis(
                    paper_id=paper_id,
                    level="brief",
                    model="m",
                    prompt_version=version,
                    result={},
                    usage={},
                )
            )
    with Session(engine()) as s, s.begin():
        assert brief.redo(s, limit=1) == 1  # the newest first
    assert session.get(Paper, 3).status == "new" and session.get(Paper, 1).status == "briefed"
    with Session(engine()) as s, s.begin():
        assert brief.redo(s) == 1  # paper 2 has a current brief, paper 4 was never briefed
    statuses = session.scalars(select(Paper.status).order_by(Paper.id)).all()
    assert statuses == ["new", "briefed", "new", "new"]


def test_brief_uses_the_prompt_edited_on_the_web(session, monkeypatch):
    """A brief job reads the prompt and the model when it runs: the text edited on the web,
    filled in like the file, its version (+ a hash of the text) recorded, and the brief model
    set there. brief --redo then counts briefs made by another text as old.
    """
    session.add(User(username="root", password_hash="x", is_admin=True))
    session.commit()
    edited = agent.default_prompt("brief") + "\nPrefer short summaries.\n"
    with Session(engine()) as s, s.begin():
        agent.set_prompt(s, "brief", edited, s.scalar(select(User)))
        agent.set_model(s, "brief_model", "web-brief", s.scalar(select(User)))
    _add_papers(2)
    calls, (run, _) = [], _fake_pi("valid")

    def seen(model, system, *args, **kwargs):
        calls.append((model, system, kwargs["endpoint"]))
        return run(model, system, *args, **kwargs)

    monkeypatch.setattr(pi, "run", seen)
    assert brief.brief_batch()["status"] == "briefed"
    model, system, endpoint = calls[0]
    assert "Prefer short summaries." in system and "{{" not in system
    analysis = config().analysis
    assert model == "web-brief" and endpoint == {
        "base_url": analysis.base_url,
        "brief_model": "web-brief",
        "report_model": analysis.report_model,  # not changed by a brief model set on the web
    }
    version = "brief.v2+" + hashlib.sha256(edited.encode()).hexdigest()[:8]
    made = session.execute(select(Analysis.prompt_version, Analysis.model)).all()
    assert made == [(version, "web-brief")] * 2
    with Session(engine()) as s, s.begin():
        assert brief.redo(s) == 0  # made by the current text
        agent.reset_prompt(s, "brief")
    with Session(engine()) as s, s.begin():
        assert brief.redo(s) == 2  # the file is the current text again
