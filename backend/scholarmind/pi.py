"""Run pi (https://pi.dev) non-interactively: the only way this project calls a model.

Conventions (docs/ARCHITECTURE.md, "调用 pi"): JSON event mode, no session file; tools,
extensions, skills, prompt templates and context files all off; stdin from a file; own process
group, killed as a whole on timeout; a minimal environment. Success is read from the event
stream (the exit code is 0 even when every model call failed).
"""

import json
import logging
import os
import random
import re
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from . import agent
from .config import settings
from .db import engine

log = logging.getLogger(__name__)

PI = "pi"  # executable; tests swap in a fake
PROVIDER = "scholarmind"  # provider name in the generated models.json
USAGE_KEYS = ("input", "output", "cacheRead", "reasoning", "totalTokens")
# The provider rate limiting or unavailable, as pi writes the error of an OpenAI-compatible
# API: "429: {...}", "503: {...}" (status, then the body), or in the body's words.
BUSY = re.compile(r"^(429|503|529)\b|rate.?limit|overloaded|temporarily unavailable", re.I)
# Waits after a busy provider (Backoff): a bulk job (briefs, automatic reports, assessments)
# waits long, so the workers send fewer requests and do not come back in step; what a member
# waits for (a translation, a manual report) pauses briefly and tries again.
BULK_PAUSES, BULK_WAIT = 2, (30, 90)  # at most 2 waits per job, each 30-90 s at random
MEMBER_PAUSES, MEMBER_WAIT = 2, (2, 5)
BULK_MAX_WAIT = BULK_PAUSES * BULK_WAIT[1]  # 180 s: room a bulk job's timeout leaves for them


class PiError(Exception):
    """The call itself failed (model API error, timeout): worth trying again later."""


class Busy(PiError):
    """The provider is rate limiting or unavailable (429, 503, 529), after pi's own retries (3,
    2, 4 and 8 s apart): worth waiting before the next call.
    """


class Incomplete(Exception):
    """The model answered but stopped short: output truncated ("length"). A bad answer, like an
    invalid one, not a failed call.
    """

    def __init__(self, message: str, usage: dict):
        super().__init__(message)
        self.usage = usage  # what the attempt cost anyway


@dataclass
class Result:
    text: str  # final assistant text
    # USAGE_KEYS summed over turns, plus "turns"
    usage: dict = field(default_factory=dict)


def write_models_json(endpoint: dict[str, str]) -> Path:
    """Generate pi's models.json from the endpoint (agent.model; pi cannot read baseUrl from the
    environment), anew for every run.

    Written atomically: other pi processes may be reading it.
    """
    agent_dir = settings().pi_coding_agent_dir
    agent_dir.mkdir(parents=True, exist_ok=True)
    # Thinking counts as output: deepseek-flash thinks ~8k tokens before a report, and 16384
    # cut reports off with nothing written.
    max_tokens = {endpoint["brief_model"]: 16384} | {endpoint["report_model"]: 32768}
    models = {
        "providers": {
            PROVIDER: {
                "baseUrl": endpoint["base_url"],
                "api": "openai-completions",
                "apiKey": "$LLM_API_KEY",  # pi reads the key from the environment
                "models": [
                    {"id": model, "contextWindow": 128000, "maxTokens": tokens}
                    for model, tokens in max_tokens.items()
                ],
            }
        }
    }
    fd, tmp = tempfile.mkstemp(dir=agent_dir, prefix=".models.json.")
    with os.fdopen(fd, "w") as f:
        json.dump(models, f, indent=1)
    path = agent_dir / "models.json"
    os.replace(tmp, path)
    return agent_dir


def parse_events(stdout: str) -> Result:
    """Read pi's JSONL event stream; the last assistant message decides. Its stopReason must
    be "stop": "error" or "aborted" (the model call failed after pi's own retries) raise
    PiError, anything else ("length") raises Incomplete.
    """
    usage = dict.fromkeys(USAGE_KEYS, 0)
    turns, last, text = 0, None, ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message") if event.get("type") == "message_end" else None
        if not message or message.get("role") != "assistant":
            continue
        turns += 1
        last = message
        for key in USAGE_KEYS:
            usage[key] += (message.get("usage") or {}).get(key) or 0
        text = "".join(c.get("text", "") for c in message["content"] if c["type"] == "text") or text
    if last is None:
        raise PiError("no assistant message in pi output")
    stop = f"stopReason {last.get('stopReason')}: {last.get('errorMessage', '')}"
    usage["turns"] = turns
    if last.get("stopReason") == "error" and BUSY.search(last.get("errorMessage") or ""):
        raise Busy(stop)
    if last.get("stopReason") in ("error", "aborted"):
        raise PiError(stop)
    if last.get("stopReason") != "stop":
        raise Incomplete(stop, usage)
    return Result(text, usage)


