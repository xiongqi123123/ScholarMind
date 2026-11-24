import os
import subprocess
import sys

import pytest
from conftest import ROOT

EXAMPLE = (ROOT / "config.example.yaml").read_text()
CLI = "from scholarmind.cli import main; main()"


def _python(code: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    env = {**os.environ, **env}
    return subprocess.run(
        [sys.executable, "-c", code, *args], env=env, capture_output=True, text=True, timeout=60
    )


@pytest.mark.parametrize(
    ("args", "config", "error"),
    [
        (
            ["import scholarmind.api"],
            EXAMPLE.split("analysis:")[0],
            "analysis\n  Field required",
        ),
        (
            ["import scholarmind.api"],
            EXAMPLE.replace("# vocabulary:", "vocabulary: {robotics: {zh: 机器人}}\n#"),
            "out-of-scope",
        ),
        (
            ["import scholarmind.api"],
            EXAMPLE.replace("brief_batch_size", "batch_size"),
            "batch_size",
        ),
        (
            [CLI, "worker", "analyze"],
            EXAMPLE.replace("brief_batch_size", "batch_size"),
            "batch_size",
        ),
        ([CLI, "cron"], EXAMPLE.replace("brief_batch_size", "batch_size"), "batch_size"),
    ],
)
def test_broken_config_stops_startup(tmp_path, args, config, error):
    (tmp_path / "config.yaml").write_text(config)
    result = _python(*args, CONFIG_FILE=str(tmp_path / "config.yaml"))
    assert result.returncode != 0 and "ValidationError" in result.stderr
    assert error in result.stderr


def test_starts_without_a_built_frontend(tmp_path):
    code = "from fastapi.testclient import TestClient; from scholarmind.api import app; "
    code += "print(TestClient(app).get('/').status_code)"
    result = _python(code, FRONTEND_DIR=str(tmp_path / "missing"))
    assert result.returncode == 0 and result.stdout.strip() == "404"


OLD_SCHEME = """
vocabulary:
  domains: [computer-vision, robotics]
  tasks: [manipulation, other]
"""


def test_old_config_still_starts(tmp_path):
    """A config.yaml from before topics: its vocabulary and report_auto.tasks are ignored."""
    config = EXAMPLE.replace("    topics: []\n    keywords", "    tasks: [x]\n    keywords")
    (tmp_path / "config.yaml").write_text(config + OLD_SCHEME)
    code = "from scholarmind.config import config; c = config(); "
    code += "print(len(c.vocabulary), c.domains[0], c.analysis.topic_review_cron)"
    result = _python(code, CONFIG_FILE=str(tmp_path / "config.yaml"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["6", "computer-vision", "0", "13", "*", "*", "1"]
    assert "ignoring the old vocabulary" in result.stderr
    assert "ignoring analysis.report_auto.tasks" in result.stderr


def test_cli_runs_as_a_module():
    result = subprocess.run(
        [sys.executable, "-m", "scholarmind.cli", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0 and "topics" in result.stdout
