"""tags, analyses, paper claim and brief columns

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"


def upgrade():
    op.add_column("papers", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.add_column("papers", sa.Column("summary", sa.Text))
    op.add_column(
        "papers", sa.Column("tags_edited", sa.Boolean, nullable=False, server_default="false")
    )
    op.create_index("ix_papers_status", "papers", ["status"])
    op.create_table(
        "tags",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "paper_id",
            sa.BigInteger,
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("paper_id", "kind", "value"),
    )
    op.create_index("ix_tags_kind_value", "tags", ["kind", "value"])
    op.create_table(
        "analyses",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "paper_id",
            sa.BigInteger,
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("level", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("result", postgresql.JSONB, nullable=False),
        sa.Column("usage", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_analyses_paper_id", "analyses", ["paper_id"])


def downgrade():
    op.drop_table("analyses")
    op.drop_table("tags")
    op.drop_index("ix_papers_status", "papers")
    op.drop_column("papers", "tags_edited")
    op.drop_column("papers", "summary")
    op.drop_column("papers", "claimed_at")
