"""HTTP/WS client for the SEPARATE proprietary commercial repo.

The open core MUST NOT import, link, or vendor proprietary modules.
See docs/ARCHITECTURE.md and docs/commercial/.
"""
from .client import CommercialClient, CommercialDisabled

__all__ = ["CommercialClient", "CommercialDisabled"]