@contextmanager
def _started(
    model: str, system_prompt: str, prompt: str, stdin: str, endpoint: dict[str, str] | None
) -> Iterator[tuple[subprocess.Popen, Path]]:
    """pi running one call without tools, stdout piped: (process, its stderr file). Its whole
    process group is killed on the way out if it is still running.
    """
    with Session(engine()) as session:  # each run: what an admin changed on the web counts
        api_key, endpoint = agent.api_key(session), endpoint or agent.model(session)
    # ponytail: one models.json for all pi processes; a run starting the moment the endpoint is
    # changed on the web may read the other's (its call fails, the job retries). Give each run
    # its own agent dir if that ever bites.
    agent_dir = write_models_json(endpoint)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/"),
        "LLM_API_KEY": api_key,
        "PI_CODING_AGENT_DIR": str(agent_dir),
        "PI_OFFLINE": "1",
        "PI_SKIP_VERSION_CHECK": "1",
        "PI_TELEMETRY": "0",
    }
    with tempfile.TemporaryDirectory(prefix="scholarmind-pi-") as tmp:
        system_file, stdin_path, stderr_path, cwd = (
            Path(tmp, "system.md"),
            Path(tmp, "input.txt"),
            Path(tmp, "stderr.txt"),
            Path(tmp, "work"),
        )
        system_file.write_text(system_prompt, encoding="utf-8")
        stdin_path.write_text(stdin, encoding="utf-8")
        cwd.mkdir()
        cmd = [
            PI, "--mode", "json", "--no-session", "--no-tools",
            "-ne", "-ns", "-np", "--no-themes", "-nc", "-na",
            "--model", f"{PROVIDER}/{model}", "--system-prompt", str(system_file), prompt,
        ]  # fmt: skip
        # stderr to a file: a pipe nobody reads while stdout is streamed could fill and stall pi
        with open(stdin_path, "rb") as stdin_file, open(stderr_path, "wb") as stderr_file:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdin=stdin_file,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                start_new_session=True,  # own process group, so a timeout kills pi's children too
            )
            try:
                yield proc, stderr_path
            finally:
                if (
                    proc.poll() is None
                ):  # timed out, or interrupted (an RQ job timeout, a client gone)
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                proc.stdout.close()


def _result(stdout: bytes, stderr_path: Path, returncode: int) -> Result:
    try:
        return parse_events(stdout.decode(errors="replace"))
    except PiError as e:
        stderr = stderr_path.read_text(errors="replace")[-300:]
        raise type(e)(f"{e} (exit {returncode}, stderr: {stderr})") from None


def run(
    model: str,
    system_prompt: str,
    prompt: str,
    timeout: float,
    *,
    stdin: str = "",
    endpoint: dict[str, str] | None = None,
) -> Result:
    """One pi call, without tools; `stdin` is prepended to `prompt`. `endpoint`: agent.model
    as the job read it with its model name (default: read now).
    """
    with _started(model, system_prompt, prompt, stdin, endpoint) as (proc, stderr_path):
        try:
            stdout, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise PiError(f"timed out after {timeout:.0f}s") from None
        return _result(stdout, stderr_path, proc.returncode)


def stream(
    model: str,
    system_prompt: str,
    prompt: str,
    timeout: float,
    *,
    stdin: str = "",
    endpoint: dict[str, str] | None = None,
) -> Generator[str, None, Result]:
    """run, yielding the answer's text as the model writes it (the deltas of pi's text blocks;
    thinking is left out); returns the Result, whose text is authoritative (a retry inside pi
    starts the text again). Closing the generator kills pi.
    """
    with _started(model, system_prompt, prompt, stdin, endpoint) as (proc, stderr_path):
        expired = threading.Event()

        def expire():
            expired.set()
            with suppress(ProcessLookupError):  # it ended just now
                os.killpg(proc.pid, signal.SIGKILL)

        timer = threading.Timer(timeout, expire)
        timer.start()
        lines = []
        try:
            for line in proc.stdout:
                lines.append(line)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                update = event.get("assistantMessageEvent") or {}
                if event.get("type") == "message_update" and update.get("type") == "text_delta":
                    yield update["delta"]
            proc.wait()
        finally:
            timer.cancel()
        if expired.is_set():
            raise PiError(f"timed out after {timeout:.0f}s")
        return _result(b"".join(lines), stderr_path, proc.returncode)


class Backoff:
    """The waits of one job, or one request, after a busy provider: at most `pauses` of them,
    each a random number of seconds in `wait`. A bulk job calls failed() after each failed
    attempt: it waits before the next attempt and before giving up. What a member waits for
    calls run(): a busy call is tried again after the pause, besides its attempts.
    """

    def __init__(self, pauses: int, wait: tuple[float, float], member: bool):
        self.left, self.wait, self.member = pauses, wait, member

    @classmethod
    def bulk(cls) -> "Backoff":
        return cls(BULK_PAUSES, BULK_WAIT, member=False)

    @classmethod
    def member_call(cls) -> "Backoff":
        return cls(MEMBER_PAUSES, MEMBER_WAIT, member=True)

    def _pause(self, error: Exception) -> bool:
        if not isinstance(error, Busy) or not self.left:
            return False
        self.left -= 1
        seconds = random.uniform(*self.wait)
        log.warning("the provider is busy, waiting %.0f s: %s", seconds, error)
        time.sleep(seconds)
        return True

    def failed(self, error: Exception):
        """After a failed attempt of a bulk job: wait if the provider was busy."""
        if not self.member:
            self._pause(error)

    def run(self, *args, **kwargs) -> Result:
        """run; for a member, tried again after a pause while the provider is busy."""
        while True:
            try:
                return run(*args, **kwargs)
            except Busy as e:
                if not (self.member and self._pause(e)):
                    raise
