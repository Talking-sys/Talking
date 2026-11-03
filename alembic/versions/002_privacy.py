"""Privacy: deletion grace fields, data_exports, 90-day events comment.

Revision ID: 002_privacy
Revises: 001_auth_foundation
Create Date: 2026-11-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_privacy"
down_revision: Union[str, Sequence[str], None] = "001_auth_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("delete_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("delete_after", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_delete_after", "users", ["status", "delete_after"])

    op.create_table(
        "data_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(200), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
    )
    op.create_index("ix_data_exports_user_id", "data_exports", ["user_id"])
    op.create_check_constraint(
        "ck_data_exports_status",
        "data_exports",
        "status IN ('pending', 'ready', 'failed', 'expired')",
    )
    op.execute(
        "COMMENT ON TABLE data_exports IS "
        "'User data-export jobs. JSON files live on disk; rows expire with AUTH_EXPORT_TTL_S.'"
    )


def downgrade() -> None:
    op.drop_table("data_exports")
    op.drop_index("ix_users_delete_after", table_name="users")
    op.drop_column("users", "delete_after")
    op.drop_column("users", "delete_requested_at")
