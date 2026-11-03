"""HTTP/WS client for the SEPARATE proprietary commercial repo.

The open core MUST NOT import, link, or vendor proprietary modules.
See docs/ARCHITECTURE.md and docs/commercial/.
"""
from .client import (
    ENTITLEMENT_VOICE_TRANSCRIPTION,
    CommercialClient,
    CommercialDisabled,
    EntitlementDecision,
)

__all__ = [
    "CommercialClient",
    "CommercialDisabled",
    "EntitlementDecision",
    "ENTITLEMENT_VOICE_TRANSCRIPTION",
]
