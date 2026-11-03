"""Auth endpoint rate limits: Redis when configured, otherwise in-memory."""
from __future__ import annotations

import logging
import time
from collections import defaultdict

log = logging.getLogger("talking.auth")


class AuthRateLimiter:
    def __init__(self, redis_url: str = ""):
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._redis = None
        if redis_url:
            try:
                import redis as redis_lib
                r = redis_lib.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
                r.ping()
                self._redis = r
            except Exception:
                log.info("auth rate limiter using memory (redis unavailable)")
                self._redis = None

    def allow(self, key: str, limit: int, window_s: int) -> bool:
        """Return True if the event is within the limit."""
        if limit <= 0:
            return True
        if self._redis is not None:
            try:
                n = self._redis.incr(key)
                if n == 1:
                    self._redis.expire(key, window_s)
                return int(n) <= limit
            except Exception:
                log.info("redis rate limit failed; falling back to memory")
                self._redis = None
        now = time.time()
        bucket = self._hits[key]
        cutoff = now - window_s
        self._hits[key] = bucket = [t for t in bucket if t > cutoff]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True
