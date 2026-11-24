"""full reports: report status columns, paper_texts

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"


def upgrade():
    op.add_column("papers", sa.Column("report_status", sa.Text))
    op.add_column("papers", sa.Column("report_trigger", sa.Text))
    op.add_column("papers", sa.Column("report_queued_at", sa.DateTime(timezone=True)))
    op.add_column("papers", sa.Column("report_claimed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_papers_report_status", "papers", ["report_status"])
    op.create_table(
        "paper_texts",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "paper_id",
            sa.BigInteger,
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pdf_url", sa.Text, nullable=False),
        sa.Column("pages", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("paper_id", "pdf_url"),
    )


def downgrade():
    op.drop_table("paper_texts")
    op.drop_index("ix_papers_report_status", "papers")
    op.drop_column("papers", "report_claimed_at")
    op.drop_column("papers", "report_queued_at")
    op.drop_column("papers", "report_trigger")
    op.drop_column("papers", "report_status")
