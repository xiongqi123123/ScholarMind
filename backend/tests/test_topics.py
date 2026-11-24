import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from scholarmind import brief, pi, topics
from scholarmind.config import DomainConfig, TopicConfig, config, topic_key
from scholarmind.db import Paper, PaperTopic, Topic, TopicMerge, engine


def test_topic_key():
    same = [
        "3D Gaussian Splatting",
        "3d-gaussian splatting",
        "3D Gaussian-Splattings",
        "３Ｄ Gaussian Splatting",
    ]
    assert {topic_key(n) for n in same} == {"3dgaussiansplatting"}
    assert topic_key("Vision-Language Models") == topic_key("vision language model")
    assert topic_key("Loss") != topic_key("Lo") and topic_key("SLAM") == "slam"


def _sync():
    with Session(engine()) as s, s.begin():
        return topics.sync(s)


def _topic(session, name) -> Topic:
    session.expire_all()
    return session.scalar(select(Topic).where(Topic.name == name))


def test_sync_adds_updates_and_keeps(session, monkeypatch):
    assert _sync() == (88, 0)
    assert _sync() == (0, 0)  # repeatable
    gaussians = _topic(session, "3D Gaussian Splatting")
    assert (gaussians.domain, gaussians.zh, gaussians.source) == (
        "computer-vision",
        "三维高斯泼溅",
        "seed",
    )
    assert gaussians.aliases == ["3DGS", "Gaussian Splatting"]

    vocabulary = {d: v.model_copy(deep=True) for d, v in config().vocabulary.items()}
    cv = vocabulary["computer-vision"].topics
    cv[0].aliases.append("GS")  # 3D Gaussian Splatting gains an alias
    cv[1].zh = "神经辐射场"  # Neural Radiance Fields
    del cv[2]  # 3D Reconstruction leaves the config
    tracking = next(t for t in cv if t.name == "Tracking")
    tracking.name, tracking.aliases = "Visual Tracking", ["Tracking", "point tracking"]  # renamed
    cv.append(TopicConfig(name="Polarization Imaging", aliases=["NeRF"]))  # NeRF is taken
    vocabulary["robotics"] = DomainConfig(
        zh="机器人", topics=[*vocabulary["robotics"].topics, TopicConfig(name="neural rendering")]
    )  # another topic's alias: skipped
    monkeypatch.setattr(config(), "vocabulary", vocabulary)
    tracking_id = _topic(session, "Tracking").id
    assert _sync() == (1, 3)
    assert _topic(session, "3D Gaussian Splatting").aliases == ["3DGS", "Gaussian Splatting", "GS"]
    assert _topic(session, "Neural Radiance Fields").zh == "神经辐射场"
    assert _topic(session, "3D Reconstruction").source == "seed"  # kept
    renamed = _topic(session, "Visual Tracking")
    assert renamed.id == tracking_id
    assert renamed.aliases == ["multi-object tracking", "point tracking", "Tracking"]
    assert _topic(session, "Polarization Imaging").aliases == []
    assert _topic(session, "neural rendering") is None
    assert _sync() == (0, 0)


def test_sync_adopts_a_created_topic(vocabulary, session, monkeypatch):
    session.add(
        Topic(domain="robotics", name="Polarization Imaging", definition="d", source="model")
    )
    session.commit()
    vocabulary = dict(config().vocabulary)
    vocabulary["computer-vision"] = DomainConfig(
        zh="计算机视觉", topics=[TopicConfig(name="Polarization Imaging", zh="偏振成像")]
    )
    monkeypatch.setattr(config(), "vocabulary", vocabulary)
    assert _sync() == (0, 1)
    adopted = _topic(session, "Polarization Imaging")
    assert (adopted.domain, adopted.zh, adopted.definition, adopted.source) == (
        "computer-vision",
        "偏振成像",
        "d",
        "seed",
    )


def _created(session, name, domain="computer-vision", aliases=()) -> int:
    topic = Topic(
        domain=domain, name=name, definition=f"{name}.", aliases=list(aliases), source="model"
    )
    session.add(topic)
    session.commit()
    return topic.id


def _papers(session, n):
    session.add_all(Paper(title=f"Paper {i}", title_norm=f"paper {i}") for i in range(n))
    session.commit()


