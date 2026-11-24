"""collections: members' own groups of starred papers

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"


def upgrade():
    op.create_table(
        "collections",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "name"),
    )
    op.create_table(
        "collection_papers",
        sa.Column(
            "collection_id",
            sa.BigInteger,
            sa.ForeignKey("collections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "paper_id",
            sa.BigInteger,
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_collection_papers_paper_id", "collection_papers", ["paper_id"])


def downgrade():
    op.drop_table("collection_papers")
    op.drop_table("collections")
