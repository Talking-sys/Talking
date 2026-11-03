"""File-backed group conversations (ROADMAP M6, groups only).

Shared metadata + JSONL live next to the existing ``ChatStore`` root. Postgres
is out of scope; this keeps the same store interface M4 already uses.

Admin views never receive member lists with message bodies — message redaction
stays on ``Message.to_admin()``.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from talking.ids import check_id, secure_dir
from talking.languages import WHISPER_LANGUAGES

from .models import Dialog, Message

ROLES = ("owner", "admin", "member")
ROLE_RANK = {"member": 0, "admin": 1, "owner": 2}
DEFAULT_MAX_MEMBERS = 20
DEFAULT_TRIAL_MAX_MEMBERS = 5
DEFAULT_INVITE_TTL_S = 7 * 24 * 3600
MAX_INVITE_TTL_S = 30 * 24 * 3600
MAX_INVITE_USES = 1000
TITLE_MAX = 80
DESC_MAX = 200
AVATAR_MAX = 500
GROUP_ID_PREFIX = "g"


class GroupError(Exception):
    status = 400

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status


def hash_invite_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_invite_token() -> str:
    return secrets.token_urlsafe(24)


def validate_lang(lang: str | None, default: str = "en") -> str:
    code = (lang or default or "en").strip().lower()
    if code not in WHISPER_LANGUAGES:
        raise GroupError("unknown language", 400)
    return code


def validate_title(title: str) -> str:
    cleaned = (title or "").strip()[:TITLE_MAX]
    if not cleaned:
        raise GroupError("name required", 400)
    return cleaned


def validate_description(description: str | None) -> str:
    return (description or "").strip()[:DESC_MAX]


def validate_avatar(avatar: str | None) -> str:
    raw = (avatar or "").strip()
    if not raw:
        return ""
    if len(raw) > AVATAR_MAX:
        raise GroupError("avatar too long", 400)
    lower = raw.lower()
    if any(lower.startswith(p) for p in ("javascript:", "data:", "vbscript:", "file:")):
        raise GroupError("avatar scheme not allowed", 400)
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme != "https" or not parsed.netloc:
            raise GroupError("avatar must be an https URL", 400)
        return raw
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    # Short emoji / preset label — no URLs, no path traversal.
    if "/" in raw or "\\" in raw or ".." in raw:
        raise GroupError("invalid avatar", 400)
    return raw[:16]


def validate_role(role: str | None, default: str = "member") -> str:
    r = (role or default).strip().lower()
    if r not in ROLES:
        raise GroupError("role must be owner, admin or member", 400)
    return r


@dataclass
class GroupMember:
    user_id: str
    role: str
    lang: str
    joined_at: float

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GroupInvite:
    id: str
    token_hash: str
    created_by: str
    created_at: float
    expires_at: float
    max_uses: int
    uses: int = 0
    revoked: bool = False

    def expired(self, now: float | None = None) -> bool:
        return float(self.expires_at) <= (time.time() if now is None else now)

    def remaining(self) -> int | None:
        if self.max_uses <= 0:
            return None
        return max(0, int(self.max_uses) - int(self.uses))

    def to_public(self, *, token: str | None = None) -> dict[str, Any]:
        d = {
            "id": self.id,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "max_uses": self.max_uses,
            "uses": self.uses,
            "revoked": self.revoked,
            "expired": self.expired(),
            "remaining": self.remaining(),
        }
        if token:
            d["token"] = token
            d["url"] = f"/chat?invite={token}"
        return d


@dataclass
class Group:
    id: str
    title: str
    created_at: float
    updated_at: float
    created_by: str
    avatar: str = ""
    description: str = ""
    members: list[GroupMember] = field(default_factory=list)
    invites: list[GroupInvite] = field(default_factory=list)

    def member(self, user_id: str) -> GroupMember | None:
        for m in self.members:
            if m.user_id == user_id:
                return m
        return None

    def is_member(self, user_id: str) -> bool:
        return self.member(user_id) is not None

    def member_ids(self) -> list[str]:
        return [m.user_id for m in self.members]

    def owners(self) -> list[GroupMember]:
        return [m for m in self.members if m.role == "owner"]

    def to_dialog(self, user_id: str) -> Dialog:
        m = self.member(user_id)
        return Dialog(
            id=self.id,
            title=self.title,
            created_at=self.created_at,
            updated_at=self.updated_at,
            kind="group",
            avatar=self.avatar or None,
            description=self.description or None,
            member_count=len(self.members),
            role=m.role if m else None,
        )

    def to_public(self, user_id: str) -> dict[str, Any]:
        d = self.to_dialog(user_id).to_public()
        d["created_by"] = self.created_by
        d["members"] = [m.to_public() for m in self.members]
        return d

    def to_preview(self) -> dict[str, Any]:
        """Public join preview: no member ids."""
        return {
            "id": self.id,
            "title": self.title,
            "avatar": self.avatar or None,
            "description": self.description or None,
            "kind": "group",
            "member_count": len(self.members),
        }

    def to_storage(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "avatar": self.avatar,
            "description": self.description,
            "members": [asdict(m) for m in self.members],
            "invites": [asdict(i) for i in self.invites],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Group":
        members = []
        for raw in d.get("members") or []:
            try:
                members.append(GroupMember(
                    user_id=check_id(str(raw["user_id"]), "user_id"),
                    role=validate_role(raw.get("role")),
                    lang=str(raw.get("lang") or "en"),
                    joined_at=float(raw.get("joined_at") or 0),
                ))
            except (KeyError, TypeError, ValueError, GroupError):
                continue
        invites = []
        for raw in d.get("invites") or []:
            try:
                invites.append(GroupInvite(
                    id=str(raw["id"]),
                    token_hash=str(raw["token_hash"]),
                    created_by=str(raw.get("created_by") or ""),
                    created_at=float(raw.get("created_at") or 0),
                    expires_at=float(raw["expires_at"]),
                    max_uses=int(raw.get("max_uses") or 0),
                    uses=int(raw.get("uses") or 0),
                    revoked=bool(raw.get("revoked")),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(
            id=str(d["id"]),
            title=str(d.get("title") or "Group"),
            created_at=float(d.get("created_at") or 0),
            updated_at=float(d.get("updated_at") or d.get("created_at") or 0),
            created_by=str(d.get("created_by") or ""),
            avatar=str(d.get("avatar") or ""),
            description=str(d.get("description") or ""),
            members=members,
            invites=invites,
        )


def can_manage(actor: GroupMember | None) -> bool:
    return bool(actor and actor.role in ("owner", "admin"))


def can_kick(actor: GroupMember, target: GroupMember) -> bool:
    if actor.user_id == target.user_id:
        return True
    if actor.role == "owner":
        return target.role != "owner" or actor.user_id != target.user_id
    if actor.role == "admin":
        return target.role == "member"
    return False


class GroupStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._lock = threading.Lock()
        secure_dir(self.root)
        secure_dir(self._groups_root())
        secure_dir(self._invites_root())
        secure_dir(self._members_root())

    def _groups_root(self) -> Path:
        return self.root / "groups"

    def _invites_root(self) -> Path:
        return self.root / "invites"

    def _members_root(self) -> Path:
        return self.root / "memberships"

    def _group_dir(self, group_id: str) -> Path:
        return secure_dir(self._groups_root() / check_id(group_id, "group_id"))

    def _meta_path(self, group_id: str) -> Path:
        return self._group_dir(group_id) / "meta.json"

    def _messages_path(self, group_id: str) -> Path:
        return self._group_dir(group_id) / "messages.jsonl"

    def _membership_path(self, user_id: str) -> Path:
        return self._members_root() / f"{check_id(user_id, 'user_id')}.json"

    def _invite_path(self, token_hash: str) -> Path:
        h = check_id(token_hash, "invite")
        return self._invites_root() / h[:2] / f"{h}.json"

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _load_group_unlocked(self, group_id: str) -> Group | None:
        try:
            group_id = check_id(group_id, "group_id")
        except ValueError:
            return None
        p = self._groups_root() / group_id / "meta.json"
        if not p.exists():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            g = Group.from_dict(raw)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        if g.id != group_id:
            return None
        return g

    def _save_group_unlocked(self, group: Group) -> None:
        self._write_json(self._meta_path(group.id), group.to_storage())

    def _membership_ids_unlocked(self, user_id: str) -> list[str]:
        p = self._membership_path(user_id)
        if not p.exists():
            return []
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw:
            try:
                out.append(check_id(str(item), "group_id"))
            except ValueError:
                continue
        return out

    def _set_membership_unlocked(self, user_id: str, group_ids: list[str]) -> None:
        uniq: list[str] = []
        seen: set[str] = set()
        for gid in group_ids:
            if gid not in seen:
                uniq.append(gid)
                seen.add(gid)
        self._write_json(self._membership_path(user_id), uniq)

    def _index_member_unlocked(self, user_id: str, group_id: str) -> None:
        ids = self._membership_ids_unlocked(user_id)
        if group_id not in ids:
            ids.append(group_id)
            self._set_membership_unlocked(user_id, ids)

    def _drop_member_index_unlocked(self, user_id: str, group_id: str) -> None:
        ids = [g for g in self._membership_ids_unlocked(user_id) if g != group_id]
        self._set_membership_unlocked(user_id, ids)

    def _index_invite_unlocked(self, token_hash: str, group_id: str, invite_id: str) -> None:
        self._write_json(self._invite_path(token_hash), {"group_id": group_id, "invite_id": invite_id})

    def _drop_invite_index_unlocked(self, token_hash: str) -> None:
        p = self._invite_path(token_hash)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    def _delete_group_files_unlocked(self, group_id: str) -> None:
        d = self._groups_root() / group_id
        if not d.exists():
            return
        for child in d.iterdir():
            try:
                child.unlink()
            except OSError:
                pass
        try:
            d.rmdir()
        except OSError:
            pass

    def get(self, group_id: str) -> Group | None:
        with self._lock:
            return self._load_group_unlocked(group_id)

    def require(self, group_id: str) -> Group:
        g = self.get(group_id)
        if g is None:
            raise GroupError("group not found", 404)
        return g

    def require_member(self, group_id: str, user_id: str) -> tuple[Group, GroupMember]:
        g = self.require(group_id)
        m = g.member(user_id)
        if m is None:
            raise GroupError("not a member", 403)
        return g, m

    def is_member(self, group_id: str, user_id: str) -> bool:
        g = self.get(group_id)
        return bool(g and g.is_member(user_id))

    def dialogs_for(self, user_id: str) -> list[Dialog]:
        with self._lock:
            ids = self._membership_ids_unlocked(user_id)
            out: list[Dialog] = []
            for gid in ids:
                g = self._load_group_unlocked(gid)
                if g is None or not g.is_member(user_id):
                    continue
                out.append(g.to_dialog(user_id))
            return out

    def list_for(self, user_id: str) -> list[Group]:
        with self._lock:
            ids = self._membership_ids_unlocked(user_id)
            out: list[Group] = []
            for gid in ids:
                g = self._load_group_unlocked(gid)
                if g is not None and g.is_member(user_id):
                    out.append(g)
            return out

    def create(
        self,
        owner_id: str,
        title: str,
        *,
        avatar: str = "",
        description: str = "",
        lang: str = "en",
        member_ids: list[str] | None = None,
        max_members: int = DEFAULT_MAX_MEMBERS,
    ) -> Group:
        owner_id = check_id(owner_id, "user_id")
        title = validate_title(title)
        avatar = validate_avatar(avatar)
        description = validate_description(description)
        lang = validate_lang(lang)
        cap = max(2, int(max_members or DEFAULT_MAX_MEMBERS))
        extras: list[str] = []
        seen = {owner_id}
        for raw in member_ids or []:
            uid = check_id(str(raw), "user_id")
            if uid in seen:
                continue
            extras.append(uid)
            seen.add(uid)
        if 1 + len(extras) > cap:
            raise GroupError("member limit reached", 409)
        now = time.time()
        gid = GROUP_ID_PREFIX + uuid.uuid4().hex[:15]
        members = [GroupMember(user_id=owner_id, role="owner", lang=lang, joined_at=now)]
        for uid in extras:
            members.append(GroupMember(user_id=uid, role="member", lang=lang, joined_at=now))
        group = Group(
            id=gid, title=title, created_at=now, updated_at=now, created_by=owner_id,
            avatar=avatar, description=description, members=members,
        )
        with self._lock:
            self._save_group_unlocked(group)
            for m in members:
                self._index_member_unlocked(m.user_id, gid)
        return group

    def update(
        self,
        group_id: str,
        actor_id: str,
        *,
        title: str | None = None,
        avatar: str | None = None,
        description: str | None = None,
    ) -> Group:
        with self._lock:
            g = self._load_group_unlocked(group_id)
            if g is None:
                raise GroupError("group not found", 404)
            actor = g.member(actor_id)
            if not can_manage(actor):
                raise GroupError("admin or owner required", 403)
            if title is not None:
                g.title = validate_title(title)
            if avatar is not None:
                g.avatar = validate_avatar(avatar)
            if description is not None:
                g.description = validate_description(description)
            g.updated_at = time.time()
            self._save_group_unlocked(g)
            return g

    def add_member(
        self,
        group_id: str,
        actor_id: str,
        user_id: str,
        *,
        role: str = "member",
        lang: str = "en",
        max_members: int = DEFAULT_MAX_MEMBERS,
    ) -> Group:
        user_id = check_id(user_id, "user_id")
        role = validate_role(role)
        if role == "owner":
            raise GroupError("use transfer to assign a new owner", 400)
        lang = validate_lang(lang)
        cap = max(2, int(max_members or DEFAULT_MAX_MEMBERS))
        with self._lock:
            g = self._load_group_unlocked(group_id)
            if g is None:
                raise GroupError("group not found", 404)
            actor = g.member(actor_id)
            if not can_manage(actor):
                raise GroupError("admin or owner required", 403)
            if g.is_member(user_id):
                raise GroupError("already a member", 409)
            if len(g.members) >= cap:
                raise GroupError("member limit reached", 409)
            g.members.append(GroupMember(
                user_id=user_id, role=role, lang=lang, joined_at=time.time(),
            ))
            g.updated_at = time.time()
            self._save_group_unlocked(g)
            self._index_member_unlocked(user_id, g.id)
            return g

    def set_member(
        self,
        group_id: str,
        actor_id: str,
        user_id: str,
        *,
        role: str | None = None,
        lang: str | None = None,
    ) -> Group:
        user_id = check_id(user_id, "user_id")
        with self._lock:
            g = self._load_group_unlocked(group_id)
            if g is None:
                raise GroupError("group not found", 404)
            actor = g.member(actor_id)
            target = g.member(user_id)
            if actor is None:
                raise GroupError("not a member", 403)
            if target is None:
                raise GroupError("member not found", 404)
            if lang is not None:
                if actor.user_id != target.user_id and not can_manage(actor):
                    raise GroupError("cannot change another member language", 403)
                target.lang = validate_lang(lang)
            if role is not None:
                role = validate_role(role)
                if role == "owner":
                    raise GroupError("use transfer to assign a new owner", 400)
                if actor.role != "owner":
                    raise GroupError("only the owner can change roles", 403)
                if target.role == "owner":
                    raise GroupError("transfer ownership first", 400)
                target.role = role
            g.updated_at = time.time()
            self._save_group_unlocked(g)
            return g

    def remove_member(self, group_id: str, actor_id: str, user_id: str) -> Group | None:
        """Kick or leave. Returns the group, or None if it was dissolved."""
        user_id = check_id(user_id, "user_id")
        with self._lock:
            return self._remove_member_unlocked(group_id, actor_id, user_id)

    def leave(self, group_id: str, user_id: str) -> Group | None:
        return self.remove_member(group_id, user_id, user_id)

    def _remove_member_unlocked(self, group_id: str, actor_id: str, user_id: str) -> Group | None:
        g = self._load_group_unlocked(group_id)
        if g is None:
            raise GroupError("group not found", 404)
        actor = g.member(actor_id)
        target = g.member(user_id)
        if actor is None:
            raise GroupError("not a member", 403)
        if target is None:
            raise GroupError("member not found", 404)
        if not can_kick(actor, target):
            raise GroupError("cannot remove this member", 403)
        leaving_owner = target.role == "owner" and actor.user_id == target.user_id
        g.members = [m for m in g.members if m.user_id != user_id]
        self._drop_member_index_unlocked(user_id, g.id)
        if not g.members:
            for inv in g.invites:
                self._drop_invite_index_unlocked(inv.token_hash)
            self._delete_group_files_unlocked(g.id)
            return None
        if leaving_owner:
            successor = next((m for m in g.members if m.role == "admin"), None)
            if successor is None:
                successor = g.members[0]
            successor.role = "owner"
        g.updated_at = time.time()
        self._save_group_unlocked(g)
        return g

    def transfer(self, group_id: str, actor_id: str, user_id: str) -> Group:
        user_id = check_id(user_id, "user_id")
        with self._lock:
            g = self._load_group_unlocked(group_id)
            if g is None:
                raise GroupError("group not found", 404)
            actor = g.member(actor_id)
            target = g.member(user_id)
            if actor is None or actor.role != "owner":
                raise GroupError("only the owner can transfer", 403)
            if target is None:
                raise GroupError("member not found", 404)
            if target.user_id == actor.user_id:
                return g
            actor.role = "admin"
            target.role = "owner"
            g.updated_at = time.time()
            self._save_group_unlocked(g)
            return g

    def dissolve(self, group_id: str, actor_id: str) -> None:
        with self._lock:
            g = self._load_group_unlocked(group_id)
            if g is None:
                raise GroupError("group not found", 404)
            actor = g.member(actor_id)
            if actor is None or actor.role != "owner":
                raise GroupError("only the owner can dissolve the group", 403)
            for m in g.members:
                self._drop_member_index_unlocked(m.user_id, g.id)
            for inv in g.invites:
                self._drop_invite_index_unlocked(inv.token_hash)
            self._delete_group_files_unlocked(g.id)

    def create_invite(
        self,
        group_id: str,
        actor_id: str,
        *,
        ttl_s: int = DEFAULT_INVITE_TTL_S,
        max_uses: int = 20,
    ) -> tuple[Group, GroupInvite, str]:
        ttl_s = int(ttl_s or DEFAULT_INVITE_TTL_S)
        if ttl_s <= 0 or ttl_s > MAX_INVITE_TTL_S:
            raise GroupError("invite expiry out of range", 400)
        max_uses = int(max_uses)
        if max_uses < 0 or max_uses > MAX_INVITE_USES:
            raise GroupError("invite max_uses out of range", 400)
        token = mint_invite_token()
        token_hash = hash_invite_token(token)
        now = time.time()
        invite = GroupInvite(
            id="i" + uuid.uuid4().hex[:15],
            token_hash=token_hash,
            created_by=actor_id,
            created_at=now,
            expires_at=now + ttl_s,
            max_uses=max_uses,
        )
        with self._lock:
            g = self._load_group_unlocked(group_id)
            if g is None:
                raise GroupError("group not found", 404)
            actor = g.member(actor_id)
            if not can_manage(actor):
                raise GroupError("admin or owner required", 403)
            g.invites.append(invite)
            g.updated_at = now
            self._save_group_unlocked(g)
            self._index_invite_unlocked(token_hash, g.id, invite.id)
            return g, invite, token

    def list_invites(self, group_id: str, actor_id: str) -> list[GroupInvite]:
        g, actor = self.require_member(group_id, actor_id)
        if not can_manage(actor):
            raise GroupError("admin or owner required", 403)
        return list(g.invites)

    def revoke_invite(self, group_id: str, actor_id: str, invite_id: str) -> Group:
        with self._lock:
            g = self._load_group_unlocked(group_id)
            if g is None:
                raise GroupError("group not found", 404)
            actor = g.member(actor_id)
            if not can_manage(actor):
                raise GroupError("admin or owner required", 403)
            found = next((i for i in g.invites if i.id == invite_id), None)
            if found is None:
                raise GroupError("invite not found", 404)
            found.revoked = True
            g.updated_at = time.time()
            self._save_group_unlocked(g)
            self._drop_invite_index_unlocked(found.token_hash)
            return g

    def preview_invite(self, token: str) -> tuple[Group, GroupInvite]:
        g, inv = self._lookup_invite(token)
        return g, inv

    def join(self, token: str, user_id: str, *, lang: str = "en", max_members: int = DEFAULT_MAX_MEMBERS) -> Group:
        user_id = check_id(user_id, "user_id")
        lang = validate_lang(lang)
        cap = max(2, int(max_members or DEFAULT_MAX_MEMBERS))
        token_hash = hash_invite_token((token or "").strip())
        with self._lock:
            g, inv = self._lookup_invite_unlocked(token_hash)
            if g.is_member(user_id):
                return g
            if len(g.members) >= cap:
                raise GroupError("member limit reached", 409)
            if inv.max_uses > 0 and inv.uses >= inv.max_uses:
                raise GroupError("invite exhausted", 410)
            inv.uses += 1
            g.members.append(GroupMember(
                user_id=user_id, role="member", lang=lang, joined_at=time.time(),
            ))
            g.updated_at = time.time()
            if inv.max_uses > 0 and inv.uses >= inv.max_uses:
                inv.revoked = True
                self._drop_invite_index_unlocked(inv.token_hash)
            self._save_group_unlocked(g)
            self._index_member_unlocked(user_id, g.id)
            return g

    def _lookup_invite(self, token: str) -> tuple[Group, GroupInvite]:
        token_hash = hash_invite_token((token or "").strip())
        with self._lock:
            return self._lookup_invite_unlocked(token_hash)

    def _lookup_invite_unlocked(self, token_hash: str) -> tuple[Group, GroupInvite]:
        try:
            token_hash = check_id(token_hash, "invite")
        except ValueError:
            raise GroupError("invite not found", 404)
        p = self._invite_path(token_hash)
        group_id = None
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                group_id = raw.get("group_id")
            except (OSError, json.JSONDecodeError, TypeError):
                group_id = None
        g = self._load_group_unlocked(group_id) if group_id else None
        if g is None:
            raise GroupError("invite not found", 404)
        inv = next((i for i in g.invites if i.token_hash == token_hash), None)
        if inv is None or inv.revoked:
            raise GroupError("invite not found", 404)
        if inv.expired():
            raise GroupError("invite expired", 410)
        if inv.max_uses > 0 and inv.uses >= inv.max_uses:
            raise GroupError("invite exhausted", 410)
        return g, inv

    def add_message(self, msg: Message) -> Message:
        line = json.dumps(msg.to_storage(), ensure_ascii=False)
        with self._lock:
            g = self._load_group_unlocked(msg.dialog_id)
            if g is None:
                raise GroupError("group not found", 404)
            p = self._messages_path(msg.dialog_id)
            with p.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            g.updated_at = msg.created_at
            self._save_group_unlocked(g)
        return msg

    def update_message(self, msg: Message) -> Message:
        with self._lock:
            p = self._messages_path(msg.dialog_id)
            if not p.exists():
                return msg
            rows = p.read_text(encoding="utf-8").splitlines()
            out: list[str] = []
            for line in rows:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    out.append(line)
                    continue
                if d.get("id") == msg.id:
                    out.append(json.dumps(msg.to_storage(), ensure_ascii=False))
                else:
                    out.append(line)
            tmp = p.with_suffix(".tmp")
            tmp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
            tmp.replace(p)
        return msg

    def list_messages(self, group_id: str, *, limit: int = 100) -> list[Message]:
        p = self._messages_path(check_id(group_id, "group_id"))
        if not p.exists():
            return []
        rows: list[Message] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(Message.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
        if limit > 0:
            rows = rows[-limit:]
        return rows

    def get_message(self, group_id: str, message_id: str) -> Message | None:
        for m in self.list_messages(group_id, limit=0):
            if m.id == message_id:
                return m
        return None

    def delete_message(self, group_id: str, message_id: str) -> Message | None:
        removed: Message | None = None
        with self._lock:
            p = self._messages_path(group_id)
            if not p.exists():
                return None
            rows = p.read_text(encoding="utf-8").splitlines()
            out: list[str] = []
            for line in rows:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    out.append(line)
                    continue
                if d.get("id") == message_id:
                    try:
                        removed = Message.from_dict(d)
                    except (TypeError, KeyError):
                        removed = None
                    continue
                out.append(line)
            tmp = p.with_suffix(".tmp")
            tmp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
            tmp.replace(p)
        return removed

    def purge_older_than(self, group_id: str, max_age_s: float) -> list[Message]:
        if max_age_s <= 0:
            return []
        cutoff = time.time() - max_age_s
        gone: list[Message] = []
        with self._lock:
            p = self._messages_path(group_id)
            if not p.exists():
                return []
            keep: list[str] = []
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    created = float(raw.get("created_at") or 0)
                except (json.JSONDecodeError, TypeError, ValueError):
                    keep.append(line)
                    continue
                if created and created < cutoff:
                    try:
                        gone.append(Message.from_dict(raw))
                    except (TypeError, KeyError):
                        pass
                    continue
                keep.append(line)
            tmp = p.with_suffix(".tmp")
            tmp.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
            tmp.replace(p)
        return gone
