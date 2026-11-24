"""tokens: several named personal tokens per member, each can expire and be revoked

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"


def upgrade():
    op.create_table(
        "tokens",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_tokens_user_id", "tokens", ["user_id"])
    # Tokens already handed out keep working.
    op.execute(
        "INSERT INTO tokens (user_id, name, token_hash) "
        "SELECT id, '默认', token_hash FROM users WHERE token_hash IS NOT NULL"
    )
    op.drop_column("users", "token_hash")


def downgrade():
    op.add_column("users", sa.Column("token_hash", sa.Text))
    op.create_unique_constraint("users_token_hash_key", "users", ["token_hash"])
    # Each member's newest token that has not expired.
    op.execute(
        "UPDATE users SET token_hash = t.token_hash FROM ("
        " SELECT DISTINCT ON (user_id) user_id, token_hash FROM tokens"
        " WHERE expires_at IS NULL OR expires_at > now()"
        " ORDER BY user_id, created_at DESC, id DESC"
        ") t WHERE users.id = t.user_id"
    )
    op.drop_table("tokens")
