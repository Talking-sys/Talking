"""Optional encrypted destination on login_challenges (email magic links).

Revision ID: 002_email_challenge_target
Revises: 001_auth_foundation
Create Date: 2026-11-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_email_challenge_target"
down_revision: Union[str, Sequence[str], None] = "001_auth_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "login_challenges",
        sa.Column("target_enc", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("login_challenges", "target_enc")
