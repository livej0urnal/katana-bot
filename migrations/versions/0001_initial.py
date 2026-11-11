"""Initial PostgreSQL schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "groups",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("chat_type", sa.Text(), nullable=False),
        sa.Column("bot_status", sa.Text(), nullable=False),
    )
    op.create_table(
        "link_filter",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True, autoincrement=False),
    )
    op.create_table(
        "reports",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("message_id", sa.BigInteger(), primary_key=True),
        sa.Column("reporter_id", sa.BigInteger(), primary_key=True),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("rowid", sa.BigInteger(), sa.Identity(), nullable=False),
    )
    op.create_table(
        "profanity_settings",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.CheckConstraint("mode IN ('log', 'delete')"),
    )
    op.create_table(
        "autoban",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True, autoincrement=False),
    )
    op.create_table(
        "profanity_strikes",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("message_id", sa.BigInteger(), primary_key=True),
        sa.Column("recorded_at", sa.BigInteger(), nullable=False),
    )


def downgrade():
    for table in ("profanity_strikes", "autoban", "profanity_settings", "reports", "link_filter", "groups"):
        op.drop_table(table)
