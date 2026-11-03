"""Passkeys: WebAuthn credential public keys, sign counts, and clone flag.

Revision ID: 002_webauthn_credentials
Revises: 001_auth_foundation
Create Date: 2026-11-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_webauthn_credentials"
down_revision: Union[str, Sequence[str], None] = "001_auth_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webauthn_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transports", postgresql.JSONB(), nullable=True),
        sa.Column("aaguid", sa.String(36), nullable=True),
        sa.Column("backup_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("backup_state", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("device_label", sa.String(80), nullable=True),
        sa.Column("cloned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("credential_id"),
        sa.UniqueConstraint("identity_id"),
    )
    op.create_index("ix_webauthn_credentials_user_id", "webauthn_credentials", ["user_id"])
    op.execute(
        "COMMENT ON TABLE webauthn_credentials IS "
        "'Passkey public keys only. No attestation blobs, no display names from the authenticator.'"
    )


def downgrade() -> None:
    op.drop_table("webauthn_credentials")
