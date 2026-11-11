"""Snapshots for reversible moderation deletions."""

import sqlalchemy as sa
from alembic import op

revision = "0003_recovery"
down_revision = "0002_features"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "moderation_recovery",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("message_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("moderation_recovery")
