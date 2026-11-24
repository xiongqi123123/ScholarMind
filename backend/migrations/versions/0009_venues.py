"""conference papers: source, year, track, venue_key

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade():
    op.add_column("papers", sa.Column("source", sa.Text, nullable=False, server_default="arXiv"))
    op.add_column("papers", sa.Column("year", sa.Integer))
    op.add_column("papers", sa.Column("track", sa.Text))
    op.add_column("papers", sa.Column("venue_key", sa.Text))
    op.create_unique_constraint("papers_venue_key_key", "papers", ["venue_key"])
    newest = [sa.text("published_at DESC NULLS LAST"), sa.text("id DESC")]
    op.create_index("ix_papers_newest", "papers", newest)
    op.create_index("ix_papers_source_year", "papers", ["source", "year", *newest])
    op.create_index("ix_papers_year", "papers", ["year", *newest])
    # Every paper so far comes from arXiv: the year of its first version.
    op.execute("UPDATE papers SET year = extract(year FROM published_at AT TIME ZONE 'UTC')")


def downgrade():
    op.drop_index("ix_papers_newest", "papers")
    op.drop_index("ix_papers_year", "papers")
    op.drop_index("ix_papers_source_year", "papers")
    op.drop_constraint("papers_venue_key_key", "papers")
    op.drop_column("papers", "venue_key")
    op.drop_column("papers", "track")
    op.drop_column("papers", "year")
    op.drop_column("papers", "source")
