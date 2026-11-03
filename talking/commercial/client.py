"""Outbound HTTP client to the proprietary commercial service.

If COMMERCIAL_API_URL is empty the client returns disabled stubs so the GPL
core still runs locally with no keys and no commercial repo.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)


class CommercialDisabled(RuntimeError):
    """Raised only if a caller insists on a live commercial call while unconfigured."""


@dataclass
class CommercialStatus:
    enabled: bool
    url: str
    reachable: bool | None
    modules: list[str]


COMMERCIAL_MODULES = [
    "admin",
    "licensing",
    "talk_coins",
    "p2p_payments",
    "analytics",
]


class CommercialClient:
    def __init__(self, base_url: str = "", token: str = "", timeout: float = 5.0):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.timeout = timeout
        self.enabled = bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json", "User-Agent": "Talking-open-core/0.1"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "url": self.base_url or None,
            "modules": COMMERCIAL_MODULES,
            "mode": "live" if self.enabled else "stub",
            "note": "Proprietary modules live in a separate All-rights-reserved repo "
                    "and talk to this core only over HTTP/WS. This process never links them.",
        }

    def _stub(self, resource: str, **extra) -> dict:
        return {"ok": False, "disabled": True, "resource": resource,
                "message": "Commercial API is not configured (COMMERCIAL_API_URL empty).", **extra}

    def get(self, path: str, default_resource: str) -> dict:
        if not self.enabled:
            return self._stub(default_resource)
        url = self.base_url + path
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.get(url, headers=self._headers())
                r.raise_for_status()
                data = r.json()
                return data if isinstance(data, dict) else {"ok": True, "data": data}
        except Exception as e:
            log.warning("commercial GET %s failed: %s", path, type(e).__name__)
            return {"ok": False, "error": type(e).__name__, "resource": default_resource}

    def post(self, path: str, body: dict[str, Any], default_resource: str) -> dict:
        if not self.enabled:
            return self._stub(default_resource)
        url = self.base_url + path
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(url, json=body, headers=self._headers())
                r.raise_for_status()
                data = r.json()
                return data if isinstance(data, dict) else {"ok": True, "data": data}
        except Exception as e:
            log.warning("commercial POST %s failed: %s", path, type(e).__name__)
            return {"ok": False, "error": type(e).__name__, "resource": default_resource}

    def license_status(self, user_id: str) -> dict:
        return self.get("/v1/license/status", "licensing") | {"user_hint": user_id[:8]}

    def track(self, event: str, payload: dict | None = None) -> dict:
        return self.post("/v1/analytics/event", {"event": event, "payload": payload or {}, "ts": time.time()},
                         "analytics")

    def talk_coins_balance(self) -> dict:
        return self.get("/v1/talk-coins/balance", "talk_coins")

    def checkout(self, plan: str) -> dict:
        return self.post("/v1/payments/checkout", {"plan": plan}, "p2p_payments")

    def admin_health(self) -> dict:
        return self.get("/v1/admin/health", "admin")


def verify_webhook(secret: str, body: bytes, signature: str) -> bool:
    if not secret or not signature:
        return False
    expect = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    given = signature.split("=")[-1].strip()
    if len(given) != len(expect):
        return False
    return hmac.compare_digest(given, expect)
