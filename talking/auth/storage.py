"""Auth storage protocol plus in-memory and Postgres implementations."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from .crypto import new_id, utcnow
from .types import (
    AuthEventRecord,
    ChallengeRecord,
    IdentityRecord,
    RecoveryRecord,
    SessionRecord,
    UserRecord,
)


class AuthStorage(Protocol):
    async def create_user(self, user: UserRecord) -> UserRecord: ...
    async def get_user(self, user_id: str) -> UserRecord | None: ...
    async def get_user_by_nickname(self, nickname: str) -> UserRecord | None: ...
    async def save_user(self, user: UserRecord) -> UserRecord: ...
    async def create_identity(self, ident: IdentityRecord) -> IdentityRecord: ...
    async def get_identity(self, provider: str, subject: str) -> IdentityRecord | None: ...
    async def get_identity_by_id(self, ident_id: str) -> IdentityRecord | None: ...
    async def list_identities(self, user_id: str) -> list[IdentityRecord]: ...
    async def save_identity(self, ident: IdentityRecord) -> IdentityRecord: ...
    async def delete_identity(self, ident_id: str) -> bool: ...
    async def create_session(self, sess: SessionRecord) -> SessionRecord: ...
    async def get_session(self, session_id: str) -> SessionRecord | None: ...
    async def get_session_by_refresh_hash(self, refresh_hash: str) -> SessionRecord | None: ...
    async def list_sessions(self, user_id: str, *, include_revoked: bool = False) -> list[SessionRecord]: ...
    async def save_session(self, sess: SessionRecord) -> SessionRecord: ...
    async def revoke_family(self, family_id: str, *, now: datetime | None = None) -> int: ...
    async def revoke_user_sessions(self, user_id: str, *, now: datetime | None = None) -> int: ...
    async def create_recovery(self, rec: RecoveryRecord) -> RecoveryRecord: ...
    async def get_recovery_by_hash(self, code_hash: str) -> RecoveryRecord | None: ...
    async def save_recovery(self, rec: RecoveryRecord) -> RecoveryRecord: ...
    async def log_event(self, ev: AuthEventRecord) -> None: ...
    async def create_challenge(self, ch: ChallengeRecord) -> ChallengeRecord: ...
    async def get_challenge(self, challenge_id: str) -> ChallengeRecord | None: ...
    async def latest_challenge(self, subject_hash: str, kind: str) -> ChallengeRecord | None: ...
    async def save_challenge(self, ch: ChallengeRecord) -> ChallengeRecord: ...
    async def aclose(self) -> None: ...


class MemoryAuthStorage:
    """In-process store for tests and AUTH_MODE=local."""

    def __init__(self):
        self._lock = threading.Lock()
        self.users: dict[str, UserRecord] = {}
        self.identities: dict[str, IdentityRecord] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.recovery: dict[str, RecoveryRecord] = {}
        self.events: list[AuthEventRecord] = []
        self.challenges: dict[str, ChallengeRecord] = {}

    async def create_user(self, user: UserRecord) -> UserRecord:
        with self._lock:
            self.users[user.id] = user
        return user

    async def get_user(self, user_id: str) -> UserRecord | None:
        return self.users.get(user_id)

    async def get_user_by_nickname(self, nickname: str) -> UserRecord | None:
        want = (nickname or "").strip()
        if not want:
            return None
        for user in self.users.values():
            if (user.nickname or "") == want:
                return user
        return None

    async def save_user(self, user: UserRecord) -> UserRecord:
        with self._lock:
            self.users[user.id] = user
        return user

    async def create_identity(self, ident: IdentityRecord) -> IdentityRecord:
        with self._lock:
            for existing in self.identities.values():
                if existing.provider == ident.provider and existing.subject == ident.subject:
                    raise ValueError("identity exists")
            self.identities[ident.id] = ident
        return ident

    async def get_identity(self, provider: str, subject: str) -> IdentityRecord | None:
        for ident in self.identities.values():
            if ident.provider == provider and ident.subject == subject:
                return ident
        return None

    async def get_identity_by_id(self, ident_id: str) -> IdentityRecord | None:
        return self.identities.get(ident_id)

    async def list_identities(self, user_id: str) -> list[IdentityRecord]:
        return [i for i in self.identities.values() if i.user_id == user_id]

    async def save_identity(self, ident: IdentityRecord) -> IdentityRecord:
        with self._lock:
            self.identities[ident.id] = ident
        return ident

    async def delete_identity(self, ident_id: str) -> bool:
        with self._lock:
            return self.identities.pop(ident_id, None) is not None

    async def create_session(self, sess: SessionRecord) -> SessionRecord:
        with self._lock:
            self.sessions[sess.id] = sess
        return sess

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self.sessions.get(session_id)

    async def get_session_by_refresh_hash(self, refresh_hash: str) -> SessionRecord | None:
        for sess in self.sessions.values():
            if sess.refresh_hash == refresh_hash:
                return sess
        return None

    async def list_sessions(self, user_id: str, *, include_revoked: bool = False) -> list[SessionRecord]:
        out = [s for s in self.sessions.values() if s.user_id == user_id]
        if not include_revoked:
            out = [s for s in out if not s.revoked]
        return sorted(out, key=lambda s: s.created_at, reverse=True)

    async def save_session(self, sess: SessionRecord) -> SessionRecord:
        with self._lock:
            self.sessions[sess.id] = sess
        return sess

    async def revoke_family(self, family_id: str, *, now: datetime | None = None) -> int:
        now = now or utcnow()
        n = 0
        with self._lock:
            for sess in self.sessions.values():
                if sess.family_id == family_id and sess.revoked_at is None:
                    sess.revoked_at = now
                    n += 1
        return n

    async def revoke_user_sessions(self, user_id: str, *, now: datetime | None = None) -> int:
        now = now or utcnow()
        n = 0
        with self._lock:
            for sess in self.sessions.values():
                if sess.user_id == user_id and sess.revoked_at is None:
                    sess.revoked_at = now
                    n += 1
        return n

    async def create_recovery(self, rec: RecoveryRecord) -> RecoveryRecord:
        with self._lock:
            self.recovery[rec.id] = rec
        return rec

    async def get_recovery_by_hash(self, code_hash: str) -> RecoveryRecord | None:
        for rec in self.recovery.values():
            if rec.code_hash == code_hash:
                return rec
        return None

    async def save_recovery(self, rec: RecoveryRecord) -> RecoveryRecord:
        with self._lock:
            self.recovery[rec.id] = rec
        return rec

    async def log_event(self, ev: AuthEventRecord) -> None:
        with self._lock:
            self.events.append(ev)

    async def create_challenge(self, ch: ChallengeRecord) -> ChallengeRecord:
        with self._lock:
            self.challenges[ch.id] = ch
        return ch

    async def get_challenge(self, challenge_id: str) -> ChallengeRecord | None:
        return self.challenges.get(challenge_id)

    async def latest_challenge(self, subject_hash: str, kind: str) -> ChallengeRecord | None:
        rows = [c for c in self.challenges.values() if c.subject_hash == subject_hash and c.kind == kind]
        if not rows:
            return None
        return max(rows, key=lambda c: c.created_at)

    async def save_challenge(self, ch: ChallengeRecord) -> ChallengeRecord:
        with self._lock:
            self.challenges[ch.id] = ch
        return ch

    async def aclose(self) -> None:
        return None


def _uuid(val) -> UUID:
    return val if isinstance(val, UUID) else UUID(str(val))


def _dt(val) -> datetime:
    if val.tzinfo is None:
        return val.replace(tzinfo=timezone.utc)
    return val


class PostgresAuthStorage:
    def __init__(self, session_factory):
        self._sf = session_factory

    async def create_user(self, user: UserRecord) -> UserRecord:
        from .models import User
        async with self._sf() as db:
            row = User(
                id=_uuid(user.id), status=user.status, nickname=user.nickname,
                native_lang=user.native_lang, ui_lang=user.ui_lang,
                profile_private=user.profile_private, created_at=user.created_at,
                updated_at=user.updated_at,
            )
            db.add(row)
            await db.commit()
        return user

    async def get_user(self, user_id: str) -> UserRecord | None:
        from .models import User
        async with self._sf() as db:
            row = await db.get(User, _uuid(user_id))
            return _user_rec(row) if row else None

    async def get_user_by_nickname(self, nickname: str) -> UserRecord | None:
        from sqlalchemy import select
        from .models import User
        want = (nickname or "").strip()
        if not want:
            return None
        async with self._sf() as db:
            q = await db.execute(select(User).where(User.nickname == want))
            row = q.scalar_one_or_none()
            return _user_rec(row) if row else None

    async def save_user(self, user: UserRecord) -> UserRecord:
        from .models import User
        async with self._sf() as db:
            row = await db.get(User, _uuid(user.id))
            if row is not None:
                row.status = user.status
                row.nickname = user.nickname
                row.native_lang = user.native_lang
                row.ui_lang = user.ui_lang
                row.profile_private = user.profile_private
                row.updated_at = user.updated_at
                await db.commit()
        return user

    async def create_identity(self, ident: IdentityRecord) -> IdentityRecord:
        from .models import Identity
        async with self._sf() as db:
            row = Identity(
                id=_uuid(ident.id), user_id=_uuid(ident.user_id), provider=ident.provider,
                subject=ident.subject, email_hash=ident.email_hash, email_enc=ident.email_enc,
                email_verified=ident.email_verified, phone_enc=ident.phone_enc,
                created_at=ident.created_at,
            )
            db.add(row)
            await db.commit()
        return ident

    async def get_identity(self, provider: str, subject: str) -> IdentityRecord | None:
        from sqlalchemy import select
        from .models import Identity
        async with self._sf() as db:
            q = await db.execute(
                select(Identity).where(Identity.provider == provider, Identity.subject == subject)
            )
            row = q.scalar_one_or_none()
            return _ident_rec(row) if row else None

    async def get_identity_by_id(self, ident_id: str) -> IdentityRecord | None:
        from .models import Identity
        async with self._sf() as db:
            row = await db.get(Identity, _uuid(ident_id))
            return _ident_rec(row) if row else None

    async def list_identities(self, user_id: str) -> list[IdentityRecord]:
        from sqlalchemy import select
        from .models import Identity
        async with self._sf() as db:
            q = await db.execute(select(Identity).where(Identity.user_id == _uuid(user_id)))
            return [_ident_rec(r) for r in q.scalars().all()]

    async def save_identity(self, ident: IdentityRecord) -> IdentityRecord:
        from .models import Identity
        async with self._sf() as db:
            row = await db.get(Identity, _uuid(ident.id))
            if row is not None:
                row.email_hash = ident.email_hash
                row.email_enc = ident.email_enc
                row.email_verified = ident.email_verified
                row.phone_enc = ident.phone_enc
                await db.commit()
        return ident

    async def delete_identity(self, ident_id: str) -> bool:
        from .models import Identity
        async with self._sf() as db:
            row = await db.get(Identity, _uuid(ident_id))
            if row is None:
                return False
            await db.delete(row)
            await db.commit()
            return True

    async def create_session(self, sess: SessionRecord) -> SessionRecord:
        from .models import Session
        async with self._sf() as db:
            db.add(_sess_model(sess))
            await db.commit()
        return sess

    async def get_session(self, session_id: str) -> SessionRecord | None:
        from .models import Session
        async with self._sf() as db:
            row = await db.get(Session, _uuid(session_id))
            return _sess_rec(row) if row else None

    async def get_session_by_refresh_hash(self, refresh_hash: str) -> SessionRecord | None:
        from sqlalchemy import select
        from .models import Session
        async with self._sf() as db:
            q = await db.execute(select(Session).where(Session.refresh_hash == refresh_hash))
            row = q.scalar_one_or_none()
            return _sess_rec(row) if row else None

    async def list_sessions(self, user_id: str, *, include_revoked: bool = False) -> list[SessionRecord]:
        from sqlalchemy import select
        from .models import Session
        async with self._sf() as db:
            stmt = select(Session).where(Session.user_id == _uuid(user_id))
            if not include_revoked:
                stmt = stmt.where(Session.revoked_at.is_(None))
            q = await db.execute(stmt)
            return [_sess_rec(r) for r in q.scalars().all()]

    async def save_session(self, sess: SessionRecord) -> SessionRecord:
        from .models import Session
        async with self._sf() as db:
            row = await db.get(Session, _uuid(sess.id))
            if row is None:
                db.add(_sess_model(sess))
            else:
                row.refresh_hash = sess.refresh_hash
                row.version = sess.version
                row.last_seen_at = sess.last_seen_at
                row.expires_at = sess.expires_at
                row.revoked_at = sess.revoked_at
                row.replaced_by = _uuid(sess.replaced_by) if sess.replaced_by else None
                row.ip_hash = sess.ip_hash
                row.device_label = sess.device_label
                row.auth_at = sess.auth_at
            await db.commit()
        return sess

    async def revoke_family(self, family_id: str, *, now: datetime | None = None) -> int:
        from sqlalchemy import update
        from .models import Session
        now = now or utcnow()
        async with self._sf() as db:
            res = await db.execute(
                update(Session)
                .where(Session.family_id == _uuid(family_id), Session.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            await db.commit()
            return res.rowcount or 0

    async def revoke_user_sessions(self, user_id: str, *, now: datetime | None = None) -> int:
        from sqlalchemy import update
        from .models import Session
        now = now or utcnow()
        async with self._sf() as db:
            res = await db.execute(
                update(Session)
                .where(Session.user_id == _uuid(user_id), Session.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            await db.commit()
            return res.rowcount or 0

    async def create_recovery(self, rec: RecoveryRecord) -> RecoveryRecord:
        from .models import RecoveryCode
        async with self._sf() as db:
            db.add(RecoveryCode(
                id=_uuid(rec.id), user_id=_uuid(rec.user_id), code_hash=rec.code_hash,
                created_at=rec.created_at, used_at=rec.used_at,
            ))
            await db.commit()
        return rec

    async def get_recovery_by_hash(self, code_hash: str) -> RecoveryRecord | None:
        from sqlalchemy import select
        from .models import RecoveryCode
        async with self._sf() as db:
            q = await db.execute(select(RecoveryCode).where(RecoveryCode.code_hash == code_hash))
            row = q.scalar_one_or_none()
            if row is None:
                return None
            return RecoveryRecord(
                id=str(row.id), user_id=str(row.user_id), code_hash=row.code_hash,
                created_at=_dt(row.created_at), used_at=_dt(row.used_at) if row.used_at else None,
            )

    async def save_recovery(self, rec: RecoveryRecord) -> RecoveryRecord:
        from .models import RecoveryCode
        async with self._sf() as db:
            row = await db.get(RecoveryCode, _uuid(rec.id))
            if row is not None:
                row.used_at = rec.used_at
                await db.commit()
        return rec

    async def log_event(self, ev: AuthEventRecord) -> None:
        from .models import AuthEvent
        async with self._sf() as db:
            db.add(AuthEvent(
                id=_uuid(ev.id or new_id()),
                user_id=_uuid(ev.user_id) if ev.user_id else None,
                event=ev.event, created_at=ev.created_at, ip_hash=ev.ip_hash, extra=ev.meta or {},
            ))
            await db.commit()

    async def create_challenge(self, ch: ChallengeRecord) -> ChallengeRecord:
        from .models import LoginChallenge
        async with self._sf() as db:
            db.add(_chal_model(ch))
            await db.commit()
        return ch

    async def get_challenge(self, challenge_id: str) -> ChallengeRecord | None:
        from .models import LoginChallenge
        async with self._sf() as db:
            row = await db.get(LoginChallenge, _uuid(challenge_id))
            return _chal_rec(row) if row else None

    async def latest_challenge(self, subject_hash: str, kind: str) -> ChallengeRecord | None:
        from sqlalchemy import desc, select
        from .models import LoginChallenge
        async with self._sf() as db:
            q = await db.execute(
                select(LoginChallenge)
                .where(LoginChallenge.subject_hash == subject_hash, LoginChallenge.kind == kind)
                .order_by(desc(LoginChallenge.created_at))
                .limit(1)
            )
            row = q.scalar_one_or_none()
            return _chal_rec(row) if row else None

    async def save_challenge(self, ch: ChallengeRecord) -> ChallengeRecord:
        from .models import LoginChallenge
        async with self._sf() as db:
            row = await db.get(LoginChallenge, _uuid(ch.id))
            if row is None:
                db.add(_chal_model(ch))
            else:
                row.attempts = ch.attempts
                row.consumed_at = ch.consumed_at
                row.provider_ref = ch.provider_ref
                row.code_hash = ch.code_hash
            await db.commit()
        return ch

    async def aclose(self) -> None:
        return None


def _user_rec(row) -> UserRecord:
    return UserRecord(
        id=str(row.id), status=row.status, nickname=row.nickname, native_lang=row.native_lang,
        ui_lang=row.ui_lang, profile_private=row.profile_private,
        created_at=_dt(row.created_at), updated_at=_dt(row.updated_at),
    )


def _ident_rec(row) -> IdentityRecord:
    return IdentityRecord(
        id=str(row.id), user_id=str(row.user_id), provider=row.provider, subject=row.subject,
        email_hash=row.email_hash, email_enc=row.email_enc, email_verified=row.email_verified,
        phone_enc=row.phone_enc, created_at=_dt(row.created_at),
    )


def _sess_rec(row) -> SessionRecord:
    return SessionRecord(
        id=str(row.id), user_id=str(row.user_id), family_id=str(row.family_id),
        refresh_hash=row.refresh_hash, version=row.version, created_at=_dt(row.created_at),
        last_seen_at=_dt(row.last_seen_at), expires_at=_dt(row.expires_at),
        revoked_at=_dt(row.revoked_at) if row.revoked_at else None,
        replaced_by=str(row.replaced_by) if row.replaced_by else None,
        ip_hash=row.ip_hash, device_label=row.device_label, auth_at=_dt(row.auth_at),
    )


def _sess_model(sess: SessionRecord):
    from .models import Session
    return Session(
        id=_uuid(sess.id), user_id=_uuid(sess.user_id), family_id=_uuid(sess.family_id),
        refresh_hash=sess.refresh_hash, version=sess.version, created_at=sess.created_at,
        last_seen_at=sess.last_seen_at, expires_at=sess.expires_at, revoked_at=sess.revoked_at,
        replaced_by=_uuid(sess.replaced_by) if sess.replaced_by else None,
        ip_hash=sess.ip_hash, device_label=sess.device_label, auth_at=sess.auth_at,
    )


def _chal_rec(row) -> ChallengeRecord:
    return ChallengeRecord(
        id=str(row.id), kind=row.kind, subject_hash=row.subject_hash, channel=row.channel,
        provider=row.provider, provider_ref=row.provider_ref, code_hash=row.code_hash,
        attempts=row.attempts, max_attempts=row.max_attempts, created_at=_dt(row.created_at),
        expires_at=_dt(row.expires_at), consumed_at=_dt(row.consumed_at) if row.consumed_at else None,
        ip_hash=row.ip_hash, user_id=str(row.user_id) if row.user_id else None,
    )


def _chal_model(ch: ChallengeRecord):
    from .models import LoginChallenge
    return LoginChallenge(
        id=_uuid(ch.id), kind=ch.kind, subject_hash=ch.subject_hash, channel=ch.channel,
        provider=ch.provider, provider_ref=ch.provider_ref, code_hash=ch.code_hash,
        attempts=ch.attempts, max_attempts=ch.max_attempts, created_at=ch.created_at,
        expires_at=ch.expires_at, consumed_at=ch.consumed_at, ip_hash=ch.ip_hash,
        user_id=_uuid(ch.user_id) if ch.user_id else None,
    )
