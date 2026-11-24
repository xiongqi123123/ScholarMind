"""users.email: accounts an admin creates on the web have one

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"


def upgrade():
    op.add_column("users", sa.Column("email", sa.Text))
    op.create_unique_constraint("users_email_key", "users", ["email"])


def downgrade():
    op.drop_column("users", "email")
