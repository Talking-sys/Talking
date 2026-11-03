"""Anonymous-account capability policy (M10 product limits).

Verified users (phone now; email/Google/Apple/passkeys later) are unrestricted
by this module. Anonymous users in AUTH_MODE=accounts get an allowlist plus
tighter rate/quota caps. AUTH_MODE=local is the single-user translator and is
never throttled here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from fastapi import HTTPException, Request

# Named product capabilities. Unknown env values are ignored.
ALL_CAPABILITIES = frozenset({
    "chat",
    "chat_notes",
    "voice_translate",
    "history",
    "settings",
    "transcription",
    "talk_coins",
    "checkout",
    "games",
    "groups",
    "nickname",
    "commercial",
})

# Default anonymous allowlist: translator + personal chat, not commerce/premium.
DEFAULT_ANON_CAPABILITIES = frozenset({
    "chat",
    "chat_notes",
    "voice_translate",
    "history",
    "settings",
})

VERIFIED_PROVIDERS = frozenset({
    "phone", "email", "google", "apple", "passkey", "webauthn",
})

RECOVERY_REISSUE_ALWAYS = "always"
RECOVERY_REISSUE_NEVER = "never"
RECOVERY_REISSUE_IF_ANONYMOUS = "if_anonymous"
RECOVERY_REISSUE_MODES = frozenset({
    RECOVERY_REISSUE_ALWAYS, RECOVERY_REISSUE_NEVER, RECOVERY_REISSUE_IF_ANONYMOUS,
})

# Defaults: 5 creates / hour / IP, 20 / day; 30-day idle expiry.
DEFAULT_CREATE_PER_IP = 5
DEFAULT_CREATE_WINDOW_S = 3600
DEFAULT_CREATE_PER_IP_DAY = 20
DEFAULT_IDLE_SECONDS = 30 * 24 * 3600
DEFAULT_CLEANUP_INTERVAL_S = 3600
DEFAULT_CLEANUP_BATCH = 50
DEFAULT_MERGE_TTL_S = 10 * 60
DEFAULT_ANON_CHAT_MSG_PER_MIN = 5
DEFAULT_ANON_CHAT_UPLOAD_PER_MIN = 5
DEFAULT_ANON_CHAT_QUOTA_BYTES = 50 * 1024 * 1024
DEFAULT_ANON_VOICE_TRANSLATE_RECORD_PER_MIN = 8
DEFAULT_ANON_WS_CONNECTS_PER_MIN = 10


def parse_capability_set(raw: str | None, *, default: frozenset[str] | None = None) -> frozenset[str]:
    text = (raw or "").strip().lower()
    if not text:
        return frozenset(default or ())
    if text in ("none", "off", "deny"):
        return frozenset()
    if text in ("*", "all"):
        return frozenset(ALL_CAPABILITIES)
    out = {p.strip() for p in text.split(",") if p.strip()}
    return frozenset(p for p in out if p in ALL_CAPABILITIES)


def parse_recovery_reissue(raw: str | None) -> str:
    text = (raw or RECOVERY_REISSUE_ALWAYS).strip().lower()
    if text in ("0", "false", "no", "off"):
        return RECOVERY_REISSUE_NEVER
    if text in ("1", "true", "yes", "on"):
        return RECOVERY_REISSUE_ALWAYS
    if text in RECOVERY_REISSUE_MODES:
        return text
    return RECOVERY_REISSUE_ALWAYS


def identities_are_anonymous(methods: Iterable[Any] | None) -> bool:
    """True when the account has no verified sign-in method yet."""
    rows = list(methods or ())
    if not rows:
        return True
    return not any(getattr(m, "provider", None) in VERIFIED_PROVIDERS for m in rows)


def should_reissue_recovery(mode: str, is_anonymous: bool) -> bool:
    mode = parse_recovery_reissue(mode)
    if mode == RECOVERY_REISSUE_NEVER:
        return False
    if mode == RECOVERY_REISSUE_IF_ANONYMOUS:
        return is_anonymous
    return True


@dataclass(frozen=True)
class AnonymousPolicy:
    allowed: frozenset[str] = field(default_factory=lambda: frozenset(DEFAULT_ANON_CAPABILITIES))
    create_per_ip: int = DEFAULT_CREATE_PER_IP
    create_window_s: int = DEFAULT_CREATE_WINDOW_S
    create_per_ip_day: int = DEFAULT_CREATE_PER_IP_DAY
    chat_msg_per_min: int = DEFAULT_ANON_CHAT_MSG_PER_MIN
    chat_upload_per_min: int = DEFAULT_ANON_CHAT_UPLOAD_PER_MIN
    chat_quota_bytes: int = DEFAULT_ANON_CHAT_QUOTA_BYTES
    voice_translate_record_per_min: int = DEFAULT_ANON_VOICE_TRANSLATE_RECORD_PER_MIN
    ws_connects_per_min: int = DEFAULT_ANON_WS_CONNECTS_PER_MIN
    recovery_reissue: str = RECOVERY_REISSUE_ALWAYS
    idle_seconds: int = DEFAULT_IDLE_SECONDS
    cleanup_interval_s: int = DEFAULT_CLEANUP_INTERVAL_S
    cleanup_batch: int = DEFAULT_CLEANUP_BATCH
    merge_ttl_s: int = DEFAULT_MERGE_TTL_S
    verified_chat_msg_per_min: int = 10
    verified_chat_upload_per_min: int = 20
    verified_chat_quota_bytes: int = 500 * 1024 * 1024
    verified_voice_translate_record_per_min: int = 20
    verified_ws_connects_per_min: int = 20

    @classmethod
    def from_settings(cls, settings) -> "AnonymousPolicy":
        allowed = parse_capability_set(
            getattr(settings, "anon_capabilities", None),
            default=DEFAULT_ANON_CAPABILITIES,
        )
        denied = parse_capability_set(getattr(settings, "anon_denied_capabilities", None), default=frozenset())
        allowed = frozenset(allowed - denied)
        return cls(
            allowed=allowed,
            create_per_ip=int(getattr(settings, "anon_create_per_ip", DEFAULT_CREATE_PER_IP) or 0),
            create_window_s=int(getattr(settings, "anon_create_window_s", DEFAULT_CREATE_WINDOW_S) or 0),
            create_per_ip_day=int(getattr(settings, "anon_create_per_ip_day", DEFAULT_CREATE_PER_IP_DAY) or 0),
            chat_msg_per_min=int(getattr(settings, "anon_chat_msg_per_min", DEFAULT_ANON_CHAT_MSG_PER_MIN) or 0),
            chat_upload_per_min=int(getattr(settings, "anon_chat_upload_per_min", DEFAULT_ANON_CHAT_UPLOAD_PER_MIN) or 0),
            chat_quota_bytes=int(getattr(settings, "anon_chat_quota_bytes", DEFAULT_ANON_CHAT_QUOTA_BYTES) or 0),
            voice_translate_record_per_min=int(
                getattr(settings, "anon_voice_translate_record_per_min", DEFAULT_ANON_VOICE_TRANSLATE_RECORD_PER_MIN) or 0
            ),
            ws_connects_per_min=int(getattr(settings, "anon_ws_connects_per_min", DEFAULT_ANON_WS_CONNECTS_PER_MIN) or 0),
            recovery_reissue=parse_recovery_reissue(getattr(settings, "anon_recovery_reissue", None)),
            idle_seconds=int(getattr(settings, "anon_idle_seconds", DEFAULT_IDLE_SECONDS) or 0),
            cleanup_interval_s=int(getattr(settings, "anon_cleanup_interval_s", DEFAULT_CLEANUP_INTERVAL_S) or 0),
            cleanup_batch=int(getattr(settings, "anon_cleanup_batch", DEFAULT_CLEANUP_BATCH) or DEFAULT_CLEANUP_BATCH),
            merge_ttl_s=int(getattr(settings, "anon_merge_ttl_s", DEFAULT_MERGE_TTL_S) or DEFAULT_MERGE_TTL_S),
            verified_chat_msg_per_min=int(getattr(settings, "chat_msg_per_min", 10) or 10),
            verified_chat_upload_per_min=int(getattr(settings, "chat_upload_per_min", 20) or 20),
            verified_chat_quota_bytes=int(getattr(settings, "chat_quota_bytes", 500 * 1024 * 1024) or 0),
            verified_voice_translate_record_per_min=int(
                getattr(settings, "voice_translate_record_per_min", 20) or 20
            ),
            verified_ws_connects_per_min=int(getattr(settings, "ws_connects_per_min", 20) or 20),
        )

    def allows(self, capability: str) -> bool:
        return capability in self.allowed

    def applies_to(self, user) -> bool:
        if user is None or not getattr(user, "authenticated", False):
            return False
        if getattr(user, "auth_mode", "") != "accounts":
            return False
        return bool(getattr(user, "is_anonymous", False))

    def limits_for(self, apply_anon: bool) -> dict[str, int]:
        if apply_anon:
            return {
                "chat_msg_per_min": self.chat_msg_per_min,
                "chat_upload_per_min": self.chat_upload_per_min,
                "chat_quota_bytes": self.chat_quota_bytes,
                "voice_translate_record_per_min": self.voice_translate_record_per_min,
                "ws_connects_per_min": self.ws_connects_per_min,
                "create_per_ip": self.create_per_ip,
                "create_window_s": self.create_window_s,
                "create_per_ip_day": self.create_per_ip_day,
                "idle_seconds": self.idle_seconds,
            }
        return {
            "chat_msg_per_min": self.verified_chat_msg_per_min,
            "chat_upload_per_min": self.verified_chat_upload_per_min,
            "chat_quota_bytes": self.verified_chat_quota_bytes,
            "voice_translate_record_per_min": self.verified_voice_translate_record_per_min,
            "ws_connects_per_min": self.verified_ws_connects_per_min,
            "idle_seconds": 0,
        }

    def public_for(self, apply_anon: bool) -> dict[str, Any]:
        if apply_anon:
            allowed = sorted(self.allowed)
            denied = sorted(ALL_CAPABILITIES - self.allowed)
        else:
            allowed = sorted(ALL_CAPABILITIES)
            denied = []
        return {
            "anonymous": bool(apply_anon),
            "allowed": allowed,
            "denied": denied,
            "limits": self.limits_for(apply_anon),
            "recovery_reissue": self.recovery_reissue,
            "verified_providers": sorted(VERIFIED_PROVIDERS),
        }

    def catalog(self) -> dict[str, Any]:
        return {
            "capabilities": sorted(ALL_CAPABILITIES),
            "default_anonymous_allowed": sorted(DEFAULT_ANON_CAPABILITIES),
            "policy": self.public_for(True),
        }


def capability_denied_body(capability: str) -> dict[str, Any]:
    return {
        "error": "capability_denied",
        "capability": capability,
        "anonymous": True,
        "hint": "Link a verified sign-in method (phone now; email, Google, Apple, or a passkey later) to keep this account and unlock the feature.",
    }


def deny_anonymous_capability(request: Request, capability: str) -> None:
    """Raise 403 if the current accounts-mode anonymous user lacks `capability`."""
    policy: AnonymousPolicy | None = getattr(request.app.state, "anon_policy", None)
    user = getattr(request.state, "user", None)
    if policy is None or user is None:
        return
    if not policy.applies_to(user):
        return
    if policy.allows(capability):
        return
    raise HTTPException(403, capability_denied_body(capability))


def policy_of(request: Request) -> AnonymousPolicy:
    policy = getattr(request.app.state, "anon_policy", None)
    return policy if isinstance(policy, AnonymousPolicy) else AnonymousPolicy()


def effective_limits(request: Request) -> dict[str, int]:
    policy = policy_of(request)
    user = getattr(request.state, "user", None)
    return policy.limits_for(policy.applies_to(user))
