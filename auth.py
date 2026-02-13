"""Bearer token verification for WebSocket and REST endpoints."""

from config import get_auth_token


def verify_token(token: str) -> bool:
    """Compare provided token against stored auth token."""
    return token == get_auth_token()
