import json
import os
import subprocess
import time
from types import SimpleNamespace

import pytest
from conftest import DATA
from sqlalchemy import select
from sqlalchemy.orm import Session

from scholarmind import agent, pi
from scholarmind.config import config, settings
from scholarmind.db import User, engine

# A real brief run (deepseek-flash, 20 abstracts) with the streaming deltas and long fields
# trimmed, and "summary_zh" renamed to "summary" as in prompts/brief.v1.md.
SAMPLE = (DATA / "pi_brief_events.jsonl").read_text()


def _assistant_end(stop_reason: str, error: str = "500 Internal Server Error") -> str:
    message = {
        "role": "assistant",
        "content": [{"type": "text", "text": "partial"}],
        "usage": {"totalTokens": 7},
        "stopReason": stop_reason,
        "errorMessage": error,
    }
    return json.dumps({"type": "message_end", "message": message}) + "\n"


def test_parse_real_output():
    result = pi.parse_events(SAMPLE)
    assert len(json.loads(result.text)["papers"]) == 20
    assert result.usage == {
        "input": 6696,
        "output": 5706,
        "cacheRead": 0,
        "reasoning": 1057,
        "totalTokens": 12402,
        "turns": 1,
    }


@pytest.mark.parametrize(
    ("stop_reason", "error"),
    [
        ("error", pi.PiError),  # the model call failed: try again later
        ("aborted", pi.PiError),
        ("length", pi.Incomplete),  # an answer, but truncated
        ("toolUse", pi.Incomplete),  # cut off by the tool-call limit
    ],
)
def test_parse_failed_run(stop_reason, error):
    # The exit code is 0 either way: the last assistant message decides.
    with pytest.raises(error, match=stop_reason):
        pi.parse_events(SAMPLE + _assistant_end(stop_reason))


@pytest.mark.parametrize(
    ("error", "busy"),
    [  # as pi wrote them on production, after its own 3 retries
        ('429: {"message":"Upstream rate limit exceeded, please retry later"}', True),
        ('503: {"message":"Service temporarily unavailable","type":"api_error"}', True),
        ('529: {"type":"error","error":{"type":"overloaded_error"}}', True),
        ("Rate limit reached for requests", True),
        ("500 Internal Server Error", False),
        ("Request timed out.", False),
        ('400: {"message":"context length exceeded"}', False),
    ],
)
def test_parse_busy_provider(error, busy):
    with pytest.raises(pi.PiError) as raised:
        pi.parse_events(SAMPLE + _assistant_end("error", error))
    assert isinstance(raised.value, pi.Busy) == busy


def test_backoff(monkeypatch):
    """A bulk job waits after a busy provider, at most twice and never otherwise; a member's
    call is tried again after a short pause instead.
    """
    slept = []
    monkeypatch.setattr(pi, "time", SimpleNamespace(sleep=slept.append))
    bulk = pi.Backoff.bulk()
    for error in (
        pi.PiError("timed out after 300s"),
        pi.Busy("429"),
        pi.Busy("429"),
        pi.Busy("429"),
    ):
        bulk.failed(error)
    assert len(slept) == 2 and all(30 <= s <= 90 for s in slept)

    slept.clear()
    answers = [pi.Busy("429"), pi.Busy("503"), pi.Busy("429")]

    def run(*args, **kwargs):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(pi, "run", run)
    with pytest.raises(pi.Busy):  # two pauses, then the third busy answer is the call's
        pi.Backoff.member_call().run("m", "S", "p", 60)
    assert len(slept) == 2 and all(2 <= s <= 5 for s in slept) and not answers
    answers[:] = [pi.Busy("429"), pi.Result("ok")]
    member = pi.Backoff.member_call()
    assert member.run("m", "S", "p", 60) == pi.Result("ok")
    member.failed(pi.Busy("429"))  # a member's call does not wait after its attempts
    assert len(slept) == 3


def test_parse_without_answer():
    with pytest.raises(pi.PiError, match="no assistant message"):
        pi.parse_events('{"type": "agent_start"}\nnot json\n')


@pytest.fixture
def fake_pi(tmp_path, monkeypatch, schema):  # schema: pi reads a key set on the web
    """Install a shell script as pi; returns a function taking the script body."""
    monkeypatch.setattr(settings(), "pi_coding_agent_dir", tmp_path / "agent")
    monkeypatch.setattr(settings(), "llm_api_key", "test-key")

    def install(body: str):
        script = tmp_path / "pi"
        script.write_text('#!/bin/sh\n[ "$1" = --warm-up ] && exit 0\n' + body)
        script.chmod(0o755)
        # macOS can take over a second to first run a new executable: do it before timing.
        subprocess.run([script, "--warm-up"], check=True)
        monkeypatch.setattr(pi, "PI", str(script))

    return install


def test_run(fake_pi, tmp_path):
    fake_pi(f"""
env > {tmp_path}/env
cat > {tmp_path}/stdin
printf '%s\\n' "$@" > {tmp_path}/args
while [ $# -gt 0 ]; do [ "$1" = --system-prompt ] && cp "$2" {tmp_path}/system; shift; done
cat {DATA}/pi_brief_events.jsonl
""")
    result = pi.run("deepseek-flash", "SYSTEM", "Return the JSON object now.", 30, stdin="PAPERS")
    assert result.usage["totalTokens"] == 12402
    assert (tmp_path / "stdin").read_text() == "PAPERS"
    assert (tmp_path / "system").read_text() == "SYSTEM"
    args = (tmp_path / "args").read_text().splitlines()
    for flag in ["--mode", "--no-session", "--no-tools", "-ne", "-ns", "-np", "-nc", "-na"]:
        assert flag in args
    assert args[args.index("--model") + 1] == "scholarmind/deepseek-flash"
    env = dict(line.split("=", 1) for line in (tmp_path / "env").read_text().splitlines())
    assert env["LLM_API_KEY"] == "test-key"
    assert env["PI_CODING_AGENT_DIR"] == str(tmp_path / "agent")
    assert not {"DATABASE_URL", "REDIS_URL", "SECRET_KEY"} & env.keys()
    models = json.loads((tmp_path / "agent" / "models.json").read_text())
    provider = models["providers"]["scholarmind"]
    assert provider["apiKey"] == "$LLM_API_KEY" and provider["baseUrl"].startswith("https://")


