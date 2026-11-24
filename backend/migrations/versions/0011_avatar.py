"""users.avatar: a preset avatar the member chose

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"


def upgrade():
    op.add_column("users", sa.Column("avatar", sa.Text))


def downgrade():
    op.drop_column("users", "avatar")
