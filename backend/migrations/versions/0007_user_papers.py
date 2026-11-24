"""stars (user_papers) and the arXiv day each paper was added on

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade():
    op.create_table(
        "user_papers",
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "paper_id",
            sa.BigInteger,
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("starred_at", sa.DateTime(timezone=True)),
    )
    # Filled in by the next syncs: `scholarmind sync --from <day> --until <today>` for the past.
    op.add_column("papers", sa.Column("added_on", sa.Date))
    op.create_index("ix_papers_added_on", "papers", ["added_on"])


def downgrade():
    op.drop_column("papers", "added_on")  # drops its index too
    op.drop_table("user_papers")
