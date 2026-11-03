"""Optional Postgres and Redis. Core runs without either (local files)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class DependencyStatus:
    configured: bool
    ok: bool
    detail: str = ""


async def ping_postgres(database_url: str) -> DependencyStatus:
    if not database_url:
        return DependencyStatus(False, True, "disabled")
    try:
        import asyncpg
        conn = await asyncpg.connect(database_url, timeout=3)
        try:
            v = await conn.fetchval("SELECT 1")
            return DependencyStatus(True, v == 1, "ok")
        finally:
            await conn.close()
    except Exception as e:
        log.warning("postgres ping failed: %s", e)
        return DependencyStatus(True, False, type(e).__name__)


def ping_redis(redis_url: str) -> DependencyStatus:
    if not redis_url:
        return DependencyStatus(False, True, "disabled")
    try:
        import redis as redis_lib
        r = redis_lib.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        ok = r.ping()
        r.close()
        return DependencyStatus(True, bool(ok), "ok")
    except Exception as e:
        log.warning("redis ping failed: %s", e)
        return DependencyStatus(True, False, type(e).__name__)
