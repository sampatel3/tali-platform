"""Dual bearer-token auth for the MCP server.

Two credential types resolve against the single ``/mcp`` mount:

* a fastapi-users JWT (issued by ``/api/v1/auth/jwt/login``) — the internal /
  session surface, unchanged; or
* a ``tali_*`` public API key (``models/api_key.py``, verified by
  ``api_key_service.verify_api_key``) — the agent-native public surface.

Both paths produce a small :class:`Principal` carrying ``organization_id`` +
``scopes``. Handlers only ever read ``.organization_id`` off it, so tenant
isolation is inherited for free. Scope enforcement applies to API-key
principals only; JWT (session) principals get implicit full read scopes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

import jwt
from fastapi_users.jwt import decode_jwt
from sqlalchemy.orm import Session

from ..models.api_key import (
    KEY_PREFIX_LIVE,
    KEY_PREFIX_TEST,
    SCOPE_APPLICATIONS_READ,
    SCOPE_ROLES_READ,
)
from ..models.user import User
from ..platform.config import settings
from ..services.api_key_service import verify_api_key

# Must match JWTStrategy in ``domains/identity_access/users_fastapi.py``.
_TOKEN_AUDIENCE = ["fastapi-users:auth"]
_ALGORITHM = "HS256"

_API_KEY_PREFIXES = (KEY_PREFIX_LIVE, KEY_PREFIX_TEST)

# JWT (session) principals act with full read access — no scope gate.
_FULL_READ_SCOPES = frozenset({SCOPE_ROLES_READ, SCOPE_APPLICATIONS_READ})


class MCPAuthError(Exception):
    """Raised when a request lacks a valid bearer token."""


class MCPScopeError(Exception):
    """Raised when an API-key principal lacks a required scope."""


@dataclass(frozen=True)
class Principal:
    """The org + grants behind one MCP tool call.

    ``user`` is populated for JWT principals only; handlers read
    ``organization_id`` and never touch ``user`` directly.
    """

    organization_id: int
    auth_kind: Literal["jwt", "api_key"]
    scopes: frozenset[str]
    user: Optional[User] = None

    def has_scope(self, scope: str) -> bool:
        # JWT principals are unscoped (full read); API keys are gated.
        return self.auth_kind == "jwt" or scope in self.scopes


def _extract_bearer_token(headers: Any) -> Optional[str]:
    """Pull an ``Authorization: Bearer <token>`` value, or ``None``."""
    if headers is None:
        return None
    try:
        raw = headers.get("authorization")
    except AttributeError:
        # Plain dict — try both cases
        raw = headers.get("Authorization") or headers.get("authorization")
    if not raw:
        return None
    parts = raw.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise MCPAuthError("authorization header must be 'Bearer <token>'")
    return parts[1]


def _extract_api_key_header(headers: Any) -> Optional[str]:
    """Pull an ``X-API-Key`` value, mirroring ``api_key_auth.py`` extraction."""
    if headers is None:
        return None
    try:
        return headers.get("x-api-key")
    except AttributeError:
        return headers.get("X-API-Key") or headers.get("x-api-key")


def _presented_token(headers: Any) -> tuple[Optional[str], bool]:
    """Return ``(token, is_api_key)`` from the inbound headers.

    A ``tali_*`` value in either the bearer slot or ``X-API-Key`` is treated as
    an API key; anything else in the bearer slot is treated as a JWT.
    """
    bearer = _extract_bearer_token(headers)
    if bearer and bearer.startswith(_API_KEY_PREFIXES):
        return bearer, True
    x_api_key = _extract_api_key_header(headers)
    if x_api_key and x_api_key.startswith(_API_KEY_PREFIXES):
        return x_api_key, True
    return bearer, False


def _authenticate_api_key(token: str, db: Session) -> Principal:
    key = verify_api_key(db, token)
    if key is None:
        raise MCPAuthError("invalid, revoked, or expired API key")
    if key.organization_id is None:
        raise MCPAuthError("API key has no organization")
    return Principal(
        organization_id=int(key.organization_id),
        auth_kind="api_key",
        scopes=frozenset(key.scopes or []),
        user=None,
    )


def _authenticate_jwt(token: str, db: Session) -> Principal:
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
    return Principal(
        organization_id=int(user.organization_id),
        auth_kind="jwt",
        scopes=_FULL_READ_SCOPES,
        user=user,
    )


def authenticate_request(request: Any, db: Session) -> Principal:
    """Validate a Starlette Request's credentials and return a :class:`Principal`.

    Accepts either a ``tali_*`` API key (bearer or ``X-API-Key``) or a
    fastapi-users JWT bearer token. Raises ``MCPAuthError`` on any auth failure;
    callers catch and re-raise as a tool-friendly error.
    """
    if request is None:
        raise MCPAuthError("no HTTP request bound to MCP context")
    headers = getattr(request, "headers", None)
    token, is_api_key = _presented_token(headers)
    if not token:
        raise MCPAuthError("missing authorization header")
    if is_api_key:
        return _authenticate_api_key(token, db)
    return _authenticate_jwt(token, db)


def enforce_scope(principal: Principal, scope: str) -> None:
    """Gate a tool/resource on ``scope`` for API-key principals.

    JWT (session) principals are exempt — they carry implicit full read access.
    """
    if not principal.has_scope(scope):
        raise MCPScopeError(f"API key missing required scope: {scope}")
