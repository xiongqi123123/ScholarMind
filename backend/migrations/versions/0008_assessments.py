"""the innovation type of reported papers (analyses level assessment), on papers for
filtering; the figure an assessment names as the method overview, for covers

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade():
    op.add_column("papers", sa.Column("innovation", sa.Text))
    op.execute(  # from the newest assessment, if any (none before this revision ran once)
        "UPDATE papers SET innovation = a.result->>'type'"
        " FROM (SELECT DISTINCT ON (paper_id) paper_id, result FROM analyses"
        " WHERE level = 'assessment' ORDER BY paper_id, id DESC) a WHERE a.paper_id = papers.id"
    )
    # `scholarmind assess` marks covers; until then covers follow the caption rule
    op.add_column("figures", sa.Column("cover", sa.Boolean, nullable=False, server_default="false"))


def downgrade():
    op.drop_column("figures", "cover")
    op.drop_column("papers", "innovation")
