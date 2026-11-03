"""File-backed dialog + message store.

Stage 1 has no Postgres requirement. M4 can replace this class with a SQL
implementation as long as ``list_dialogs`` / ``add_message`` / ``list_messages``
keep the same shapes.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from talking.ids import check_id, secure_dir

from .models import Dialog, Message

DEFAULT_DIALOG_ID = "local"
DEFAULT_DIALOG_TITLE = "Чат"


class ChatStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._lock = threading.Lock()
        secure_dir(self.root)

    def _user_root(self, user_id: str) -> Path:
        return secure_dir(self.root / check_id(user_id, "user_id"))

    def _dialogs_path(self, user_id: str) -> Path:
        return self._user_root(user_id) / "dialogs.json"

    def _messages_path(self, user_id: str, dialog_id: str) -> Path:
        d = secure_dir(self._user_root(user_id) / "messages")
        return d / f"{check_id(dialog_id, 'dialog_id')}.jsonl"

    def _load_dialogs(self, user_id: str) -> list[Dialog]:
        p = self._dialogs_path(user_id)
        if not p.exists():
            return []
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        out: list[Dialog] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                out.append(Dialog(id=item["id"], title=item.get("title") or DEFAULT_DIALOG_TITLE,
                                  created_at=float(item["created_at"]),
                                  updated_at=float(item.get("updated_at") or item["created_at"])))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _save_dialogs(self, user_id: str, dialogs: list[Dialog]) -> None:
        p = self._dialogs_path(user_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps([d.to_public() for d in dialogs], ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(p)

    def ensure_default_dialog(self, user_id: str) -> Dialog:
        with self._lock:
            dialogs = self._load_dialogs(user_id)
            for d in dialogs:
                if d.id == DEFAULT_DIALOG_ID:
                    return d
            now = time.time()
            d = Dialog(id=DEFAULT_DIALOG_ID, title=DEFAULT_DIALOG_TITLE, created_at=now, updated_at=now)
            dialogs.append(d)
            self._save_dialogs(user_id, dialogs)
            return d

    def list_dialogs(self, user_id: str) -> list[Dialog]:
        self.ensure_default_dialog(user_id)
        with self._lock:
            return self._load_dialogs(user_id)

    def get_dialog(self, user_id: str, dialog_id: str) -> Dialog:
        dialog_id = check_id(dialog_id, "dialog_id")
        for d in self.list_dialogs(user_id):
            if d.id == dialog_id:
                return d
        raise KeyError("dialog not found")

    def create_dialog(self, user_id: str, title: str = DEFAULT_DIALOG_TITLE) -> Dialog:
        title = (title or DEFAULT_DIALOG_TITLE).strip()[:80] or DEFAULT_DIALOG_TITLE
        now = time.time()
        d = Dialog(id="d" + uuid.uuid4().hex[:15], title=title, created_at=now, updated_at=now)
        with self._lock:
            dialogs = self._load_dialogs(user_id)
            dialogs.append(d)
            self._save_dialogs(user_id, dialogs)
        return d

    def add_message(self, user_id: str, msg: Message) -> Message:
        self.get_dialog(user_id, msg.dialog_id)
        line = json.dumps(msg.to_public(), ensure_ascii=False)
        with self._lock:
            p = self._messages_path(user_id, msg.dialog_id)
            with p.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            dialogs = self._load_dialogs(user_id)
            for d in dialogs:
                if d.id == msg.dialog_id:
                    d.updated_at = msg.created_at
            self._save_dialogs(user_id, dialogs)
        return msg

    def update_message(self, user_id: str, msg: Message) -> Message:
        """Rewrite one JSONL line (used when a later STT pass fills a transcript)."""
        with self._lock:
            p = self._messages_path(user_id, msg.dialog_id)
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
                    out.append(json.dumps(msg.to_public(), ensure_ascii=False))
                else:
                    out.append(line)
            tmp = p.with_suffix(".tmp")
            tmp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
            tmp.replace(p)
        return msg

    def list_messages(self, user_id: str, dialog_id: str, *, limit: int = 100) -> list[Message]:
        self.get_dialog(user_id, dialog_id)
        p = self._messages_path(user_id, dialog_id)
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

    def get_message(self, user_id: str, dialog_id: str, message_id: str) -> Message | None:
        for m in self.list_messages(user_id, dialog_id, limit=0):
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
            p = self._messages_path(user_id, dialog_id)
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

    def purge_older_than(self, user_id: str, max_age_s: float) -> list[Message]:
        """Drop messages past retention. Transcripts are on the rows, so they expire too."""
        if max_age_s <= 0:
            return []
        cutoff = time.time() - max_age_s
        gone: list[Message] = []
        for d in self.list_dialogs(user_id):
            with self._lock:
                p = self._messages_path(user_id, d.id)
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
