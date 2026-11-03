"""File-backed nicks, chat profiles, blocks, reports, and 1:1 invite links.

Reports store hashed identifiers and a reason only — never message text
(owner spec: admin must not read dialogs).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from talking.ids import check_id, secure_dir

from .nicks import (
    RENAME_SECONDS,
    SEARCH_LIMIT,
    NickError,
    fold_nick,
    nick_prefix,
    validate_nick,
)

INVITE_TTL_S = 7 * 24 * 3600
INVITE_MAX_USES = 1
REPORT_REASONS = frozenset({"spam", "abuse", "other"})


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _hash_id(secret: bytes, user_id: str) -> str:
    return hashlib.sha256(secret + b":uid:" + user_id.encode()).hexdigest()[:32]


@dataclass
class ChatProfile:
    user_id: str
    nickname: str | None = None
    native_lang: str | None = None
    auto_translate: bool = True
    nick_changed_at: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_public(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "nickname": self.nickname,
            "native_lang": self.native_lang,
            "auto_translate": self.auto_translate,
            "nick_changed_at": self.nick_changed_at or None,
        }


class ChatDirectory:
    def __init__(self, root: str | Path, *, secret: bytes = b"", rename_seconds: int = RENAME_SECONDS):
        self.root = secure_dir(Path(root) / "_core")
        self.secret = secret or b"talking-chat-directory"
        self.rename_seconds = int(rename_seconds)
        self._lock = threading.Lock()

    def _profiles_path(self) -> Path:
        return self.root / "profiles.json"

    def _nicks_path(self) -> Path:
        return self.root / "nicks.json"

    def _blocks_path(self) -> Path:
        return self.root / "blocks.json"

    def _invites_path(self) -> Path:
        return self.root / "invites.json"

    def _reports_path(self) -> Path:
        return self.root / "reports.jsonl"

    def get_profile(self, user_id: str) -> ChatProfile:
        user_id = check_id(user_id, "user_id")
        with self._lock:
            raw = _read_json(self._profiles_path(), {})
            item = raw.get(user_id) if isinstance(raw, dict) else None
        if not isinstance(item, dict):
            now = time.time()
            return ChatProfile(user_id=user_id, created_at=now, updated_at=now)
        return ChatProfile(
            user_id=user_id,
            nickname=item.get("nickname"),
            native_lang=item.get("native_lang"),
            auto_translate=bool(item.get("auto_translate", True)),
            nick_changed_at=float(item.get("nick_changed_at") or 0),
            created_at=float(item.get("created_at") or 0),
            updated_at=float(item.get("updated_at") or 0),
        )

    def _save_profile_locked(self, profiles: dict, nicks: dict, prof: ChatProfile) -> None:
        profiles[prof.user_id] = asdict(prof)
        _write_json(self._profiles_path(), profiles)
        _write_json(self._nicks_path(), nicks)

    def set_profile(
        self,
        user_id: str,
        *,
        nickname: str | None = None,
        native_lang: str | None = None,
        auto_translate: bool | None = None,
        now: float | None = None,
    ) -> ChatProfile:
        user_id = check_id(user_id, "user_id")
        now = time.time() if now is None else float(now)
        with self._lock:
            profiles = _read_json(self._profiles_path(), {})
            if not isinstance(profiles, dict):
                profiles = {}
            nicks = _read_json(self._nicks_path(), {})
            if not isinstance(nicks, dict):
                nicks = {}
            item = profiles.get(user_id) or {}
            prof = ChatProfile(
                user_id=user_id,
                nickname=item.get("nickname"),
                native_lang=item.get("native_lang"),
                auto_translate=bool(item.get("auto_translate", True)),
                nick_changed_at=float(item.get("nick_changed_at") or 0),
                created_at=float(item.get("created_at") or now),
                updated_at=now,
            )
            if native_lang is not None:
                prof.native_lang = native_lang or None
            if auto_translate is not None:
                prof.auto_translate = bool(auto_translate)
            if nickname is not None:
                nick = validate_nick(nickname)
                folded = fold_nick(nick)
                held = nicks.get(folded)
                if held and held.get("user_id") != user_id:
                    raise NickError("nickname taken", status=409)
                if prof.nickname and fold_nick(prof.nickname) != folded:
                    elapsed = now - (prof.nick_changed_at or 0)
                    if prof.nick_changed_at and elapsed < self.rename_seconds:
                        raise NickError("nickname can be changed once every 30 days", status=429)
                    old_key = fold_nick(prof.nickname)
                    if nicks.get(old_key, {}).get("user_id") == user_id:
                        nicks.pop(old_key, None)
                    prof.nick_changed_at = now
                elif not prof.nickname:
                    prof.nick_changed_at = now
                prof.nickname = nick
                nicks[folded] = {"user_id": user_id, "nickname": nick, "changed_at": prof.nick_changed_at}
            self._save_profile_locked(profiles, nicks, prof)
            return prof

    def find_nick(self, nickname: str) -> tuple[str, str] | None:
        """Return (user_id, canonical_nick) or None."""
        folded = fold_nick(nickname)
        if not folded:
            return None
        with self._lock:
            nicks = _read_json(self._nicks_path(), {})
        item = nicks.get(folded) if isinstance(nicks, dict) else None
        if not isinstance(item, dict) or not item.get("user_id"):
            return None
        try:
            uid = check_id(str(item["user_id"]), "user_id")
        except ValueError:
            return None
        return uid, str(item.get("nickname") or nickname)

    def search_prefix(self, query: str, *, viewer_id: str, limit: int = SEARCH_LIMIT) -> list[dict[str, str]]:
        prefix = fold_nick(nick_prefix(query))
        viewer_id = check_id(viewer_id, "user_id")
        blocked = self.blocked_set(viewer_id) | self.blocked_by_set(viewer_id)
        with self._lock:
            nicks = _read_json(self._nicks_path(), {})
        if not isinstance(nicks, dict):
            return []
        hits: list[dict[str, str]] = []
        for folded, item in sorted(nicks.items(), key=lambda kv: kv[0]):
            if not folded.startswith(prefix):
                continue
            if not isinstance(item, dict):
                continue
            uid = str(item.get("user_id") or "")
            if uid == viewer_id or uid in blocked:
                continue
            nick = str(item.get("nickname") or "")
            if not nick:
                continue
            hits.append({"user_id": uid, "nickname": nick})
            if len(hits) >= max(1, min(limit, 50)):
                break
        return hits

    def public_peer(self, user_id: str) -> dict[str, Any]:
        try:
            prof = self.get_profile(user_id)
        except ValueError:
            return {"user_id": user_id, "nickname": None}
        return {"user_id": prof.user_id, "nickname": prof.nickname}

    # --- blocks ---

    def _load_blocks_locked(self) -> dict[str, list[str]]:
        raw = _read_json(self._blocks_path(), {})
        if not isinstance(raw, dict):
            return {}
        out: dict[str, list[str]] = {}
        for uid, ids in raw.items():
            if isinstance(ids, list):
                out[str(uid)] = [str(x) for x in ids]
        return out

    def block(self, user_id: str, peer_id: str) -> None:
        user_id = check_id(user_id, "user_id")
        peer_id = check_id(peer_id, "user_id")
        if user_id == peer_id:
            raise NickError("cannot block yourself")
        with self._lock:
            blocks = self._load_blocks_locked()
            bucket = blocks.setdefault(user_id, [])
            if peer_id not in bucket:
                bucket.append(peer_id)
            _write_json(self._blocks_path(), blocks)

    def unblock(self, user_id: str, peer_id: str) -> bool:
        user_id = check_id(user_id, "user_id")
        peer_id = check_id(peer_id, "user_id")
        with self._lock:
            blocks = self._load_blocks_locked()
            bucket = blocks.get(user_id) or []
            if peer_id not in bucket:
                return False
            blocks[user_id] = [x for x in bucket if x != peer_id]
            _write_json(self._blocks_path(), blocks)
            return True

    def blocked_set(self, user_id: str) -> set[str]:
        try:
            user_id = check_id(user_id, "user_id")
        except ValueError:
            return set()
        with self._lock:
            blocks = self._load_blocks_locked()
        return set(blocks.get(user_id) or [])

    def blocked_by_set(self, user_id: str) -> set[str]:
        try:
            user_id = check_id(user_id, "user_id")
        except ValueError:
            return set()
        with self._lock:
            blocks = self._load_blocks_locked()
        return {uid for uid, ids in blocks.items() if user_id in ids}

    def is_blocked_pair(self, a: str, b: str) -> bool:
        return b in self.blocked_set(a) or a in self.blocked_set(b)

    def list_blocks(self, user_id: str) -> list[dict[str, Any]]:
        ids = sorted(self.blocked_set(user_id))
        return [self.public_peer(i) for i in ids]

    # --- reports (no message contents) ---

    def report(self, reporter_id: str, target_id: str, reason: str) -> dict[str, Any]:
        reporter_id = check_id(reporter_id, "user_id")
        target_id = check_id(target_id, "user_id")
        reason = (reason or "other").strip().lower()
        if reason not in REPORT_REASONS:
            raise NickError("reason must be spam, abuse, or other")
        if reporter_id == target_id:
            raise NickError("cannot report yourself")
        rec = {
            "id": "r" + uuid.uuid4().hex[:15],
            "reporter_hash": _hash_id(self.secret, reporter_id),
            "target_hash": _hash_id(self.secret, target_id),
            "reason": reason,
            "created_at": time.time(),
        }
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            p = self._reports_path()
            with p.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return {"ok": True, "id": rec["id"]}

    def list_reports_admin(self) -> list[dict[str, Any]]:
        """Hashed ids + reason only. Never includes message bodies or nicknames."""
        p = self._reports_path()
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append({
                "id": rec.get("id"),
                "reporter_hash": rec.get("reporter_hash"),
                "target_hash": rec.get("target_hash"),
                "reason": rec.get("reason"),
                "created_at": rec.get("created_at"),
            })
        return out

    # --- invites ---

    def create_invite(self, user_id: str, *, ttl: int = INVITE_TTL_S, max_uses: int = INVITE_MAX_USES) -> dict[str, Any]:
        user_id = check_id(user_id, "user_id")
        ttl = max(60, min(int(ttl), 30 * 24 * 3600))
        max_uses = max(1, min(int(max_uses), 50))
        token = secrets.token_urlsafe(16)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        rec = {
            "from_id": user_id,
            "created_at": now,
            "expires_at": now + ttl,
            "max_uses": max_uses,
            "uses": 0,
        }
        with self._lock:
            invites = _read_json(self._invites_path(), {})
            if not isinstance(invites, dict):
                invites = {}
            invites[token_hash] = rec
            _write_json(self._invites_path(), invites)
        return {
            "token": token,
            "expires_in": ttl,
            "max_uses": max_uses,
            "url": f"/chat?invite={token}",
        }

    def peek_invite(self, token: str) -> dict[str, Any]:
        rec = self._get_invite(token)
        if rec is None:
            raise NickError("invite not found", status=404)
        if rec["uses"] >= rec["max_uses"] or rec["expires_at"] <= time.time():
            raise NickError("invite expired", status=410)
        peer = self.public_peer(rec["from_id"])
        return {"ok": True, "from": peer, "expires_at": rec["expires_at"]}

    def consume_invite(self, token: str, accepter_id: str) -> str:
        accepter_id = check_id(accepter_id, "user_id")
        token_hash = hashlib.sha256((token or "").encode()).hexdigest()
        with self._lock:
            invites = _read_json(self._invites_path(), {})
            if not isinstance(invites, dict):
                invites = {}
            rec = invites.get(token_hash)
            if not isinstance(rec, dict):
                raise NickError("invite not found", status=404)
            if float(rec.get("expires_at") or 0) <= time.time():
                raise NickError("invite expired", status=410)
            if int(rec.get("uses") or 0) >= int(rec.get("max_uses") or 1):
                raise NickError("invite expired", status=410)
            from_id = str(rec.get("from_id") or "")
            if from_id == accepter_id:
                raise NickError("cannot accept your own invite")
            rec["uses"] = int(rec.get("uses") or 0) + 1
            invites[token_hash] = rec
            _write_json(self._invites_path(), invites)
        return from_id

    def _get_invite(self, token: str) -> dict[str, Any] | None:
        token_hash = hashlib.sha256((token or "").encode()).hexdigest()
        with self._lock:
            invites = _read_json(self._invites_path(), {})
        rec = invites.get(token_hash) if isinstance(invites, dict) else None
        return rec if isinstance(rec, dict) else None
