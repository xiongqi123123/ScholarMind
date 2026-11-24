"""topics, paper types; the old domain and task tags go

Revision ID: 0006
Revises: 0005

Domains got a new set of values and tasks gave way to topics, so every domain and task tag is
deleted (`scholarmind brief --redo` tags the papers again). Edits marked tags_edited were made
to those tags, so the flag is cleared too; manual keyword tags stay. A downgrade does not bring
the deleted tags back.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"


def _created_at():
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade():
    op.add_column("papers", sa.Column("paper_type", sa.Text))
    op.create_index("ix_papers_paper_type", "papers", ["paper_type"])
    op.create_table(
        "topics",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("domain", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("zh", sa.Text, nullable=False, server_default=""),
        sa.Column("definition", sa.Text, nullable=False, server_default=""),
        sa.Column("aliases", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("source", sa.Text, nullable=False),
        _created_at(),
    )
    op.create_table(
        "paper_topics",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "paper_id",
            sa.BigInteger,
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "topic_id",
            sa.BigInteger,
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        _created_at(),
        sa.UniqueConstraint("paper_id", "topic_id"),
    )
    op.create_index("ix_paper_topics_topic_id", "paper_topics", ["topic_id"])
    op.create_table(
        "topic_merges",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("source_id", sa.BigInteger, nullable=False),
        sa.Column("source", postgresql.JSONB, nullable=False),
        sa.Column("target_id", sa.BigInteger, nullable=False),
        sa.Column("target_name", sa.Text, nullable=False),
        sa.Column("merged_by", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("papers", sa.Integer, nullable=False),
        _created_at(),
    )
    op.execute("DELETE FROM tags WHERE kind IN ('domain', 'task')")
    op.execute("UPDATE papers SET tags_edited = false WHERE tags_edited")


def downgrade():
    op.drop_table("topic_merges")
    op.drop_table("paper_topics")
    op.drop_table("topics")
    op.drop_index("ix_papers_paper_type", "papers")
    op.drop_column("papers", "paper_type")
