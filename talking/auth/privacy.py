"""GDPR-style account deletion, data export, and user-facing audit events.

File stores (chat, media, history, archive, export JSON) are wiped/collected via
callables bound after chat registration so this module stays import-safe.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from talking.ids import check_id
from talking.security import unsign

from .crypto import (
    AUTH_EVENTS_RETENTION_DAYS,
    DELETE_GRACE_S,
    EXPORT_DOWNLOAD_TTL_S,
    EXPORT_TTL_S,
    decrypt_pii,
    new_id,
    utcnow,
)
from .service import AuthError, AuthService
from .sockets import SocketRegistry
from .types import ExportRecord, IdentityRecord, UserRecord

log = logging.getLogger("talking.auth")

# Shown to the account owner. Never include hashes, tokens, phones, IPs.
SAFE_EVENT_META = frozenset({
    "channel", "created", "provider", "family", "already", "linked", "grace_s",
})
USER_AUDIT_EVENTS = frozenset({
    "anonymous_create", "recover", "phone_start", "phone_verify", "phone_link",
    "method_unlink", "logout", "logout_all", "session_revoke", "refresh_reuse",
    "account_delete_requested", "account_delete_cancelled", "account_deleted",
    "export_requested", "export_ready", "export_failed",
})

CollectFn = Callable[[str], dict[str, Any]]
EraseFn = Callable[[str], dict[str, int]]


class PrivacyService:
    def __init__(
        self,
        auth: AuthService,
        *,
        sockets: SocketRegistry,
        data_dir: str | Path,
        grace_s: int = DELETE_GRACE_S,
        export_ttl_s: int = EXPORT_TTL_S,
        download_ttl_s: int = EXPORT_DOWNLOAD_TTL_S,
        events_retention_days: int = AUTH_EVENTS_RETENTION_DAYS,
        collect: CollectFn | None = None,
        erase: EraseFn | None = None,
    ):
        self.auth = auth
        self.sockets = sockets
        self.data_dir = Path(data_dir)
        self.grace_s = max(0, int(grace_s))
        self.export_ttl_s = max(60, int(export_ttl_s))
        self.download_ttl_s = max(60, int(download_ttl_s))
        self.events_retention_days = max(1, int(events_retention_days))
        self.collect = collect or (lambda _uid: {})
        self.erase = erase or (lambda _uid: {})

    def bind(self, collect: CollectFn, erase: EraseFn) -> None:
        self.collect = collect
        self.erase = erase

    def _exports_dir(self, user_id: str) -> Path:
        return self.data_dir / "exports" / check_id(user_id, "user_id")

    def export_path(self, user_id: str, export_id: str) -> Path:
        return self._exports_dir(user_id) / f"{check_id(export_id, 'export_id')}.json"

    def account_view(self, user: UserRecord) -> dict[str, Any]:
        body: dict[str, Any] = {
            "status": user.status,
            "grace_s": self.grace_s,
            "can_cancel": user.status == "deleting",
            "delete_requested_at": user.delete_requested_at.isoformat() if user.delete_requested_at else None,
            "delete_after": user.delete_after.isoformat() if user.delete_after else None,
        }
        return body

    async def request_deletion(self, user: UserRecord, session, *, ip: str | None = None,
                               confirm: str = "") -> dict[str, Any]:
        if (confirm or "").strip() != "DELETE":
            raise AuthError(400, "confirm_required")
        if not self.auth.session_is_fresh(session):
            raise AuthError(403, "fresh_session_required")
        if user.status == "deleted":
            raise AuthError(401, "account_deleted")
        if user.status == "deleting" and user.delete_after is not None:
            await self.auth._event("account_delete_requested", user_id=user.id, ip=ip, grace_s=self.grace_s)
            return self.account_view(user)
        now = utcnow()
        user.status = "deleting"
        user.delete_requested_at = now
        user.delete_after = now + timedelta(seconds=self.grace_s)
        user.updated_at = now
        await self.auth.store.save_user(user)
        await self.auth._event("account_delete_requested", user_id=user.id, ip=ip, grace_s=self.grace_s)
        log.info("delete requested user=%s grace_s=%s", self.auth.uid_log(user.id), self.grace_s)
        if self.grace_s <= 0:
            await self.complete_deletion(user.id, ip=ip)
            user = await self.auth.store.get_user(user.id) or user
        return self.account_view(user)

    async def cancel_deletion(self, user: UserRecord, *, ip: str | None = None) -> dict[str, Any]:
        if user.status != "deleting":
            raise AuthError(400, "not_pending")
        now = utcnow()
        if user.delete_after is not None and user.delete_after <= now:
            # Due now — complete rather than racing a cancel past the deadline.
            await self.complete_deletion(user.id, ip=ip)
            raise AuthError(409, "delete_due")
        user.status = "active"
        user.delete_requested_at = None
        user.delete_after = None
        user.updated_at = now
        await self.auth.store.save_user(user)
        await self.auth._event("account_delete_cancelled", user_id=user.id, ip=ip)
        log.info("delete cancelled user=%s", self.auth.uid_log(user.id))
        return self.account_view(user)

    async def complete_deletion(self, user_id: str, *, ip: str | None = None) -> None:
        user = await self.auth.store.get_user(user_id)
        if user is None or user.status == "deleted":
            return
        await self.auth.store.revoke_user_sessions(user_id)
        await self.sockets.close_user(user_id, code=4010, reason="account_deleted")
        try:
            wiped = self.erase(user_id) or {}
        except Exception:
            log.exception("file wipe failed user=%s", self.auth.uid_log(user_id))
            wiped = {}
        self._rm_export_dir(user_id)
        counts = await self.auth.store.purge_account_auth_rows(user_id)
        now = utcnow()
        user.status = "deleted"
        user.nickname = None
        user.native_lang = None
        user.ui_lang = None
        user.profile_private = True
        user.delete_requested_at = user.delete_requested_at or now
        user.delete_after = now
        user.updated_at = now
        await self.auth.store.save_user(user)
        await self.auth._event("account_deleted", user_id=user_id, ip=ip, **{
            k: int(v) for k, v in {**counts, **wiped}.items() if isinstance(v, int)
        })
        log.info("account deleted user=%s rows=%s files=%s", self.auth.uid_log(user_id), counts, wiped)

    async def process_due_deletions(self) -> int:
        due = await self.auth.store.list_due_deletions()
        n = 0
        for user in due:
            await self.complete_deletion(user.id)
            n += 1
        return n

    async def purge_old_events(self) -> int:
        cutoff = utcnow() - timedelta(days=self.events_retention_days)
        return await self.auth.store.purge_events(older_than=cutoff)

    async def expire_exports(self) -> int:
        n = 0
        for rec in await self.auth.store.list_expired_exports():
            rec.status = "expired"
            await self.auth.store.save_export(rec)
            self._unlink_export(rec)
            n += 1
        return n

    async def public_events(self, user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 100))
        rows = await self.auth.store.list_events(user_id, limit=limit)
        out = []
        for ev in rows:
            if ev.event not in USER_AUDIT_EVENTS:
                continue
            meta = {k: v for k, v in (ev.meta or {}).items() if k in SAFE_EVENT_META}
            out.append({
                "id": ev.id,
                "event": ev.event,
                "created_at": ev.created_at.isoformat(),
                "detail": meta,
            })
        return out

    async def request_export(self, user: UserRecord, *, ip: str | None = None) -> ExportRecord:
        if user.status == "deleted":
            raise AuthError(401, "account_deleted")
        rec = ExportRecord(id=new_id(), user_id=user.id, status="pending")
        await self.auth.store.create_export(rec)
        await self.auth._event("export_requested", user_id=user.id, ip=ip)
        log.info("export requested user=%s job=%s", self.auth.uid_log(user.id), rec.id[:8])
        return rec

    def public_export(self, rec: ExportRecord, *, include_token: bool = False) -> dict[str, Any]:
        body = {
            "id": rec.id,
            "status": rec.status,
            "created_at": rec.created_at.isoformat(),
            "ready_at": rec.ready_at.isoformat() if rec.ready_at else None,
            "expires_at": rec.expires_at.isoformat() if rec.expires_at else None,
            "size_bytes": rec.size_bytes,
            "error": rec.error if rec.status == "failed" else None,
            "download_ttl_s": self.download_ttl_s,
        }
        if include_token and rec.status == "ready":
            body["download_token"] = self.auth.sign(
                self.auth.secret, {"eid": rec.id, "u": rec.user_id}, "export",
                ttl=self.download_ttl_s,
            )
        return body

    async def get_owned_export(self, user_id: str, export_id: str) -> ExportRecord:
        rec = await self.auth.store.get_export(export_id)
        if rec is None or rec.user_id != user_id:
            raise AuthError(404, "not_found")
        return rec

    def verify_download_token(self, user_id: str, export_id: str, token: str) -> bool:
        payload = unsign(self.auth.secret, token or "", "export")
        if not payload:
            return False
        return str(payload.get("eid") or "") == export_id and str(payload.get("u") or "") == user_id

    async def build_export(self, export_id: str) -> ExportRecord | None:
        rec = await self.auth.store.get_export(export_id)
        if rec is None or rec.status not in ("pending",):
            return rec
        try:
            archive = await self._assemble(rec.user_id)
            raw = json.dumps(archive, ensure_ascii=False, indent=2).encode("utf-8")
            path = self.export_path(rec.user_id, rec.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(path)
            now = utcnow()
            rec.status = "ready"
            rec.ready_at = now
            rec.expires_at = now + timedelta(seconds=self.export_ttl_s)
            rec.size_bytes = len(raw)
            rec.error = None
            await self.auth.store.save_export(rec)
            await self.auth._event("export_ready", user_id=rec.user_id)
            log.info("export ready user=%s job=%s bytes=%s", self.auth.uid_log(rec.user_id), rec.id[:8], rec.size_bytes)
        except Exception as exc:
            rec.status = "failed"
            rec.error = type(exc).__name__
            await self.auth.store.save_export(rec)
            await self.auth._event("export_failed", user_id=rec.user_id)
            log.exception("export failed user=%s job=%s", self.auth.uid_log(rec.user_id), rec.id[:8])
        return rec

    async def _assemble(self, user_id: str) -> dict[str, Any]:
        user = await self.auth.store.get_user(user_id)
        if user is None:
            raise AuthError(404, "not_found")
        methods = []
        for ident in await self.auth.store.list_identities(user_id):
            methods.append(_public_identity(ident, self.auth.enc_key))
        sessions = [self.auth.public_session(s) for s in await self.auth.store.list_sessions(user_id, include_revoked=True)]
        for item in sessions:
            item.pop("current", None)
        events = await self.public_events(user_id, limit=100)
        files = {}
        try:
            files = self.collect(user_id) or {}
        except Exception:
            log.exception("export collect failed user=%s", self.auth.uid_log(user_id))
        return {
            "exported_at": utcnow().isoformat(),
            "user": {
                "id": user.id,
                "status": user.status,
                "nickname": user.nickname,
                "native_lang": user.native_lang,
                "ui_lang": user.ui_lang,
                "profile_private": user.profile_private,
                "created_at": user.created_at.isoformat(),
            },
            "methods": methods,
            "sessions": sessions,
            "security_events": events,
            "chat": files.get("chat") or [],
            "attachments": files.get("attachments") or [],
            "history": files.get("history") or [],
            "archive": files.get("archive") or [],
            "settings": files.get("settings") or {},
        }

    def read_export_bytes(self, rec: ExportRecord) -> bytes:
        if rec.status != "ready":
            raise AuthError(409, "export_not_ready")
        if rec.expires_at and rec.expires_at <= utcnow():
            raise AuthError(410, "export_expired")
        path = self.export_path(rec.user_id, rec.id)
        if not path.is_file():
            raise AuthError(410, "export_expired")
        return path.read_bytes()

    def _unlink_export(self, rec: ExportRecord) -> None:
        try:
            self.export_path(rec.user_id, rec.id).unlink(missing_ok=True)
        except OSError:
            pass

    def _rm_export_dir(self, user_id: str) -> None:
        try:
            root = self._exports_dir(user_id)
        except ValueError:
            return
        if root.is_dir():
            shutil.rmtree(root, ignore_errors=True)


def _public_identity(ident: IdentityRecord, enc_key: bytes | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": ident.id,
        "provider": ident.provider,
        "created_at": ident.created_at.isoformat(),
        "email_verified": ident.email_verified,
    }
    if ident.provider == "phone":
        phone = decrypt_pii(enc_key, ident.phone_enc)
        body["phone"] = phone  # None when stored hash-only
    if ident.provider == "email":
        email = decrypt_pii(enc_key, ident.email_enc)
        body["email"] = email
    return body


def collect_user_files(app, user_id: str) -> dict[str, Any]:
    """Snapshot the user's file-backed stores for an export archive."""
    out: dict[str, Any] = {
        "chat": [], "attachments": [], "history": [], "archive": [], "settings": {},
    }
    try:
        uid = check_id(user_id, "user_id")
    except ValueError:
        return out
    chat = getattr(app.state, "chat_store", None)
    if chat is not None and hasattr(chat, "dump_user"):
        out["chat"] = chat.dump_user(uid)
    media = getattr(app.state, "chat_media", None)
    if media is not None and hasattr(media, "dump_user"):
        out["attachments"] = media.dump_user(uid)
    eng = getattr(app.state, "engine", None)
    if eng is not None:
        hist_dir = Path(eng.s.data_dir) / "history" / uid
        if hist_dir.is_dir():
            try:
                out["history"] = [e.to_dict() for e in eng.history_for(uid).entries()]
            except Exception:
                log.debug("history export failed", exc_info=True)
        if getattr(eng, "archive", None) is not None and hasattr(eng.archive.local, "list_sessions"):
            try:
                sessions = eng.archive.local.list_sessions(uid)
                archive_rows = []
                for summary in sessions:
                    archive_rows.append({
                        "summary": summary,
                        "segments": eng.archive.local.read_session(uid, summary["date"], summary["session_id"]),
                    })
                out["archive"] = archive_rows
            except Exception:
                log.debug("archive export failed", exc_info=True)
        store = getattr(eng, "store", None)
        if store is not None:
            out["settings"] = {
                "pause_ms": store.get("pause_ms"),
                "target_lang": store.get("target_lang"),
                "partner_lang": store.get("partner_lang"),
                "tts_mode": store.get("tts_mode"),
                "whisper_model": store.get("whisper_model"),
            }
    return out


