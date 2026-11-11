"""Group policies, audit log, reputation and announcements."""

import sqlalchemy as sa
from alembic import op

revision = "0002_features"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feature_options",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )
    op.create_table(
        "allowlist",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), primary_key=True),
    )
    op.create_table(
        "moderation_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger()),
        sa.Column("actor_id", sa.BigInteger()),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_moderation_log_chat_id", "moderation_log", ["chat_id", "id"])
    op.create_index(
        "ux_moderation_appeal", "moderation_log", ["chat_id", "user_id", "action"],
        unique=True, postgresql_where=sa.text("action LIKE 'appeal:%'"),
    )
    op.create_table(
        "reputation_votes",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("voter_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("day", sa.BigInteger(), primary_key=True),
    )
    op.create_table(
        "announcements",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("next_at", sa.BigInteger(), nullable=False),
    )


def downgrade():
    for table in ("announcements", "reputation_votes", "moderation_log", "allowlist", "feature_options"):
        op.drop_table(table)
