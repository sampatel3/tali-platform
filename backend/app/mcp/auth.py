"""Bearer-token auth for the MCP server.

Reuses the fastapi-users JWT secret/audience so any access token issued by
``/api/v1/auth/jwt/login`` works against MCP without a separate flow.
"""

from __future__ import annotations

from typing import Any

import jwt
from fastapi_users.jwt import decode_jwt
from sqlalchemy.orm import Session

from ..models.user import User
from ..platform.config import settings

# Must match JWTStrategy in ``domains/identity_access/users_fastapi.py``.
_TOKEN_AUDIENCE = ["fastapi-users:auth"]
_ALGORITHM = "HS256"


class MCPAuthError(Exception):
    """Raised when a request lacks a valid bearer token."""


def _extract_bearer_token(headers: Any) -> str:
    """Pull a bearer token off a Starlette/Headers-like mapping."""
    if headers is None:
        raise MCPAuthError("missing authorization header")
    raw = None
    # Starlette Headers are case-insensitive
    try:
        raw = headers.get("authorization")
    except AttributeError:
        # Plain dict — try both cases
        raw = headers.get("Authorization") or headers.get("authorization")
    if not raw:
        raise MCPAuthError("missing authorization header")
    parts = raw.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise MCPAuthError("authorization header must be 'Bearer <token>'")
    return parts[1]


def authenticate_request(request: Any, db: Session) -> User:
    """Validate a Starlette Request's bearer token and return the active User.

    Raises ``MCPAuthError`` on any auth failure. Callers should catch and
    re-raise as a tool-friendly error.
    """
    if request is None:
        raise MCPAuthError("no HTTP request bound to MCP context")
    token = _extract_bearer_token(getattr(request, "headers", None))
    try:
        data = decode_jwt(token, settings.SECRET_KEY, _TOKEN_AUDIENCE, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise MCPAuthError(f"invalid token: {exc}") from exc
    user_id = data.get("sub")
    if user_id is None:
        raise MCPAuthError("token has no subject")
    try:
        user_pk = int(user_id)
    except (TypeError, ValueError) as exc:
        raise MCPAuthError("invalid user id in token") from exc
    user = db.query(User).filter(User.id == user_pk).first()
    if user is None:
        raise MCPAuthError("user not found")
    if not bool(getattr(user, "is_active", True)):
        raise MCPAuthError("user inactive")
    if user.organization_id is None:
        raise MCPAuthError("user has no organization")
    return user