def erase_user_files(app, user_id: str) -> dict[str, int]:
    """Hard-delete file-backed personal data. Missing trees are a no-op."""
    counts = {"chat": 0, "history": 0, "archive": 0}
    try:
        uid = check_id(user_id, "user_id")
    except ValueError:
        return counts
    chat = getattr(app.state, "chat_store", None)
    if chat is not None and hasattr(chat, "purge_user"):
        counts["chat"] = 1 if chat.purge_user(uid) else 0
    media = getattr(app.state, "chat_media", None)
    if media is not None and hasattr(media, "purge_user"):
        media.purge_user(uid)
    translations = getattr(app.state, "chat_translations", None)
    if translations is not None and hasattr(translations, "clear"):
        # Cache is not partitioned by user; entries expire with messages.
        pass
    eng = getattr(app.state, "engine", None)
    if eng is not None:
        if hasattr(eng, "drop_history"):
            counts["history"] = 1 if eng.drop_history(uid) else 0
        else:
            hist = Path(eng.s.data_dir) / "history" / uid
            if hist.is_dir():
                shutil.rmtree(hist, ignore_errors=True)
                counts["history"] = 1
        if getattr(eng, "archive", None) is not None and hasattr(eng.archive, "purge_user"):
            counts["archive"] = 1 if eng.archive.purge_user(uid) else 0
    return counts
