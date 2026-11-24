"""reports as a 论文解读: figures, and the structured v1 reports retired

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade():
    op.create_table(
        "figures",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "analysis_id",
            sa.BigInteger,
            sa.ForeignKey("analyses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("caption", sa.Text, nullable=False),
        sa.Column("page", sa.Integer, nullable=False),
        sa.Column("image", sa.LargeBinary, nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
    )
    op.create_index("ix_figures_analysis_id", "figures", ["analysis_id"])
    # The few v1 reports (structured JSON with checked quotes) were trials; clients only read the
    # new format. Their papers can be reported again, and the method/dataset tags they set go.
    op.execute("DELETE FROM analyses WHERE level = 'report' AND prompt_version = 'report.v1'")
    op.execute(
        "UPDATE papers SET report_status = NULL"
        " WHERE report_status IN ('reported', 'report_failed') AND NOT EXISTS"
        " (SELECT 1 FROM analyses a WHERE a.paper_id = papers.id AND a.level = 'report')"
    )
    op.execute("DELETE FROM tags WHERE source = 'ai' AND kind IN ('method', 'dataset')")


def downgrade():
    op.drop_table("figures")  # the retired v1 reports are not restored
