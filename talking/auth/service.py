"""Session, anonymous, recovery, and phone auth operations."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from .crypto import (
    ACCESS_TTL_S,
    CSRF_TTL_S,
    FRESH_AUTH_S,
    PHONE_OTP_TTL_S,
    REFRESH_TTL_S,
    encrypt_pii,
    hash_token,
    ip_hash,
    new_id,
    new_recovery_code,
    new_refresh_token,
    phone_subject,
    user_log_id,
    utcnow,
)
from .providers import PhoneProvider, StartResult
from .storage import AuthStorage
from .types import (
    ChallengeRecord,
    IdentityRecord,
    RecoveryRecord,
    SessionRecord,
    UserRecord,
)

log = logging.getLogger("talking.auth")


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str = "", user_id: str | None = None):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message or code
        self.user_id = user_id


class AuthService:
    def __init__(
        self,
        store: AuthStorage,
        *,
        secret: bytes,
        log_salt: bytes,
        enc_key: bytes | None,
        phone_provider: PhoneProvider | None = None,
        sign_fn=None,
        allow_debug_otp: bool = False,
    ):
        self.store = store
        self.secret = secret
        self.log_salt = log_salt
        self.enc_key = enc_key
        self.phone_provider = phone_provider
        self.sign = sign_fn
        self.allow_debug_otp = allow_debug_otp

    def uid_log(self, user_id: str) -> str:
        return user_log_id(self.log_salt, user_id)

    async def _event(self, event: str, user_id: str | None = None, ip: str | None = None, **meta: Any) -> None:
        from .types import AuthEventRecord
        await self.store.log_event(AuthEventRecord(
            id=new_id(), event=event, user_id=user_id,
            ip_hash=ip_hash(self.log_salt, ip) if ip else None, meta=meta,
        ))

    def access_token(self, session: SessionRecord) -> str:
        return self.sign(self.secret, {"sid": session.id, "v": session.version, "u": session.user_id},
                         "access", ttl=ACCESS_TTL_S)

    def csrf_token(self, session: SessionRecord) -> str:
        return self.sign(self.secret, {"sid": session.id}, "csrf", ttl=CSRF_TTL_S)

    def ws_token(self, session: SessionRecord, ttl: int) -> str:
        return self.sign(
            self.secret,
            {"u": session.user_id, "sid": session.id, "v": session.version},
            "ws", ttl=ttl,
        )

    def _device(self, user_agent: str | None) -> str | None:
        if not user_agent:
            return None
        return user_agent.strip()[:80] or None

    async def issue_session(
        self,
        user_id: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
        family_id: str | None = None,
    ) -> tuple[SessionRecord, str]:
        raw = new_refresh_token()
        now = utcnow()
        sess = SessionRecord(
            id=new_id(), user_id=user_id, family_id=family_id or new_id(),
            refresh_hash=hash_token(raw), version=1, created_at=now, last_seen_at=now,
            expires_at=now + timedelta(seconds=REFRESH_TTL_S),
            ip_hash=ip_hash(self.log_salt, ip) if ip else None,
            device_label=self._device(user_agent), auth_at=now,
        )
        await self.store.create_session(sess)
        return sess, raw

    async def rotate_refresh(self, raw_refresh: str, *, ip: str | None = None) -> tuple[SessionRecord, str]:
        found = await self.store.get_session_by_refresh_hash(hash_token(raw_refresh))
        if found is None:
            raise AuthError(401, "invalid_refresh")
        if found.replaced_by is not None:
            n = await self.store.revoke_family(found.family_id)
            await self._event("refresh_reuse", user_id=found.user_id, ip=ip, family="1")
            log.info("refresh reuse user=%s revoked=%s", self.uid_log(found.user_id), n)
            raise AuthError(401, "refresh_reuse", user_id=found.user_id)
        if found.revoked_at is not None:
            raise AuthError(401, "invalid_refresh")
        now = utcnow()
        if found.expires_at < now:
            found.revoked_at = now
            await self.store.save_session(found)
            raise AuthError(401, "invalid_refresh")
        raw = new_refresh_token()
        new_sess, _placeholder = await self.issue_session(
            found.user_id, ip=ip, user_agent=found.device_label, family_id=found.family_id,
        )
        # issue_session already stored new_sess with a random refresh; replace hash
        new_sess.refresh_hash = hash_token(raw)
        new_sess.version = found.version + 1
        new_sess.device_label = found.device_label
        new_sess.auth_at = found.auth_at  # refresh is not a fresh login
        await self.store.save_session(new_sess)
        found.revoked_at = now
        found.replaced_by = new_sess.id
        await self.store.save_session(found)
        return new_sess, raw

    async def load_access(self, payload: dict | None) -> tuple[UserRecord, SessionRecord] | None:
        if not payload:
            return None
        sid = str(payload.get("sid") or "")
        version = payload.get("v")
        sess = await self.store.get_session(sid)
        if sess is None or sess.revoked_at is not None:
            return None
        if version is not None and int(version) != int(sess.version):
            return None
        if sess.expires_at < utcnow():
            return None
        user = await self.store.get_user(sess.user_id)
        if user is None or user.status not in ("active", "deleting"):
            return None
        sess.last_seen_at = utcnow()
        await self.store.save_session(sess)
        return user, sess

    def session_is_fresh(self, sess: SessionRecord) -> bool:
        return (utcnow() - sess.auth_at).total_seconds() <= FRESH_AUTH_S

    async def create_anonymous(self, *, ip: str | None = None, user_agent: str | None = None) -> dict:
        user = UserRecord(id=new_id())
        await self.store.create_user(user)
        ident = IdentityRecord(
            id=new_id(), user_id=user.id, provider="anonymous", subject=user.id,
        )
        await self.store.create_identity(ident)
        code = new_recovery_code()
        await self.store.create_recovery(RecoveryRecord(
            id=new_id(), user_id=user.id, code_hash=hash_token(code),
        ))
        sess, refresh = await self.issue_session(user.id, ip=ip, user_agent=user_agent)
        await self._event("anonymous_create", user_id=user.id, ip=ip)
        log.info("anonymous account user=%s", self.uid_log(user.id))
        return {
            "user": self.public_user(user),
            "recovery_code": code,
            "session": sess,
            "refresh": refresh,
        }

    async def recover(self, code: str, *, ip: str | None = None, user_agent: str | None = None) -> dict:
        rec = await self.store.get_recovery_by_hash(hash_token((code or "").strip().lower()))
        # codes are stored as generated (mixed); try raw too
        if rec is None:
            rec = await self.store.get_recovery_by_hash(hash_token((code or "").strip()))
        if rec is None or rec.used_at is not None:
            raise AuthError(401, "invalid_code")
        user = await self.store.get_user(rec.user_id)
        if user is None or user.status not in ("active", "deleting"):
            raise AuthError(401, "invalid_code")
        rec.used_at = utcnow()
        await self.store.save_recovery(rec)
        sess, refresh = await self.issue_session(user.id, ip=ip, user_agent=user_agent)
        await self._event("recover", user_id=user.id, ip=ip)
        return {"user": self.public_user(user), "session": sess, "refresh": refresh}

    async def logout(self, session: SessionRecord, *, ip: str | None = None) -> None:
        session.revoked_at = utcnow()
        await self.store.save_session(session)
        await self._event("logout", user_id=session.user_id, ip=ip)

    async def logout_all(self, user_id: str, *, ip: str | None = None) -> int:
        n = await self.store.revoke_user_sessions(user_id)
        await self._event("logout_all", user_id=user_id, ip=ip)
        return n

    async def revoke_session(self, user_id: str, session_id: str) -> SessionRecord:
        sess = await self.store.get_session(session_id)
        if sess is None or sess.user_id != user_id:
            raise AuthError(404, "not_found")
        sess.revoked_at = utcnow()
        await self.store.save_session(sess)
        await self._event("session_revoke", user_id=user_id)
        return sess

    def public_user(self, user: UserRecord, *, methods: list | None = None, csrf: str | None = None) -> dict:
        body = {
            "id": user.id,
            "status": user.status,
            "nickname": user.nickname,
            "native_lang": user.native_lang,
            "ui_lang": user.ui_lang,
            "profile_private": user.profile_private,
            "created_at": user.created_at.isoformat(),
        }
        if user.status == "deleting":
            body["delete_requested_at"] = (
                user.delete_requested_at.isoformat() if user.delete_requested_at else None
            )
            body["delete_after"] = user.delete_after.isoformat() if user.delete_after else None
        if methods is not None:
            body["methods"] = methods
        if csrf is not None:
            body["csrf_token"] = csrf
        return body

    def public_session(self, sess: SessionRecord) -> dict:
        return {
            "id": sess.id,
            "family_id": sess.family_id,
            "created_at": sess.created_at.isoformat(),
            "last_seen_at": sess.last_seen_at.isoformat(),
            "expires_at": sess.expires_at.isoformat(),
            "device_label": sess.device_label,
            "current": False,
        }

    async def public_methods(self, user_id: str) -> list[dict]:
        rows = await self.store.list_identities(user_id)
        return [
            {"id": i.id, "provider": i.provider, "created_at": i.created_at.isoformat()}
            for i in rows
        ]

    # --- phone ---

    def _subject_phone(self, phone_e164: str) -> str:
        return phone_subject(self.log_salt, phone_e164)

    async def start_phone(
        self, phone_e164: str, channel: str, *, ip: str | None = None, user_id: str | None = None,
    ) -> dict:
        if self.phone_provider is None:
            raise AuthError(503, "sms_unconfigured")
        if channel not in ("sms", "call"):
            raise AuthError(400, "invalid_channel")
        latest = await self.store.latest_challenge(self._subject_phone(phone_e164), "phone_otp")
        if latest and (utcnow() - latest.created_at).total_seconds() < 60:
            raise AuthError(429, "resend_cooldown")
        result: StartResult = await self.phone_provider.start(phone_e164, channel)
        if not result.ok:
            raise AuthError(502, result.error or "provider_error")
        ch = ChallengeRecord(
            id=new_id(), kind="phone_otp", subject_hash=self._subject_phone(phone_e164),
            channel=channel, provider=result.provider, provider_ref=result.provider_ref,
            code_hash=result.code_hash, expires_at=utcnow() + timedelta(seconds=PHONE_OTP_TTL_S),
            ip_hash=ip_hash(self.log_salt, ip) if ip else None, user_id=user_id,
        )
        await self.store.create_challenge(ch)
        await self._event("phone_start", user_id=user_id, ip=ip, channel=channel)
        body = {
            "ok": True,
            "channel": channel,
            "expires_in": PHONE_OTP_TTL_S,
            "resend_after": 60,
            "challenge_id": ch.id,
        }
        if self.allow_debug_otp and result.debug_code:
            body["debug_code"] = result.debug_code
        return body

    async def _check_phone_code(self, phone_e164: str, code: str) -> ChallengeRecord:
        if self.phone_provider is None:
            raise AuthError(503, "sms_unconfigured")
        code = (code or "").strip()
        if not code:
            raise AuthError(400, "code_required")
        ch = await self.store.latest_challenge(self._subject_phone(phone_e164), "phone_otp")
        if ch is None:
            raise AuthError(401, "invalid_code")
        if ch.consumed_at is not None:
            raise AuthError(401, "invalid_code")
        if ch.expires_at < utcnow():
            raise AuthError(401, "code_expired")
        if ch.attempts >= ch.max_attempts:
            raise AuthError(429, "attempts_exhausted")
        ch.attempts += 1
        ok = await self.phone_provider.check(phone_e164, code, ch.provider_ref, ch.code_hash)
        if not ok:
            await self.store.save_challenge(ch)
            if ch.attempts >= ch.max_attempts:
                raise AuthError(429, "attempts_exhausted")
            raise AuthError(401, "invalid_code")
        ch.consumed_at = utcnow()
        await self.store.save_challenge(ch)
        return ch

    async def verify_phone(
        self, phone_e164: str, code: str, *, ip: str | None = None, user_agent: str | None = None,
    ) -> dict:
        await self._check_phone_code(phone_e164, code)
        subject = self._subject_phone(phone_e164)
        ident = await self.store.get_identity("phone", subject)
        created = False
        if ident is None:
            user = UserRecord(id=new_id())
            await self.store.create_user(user)
            ident = IdentityRecord(
                id=new_id(), user_id=user.id, provider="phone", subject=subject,
                phone_enc=encrypt_pii(self.enc_key, phone_e164),
            )
            await self.store.create_identity(ident)
            created = True
        else:
            user = await self.store.get_user(ident.user_id)
            if user is None or user.status not in ("active", "deleting"):
                raise AuthError(401, "invalid_code")
        sess, refresh = await self.issue_session(user.id, ip=ip, user_agent=user_agent)
        await self._event("phone_verify", user_id=user.id, ip=ip, created=int(created))
        return {
            "user": self.public_user(user),
            "session": sess,
            "refresh": refresh,
            "created": created,
        }

    async def link_phone(
        self, user: UserRecord, session: SessionRecord, phone_e164: str, code: str, *, ip: str | None = None,
    ) -> dict:
        if not self.session_is_fresh(session):
            raise AuthError(403, "fresh_session_required")
        await self._check_phone_code(phone_e164, code)
        subject = self._subject_phone(phone_e164)
        existing = await self.store.get_identity("phone", subject)
        if existing is not None:
            if existing.user_id == user.id:
                return {"ok": True, "linked": True, "already": True}
            # Never auto-merge.
            raise AuthError(409, "phone_taken")
        ident = IdentityRecord(
            id=new_id(), user_id=user.id, provider="phone", subject=subject,
            phone_enc=encrypt_pii(self.enc_key, phone_e164),
        )
        await self.store.create_identity(ident)
        await self._event("phone_link", user_id=user.id, ip=ip)
        return {"ok": True, "linked": True, "method": {"id": ident.id, "provider": "phone"}}

    async def unlink_method(self, user_id: str, ident_id: str) -> None:
        ident = await self.store.get_identity_by_id(ident_id)
        if ident is None or ident.user_id != user_id:
            raise AuthError(404, "not_found")
        methods = await self.store.list_identities(user_id)
        if len(methods) <= 1:
            raise AuthError(400, "last_method")
        await self.store.delete_identity(ident_id)
        await self._event("method_unlink", user_id=user_id, provider=ident.provider)
