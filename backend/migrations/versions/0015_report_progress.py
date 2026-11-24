"""report progress: the step a running report is in, why one failed

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015"
down_revision = "0014"


def upgrade():
    op.add_column("papers", sa.Column("report_stage", sa.Text))
    op.add_column("papers", sa.Column("report_stage_at", sa.DateTime(timezone=True)))
    op.add_column("papers", sa.Column("report_error", JSONB))
    # A report running now (or left by a dead worker) started with its PDF.
    op.execute(
        "UPDATE papers SET report_stage = 'pdf', report_stage_at = report_claimed_at "
        "WHERE report_status = 'reporting'"
    )
    op.create_index(
        "ix_analyses_reports", "analyses", ["id"], postgresql_where=sa.text("level = 'report'")
    )


def downgrade():
    op.drop_index("ix_analyses_reports", table_name="analyses")
    op.drop_column("papers", "report_error")
    op.drop_column("papers", "report_stage_at")
    op.drop_column("papers", "report_stage")
