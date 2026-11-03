"""Session, anonymous, recovery, phone, and email auth operations."""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from html import escape as html_escape
from typing import Any

from .crypto import (
    ACCESS_TTL_S,
    CSRF_TTL_S,
    EMAIL_OTP_TTL_S,
    FRESH_AUTH_S,
    PHONE_OTP_TTL_S,
    REFRESH_TTL_S,
    email_subject,
    decrypt_pii,
    encrypt_pii,
    hash_token,
    ip_hash,
    new_id,
    new_magic_token,
    new_otp_code,
    new_recovery_code,
    new_refresh_token,
    phone_subject,
    user_log_id,
    utcnow,
)
from .email_util import EMAIL_CHALLENGE_KINDS
from .mail import MailProvider, MailSendResult
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
        mail_provider: MailProvider | None = None,
        sign_fn=None,
        allow_debug_otp: bool = False,
        app_name: str = "Talking",
    ):
        self.store = store
        self.secret = secret
        self.log_salt = log_salt
        self.enc_key = enc_key
        self.phone_provider = phone_provider
        self.mail_provider = mail_provider
        self.sign = sign_fn
        self.allow_debug_otp = allow_debug_otp
        self.app_name = app_name or "Talking"

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
        if user is None or user.status != "active":
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
        if user is None or user.status != "active":
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
            if user is None or user.status != "active":
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

    # --- email ---

    def _subject_email(self, email: str) -> str:
        return email_subject(self.log_salt, email)

    def _email_identity_fields(self, email: str) -> dict[str, Any]:
        subject = self._subject_email(email)
        return {
            "subject": subject,
            "email_hash": subject,
            "email_enc": encrypt_pii(self.enc_key, email),
            "email_verified": True,
        }

    async def _latest_email_challenge(self, subject: str):
        latest = None
        for kind in EMAIL_CHALLENGE_KINDS:
            ch = await self.store.latest_challenge(subject, kind)
            if ch is None:
                continue
            if latest is None or ch.created_at > latest.created_at:
                latest = ch
        return latest

    async def _consume_email_challenges(self, subject: str) -> None:
        now = utcnow()
        for kind in EMAIL_CHALLENGE_KINDS:
            ch = await self.store.latest_challenge(subject, kind)
            if ch is None or ch.consumed_at is not None:
                continue
            ch.consumed_at = now
            await self.store.save_challenge(ch)

    def _email_copy(
        self, *, code: str | None, link: str | None, ttl_min: int,
    ) -> tuple[str, str, str]:
        name = self.app_name
        subject = f"{name} sign-in"
        lines = [
            f"Use this to sign in to {name}.",
            f"It expires in {ttl_min} minutes and can be used once.",
            "",
        ]
        html_parts = [
            f"<p>Use this to sign in to {html_escape(name)}.</p>",
            f"<p>It expires in {ttl_min} minutes and can be used once.</p>",
        ]
        if code:
            lines.extend([f"Code: {code}", ""])
            html_parts.append(f"<p>Code: {html_escape(code)}</p>")
        if link:
            lines.extend([f"Link: {link}", ""])
            html_parts.append(
                f'<p><a href="{html_escape(link, quote=True)}">Open sign-in link</a></p>'
            )
        lines.append("If you did not request this, ignore this message.")
        html_parts.append("<p>If you did not request this, ignore this message.</p>")
        return subject, "\n".join(lines), "".join(html_parts)

    def _magic_link_url(self, origin: str, token: str) -> str:
        return f"{origin.rstrip('/')}/api/auth/email/consume?token={token}"

    async def start_email(
        self,
        email: str,
        channel: str,
        *,
        ip: str | None = None,
        user_id: str | None = None,
        origin: str = "",
    ) -> dict:
        if self.mail_provider is None:
            raise AuthError(503, "email_unconfigured")
        if channel not in ("code", "link", "both"):
            raise AuthError(400, "invalid_channel")
        if channel in ("link", "both") and not (origin or "").strip():
            raise AuthError(400, "origin_required")
        subject = self._subject_email(email)
        latest = await self._latest_email_challenge(subject)
        if latest and (utcnow() - latest.created_at).total_seconds() < 60:
            raise AuthError(429, "resend_cooldown")
        want_code = channel in ("code", "both")
        want_link = channel in ("link", "both")
        code = new_otp_code() if want_code else None
        token = new_magic_token() if want_link else None
        ttl_min = EMAIL_OTP_TTL_S // 60
        link = self._magic_link_url(origin, token) if token else None
        mail_subject, text, html = self._email_copy(code=code, link=link, ttl_min=ttl_min)
        result: MailSendResult = await self.mail_provider.send(
            to_email=email, subject=mail_subject, text=text, html=html,
        )
        if not result.ok:
            raise AuthError(502, result.error or "provider_error")
        now = utcnow()
        expires = now + timedelta(seconds=EMAIL_OTP_TTL_S)
        ip_h = ip_hash(self.log_salt, ip) if ip else None
        target_enc = encrypt_pii(self.enc_key, email)
        primary_id = None
        if code:
            ch = ChallengeRecord(
                id=new_id(), kind="email_otp", subject_hash=subject,
                channel="code", provider=result.provider, provider_ref=result.provider,
                code_hash=hash_token(code), expires_at=expires,
                ip_hash=ip_h, user_id=user_id, target_enc=target_enc,
            )
            await self.store.create_challenge(ch)
            primary_id = ch.id
        if token:
            ch = ChallengeRecord(
                id=new_id(), kind="email_link", subject_hash=subject,
                channel="link", provider=result.provider, provider_ref=result.provider,
                code_hash=hash_token(token), expires_at=expires,
                ip_hash=ip_h, user_id=user_id, target_enc=target_enc,
            )
            await self.store.create_challenge(ch)
            if primary_id is None:
                primary_id = ch.id
        await self._event("email_start", user_id=user_id, ip=ip, channel=channel)
        body = {
            "ok": True,
            "channel": channel,
            "expires_in": EMAIL_OTP_TTL_S,
            "resend_after": 60,
            "challenge_id": primary_id,
        }
        if self.allow_debug_otp and code:
            body["debug_code"] = code
        if self.allow_debug_otp and token:
            body["debug_token"] = token
        return body

    async def _check_email_code(self, email: str, code: str) -> ChallengeRecord:
        code = (code or "").strip()
        if not code:
            raise AuthError(400, "code_required")
        ch = await self.store.latest_challenge(self._subject_email(email), "email_otp")
        if ch is None or ch.consumed_at is not None:
            raise AuthError(401, "invalid_code")
        if ch.expires_at < utcnow():
            raise AuthError(401, "code_expired")
        if ch.attempts >= ch.max_attempts:
            raise AuthError(429, "attempts_exhausted")
        ch.attempts += 1
        stored = ch.code_hash or ""
        if len(stored) != 64 or not secrets.compare_digest(hash_token(code), stored):
            await self.store.save_challenge(ch)
            if ch.attempts >= ch.max_attempts:
                raise AuthError(429, "attempts_exhausted")
            raise AuthError(401, "invalid_code")
        ch.consumed_at = utcnow()
        await self.store.save_challenge(ch)
        await self._consume_email_challenges(ch.subject_hash)
        return ch

    async def _check_email_token(self, token: str) -> ChallengeRecord:
        token = (token or "").strip()
        if not token:
            raise AuthError(400, "token_required")
        ch = await self.store.get_challenge_by_code_hash(hash_token(token))
        if ch is None or ch.kind != "email_link" or ch.consumed_at is not None:
            raise AuthError(401, "invalid_code")
        if ch.expires_at < utcnow():
            raise AuthError(401, "code_expired")
        if ch.attempts >= ch.max_attempts:
            raise AuthError(429, "attempts_exhausted")
        ch.attempts += 1
        ch.consumed_at = utcnow()
        await self.store.save_challenge(ch)
        await self._consume_email_challenges(ch.subject_hash)
        return ch

    async def _session_for_email(
        self, email: str, *, ip: str | None = None, user_agent: str | None = None,
    ) -> dict:
        subject = self._subject_email(email)
        ident = await self.store.get_identity("email", subject)
        created = False
        if ident is None:
            user = UserRecord(id=new_id())
            await self.store.create_user(user)
            fields = self._email_identity_fields(email)
            ident = IdentityRecord(
                id=new_id(), user_id=user.id, provider="email", **fields,
            )
            await self.store.create_identity(ident)
            created = True
        else:
            user = await self.store.get_user(ident.user_id)
            if user is None or user.status != "active":
                raise AuthError(401, "invalid_code")
        sess, refresh = await self.issue_session(user.id, ip=ip, user_agent=user_agent)
        return {
            "user": self.public_user(user),
            "session": sess,
            "refresh": refresh,
            "created": created,
        }

    async def verify_email(
        self, email: str, code: str, *, ip: str | None = None, user_agent: str | None = None,
    ) -> dict:
        await self._check_email_code(email, code)
        out = await self._session_for_email(email, ip=ip, user_agent=user_agent)
        await self._event("email_verify", user_id=out["user"]["id"], ip=ip, created=int(out["created"]))
        return out

    def _email_from_challenge(self, ch: ChallengeRecord) -> str | None:
        return decrypt_pii(self.enc_key, ch.target_enc)

    async def consume_email_token(
        self, token: str, *, ip: str | None = None, user_agent: str | None = None,
    ) -> dict:
        ch = await self._check_email_token(token)
        email = self._email_from_challenge(ch)
        ident = await self.store.get_identity("email", ch.subject_hash)
        created = False
        if ident is None:
            user = UserRecord(id=new_id())
            await self.store.create_user(user)
            ident = IdentityRecord(
                id=new_id(), user_id=user.id, provider="email",
                subject=ch.subject_hash, email_hash=ch.subject_hash,
                email_enc=encrypt_pii(self.enc_key, email) if email else ch.target_enc,
                email_verified=True,
            )
            await self.store.create_identity(ident)
            created = True
        else:
            user = await self.store.get_user(ident.user_id)
            if user is None or user.status != "active":
                raise AuthError(401, "invalid_code")
        sess, refresh = await self.issue_session(user.id, ip=ip, user_agent=user_agent)
        await self._event("email_verify", user_id=user.id, ip=ip, created=int(created), channel="link")
        return {
            "user": self.public_user(user),
            "session": sess,
            "refresh": refresh,
            "created": created,
        }

    async def link_email(
        self,
        user: UserRecord,
        session: SessionRecord,
        *,
        email: str | None = None,
        code: str | None = None,
        token: str | None = None,
        ip: str | None = None,
    ) -> dict:
        if not self.session_is_fresh(session):
            raise AuthError(403, "fresh_session_required")
        if token:
            ch = await self._check_email_token(token)
            subject = ch.subject_hash
            email = email or self._email_from_challenge(ch)
        else:
            if not email:
                raise AuthError(400, "email_required")
            await self._check_email_code(email, code or "")
            subject = self._subject_email(email)
        existing = await self.store.get_identity("email", subject)
        if existing is not None:
            if existing.user_id == user.id:
                return {"ok": True, "linked": True, "already": True}
            raise AuthError(409, "email_taken")
        fields = {
            "subject": subject,
            "email_hash": subject,
            "email_enc": encrypt_pii(self.enc_key, email) if email else None,
            "email_verified": True,
        }
        ident = IdentityRecord(
            id=new_id(), user_id=user.id, provider="email", **fields,
        )
        await self.store.create_identity(ident)
        await self._event("email_link", user_id=user.id, ip=ip)
        return {"ok": True, "linked": True, "method": {"id": ident.id, "provider": "email"}}

