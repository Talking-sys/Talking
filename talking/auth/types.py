"""Dataclasses shared by memory and Postgres auth storage."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class UserRecord:
    id: str
    status: str = "active"  # active | deleting | deleted
    nickname: str | None = None
    native_lang: str | None = None
    ui_lang: str | None = None
    profile_private: bool = True
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


@dataclass
class IdentityRecord:
    id: str
    user_id: str
    provider: str
    subject: str
    email_hash: str | None = None
    email_enc: str | None = None
    email_verified: bool = False
    phone_enc: str | None = None
    created_at: datetime = field(default_factory=_now)


@dataclass
class SessionRecord:
    id: str
    user_id: str
    family_id: str
    refresh_hash: str
    version: int = 1
    created_at: datetime = field(default_factory=_now)
    last_seen_at: datetime = field(default_factory=_now)
    expires_at: datetime = field(default_factory=lambda: _now() + timedelta(days=30))
    revoked_at: datetime | None = None
    replaced_by: str | None = None
    ip_hash: str | None = None
    device_label: str | None = None
    auth_at: datetime = field(default_factory=_now)

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def rotated(self) -> bool:
        return self.replaced_by is not None


@dataclass
class RecoveryRecord:
    id: str
    user_id: str
    code_hash: str
    created_at: datetime = field(default_factory=_now)
    used_at: datetime | None = None


@dataclass
class AuthEventRecord:
    id: str
    event: str
    created_at: datetime = field(default_factory=_now)
    user_id: str | None = None
    ip_hash: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChallengeRecord:
    id: str
    kind: str
    subject_hash: str
    channel: str
    provider: str
    created_at: datetime = field(default_factory=_now)
    expires_at: datetime = field(default_factory=lambda: _now() + timedelta(minutes=10))
    provider_ref: str | None = None
    code_hash: str | None = None
    attempts: int = 0
    max_attempts: int = 5
    consumed_at: datetime | None = None
    ip_hash: str | None = None
    user_id: str | None = None
    target_enc: str | None = None
