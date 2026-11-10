"""M10 auth core (stages 0–2 + phone + Sign in with Apple). Google/passkeys later."""
from __future__ import annotations

from .deps import UserContext, authenticate_websocket, local_user_id, resolve_user
from .http import register_auth
from .sockets import SocketRegistry

__all__ = [
    "UserContext",
    "SocketRegistry",
    "authenticate_websocket",
    "local_user_id",
    "register_auth",
    "resolve_user",
]
