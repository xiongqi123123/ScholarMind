"""users, papers, sync_state

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("username", sa.Text, nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("token_hash", sa.Text, unique=True),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "papers",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("arxiv_id", sa.Text, unique=True),
        sa.Column("arxiv_version", sa.Integer),
        sa.Column("doi", sa.Text),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("title_norm", sa.Text, nullable=False),
        sa.Column("abstract", sa.Text, nullable=False, server_default=""),
        sa.Column("authors", sa.Text, nullable=False, server_default=""),
        sa.Column("primary_category", sa.Text),
        sa.Column("categories", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("comment", sa.Text),
        sa.Column("journal_ref", sa.Text),
        sa.Column("pdf_url", sa.Text),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="new"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "search",
            postgresql.TSVECTOR,
            sa.Computed("to_tsvector('english', title || ' ' || abstract)", persisted=True),
            nullable=False,
        ),
    )
    op.create_index("ix_papers_doi", "papers", ["doi"])
    op.create_index("ix_papers_title_norm", "papers", ["title_norm"])
    op.create_index("ix_papers_published_at", "papers", ["published_at"])
    op.create_index("ix_papers_search", "papers", ["search"], postgresql_using="gin")
    op.create_index("ix_papers_categories", "papers", ["categories"], postgresql_using="gin")
    op.create_table(
        "sync_state",
        sa.Column("source", sa.Text, primary_key=True),
        sa.Column("cursor", sa.Date, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade():
    op.drop_table("sync_state")
    op.drop_table("papers")
    op.drop_table("users")
