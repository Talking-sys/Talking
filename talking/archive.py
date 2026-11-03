"""Two-layer archive: local JSONL always; optional S3-compatible (R2/MinIO/AWS) if S3_* are set."""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from .ids import DATE_RE, check_id, safe_join

log = logging.getLogger(__name__)


def session_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _merge(existing: list[dict], new: list[dict]) -> list[dict]:
    by_id = {r["segment_id"]: r for r in existing}
    for r in new:
        by_id[r["segment_id"]] = r
    return sorted(by_id.values(), key=lambda r: (r.get("start", 0), r["segment_id"]))


def _summary(user_id: str, date: str, session_id: str, recs: list[dict]) -> dict:
    langs = sorted({r.get("src_lang", "") for r in recs} | {r.get("tgt_lang", "") for r in recs} - {""})
    return {"user_id": user_id, "date": date, "session_id": session_id, "segments": len(recs),
            "languages": langs, "started": min((r.get("start", 0) for r in recs), default=0),
            "ended": max((r.get("end", 0) for r in recs), default=0),
            "deferred": sum(1 for r in recs if r.get("deferred"))}


class StorageBackend(ABC):
    name = "base"

    @abstractmethod
    def append_segments(self, user_id: str, date: str, session_id: str, segments: list[dict]) -> None: ...

    @abstractmethod
    def list_sessions(self, user_id: str, date: str | None = None) -> list[dict]: ...

    @abstractmethod
    def read_session(self, user_id: str, date: str, session_id: str) -> list[dict]: ...


class LocalStorageBackend(StorageBackend):
    name = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, user_id, date, session_id) -> Path:
        check_id(user_id, "user_id")
        check_id(session_id, "session_id")
        if not DATE_RE.match(date or ""):
            raise ValueError("invalid date")
        return safe_join(self.root, user_id, date, f"{session_id}.jsonl")

    def append_segments(self, user_id, date, session_id, segments):
        p = self._path(user_id, date, session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            have = {r["segment_id"] for r in self._read(p)}
            new = [s for s in segments if s["segment_id"] not in have]
            if new:
                with open(p, "a", encoding="utf-8") as f:
                    for s in new:
                        f.write(json.dumps(s, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())

    @staticmethod
    def _read(p: Path) -> list[dict]:
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return out

    def read_session(self, user_id, date, session_id):
        return _merge([], self._read(self._path(user_id, date, session_id)))

    def list_sessions(self, user_id, date=None):
        check_id(user_id, "user_id")
        base = self.root / user_id
        if not base.exists():
            return []
        days = [base / date] if date else sorted(base.iterdir(), reverse=True)
        out = []
        for d in days:
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.jsonl")):
                out.append(_summary(user_id, d.name, f.stem, self.read_session(user_id, d.name, f.stem)))
        return sorted(out, key=lambda s: -s["started"])


class S3StorageBackend(StorageBackend):
    name = "s3"

    def __init__(self, bucket: str, endpoint_url: str | None = None, access_key: str | None = None,
                 secret_key: str | None = None, region: str | None = None, client=None):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self.s3 = client or boto3.client(
            "s3", endpoint_url=endpoint_url or None, aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None, region_name=region or "us-east-1",
            config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}))

    @staticmethod
    def key(user_id, date, session_id) -> str:
        return f"{user_id}/{date}/{session_id}.jsonl"

    def _get(self, key) -> list[dict]:
        from botocore.exceptions import ClientError
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode("utf-8")
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
                return []
            raise
        return [json.loads(l) for l in body.splitlines() if l.strip()]

    def append_segments(self, user_id, date, session_id, segments):
        k = self.key(user_id, date, session_id)
        merged = _merge(self._get(k), segments)
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged).encode("utf-8")
        self.s3.put_object(Bucket=self.bucket, Key=k, Body=body,
                           ContentType="application/x-ndjson")

    def read_session(self, user_id, date, session_id):
        return _merge([], self._get(self.key(user_id, date, session_id)))

    def list_sessions(self, user_id, date=None):
        prefix = f"{user_id}/{date}/" if date else f"{user_id}/"
        out = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                parts = obj["Key"].split("/")
                if len(parts) == 3 and parts[2].endswith(".jsonl"):
                    sid = parts[2][:-6]
                    out.append(_summary(user_id, parts[1], sid, self.read_session(user_id, parts[1], sid)))
        return sorted(out, key=lambda s: -s["started"])


def s3_from_env() -> S3StorageBackend | None:
    bucket = os.environ.get("S3_BUCKET")
    if not bucket or not (os.environ.get("S3_ACCESS_KEY") and os.environ.get("S3_SECRET_KEY")):
        return None
    return S3StorageBackend(bucket, os.environ.get("S3_ENDPOINT"), os.environ.get("S3_ACCESS_KEY"),
                            os.environ.get("S3_SECRET_KEY"), os.environ.get("S3_REGION"))


class ArchiveManager:
    def __init__(self, data_dir: str | Path, remote: StorageBackend | None = None):
        self.data_dir = Path(data_dir)
        self.local = LocalStorageBackend(self.data_dir / "archive")
        self.remote = remote
        self.last_error: str | None = None

    @property
    def cloud_enabled(self) -> bool:
        return self.remote is not None

    def archive(self, rec: dict) -> None:
        user, date, sid = rec["user_id"], session_date(rec.get("start") or time.time()), rec["session_id"]
        rec = {**rec, "date": date}
        try:
            self.local.append_segments(user, date, sid, [rec])
        except Exception as e:
            log.warning("local archive: %s", e)
        if not self.remote:
            return
        try:
            self.remote.append_segments(user, date, sid, [rec])
        except Exception as e:
            self.last_error = str(e)[:200]
            log.warning("remote archive: %s", e)

    def status(self) -> dict:
        return {"cloud": self.cloud_enabled, "last_error": self.last_error}

    def purge_user(self, user_id: str) -> bool:
        check_id(user_id, "user_id")
        gone = False
        local = self.local.root / user_id
        if local.is_dir():
            shutil.rmtree(local, ignore_errors=True)
            gone = True
        if self.remote is not None and hasattr(self.remote, "list_sessions"):
            try:
                for summary in self.remote.list_sessions(user_id):
                    key = f"{user_id}/{summary['date']}/{summary['session_id']}.jsonl"
                    try:
                        self.remote.s3.delete_object(Bucket=self.remote.bucket, Key=key)
                    except Exception:
                        log.debug("remote archive delete failed key=%s", key, exc_info=True)
            except Exception:
                log.debug("remote archive purge failed user=%s", user_id, exc_info=True)
        return gone
