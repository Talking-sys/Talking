"""Premium hook for live conversation mode. Default: free in the GPL core.

Set ``VOICE_TRANSLATE_LIVE_PREMIUM=1`` to require a commercial licence
(``GET {COMMERCIAL_API_URL}/v1/license/status``). Record-for-translation stays
free either way — it is the same cost class as sending a chat voice note.

The proprietary service is allowed to add ``entitlements: ["live_translate"]``
later without a core schema change. Until then ``tier`` in {trial, paid, premium}
counts as entitled.
"""
from __future__ import annotations

from typing import Any


PREMIUM_TIERS = frozenset({"trial", "paid", "premium"})
ENTITLEMENT_KEYS = frozenset({"live_translate", "voice_translate", "live_voice_translate"})


def _entitlements_from(status: dict[str, Any]) -> set[str]:
    raw = status.get("entitlements") or status.get("modules") or status.get("features") or []
    if isinstance(raw, dict):
        return {str(k) for k, v in raw.items() if v}
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, (list, tuple, set)):
        return {str(x) for x in raw}
    return set()


def license_grants_live(status: dict[str, Any] | None) -> bool:
    if not status or status.get("disabled") or status.get("ok") is False:
        return False
    if _entitlements_from(status) & ENTITLEMENT_KEYS:
        return True
    if status.get("entitled") is True:
        return True
    tier = str(status.get("tier") or "").strip().lower()
    return tier in PREMIUM_TIERS


def live_translate_allowed(engine, user_id: str = "local") -> dict[str, Any]:
    """Return a JSON-ready entitlement snapshot. Never raises."""
    s = engine.s
    premium = bool(getattr(s, "voice_translate_live_premium", False))
    info: dict[str, Any] = {
        "premium_gated": premium,
        "allowed": True,
        "reason": "free_core",
        "tier": "free",
        "commercial": engine.commercial.status() if getattr(engine, "commercial", None) else {"mode": "stub"},
    }
    if not premium:
        return info
    try:
        status = engine.commercial.license_status(user_id)
    except Exception as e:  # noqa: BLE001 — fail closed when gated
        info.update(allowed=False, reason=f"license_check_failed:{type(e).__name__}", tier="unknown")
        return info
    info["license"] = {k: status[k] for k in status if k in ("ok", "disabled", "tier", "entitlements", "entitled", "resource")}
    if license_grants_live(status):
        info.update(allowed=True, reason="licensed", tier=str(status.get("tier") or "paid"))
        return info
    if status.get("disabled"):
        info.update(allowed=False, reason="commercial_unconfigured", tier="none")
        return info
    info.update(allowed=False, reason="not_entitled", tier=str(status.get("tier") or "free"))
    return info
