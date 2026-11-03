"""Anonymous accounts: last_seen_at for inactivity expiry.

Revision ID: 002_anonymous_accounts
Revises: 001_auth_foundation
Create Date: 2026-11-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_anonymous_accounts"
down_revision: Union[str, Sequence[str], None] = "001_auth_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE users SET last_seen_at = created_at WHERE last_seen_at IS NULL")
    op.alter_column("users", "last_seen_at", nullable=False)
    op.create_index("ix_users_last_seen_at", "users", ["last_seen_at"])
    op.create_index("ix_users_status", "users", ["status"])
    op.create_index("ix_identities_provider", "identities", ["provider"])


def downgrade() -> None:
    op.drop_index("ix_identities_provider", table_name="identities")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_index("ix_users_last_seen_at", table_name="users")
    op.drop_column("users", "last_seen_at")