def _link(session, paper_id, topic_id, source="ai"):
    session.add(PaperTopic(paper_id=paper_id, topic_id=topic_id, source=source))
    session.commit()


def _merge(source, target, by="review", reason="same"):
    with Session(engine()) as s, s.begin():
        record = topics.merge(s, source, target, by, reason)
        return record.id


def test_merge(vocabulary, session):
    _papers(session, 3)
    gaussians = _topic(session, "3D Gaussian Splatting").id
    duplicate = _created(session, "Gaussian Splatting Rendering", aliases=["GS Rendering", "3DGS"])
    _link(session, 1, duplicate)
    _link(session, 2, duplicate, "human")
    _link(session, 2, gaussians)
    _link(session, 3, gaussians)
    _merge(duplicate, gaussians, "root", "a rewording of 3DGS")

    assert session.get(Topic, duplicate) is None
    links = session.execute(select(PaperTopic.paper_id, PaperTopic.topic_id, PaperTopic.source))
    assert sorted(links) == [(1, gaussians, "ai"), (2, gaussians, "ai"), (3, gaussians, "ai")]
    assert _topic(session, "3D Gaussian Splatting").aliases == [
        "3DGS",
        "Gaussian Splatting",
        "Gaussian Splatting Rendering",
        "GS Rendering",
    ]
    record = session.scalar(select(TopicMerge))
    assert (record.source_id, record.target_id, record.target_name) == (
        duplicate,
        gaussians,
        "3D Gaussian Splatting",
    )
    assert (record.merged_by, record.reason, record.papers) == ("root", "a rewording of 3DGS", 1)
    assert record.source == {
        "domain": "computer-vision",
        "name": "Gaussian Splatting Rendering",
        "zh": "",
        "definition": "Gaussian Splatting Rendering.",
        "aliases": ["GS Rendering", "3DGS"],
    }
    assert record.created_at is not None

    # Creating the merged name again lands on the target.
    with Session(engine()) as s, s.begin():
        topics.lock(s)
        new = {"name": "gaussian-splatting rendering", "definition": "x", "domain": "robotics"}
        assert topics.create(s, topics.keys(s), new).id == gaussians
    item = {"id": 1, "domains": ["computer-vision"], "topics": ["Gaussian Splatting Rendering"]}
    item |= {"new_topic": None, "type": "method", "keywords": ["k"], "summary": "s"}
    names = [(t, n) for t in session.scalars(select(Topic)) for n in (t.name, *t.aliases)]
    known = {topic_key(n): (t.name, t.domain) for t, n in names}
    by_id = brief.parse(json.dumps({"papers": [item]}), ["1"], known)
    with Session(engine()) as s, s.begin():
        brief.save(
            s,
            [s.get(Paper, 1)],
            by_id,
            dict.fromkeys(pi.USAGE_KEYS, 0),
            "brief.v2",
            "deepseek-flash",
        )
    assert session.scalars(select(PaperTopic.topic_id).where(PaperTopic.paper_id == 1)).all() == [
        gaussians
    ]


def test_merge_refuses(vocabulary, session):
    gaussians = _topic(session, "3D Gaussian Splatting").id
    nerf = _topic(session, "Neural Radiance Fields").id
    created = _created(session, "Splats")
    for source, target, error in [
        (gaussians, nerf, "starting vocabulary"),
        (created, created, "itself"),
        (created, 99999, "no topic 99999"),
        (99999, gaussians, "no topic 99999"),
    ]:
        with pytest.raises(topics.MergeRefused, match=error):
            _merge(source, target)
    assert session.scalar(select(TopicMerge)) is None


