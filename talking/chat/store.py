"""File-backed dialog + message store.

Stage 1 has no Postgres requirement. M4 can replace this class with a SQL
implementation as long as ``list_dialogs`` / ``add_message`` / ``list_messages``
keep the same shapes.

1:1 (direct) dialogs live under ``_core/`` so both participants share one
JSONL. Local notes dialogs stay per-user as before.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from talking.ids import check_id, secure_dir

from .models import Dialog, Message, preview_text

DEFAULT_DIALOG_ID = "local"
DEFAULT_DIALOG_TITLE = "Чат"


def direct_dialog_id(a: str, b: str) -> str:
    lo, hi = sorted((a, b))
    digest = hashlib.sha256(f"{lo}\n{hi}".encode()).hexdigest()[:20]
    return "dm" + digest


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


class ChatStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._lock = threading.Lock()
        secure_dir(self.root)
        secure_dir(self._core_root())

    def _user_root(self, user_id: str) -> Path:
        return secure_dir(self.root / check_id(user_id, "user_id"))

    def _core_root(self) -> Path:
        return secure_dir(self.root / "_core")

    def _dialogs_path(self, user_id: str) -> Path:
        return self._user_root(user_id) / "dialogs.json"

    def _messages_path(self, user_id: str, dialog_id: str) -> Path:
        d = secure_dir(self._user_root(user_id) / "messages")
        return d / f"{check_id(dialog_id, 'dialog_id')}.jsonl"

    def _shared_dialogs_path(self) -> Path:
        return self._core_root() / "direct_dialogs.json"

    def _shared_messages_path(self, dialog_id: str) -> Path:
        d = secure_dir(self._core_root() / "messages")
        return d / f"{check_id(dialog_id, 'dialog_id')}.jsonl"

    def _receipts_path(self) -> Path:
        return self._core_root() / "receipts.json"

    def _tombstones_path(self) -> Path:
        return self._core_root() / "tombstones.jsonl"

    def is_direct(self, dialog_id: str) -> bool:
        return str(dialog_id).startswith("dm")

    def _load_dialogs(self, user_id: str) -> list[Dialog]:
        p = self._dialogs_path(user_id)
        raw = _read_json(p, [])
        out: list[Dialog] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                item = dict(item)
                item.setdefault("kind", "local")
                item.setdefault("participants", [user_id] if item.get("kind") == "local" else [])
                out.append(Dialog.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _save_dialogs(self, user_id: str, dialogs: list[Dialog]) -> None:
        p = self._dialogs_path(user_id)
        _write_json(p, [d.to_storage() for d in dialogs])

    def _load_shared_dialogs(self) -> dict[str, Dialog]:
        raw = _read_json(self._shared_dialogs_path(), {})
        if not isinstance(raw, dict):
            return {}
        out: dict[str, Dialog] = {}
        for did, item in raw.items():
            if not isinstance(item, dict):
                continue
            try:
                item = dict(item)
                item.setdefault("id", did)
                item.setdefault("kind", "direct")
                out[did] = Dialog.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _save_shared_dialogs(self, dialogs: dict[str, Dialog]) -> None:
        _write_json(self._shared_dialogs_path(), {k: v.to_storage() for k, v in dialogs.items()})

    def ensure_default_dialog(self, user_id: str) -> Dialog:
        with self._lock:
            dialogs = self._load_dialogs(user_id)
            for d in dialogs:
                if d.id == DEFAULT_DIALOG_ID:
                    return d
            now = time.time()
            d = Dialog(
                id=DEFAULT_DIALOG_ID, title=DEFAULT_DIALOG_TITLE,
                created_at=now, updated_at=now, kind="local", participants=[user_id],
            )
            dialogs.append(d)
            self._save_dialogs(user_id, dialogs)
            return d

    def list_dialogs(self, user_id: str) -> list[Dialog]:
        self.ensure_default_dialog(user_id)
        with self._lock:
            local = self._load_dialogs(user_id)
            shared = [
                d for d in self._load_shared_dialogs().values()
                if user_id in (d.participants or [])
            ]
        by_id = {d.id: d for d in local}
        for d in shared:
            by_id[d.id] = d
        return sorted(by_id.values(), key=lambda d: d.updated_at, reverse=True)

    def get_dialog(self, user_id: str, dialog_id: str) -> Dialog:
        dialog_id = check_id(dialog_id, "dialog_id")
        if self.is_direct(dialog_id):
            with self._lock:
                d = self._load_shared_dialogs().get(dialog_id)
            if d is None or user_id not in (d.participants or []):
                raise KeyError("dialog not found")
            return d
        for d in self.list_dialogs(user_id):
            if d.id == dialog_id and d.kind != "direct":
                return d
        raise KeyError("dialog not found")

    def participants(self, user_id: str, dialog_id: str) -> list[str]:
        d = self.get_dialog(user_id, dialog_id)
        if d.kind == "direct":
            return list(d.participants)
        return [user_id]

    def peer_id(self, user_id: str, dialog_id: str) -> str | None:
        parts = self.participants(user_id, dialog_id)
        return next((p for p in parts if p != user_id), None)

    def create_dialog(self, user_id: str, title: str = DEFAULT_DIALOG_TITLE) -> Dialog:
        title = (title or DEFAULT_DIALOG_TITLE).strip()[:80] or DEFAULT_DIALOG_TITLE
        now = time.time()
        d = Dialog(
            id="d" + uuid.uuid4().hex[:15], title=title, created_at=now, updated_at=now,
            kind="local", participants=[user_id],
        )
        with self._lock:
            dialogs = self._load_dialogs(user_id)
            dialogs.append(d)
            self._save_dialogs(user_id, dialogs)
        return d

    def open_direct(self, user_id: str, peer_id: str, *, title: str | None = None) -> Dialog:
        user_id = check_id(user_id, "user_id")
        peer_id = check_id(peer_id, "user_id")
        if user_id == peer_id:
            raise ValueError("cannot open a 1:1 chat with yourself")
        did = direct_dialog_id(user_id, peer_id)
        now = time.time()
        with self._lock:
            shared = self._load_shared_dialogs()
            existing = shared.get(did)
            if existing is not None:
                return existing
            d = Dialog(
                id=did,
                title=title or "Чат",
                created_at=now,
                updated_at=now,
                kind="direct",
                participants=sorted([user_id, peer_id]),
            )
            shared[did] = d
            self._save_shared_dialogs(shared)
            return d

    def set_dialog_pref(self, user_id: str, dialog_id: str, **fields: Any) -> Dialog:
        d = self.get_dialog(user_id, dialog_id)
        prefs = dict(d.prefs or {})
        bucket = dict(prefs.get(user_id) or {})
        for key in ("auto_translate", "target_lang"):
            if key in fields:
                bucket[key] = fields[key]
        prefs[user_id] = bucket
        d.prefs = prefs
        d.updated_at = time.time()
        self._save_dialog(user_id, d)
        return d

    def dialog_pref(self, user_id: str, dialog_id: str) -> dict[str, Any]:
        try:
            d = self.get_dialog(user_id, dialog_id)
        except (KeyError, ValueError):
            return {}
        return dict((d.prefs or {}).get(user_id) or {})

    def _save_dialog(self, user_id: str, dialog: Dialog) -> None:
        with self._lock:
            if dialog.kind == "direct" or self.is_direct(dialog.id):
                shared = self._load_shared_dialogs()
                shared[dialog.id] = dialog
                self._save_shared_dialogs(shared)
                return
            dialogs = self._load_dialogs(user_id)
            out = []
            found = False
            for d in dialogs:
                if d.id == dialog.id:
                    out.append(dialog)
                    found = True
                else:
                    out.append(d)
            if not found:
                out.append(dialog)
            self._save_dialogs(user_id, out)

    def _path_for(self, user_id: str, dialog_id: str) -> Path:
        if self.is_direct(dialog_id):
            return self._shared_messages_path(dialog_id)
        return self._messages_path(user_id, dialog_id)

    def add_message(self, user_id: str, msg: Message) -> Message:
        self.get_dialog(user_id, msg.dialog_id)
        line = json.dumps(msg.to_storage(), ensure_ascii=False)
        with self._lock:
            p = self._path_for(user_id, msg.dialog_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._touch_dialog_locked(user_id, msg)
        return msg

    def _touch_dialog_locked(self, user_id: str, msg: Message) -> None:
        preview = preview_text(msg)
        if self.is_direct(msg.dialog_id):
            shared = self._load_shared_dialogs()
            d = shared.get(msg.dialog_id)
            if d is None:
                return
            d.updated_at = msg.created_at
            d.last_message_id = msg.id
            d.last_sender_id = msg.sender_id
            d.last_preview = preview
            d.last_type = msg.type
            shared[msg.dialog_id] = d
            self._save_shared_dialogs(shared)
            return
        dialogs = self._load_dialogs(user_id)
        for d in dialogs:
            if d.id == msg.dialog_id:
                d.updated_at = msg.created_at
                d.last_message_id = msg.id
                d.last_sender_id = msg.sender_id
                d.last_preview = preview
                d.last_type = msg.type
        self._save_dialogs(user_id, dialogs)

    def update_message(self, user_id: str, msg: Message) -> Message:
        """Rewrite one JSONL line (STT backfill, text edits)."""
        with self._lock:
            p = self._path_for(user_id, msg.dialog_id)
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

    def _read_all_messages(self, user_id: str, dialog_id: str) -> list[Message]:
        p = self._path_for(user_id, dialog_id)
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
        return rows

    def list_messages(
        self,
        user_id: str,
        dialog_id: str,
        *,
        limit: int = 100,
        before: str | None = None,
        after: str | None = None,
        after_ts: float | None = None,
    ) -> list[Message]:
        self.get_dialog(user_id, dialog_id)
        rows = self._read_all_messages(user_id, dialog_id)
        if after_ts is not None:
            rows = [m for m in rows if m.created_at > after_ts]
        if after:
            idx = next((i for i, m in enumerate(rows) if m.id == after), None)
            if idx is not None:
                rows = rows[idx + 1 :]
            else:
                try:
                    ts = float(after)
                except (TypeError, ValueError):
                    ts = None
                if ts is not None:
                    rows = [m for m in rows if m.created_at > ts]
        if before:
            idx = next((i for i, m in enumerate(rows) if m.id == before), None)
            if idx is None:
                rows = []
            else:
                rows = rows[:idx]
        if limit > 0:
            rows = rows[-limit:]
        return rows

    def has_more_before(self, user_id: str, dialog_id: str, first_id: str | None) -> bool:
        if not first_id:
            return False
        rows = self._read_all_messages(user_id, dialog_id)
        idx = next((i for i, m in enumerate(rows) if m.id == first_id), None)
        return bool(idx)

    def get_message(self, user_id: str, dialog_id: str, message_id: str) -> Message | None:
        for m in self._read_all_messages(user_id, dialog_id):
            if m.id == message_id:
                return m
        return None

    def find_message(self, user_id: str, message_id: str) -> Message | None:
        try:
            message_id = check_id(message_id, "message_id")
        except ValueError:
            return None
        for d in self.list_dialogs(user_id):
            found = self.get_message(user_id, d.id, message_id)
            if found is not None:
                return found
        return None

    def delete_message(self, user_id: str, dialog_id: str, message_id: str) -> Message | None:
        """Remove one JSONL row. Transcript fields live on the row, so they go too."""
        dialog_id = check_id(dialog_id, "dialog_id")
        try:
            message_id = check_id(message_id, "message_id")
        except ValueError:
            return None
        removed: Message | None = None
        with self._lock:
            p = self._path_for(user_id, dialog_id)
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
            if removed is not None:
                tomb = {
                    "id": message_id,
                    "dialog_id": dialog_id,
                    "deleted_at": time.time(),
                    "sender_id": removed.sender_id,
                }
                tp = self._tombstones_path()
                with tp.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(tomb, ensure_ascii=False) + "\n")
        return removed

    def list_tombstones(self, user_id: str, *, after_ts: float = 0.0) -> list[dict[str, Any]]:
        p = self._tombstones_path()
        if not p.exists():
            return []
        allowed = {d.id for d in self.list_dialogs(user_id)}
        out: list[dict[str, Any]] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("dialog_id") not in allowed:
                continue
            if float(rec.get("deleted_at") or 0) <= after_ts:
                continue
            out.append({
                "id": rec.get("id"),
                "dialog_id": rec.get("dialog_id"),
                "deleted_at": rec.get("deleted_at"),
            })
        return out

    def messages_since(self, user_id: str, after_ts: float) -> list[Message]:
        out: list[Message] = []
        for d in self.list_dialogs(user_id):
            for m in self._read_all_messages(user_id, d.id):
                if m.created_at > after_ts:
                    out.append(m)
        out.sort(key=lambda m: m.created_at)
        return out

    def unread_count(self, user_id: str, dialog_id: str) -> int:
        last_id, last_at = self.last_read(user_id, dialog_id)
        n = 0
        seen = not last_id
        for m in self._read_all_messages(user_id, dialog_id):
            if last_id and m.id == last_id:
                seen = True
                continue
            if last_id and not seen:
                continue
            if not last_id and last_at and m.created_at <= last_at:
                continue
            if m.sender_id != user_id:
                n += 1
        if last_id and not seen:
            return sum(1 for m in self._read_all_messages(user_id, dialog_id) if m.sender_id != user_id)
        return n

    def last_read(self, user_id: str, dialog_id: str) -> tuple[str | None, float]:
        rec = self._receipt(dialog_id, user_id)
        return rec.get("last_read_id"), float(rec.get("last_read_at") or 0)

    def _receipt(self, dialog_id: str, user_id: str) -> dict[str, Any]:
        with self._lock:
            raw = _read_json(self._receipts_path(), {})
        bucket = (raw.get(dialog_id) or {}) if isinstance(raw, dict) else {}
        item = bucket.get(user_id) if isinstance(bucket, dict) else None
        return dict(item) if isinstance(item, dict) else {}

    def mark_delivered(self, user_id: str, dialog_id: str, message_id: str | None = None) -> dict[str, Any]:
        return self._mark(user_id, dialog_id, delivered=True, message_id=message_id)

    def mark_read(self, user_id: str, dialog_id: str, message_id: str | None = None) -> dict[str, Any]:
        return self._mark(user_id, dialog_id, read=True, message_id=message_id)

    def _mark(
        self,
        user_id: str,
        dialog_id: str,
        *,
        delivered: bool = False,
        read: bool = False,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        user_id = check_id(user_id, "user_id")
        dialog_id = check_id(dialog_id, "dialog_id")
        now = time.time()
        with self._lock:
            raw = _read_json(self._receipts_path(), {})
            if not isinstance(raw, dict):
                raw = {}
            bucket = raw.setdefault(dialog_id, {})
            item = dict(bucket.get(user_id) or {})
            if delivered:
                item["last_delivered_id"] = message_id or item.get("last_delivered_id")
                item["last_delivered_at"] = now
            if read:
                item["last_read_id"] = message_id or item.get("last_read_id")
                item["last_read_at"] = now
                item["last_delivered_id"] = item.get("last_delivered_id") or message_id
                item["last_delivered_at"] = item.get("last_delivered_at") or now
            bucket[user_id] = item
            raw[dialog_id] = bucket
            _write_json(self._receipts_path(), raw)
        if self.is_direct(dialog_id) and (delivered or read):
            self._stamp_messages(user_id, dialog_id, message_id, delivered=delivered, read=read, now=now)
        return item

    def _stamp_messages(
        self,
        user_id: str,
        dialog_id: str,
        message_id: str | None,
        *,
        delivered: bool,
        read: bool,
        now: float,
    ) -> None:
        rows = self._read_all_messages(user_id, dialog_id)
        changed = False
        stop = False
        for m in rows:
            if m.sender_id == user_id:
                continue
            if delivered and not m.delivered_at:
                m.delivered_at = now
                changed = True
            if read and not m.read_at:
                m.read_at = now
                if not m.delivered_at:
                    m.delivered_at = now
                changed = True
            if message_id and m.id == message_id:
                stop = True
                break
        if stop:
            # only stamp up to message_id (already included)
            pass
        if not changed:
            return
        p = self._path_for(user_id, dialog_id)
        lines = [json.dumps(m.to_storage(), ensure_ascii=False) for m in rows]
        tmp = p.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(p)

    def apply_peer_receipt(self, sender_id: str, dialog_id: str, *, read: bool, now: float | None = None) -> None:
        """Stamp the sender's outgoing messages when the peer delivers/reads."""
        now = time.time() if now is None else now
        rows = self._read_all_messages(sender_id, dialog_id)
        changed = False
        for m in rows:
            if m.sender_id != sender_id:
                continue
            if not m.delivered_at:
                m.delivered_at = now
                changed = True
            if read and not m.read_at:
                m.read_at = now
                changed = True
        if not changed:
            return
        p = self._path_for(sender_id, dialog_id)
        lines = [json.dumps(m.to_storage(), ensure_ascii=False) for m in rows]
        tmp = p.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(p)

    def purge_older_than(self, user_id: str, max_age_s: float) -> list[Message]:
        """Drop messages past retention. Transcripts are on the rows, so they expire too."""
        if max_age_s <= 0:
            return []
        cutoff = time.time() - max_age_s
        gone: list[Message] = []
        for d in self.list_dialogs(user_id):
            with self._lock:
                p = self._path_for(user_id, d.id)
                if not p.exists():
                    continue
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
