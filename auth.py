"""Bearer token verification for WebSocket and REST endpoints."""

import hmac

from config import get_auth_token


def verify_token(token: str) -> bool:
    """Compare provided token against stored auth token (timing-safe)."""
    return hmac.compare_digest(token, get_auth_token())