def test_parse_review():
    text = json.dumps(
        {
            "topics": [
                {"id": 70, "duplicate_of": 1, "reason": "same"},
                {"id": 71, "duplicate_of": None, "reason": "new"},
                {"id": 1, "duplicate_of": 2, "reason": "a starting topic"},
                {"id": 72, "duplicate_of": 72, "reason": "itself"},
                {"id": 73, "duplicate_of": 999, "reason": "no such topic"},
                {"id": 74, "duplicate_of": 70},
                {"id": True, "duplicate_of": 1},
                "garbage",
            ]
        }
    )
    merges, rejected = topics.parse_review(text, {70, 71, 72, 73, 74}, {1, 2, 70, 71, 72, 73, 74})
    assert merges == [(70, 1, "same"), (74, 70, "")]
    assert [why for _, why in rejected] == [
        "id is not a created topic",
        "duplicate_of is not another existing topic",
        "duplicate_of is not another existing topic",
        "id is not a created topic",
        "not an object",
    ]
    for bad in ("no json", '{"topics": 3}', '{"merges": []}'):
        with pytest.raises(topics.ReviewInvalid):
            topics.parse_review(bad, set(), set())


def test_review(vocabulary, session, monkeypatch):
    _papers(session, 4)
    gaussians = _topic(session, "3D Gaussian Splatting").id
    nerf = _topic(session, "Neural Radiance Fields").id
    a = _created(session, "Gaussian Splatting Rendering")
    b = _created(session, "Splat Rendering")  # duplicates a, which goes on to 3DGS
    c = _created(session, "Micro Robotics", "robotics")
    d = _created(session, "Radiance Field Rendering")
    for paper_id, topic_id in [(1, a), (2, b), (3, c), (4, d), (4, nerf)]:
        _link(session, paper_id, topic_id)
    answer = {
        "topics": [
            {"id": a, "duplicate_of": gaussians, "reason": "same as 3DGS"},
            {"id": b, "duplicate_of": a, "reason": "same as a"},
            {"id": c, "duplicate_of": None, "reason": "a new direction"},
            {"id": d, "duplicate_of": nerf, "reason": "same as NeRF"},
            {"id": nerf, "duplicate_of": gaussians, "reason": "not allowed"},
        ]
    }
    calls = []

    def run(model, system_prompt, prompt, timeout, *, stdin="", **_):
        calls.append((model, system_prompt, stdin))
        output = "not json" if len(calls) == 1 else json.dumps(answer)
        return pi.Result(output, {key: 10 for key in pi.USAGE_KEYS} | {"turns": 1})

    monkeypatch.setattr(pi, "run", run)
    result = topics.review()
    assert len(calls) == 2 and calls[0][0] == config().analysis.brief_model
    assert "accidental duplicate" in calls[0][1]
    stdin = calls[0][2]
    assert f"- [{gaussians}] 3D Gaussian Splatting (三维高斯泼溅; aliases: 3DGS" in stdin
    assert f"- [{c}] Micro Robotics (Micro Robotics.) [created]: 1 papers" in stdin
    assert f"[{a}] Gaussian Splatting Rendering, under computer-vision\n" in stdin
    assert "Papers (1):\n- Paper 0" in stdin
    assert result["created"] == 4 and result["tokens"] == 20
    assert [(m[0], m[2]) for m in result["merged"]] == [(a, gaussians), (b, gaussians), (d, nerf)]
    assert [why for _, why in result["rejected"]] == ["id is not a created topic"]
    session.expire_all()
    assert session.scalars(select(Topic.name).where(Topic.source == "model")).all() == [
        "Micro Robotics"
    ]
    links = sorted(session.execute(select(PaperTopic.paper_id, PaperTopic.topic_id)))
    assert links == [(1, gaussians), (2, gaussians), (3, c), (4, nerf)]
    assert _topic(session, "3D Gaussian Splatting").aliases[-2:] == [
        "Gaussian Splatting Rendering",
        "Splat Rendering",
    ]
    assert [r.merged_by for r in session.scalars(select(TopicMerge))] == ["review"] * 3


def test_review_without_created_topics_calls_no_model(vocabulary, monkeypatch):
    monkeypatch.setattr(pi, "run", None)  # would fail if called
    assert topics.review() == {"created": 0, "merged": [], "rejected": []}


def test_review_gives_up_after_two_bad_answers(vocabulary, session, monkeypatch):
    _created(session, "Splats")
    calls = []

    def run(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise pi.PiError("relay down")
        return pi.Result("[]", {key: 1 for key in pi.USAGE_KEYS})

    monkeypatch.setattr(pi, "run", run)
    with pytest.raises(topics.ReviewInvalid):
        topics.review()
    assert len(calls) == 2
