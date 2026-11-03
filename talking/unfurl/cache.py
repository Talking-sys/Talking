"""TTL cache for unfurl JSON and proxied JPEGs. Redis if REDIS_URL else memory."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TTL = 86_400  # 24h
FAILURE_TTL = 600     # 10 min for domain-only / errors


class PreviewCache:
    """JSON + bytes cache. Redis values are opaque; memory keeps (expires, value)."""

    PREFIX = "talking:unfurl:"

    def __init__(self, redis_url: str = ""):
        self._mem: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._redis = None
        self.backend = "memory"
        if redis_url:
            try:
                import redis as redis_lib
                r = redis_lib.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
                r.ping()
                self._redis = r
                self.backend = "redis"
            except Exception as e:
                log.warning("unfurl redis unavailable, using memory: %s", type(e).__name__)

    def _k(self, key: str) -> str:
        return self.PREFIX + key

    def get_json(self, key: str) -> dict | None:
        raw = self._get(key)
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, (bytes, bytearray)):
            try:
                data = json.loads(bytes(raw).decode("utf-8"))
                return data if isinstance(data, dict) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
        return None

    def set_json(self, key: str, value: dict, ttl: int = DEFAULT_TTL) -> None:
        self._set(key, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), ttl)

    def get_bytes(self, key: str) -> bytes | None:
        raw = self._get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, bytearray):
            return bytes(raw)
        if isinstance(raw, str):
            return raw.encode("utf-8")
        return None

    def set_bytes(self, key: str, value: bytes, ttl: int = DEFAULT_TTL) -> None:
        self._set(key, value, ttl)

    def _get(self, key: str) -> Any:
        k = self._k(key)
        if self._redis is not None:
            try:
                return self._redis.get(k)
            except Exception as e:
                log.warning("unfurl redis get failed: %s", type(e).__name__)
        with self._lock:
            item = self._mem.get(k)
            if not item:
                return None
            exp, val = item
            if exp < time.time():
                self._mem.pop(k, None)
                return None
            return val

    def _set(self, key: str, value: bytes, ttl: int) -> None:
        k = self._k(key)
        ttl = max(1, int(ttl))
        if self._redis is not None:
            try:
                self._redis.setex(k, ttl, value)
                return
            except Exception as e:
                log.warning("unfurl redis set failed: %s", type(e).__name__)
        with self._lock:
            self._mem[k] = (time.time() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._mem.clear()
