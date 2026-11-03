"""Google OIDC: server-side oauth_states (PKCE verifier + nonce hash).

Revision ID: 002_google_oauth_states
Revises: 001_auth_foundation
Create Date: 2026-11-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_google_oauth_states"
down_revision: Union[str, Sequence[str], None] = "001_auth_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("code_verifier_enc", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("intent", sa.String(16), nullable=False),
        sa.Column("redirect_uri", sa.String(512), nullable=False),
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.UniqueConstraint("state_hash", name="uq_oauth_states_state_hash"),
    )
    op.execute(
        "COMMENT ON TABLE oauth_states IS "
        "'OIDC authorization-code state. PKCE verifier encrypted; nonce hashed; "
        "never store Google tokens. TTL ~10 min.'"
    )


def downgrade() -> None:
    op.drop_table("oauth_states")
