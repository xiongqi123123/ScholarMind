"""notes: a quote with its anchor, edits, and one free block per member and paper

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016"
down_revision = "0015"


def upgrade():
    op.add_column("notes", sa.Column("kind", sa.Text, nullable=False, server_default="note"))
    op.add_column("notes", sa.Column("quote", sa.Text))
    op.add_column("notes", sa.Column("anchor", JSONB))
    op.add_column("notes", sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE notes SET updated_at = created_at")
    op.alter_column("notes", "updated_at", nullable=False, server_default=sa.func.now())
    op.create_index(
        "ix_notes_page",
        "notes",
        ["user_id", "paper_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'page'"),
    )


def downgrade():  # free blocks stay, as plain notes
    op.drop_index("ix_notes_page", table_name="notes")
    for column in ("updated_at", "anchor", "quote", "kind"):
        op.drop_column("notes", column)
