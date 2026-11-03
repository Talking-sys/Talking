"""Media pipeline for chat notes: magic-byte sniff, UUID names, quota, tokens.

M5 (general attachments) should reuse these helpers rather than invent a second
upload path. Filenames from the client are ignored; only sniffed containers
from an allowlist are stored.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from talking.ids import check_id, secure_dir
from talking.security import sign, unsign

MEDIA_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Owner spec: files ≤25 MB, 500 MB / user. Voice notes are smaller.
DEFAULT_VOICE_MAX = 8 * 1024 * 1024
DEFAULT_VIDEO_MAX = 25 * 1024 * 1024
DEFAULT_QUOTA = 500 * 1024 * 1024
DEFAULT_NOTE_MAX_MS = 60_000
MIN_NOTE_MS = 200

VOICE_CONTAINERS = frozenset({"wav", "webm", "ogg", "mp4", "mp3"})
VIDEO_CONTAINERS = frozenset({"webm", "mp4"})
NOTE_CONTAINERS = {
    "voice_note": VOICE_CONTAINERS,
    "video_note": VIDEO_CONTAINERS,
}
EXT = {"wav": ".wav", "webm": ".webm", "ogg": ".ogg", "mp4": ".mp4", "mp3": ".mp3"}
MIME = {
    ("voice_note", "wav"): "audio/wav",
    ("voice_note", "webm"): "audio/webm",
    ("voice_note", "ogg"): "audio/ogg",
    ("voice_note", "mp4"): "audio/mp4",
    ("voice_note", "mp3"): "audio/mpeg",
    ("video_note", "webm"): "video/webm",
    ("video_note", "mp4"): "video/mp4",
}

DANGEROUS_EXT = frozenset({
    ".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".js", ".mjs", ".cjs",
    ".html", ".htm", ".shtml", ".xhtml", ".php", ".phtml", ".asp", ".aspx",
    ".sh", ".bash", ".zsh", ".ps1", ".dll", ".so", ".dylib", ".wasm",
    ".svg", ".xml", ".xsl", ".hta", ".vbs", ".vbe", ".jar", ".apk", ".msi",
    ".deb", ".rpm", ".py", ".rb", ".pl", ".cgi",
})

# First bytes we never accept as a note, even if the client lies about MIME.
_REJECT_PREFIXES = (
    b"MZ",
    b"\x7fELF",
    b"%PDF",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"\x89PNG",
    b"\xff\xd8\xff",
    b"GIF8",
    b"<!DO",
    b"<htm",
    b"<HTM",
    b"<html",
    b"<?ph",
    b"#!/",
    b"\xca\xfe\xba\xbe",
    b"\x00asm",
)


class MediaError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def check_media_id(value: str) -> str:
    if not isinstance(value, str) or not MEDIA_ID_RE.match(value):
        raise MediaError("invalid media id")
    return value


def sniff_container(data: bytes) -> str | None:
    """Return a container key from magic bytes, or None if not a note format."""
    if not data or len(data) < 12:
        return None
    for prefix in _REJECT_PREFIXES:
        if data.startswith(prefix):
            return None
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4"
    if data[:3] == b"ID3":
        return "mp3"
    if data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa", b"\xff\xf4"):
        return "mp3"
    return None


def mime_for(kind: str, container: str) -> str:
    mime = MIME.get((kind, container))
    if not mime:
        raise MediaError("unsupported media type")
    return mime


def validate_kind(kind: str) -> str:
    if kind not in NOTE_CONTAINERS:
        raise MediaError("kind must be voice_note or video_note")
    return kind


def validate_filename(name: str | None) -> None:
    if not name:
        return
    lower = name.lower()
    for ext in DANGEROUS_EXT:
        if lower.endswith(ext):
            raise MediaError("dangerous filename rejected")


def validate_duration_ms(value: Any, max_ms: int = DEFAULT_NOTE_MAX_MS) -> int:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        raise MediaError("duration_ms must be an integer")
    if ms < MIN_NOTE_MS:
        raise MediaError("note too short")
    if ms > max_ms:
        raise MediaError("note exceeds 60 s cap", status=400)
    return ms


def parse_waveform(raw: Any) -> list[int] | None:
    if raw in (None, "", []):
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            raise MediaError("waveform must be JSON") from e
    if not isinstance(raw, list) or len(raw) > 128:
        raise MediaError("waveform must be a list of at most 128 peaks")
    out: list[int] = []
    for x in raw:
        try:
            v = int(x)
        except (TypeError, ValueError) as e:
            raise MediaError("waveform peaks must be integers") from e
        out.append(max(0, min(255, v)))
    return out


@dataclass
class StoredMedia:
    id: str
    user_id: str
    kind: str
    container: str
    mime: str
    size: int
    path: str
    created_at: float
    sha256: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "mime": self.mime,
            "size": self.size,
            "container": self.container,
            "created_at": self.created_at,
        }


class MediaLibrary:
    """Per-user UUID blob store with a byte quota."""

    def __init__(self, root: str | Path, quota_bytes: int = DEFAULT_QUOTA,
                 voice_max: int = DEFAULT_VOICE_MAX, video_max: int = DEFAULT_VIDEO_MAX):
        self.root = Path(root)
        self.quota_bytes = quota_bytes
        self.voice_max = voice_max
        self.video_max = video_max
        self._lock = threading.Lock()
        secure_dir(self.root)

    def _user_dir(self, user_id: str) -> Path:
        return secure_dir(self.root / check_id(user_id, "user_id") / "media")

    def _index_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "index.json"

    def _load_index(self, user_id: str) -> dict[str, Any]:
        p = self._index_path(user_id)
        if not p.exists():
            return {"used_bytes": 0, "files": {}}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"used_bytes": 0, "files": {}}

    def _save_index(self, user_id: str, idx: dict[str, Any]) -> None:
        p = self._index_path(user_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    def max_for(self, kind: str) -> int:
        return self.video_max if kind == "video_note" else self.voice_max

    def put(self, user_id: str, kind: str, data: bytes, filename: str | None = None) -> StoredMedia:
        kind = validate_kind(kind)
        validate_filename(filename)
        if not data:
            raise MediaError("empty file")
        limit = self.max_for(kind)
        if len(data) > limit:
            raise MediaError("file too large", status=413)
        container = sniff_container(data)
        if container is None:
            raise MediaError("unrecognized or disallowed file type")
        if container not in NOTE_CONTAINERS[kind]:
            raise MediaError(f"{kind} does not accept {container} files")
        mime = mime_for(kind, container)
        mid = uuid.uuid4().hex
        dest = self._user_dir(user_id) / (mid + EXT[container])
        with self._lock:
            idx = self._load_index(user_id)
            used = int(idx.get("used_bytes") or 0)
            if used + len(data) > self.quota_bytes:
                raise MediaError("per-user storage quota exceeded", status=413)
            dest.write_bytes(data)
            rec = {
                "id": mid,
                "kind": kind,
                "container": container,
                "mime": mime,
                "size": len(data),
                "path": dest.name,
                "created_at": time.time(),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            files = idx.setdefault("files", {})
            files[mid] = rec
            idx["used_bytes"] = used + len(data)
            self._save_index(user_id, idx)
        return StoredMedia(id=mid, user_id=user_id, kind=kind, container=container,
                           mime=mime, size=len(data), path=str(dest),
                           created_at=rec["created_at"], sha256=rec["sha256"])

    def get(self, user_id: str, media_id: str) -> StoredMedia:
        media_id = check_media_id(media_id)
        with self._lock:
            idx = self._load_index(user_id)
            rec = (idx.get("files") or {}).get(media_id)
        if not rec:
            raise MediaError("media not found", status=404)
        path = self._user_dir(user_id) / rec["path"]
        if not path.is_file():
            raise MediaError("media not found", status=404)
        return StoredMedia(id=media_id, user_id=user_id, kind=rec["kind"],
                           container=rec["container"], mime=rec["mime"], size=rec["size"],
                           path=str(path), created_at=rec["created_at"],
                           sha256=rec.get("sha256", ""))

    def read_bytes(self, media: StoredMedia) -> bytes:
        return Path(media.path).read_bytes()

    def delete(self, user_id: str, media_id: str) -> None:
        """Remove a blob and drop it from the quota index. Missing ids are a no-op."""
        try:
            media_id = check_media_id(media_id)
        except MediaError:
            return
        path: Path | None = None
        with self._lock:
            idx = self._load_index(user_id)
            files = idx.get("files") or {}
            rec = files.pop(media_id, None)
            if not rec:
                return
            idx["files"] = files
            idx["used_bytes"] = max(0, int(idx.get("used_bytes") or 0) - int(rec.get("size") or 0))
            self._save_index(user_id, idx)
            path = self._user_dir(user_id) / rec["path"]
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def quota(self, user_id: str) -> dict[str, int]:
        idx = self._load_index(user_id)
        return {
            "used_bytes": int(idx.get("used_bytes") or 0),
            "quota_bytes": self.quota_bytes,
            "files": len(idx.get("files") or {}),
        }

    def dump_user(self, user_id: str) -> list[dict[str, Any]]:
        """Metadata only — blobs are not inlined into the JSON export."""
        uid = check_id(user_id, "user_id")
        p = self.root / uid / "media" / "index.json"
        if not p.exists():
            return []
        try:
            idx = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        files = idx.get("files") or {}
        out = []
        for mid, rec in files.items():
            if not isinstance(rec, dict):
                continue
            out.append({
                "id": mid,
                "kind": rec.get("kind"),
                "mime": rec.get("mime"),
                "size": rec.get("size"),
                "sha256": rec.get("sha256"),
                "created_at": rec.get("created_at"),
            })
        return out

    def purge_user(self, user_id: str) -> bool:
        uid = check_id(user_id, "user_id")
        # ChatStore.purge_user already removes `{root}/{uid}` including media/.
        media = self.root / uid / "media"
        if not media.exists():
            return False
        shutil.rmtree(media, ignore_errors=True)
        return True


class DownloadTokens:
    """Expiring play tokens (reusable until TTL) and one-time download tokens."""

    def __init__(self, path: str | Path, secret: bytes):
        self.path = Path(path)
        self.secret = secret
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_used(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return set(data.get("used") or [])
        except (OSError, json.JSONDecodeError):
            return set()

    def _save_used(self, used: set[str]) -> None:
        # Keep the set bounded so the file cannot grow without limit.
        items = list(used)[-4000:]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"used": items}), encoding="utf-8")
        tmp.replace(self.path)

    def mint(self, media_id: str, user_id: str, *, one_time: bool, ttl: int) -> str:
        nonce = uuid.uuid4().hex
        payload = {"m": media_id, "u": user_id, "n": nonce, "once": 1 if one_time else 0}
        return sign(self.secret, payload, "media", ttl=ttl)

    def consume(self, token: str, media_id: str) -> dict[str, Any]:
        body = unsign(self.secret, token, "media")
        if not body:
            raise MediaError("invalid or expired download token", status=401)
        if body.get("m") != media_id:
            raise MediaError("token does not match media", status=401)
        if body.get("once"):
            nonce = str(body.get("n") or "")
            with self._lock:
                used = self._load_used()
                if nonce in used:
                    raise MediaError("one-time token already used", status=410)
                used.add(nonce)
                self._save_used(used)
        return body
