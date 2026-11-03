"""Outbound HTTP client to the proprietary commercial service.

If COMMERCIAL_API_URL is empty the client returns disabled stubs so the GPL
core still runs locally with no keys and no commercial repo.

Entitlements (e.g. ``voice_transcription``) are checked here and **fail closed**
when the commercial service is configured but unreachable. A local env stub
(``COMMERCIAL_DEV_ENTITLEMENTS`` / ``COMMERCIAL_DEV_PREMIUM``) is for
development and tests only — it is not a licence.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

ENTITLEMENT_VOICE_TRANSCRIPTION = "voice_transcription"
PREMIUM_TIERS = frozenset({"paid", "premium", "trial"})


class CommercialDisabled(RuntimeError):
    """Raised only if a caller insists on a live commercial call while unconfigured."""


@dataclass
class CommercialStatus:
    enabled: bool
    url: str
    reachable: bool | None
    modules: list[str]


@dataclass
class EntitlementDecision:
    entitled: bool
    source: str  # commercial | dev_override | stub | unreachable | cache
    entitlement: str
    tier: str | None = None
    cached: bool = False
    error: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "entitled": self.entitled,
            "source": self.source,
            "entitlement": self.entitlement,
            "tier": self.tier,
            "cached": self.cached,
        }


COMMERCIAL_MODULES = [
    "admin",
    "licensing",
    "talk_coins",
    "p2p_payments",
    "analytics",
]


def _parse_entitlements(raw: str) -> set[str]:
    if not raw:
        return set()
    return {p.strip() for p in raw.replace(";", ",").split(",") if p.strip()}


class CommercialClient:
    def __init__(self, base_url: str = "", token: str = "", timeout: float = 5.0,
                 app_env: str = "development",
                 dev_entitlements: str = "",
                 dev_premium: bool = False,
                 cache_ttl: float = 60.0):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.timeout = timeout
        self.enabled = bool(self.base_url)
        self.app_env = (app_env or "development").lower()
        self._dev_entitlements = _parse_entitlements(dev_entitlements)
        if dev_premium:
            self._dev_entitlements.add(ENTITLEMENT_VOICE_TRANSCRIPTION)
            self._dev_entitlements.add("*")
        self.cache_ttl = max(0.0, float(cache_ttl or 0.0))
        self._cache: dict[tuple[str, str], tuple[float, EntitlementDecision]] = {}
        self._cache_lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings) -> "CommercialClient":
        return cls(
            base_url=getattr(settings, "commercial_api_url", "") or "",
            token=getattr(settings, "commercial_api_token", "") or "",
            app_env=getattr(settings, "app_env", "development") or "development",
            dev_entitlements=getattr(settings, "commercial_dev_entitlements", "") or "",
            dev_premium=bool(getattr(settings, "commercial_dev_premium", False)),
            cache_ttl=float(getattr(settings, "commercial_entitlement_cache_ttl", 60.0) or 0.0),
            timeout=float(getattr(settings, "commercial_api_timeout", 5.0) or 5.0),
        )

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

    def _dev_grants(self, entitlement: str) -> bool:
        if "*" in self._dev_entitlements:
            return True
        return entitlement in self._dev_entitlements

    def _cache_get(self, user_id: str, entitlement: str) -> EntitlementDecision | None:
        if self.cache_ttl <= 0:
            return None
        key = (user_id, entitlement)
        with self._cache_lock:
            hit = self._cache.get(key)
            if not hit:
                return None
            expires, decision = hit
            if expires < time.time():
                self._cache.pop(key, None)
                return None
            cached = EntitlementDecision(
                entitled=decision.entitled, source=decision.source, entitlement=entitlement,
                tier=decision.tier, cached=True, error=decision.error,
            )
            return cached

    def _cache_put(self, user_id: str, decision: EntitlementDecision) -> None:
        if self.cache_ttl <= 0:
            return
        ttl = self.cache_ttl
        if not decision.entitled:
            ttl = min(ttl, 10.0)
        with self._cache_lock:
            self._cache[(user_id, decision.entitlement)] = (time.time() + ttl, decision)

    def _granted_from_payload(self, data: dict, entitlement: str) -> bool:
        ents = data.get("entitlements")
        if isinstance(ents, list):
            names = {str(x) for x in ents}
            if entitlement in names or "*" in names:
                return True
        flags = data.get("flags")
        if isinstance(flags, dict) and flags.get(entitlement) is True:
            return True
        if data.get(entitlement) is True:
            return True
        tier = str(data.get("tier") or data.get("plan") or "").lower()
        if entitlement == ENTITLEMENT_VOICE_TRANSCRIPTION and tier in PREMIUM_TIERS:
            return True
        return False

    def check_entitlement(self, user_id: str, entitlement: str) -> EntitlementDecision:
        """Authoritative entitlement check. Fail-closed on commercial errors."""
        uid = user_id or "local"
        cached = self._cache_get(uid, entitlement)
        if cached is not None:
            return cached

        if self._dev_grants(entitlement) and self.app_env not in ("production", "prod"):
            decision = EntitlementDecision(True, "dev_override", entitlement)
            self._cache_put(uid, decision)
            return decision

        if not self.enabled:
            decision = EntitlementDecision(False, "stub", entitlement)
            self._cache_put(uid, decision)
            return decision

        data = self.get(
            f"/v1/license/entitlements?user_id={uid}",
            "licensing",
        )
        if data.get("error") or (data.get("ok") is False and "disabled" not in data):
            # Unreachable / 5xx / transport error. Try licence status once, then deny.
            lic = self.license_status(uid)
            if lic.get("error") or (lic.get("ok") is False and not lic.get("disabled")):
                decision = EntitlementDecision(
                    False, "unreachable", entitlement, error=str(data.get("error") or "unreachable"),
                )
                self._cache_put(uid, decision)
                return decision
            data = lic
        entitled = self._granted_from_payload(data, entitlement)
        tier = str(data.get("tier") or data.get("plan") or "") or None
        decision = EntitlementDecision(entitled, "commercial", entitlement, tier=tier)
        self._cache_put(uid, decision)
        return decision

    def has_entitlement(self, user_id: str, entitlement: str) -> bool:
        """Return True iff the commercial service (or the local dev stub) grants ``entitlement``."""
        return self.check_entitlement(user_id, entitlement).entitled


def verify_webhook(secret: str, body: bytes, signature: str) -> bool:
    if not secret or not signature:
        return False
    expect = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    given = signature.split("=")[-1].strip()
    if len(given) != len(expect):
        return False
    return hmac.compare_digest(given, expect)