def test_run_timeout_kills_process_group(fake_pi, tmp_path):
    fake_pi(f"sleep 30 &\necho $! > {tmp_path}/child\nsleep 30\n")
    start = time.monotonic()
    with pytest.raises(pi.PiError, match="timed out"):
        pi.run("deepseek-flash", "SYSTEM", "go", 1)
    assert time.monotonic() - start < 10
    child = (tmp_path / "child").read_text().strip()
    for _ in range(50):  # the killed grandchild is gone, or a zombie until init reaps it
        ps = subprocess.run(["ps", "-o", "stat=", "-p", child], capture_output=True, text=True)
        if ps.stdout.strip()[:1] in ("", "Z"):
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"pi's child {child} survived the timeout: {ps.stdout}")


def test_run_uses_the_key_set_on_the_web(fake_pi, tmp_path, session):
    """A key an admin set on the web reaches pi from the next run on, in place of LLM_API_KEY."""
    fake_pi(f"env > {tmp_path}/env\ncat {DATA}/pi_brief_events.jsonl\n")

    def key_given():
        pi.run("deepseek-flash", "SYSTEM", "PROMPT", 30)
        env = dict(line.split("=", 1) for line in (tmp_path / "env").read_text().splitlines())
        return env["LLM_API_KEY"]

    assert key_given() == "test-key"
    session.add(User(username="root", password_hash="x", is_admin=True))
    session.commit()
    with Session(engine()) as s, s.begin():
        base_url = config().analysis.base_url
        agent.set_connection(s, base_url, "web-key-0123456789", s.scalar(select(User)))
    assert key_given() == "web-key-0123456789"


def test_run_uses_the_endpoint_set_on_the_web(fake_pi, tmp_path, session):
    """The base URL and model names an admin set on the web go into models.json from the next
    run on; removed, config.yaml's again.
    """
    fake_pi(f'cp "$PI_CODING_AGENT_DIR/models.json" {tmp_path}/models.json\n'
            f"cat {DATA}/pi_brief_events.jsonl\n")  # fmt: skip

    def provider(model):
        pi.run(model, "SYSTEM", "PROMPT", 30)
        models = json.loads((tmp_path / "models.json").read_text())["providers"]["scholarmind"]
        return models["baseUrl"], {m["id"]: m["maxTokens"] for m in models["models"]}

    analysis = config().analysis
    from_config = (
        analysis.base_url,
        {analysis.brief_model: 16384} | {analysis.report_model: 32768},
    )
    assert provider(analysis.brief_model) == from_config
    session.add(User(username="root", password_hash="x", is_admin=True))
    session.commit()
    with Session(engine()) as s, s.begin():
        admin = s.scalar(select(User))
        agent.set_connection(s, "https://llm.example.org/v1", "web-key-0123456789", admin)
        agent.set_model(s, "brief_model", "web-brief", admin)
        agent.set_model(s, "report_model", "web-report", admin)
    web = ("https://llm.example.org/v1", {"web-brief": 16384, "web-report": 32768})
    assert provider("web-brief") == web
    with Session(engine()) as s, s.begin():
        admin = s.scalar(select(User))
        agent.reset_connection(s, admin)
        for name in agent.NAMES:
            agent.reset_model(s, name, admin)
    assert provider(analysis.brief_model) == from_config


def _drain(stream) -> tuple[list[str], pi.Result]:
    deltas = []
    while True:
        try:
            deltas.append(next(stream))
        except StopIteration as end:
            return deltas, end.value


DELTA = '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"Hel"}}'


def test_stream(fake_pi, tmp_path):
    """The answer's text as pi writes it, thinking left out, then the Result; past the deadline
    or closed, pi is killed.
    """
    fake_pi(f"cat {DATA}/pi_brief_events.jsonl\n")
    deltas, result = _drain(pi.stream("deepseek-flash", "SYSTEM", "go", 30, stdin="PAPER"))
    events = [json.loads(line) for line in SAMPLE.splitlines()]
    updates = [e["assistantMessageEvent"] for e in events if e["type"] == "message_update"]
    assert deltas == [u["delta"] for u in updates if u["type"] == "text_delta"]
    assert result == pi.parse_events(SAMPLE)

    fake_pi(f"echo '{DELTA}'\necho $$ > {tmp_path}/pid\nsleep 30\n")
    start = time.monotonic()
    stream = pi.stream("deepseek-flash", "SYSTEM", "go", 1)
    assert next(stream) == "Hel"
    with pytest.raises(pi.PiError, match="timed out after 1s"):
        next(stream)
    assert time.monotonic() - start < 10

    (tmp_path / "pid").unlink()
    stream = pi.stream("deepseek-flash", "SYSTEM", "go", 30)
    assert next(stream) == "Hel"
    for _ in range(100):  # the script writes its pid just after the delta
        if (tmp_path / "pid").exists() and (tmp_path / "pid").read_text().strip():
            break
        time.sleep(0.05)
    os.kill(int((tmp_path / "pid").read_text()), 0)  # still writing
    stream.close()  # the client went away
    with pytest.raises(ProcessLookupError):  # killed and reaped
        os.kill(int((tmp_path / "pid").read_text()), 0)
