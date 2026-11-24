"""Database tests run in a throwaway schema so they never touch real data.

The database comes from TEST_DATABASE_URL, falling back to DATABASE_URL (each from the
environment or .env).
A schema `test_<random>` is created, migrated, and dropped afterwards.
"""

import os
import secrets
import urllib.parse
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).parent / "data"

_env = dotenv_values(ROOT / ".env")
_base_url = (
    os.environ.get("TEST_DATABASE_URL")
    or _env.get("TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or _env.get("DATABASE_URL")
)
SCHEMA = f"test_{secrets.token_hex(4)}"
if _base_url:
    # Set before anything calls settings(), so every connection lands in the test schema.
    _sep = "&" if "?" in _base_url else "?"
    os.environ["DATABASE_URL"] = (
        _base_url + _sep + "options=" + urllib.parse.quote(f"-csearch_path={SCHEMA}")
    )
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")  # unused by the tests
# The example config, so tests do not depend on a local config.yaml (CI has none).
os.environ["CONFIG_FILE"] = str(ROOT / "config.example.yaml")
# A stand-in for the built web app (frontend/dist is not in the repository).
os.environ["FRONTEND_DIR"] = str(DATA / "frontend")


def _admin(sql: str):
    url = _base_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql)


@pytest.fixture(scope="session")
def schema():
    """Only tests that need the database request this (via `session`)."""
    from alembic import command
    from alembic.config import Config

    _admin(f"CREATE SCHEMA {SCHEMA}")
    try:
        command.upgrade(Config(str(ROOT / "backend" / "alembic.ini")), "head")
        yield
    finally:
        from scholarmind.db import engine

        engine().dispose()
        _admin(f"DROP SCHEMA {SCHEMA} CASCADE")


@pytest.fixture
def session(schema):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from scholarmind.db import engine

    with Session(engine()) as session:
        yield session
    with engine().begin() as connection:
        # CASCADE: also every table that refers to these (tags, analyses, paper_texts, ...)
        connection.execute(
            text(
                "TRUNCATE papers, users, sync_state, topics, topic_merges RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def vocabulary(session):
    """The starting topics of config.example.yaml (the built-in vocabulary), synced in."""
    from sqlalchemy.orm import Session

    from scholarmind import topics
    from scholarmind.db import engine

    with Session(engine()) as s, s.begin():
        topics.sync(s)


def arxiv_lock_free() -> bool:
    """Whether another session could take the arXiv lock now (it is let go again at once)."""
    from sqlalchemy import func, select

    from scholarmind.db import engine
    from scholarmind.ingest import ARXIV_LOCK

    with engine().connect() as c:
        free = c.scalar(select(func.pg_try_advisory_lock(ARXIV_LOCK)))
        if free:
            c.scalar(select(func.pg_advisory_unlock(ARXIV_LOCK)))
        c.commit()
    return free
