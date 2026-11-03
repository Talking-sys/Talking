"""Background cleanup for abandoned anonymous accounts."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Awaitable, Callable

from .crypto import utcnow
from .service import AuthService

log = logging.getLogger("talking.auth")

CleanFn = Callable[[str], None]


async def purge_abandoned_anonymous(
    svc: AuthService,
    *,
    now=None,
    idle_seconds: int | None = None,
    limit: int | None = None,
    cleaner: CleanFn | None = None,
) -> dict:
    """Delete idle anonymous-only accounts and all of their stored data.

    Users with any verified identity (phone/email/google/apple/passkey) are
    never expired here. `cleaner` removes file-backed chat/history; auth rows
    are soft-deleted (status=deleted) after child rows are dropped.
    """
    policy = getattr(svc, "policy", None)
    idle = idle_seconds if idle_seconds is not None else int(getattr(policy, "idle_seconds", 0) or 0)
    if idle <= 0:
        return {"purged": 0, "skipped": "disabled"}
    batch = limit if limit is not None else int(getattr(policy, "cleanup_batch", 50) or 50)
    when = now or utcnow()
    cutoff = when - timedelta(seconds=idle)
    rows = await svc.store.list_stale_anonymous(cutoff, limit=batch)
    n = 0
    for user in rows:
        try:
            if cleaner is not None:
                cleaner(user.id)
            await svc.expire_anonymous_user(user.id)
            n += 1
        except Exception:
            log.info("anonymous expire failed user=%s", svc.uid_log(user.id), exc_info=True)
    if n:
        log.info("purged %s abandoned anonymous accounts", n)
    return {"purged": n, "cutoff": cutoff.isoformat(), "idle_seconds": idle}


async def cleanup_loop(
    svc: AuthService,
    *,
    interval_s: int,
    cleaner: CleanFn | None = None,
    stop: asyncio.Event,
    on_tick: Callable[[], Awaitable[None]] | None = None,
) -> None:
    if interval_s <= 0:
        return
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
            return
        except asyncio.TimeoutError:
            pass
        try:
            if on_tick is not None:
                await on_tick()
            await purge_abandoned_anonymous(svc, cleaner=cleaner)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.info("anonymous cleanup tick failed", exc_info=True)
