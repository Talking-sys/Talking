"""Session, anonymous, recovery, phone, and Google OIDC auth operations."""
from __future__ import annotations

import hmac
import logging
from datetime import timedelta
from typing import Any

from .crypto import (
    ACCESS_TTL_S,
    CSRF_TTL_S,
    FRESH_AUTH_S,
    OAUTH_TTL_S,
    PHONE_OTP_TTL_S,
    REFRESH_TTL_S,
    decrypt_pii,
    email_hmac,
    encrypt_pii,
    google_subject,
    hash_token,
    ip_hash,
    new_id,
    new_recovery_code,
    new_refresh_token,
    phone_subject,
    user_log_id,
    utcnow,
)
from .google import (
    GOOGLE_SCOPES,
    GoogleOidc,
    GoogleOidcError,
    PLATFORMS,
    is_https_redirect_uri,
    new_oauth_token,
    new_pkce_verifier,
    pkce_s256_challenge,
    unverified_nonce,
)
from .providers import PhoneProvider, StartResult
from .storage import AuthStorage
from .types import (
    ChallengeRecord,
    IdentityRecord,
    OAuthStateRecord,
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
        google: GoogleOidc | None = None,
        sign_fn=None,
        allow_debug_otp: bool = False,
    ):
        self.store = store
        self.secret = secret
        self.log_salt = log_salt
        self.enc_key = enc_key
        self.phone_provider = phone_provider
        self.google = google
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

    # --- Google OIDC ---

    def google_status(self, *, accounts: bool) -> dict:
        if not accounts or self.google is None:
            return {
                "enabled": False,
                "platforms": {p: False for p in PLATFORMS},
                "scopes": list(GOOGLE_SCOPES),
            }
        return self.google.public_status()

    def _google_ready(self, platform: str | None = None) -> GoogleOidc:
        if self.google is None or not self.google.enabled():
            raise AuthError(503, "google_unconfigured")
        if platform is not None and not self.google.platform_ready(platform):
            raise AuthError(400, "unsupported_platform")
        return self.google

    async def start_google(
        self,
        *,
        platform: str = "web",
        intent: str = "login",
        confirm: bool = False,
        user_id: str | None = None,
        session: SessionRecord | None = None,
        ip: str | None = None,
    ) -> dict:
        platform = (platform or "web").strip().lower()
        intent = (intent or "login").strip().lower()
        if platform not in PLATFORMS:
            raise AuthError(400, "unsupported_platform")
        if intent not in ("login", "link"):
            raise AuthError(400, "invalid_intent")
        oidc = self._google_ready(platform)
        if intent == "link":
            if not user_id or session is None:
                raise AuthError(401, "not signed in")
            if not confirm:
                raise AuthError(400, "confirm_required")
            if not self.session_is_fresh(session):
                raise AuthError(403, "fresh_session_required")
        else:
            user_id = None
        redirect_uri = oidc.redirect_uri(platform)
        if not is_https_redirect_uri(redirect_uri):
            raise AuthError(503, "google_unconfigured")
        client_id = oidc.client_id(platform)
        verifier = new_pkce_verifier()
        enc = encrypt_pii(self.enc_key, verifier)
        if not enc:
            raise AuthError(503, "google_unconfigured")
        state = new_oauth_token()
        nonce = new_oauth_token()
        rec = OAuthStateRecord(
            id=new_id(),
            state_hash=hash_token(state),
            nonce_hash=hash_token(nonce),
            code_verifier_enc=enc,
            platform=platform,
            intent=intent,
            redirect_uri=redirect_uri,
            client_id=client_id,
            expires_at=utcnow() + timedelta(seconds=OAUTH_TTL_S),
            ip_hash=ip_hash(self.log_salt, ip) if ip else None,
            user_id=user_id,
        )
        await self.store.create_oauth_state(rec)
        url = oidc.authorization_url(
            client_id=client_id, redirect_uri=redirect_uri,
            state=state, nonce=nonce, challenge=pkce_s256_challenge(verifier),
        )
        await self._event("google_start", user_id=user_id, ip=ip, platform=platform, intent=intent)
        return {
            "ok": True,
            "authorization_url": url,
            "expires_in": OAUTH_TTL_S,
            "platform": platform,
            "intent": intent,
        }

    async def _load_google_state(self, state: str) -> OAuthStateRecord:
        raw = (state or "").strip()
        if not raw:
            raise AuthError(400, "invalid_state")
        rec = await self.store.get_oauth_state_by_hash(hash_token(raw))
        if rec is None or rec.consumed_at is not None:
            raise AuthError(401, "invalid_state")
        if rec.expires_at < utcnow():
            rec.consumed_at = utcnow()
            await self.store.save_oauth_state(rec)
            raise AuthError(401, "state_expired")
        rec.consumed_at = utcnow()
        await self.store.save_oauth_state(rec)
        return rec

    def _google_identity_fields(self, claims: dict) -> tuple[str, str | None, str | None, bool]:
        sub = str(claims.get("sub") or "")
        subject = google_subject(self.log_salt, sub)
        email = claims.get("email")
        ev = claims.get("email_verified")
        verified = ev is True or ev == "true"
        email_h = None
        email_e = None
        if isinstance(email, str) and email.strip() and verified:
            email_h = email_hmac(self.log_salt, email)
            email_e = encrypt_pii(self.enc_key, email.strip().lower())
        return subject, email_h, email_e, verified

    async def finish_google(
        self,
        *,
        code: str,
        state: str,
        error: str = "",
        ip: str | None = None,
        user_agent: str | None = None,
        current_user_id: str | None = None,
    ) -> dict:
        oidc = self._google_ready()
        if error:
            try:
                await self._load_google_state(state)
            except AuthError:
                pass
            raise AuthError(401, "oauth_denied")
        code = (code or "").strip()
        if not code:
            raise AuthError(400, "code_required")
        rec = await self._load_google_state(state)
        if not oidc.platform_ready(rec.platform):
            raise AuthError(503, "google_unconfigured")
        verifier = decrypt_pii(self.enc_key, rec.code_verifier_enc)
        if not verifier:
            raise AuthError(401, "invalid_state")
        try:
            token_body = await oidc.exchange_code(
                code=code,
                code_verifier=verifier,
                redirect_uri=rec.redirect_uri,
                client_id=rec.client_id,
                client_secret=oidc.client_secret(rec.platform),
            )
        except GoogleOidcError as e:
            raise AuthError(401 if e.code != "google_unavailable" else 503, e.code)
        id_token = str(token_body.get("id_token") or "")
        nonce = unverified_nonce(id_token)
        if not nonce or not hmac.compare_digest(hash_token(nonce), rec.nonce_hash):
            raise AuthError(401, "nonce_mismatch")
        try:
            claims = await oidc.verify_id_token(id_token, audience=rec.client_id, nonce=nonce)
        except GoogleOidcError as e:
            raise AuthError(401 if e.code != "google_unavailable" else 503, e.code)
        subject, email_h, email_e, email_verified = self._google_identity_fields(claims)
        existing = await self.store.get_identity("google", subject)
        if rec.intent == "link":
            return await self._link_google(
                rec, existing, subject, email_h, email_e, email_verified,
                current_user_id=current_user_id, ip=ip,
            )
        created = False
        if existing is None:
            # Never look up by email — matching emails do not merge accounts.
            user = UserRecord(id=new_id())
            await self.store.create_user(user)
            ident = IdentityRecord(
                id=new_id(), user_id=user.id, provider="google", subject=subject,
                email_hash=email_h, email_enc=email_e, email_verified=email_verified,
            )
            await self.store.create_identity(ident)
            created = True
        else:
            user = await self.store.get_user(existing.user_id)
            if user is None or user.status != "active":
                raise AuthError(401, "invalid_id_token")
        sess, refresh = await self.issue_session(user.id, ip=ip, user_agent=user_agent)
        await self._event("google_login", user_id=user.id, ip=ip, created=int(created), platform=rec.platform)
        return {
            "user": self.public_user(user),
            "session": sess,
            "refresh": refresh,
            "created": created,
        }

    async def _link_google(
        self,
        rec: OAuthStateRecord,
        existing: IdentityRecord | None,
        subject: str,
        email_h: str | None,
        email_e: str | None,
        email_verified: bool,
        *,
        current_user_id: str | None,
        ip: str | None,
    ) -> dict:
        if not rec.user_id:
            raise AuthError(401, "not signed in")
        if not current_user_id or current_user_id != rec.user_id:
            raise AuthError(401, "not signed in")
        user = await self.store.get_user(rec.user_id)
        if user is None or user.status != "active":
            raise AuthError(401, "not signed in")
        if existing is not None:
            if existing.user_id == user.id:
                return {"ok": True, "linked": True, "already": True}
            raise AuthError(409, "google_taken")
        ident = IdentityRecord(
            id=new_id(), user_id=user.id, provider="google", subject=subject,
            email_hash=email_h, email_enc=email_e, email_verified=email_verified,
        )
        await self.store.create_identity(ident)
        await self._event("google_link", user_id=user.id, ip=ip, platform=rec.platform)
        return {"ok": True, "linked": True, "method": {"id": ident.id, "provider": "google"}}
