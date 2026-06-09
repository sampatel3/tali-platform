"""FastAPI dependencies for API-key (machine-to-machine) auth.

``get_api_principal`` resolves a ``tali_*`` key from the ``Authorization:
Bearer`` header or ``X-API-Key`` to its ApiKey row (which carries
``organization_id`` + ``scopes``). Public routes depend on it and reuse the
same org-scoped queries as the JWT surface, so tenant isolation comes for
free. ``require_scope`` gates writes.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Security
from fastapi.security import (
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from sqlalchemy.orm import Session

from ...models.api_key import ApiKey, KEY_PREFIX_LIVE, KEY_PREFIX_TEST
from ...platform.database import get_db
from ...services.api_key_service import verify_api_key

# auto_error=False so a missing header falls through to our combined check
# instead of either scheme 401-ing on its own before we've tried the other.
_bearer_scheme = HTTPBearer(
    auto_error=False, description="Taali API key as a Bearer token"
)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _extract_token(
    creds: Optional[HTTPAuthorizationCredentials],
    x_api_key: Optional[str],
) -> Optional[str]:
    if creds is not None and creds.scheme.lower() == "bearer" and creds.credentials:
        return creds.credentials
    if x_api_key:
        return x_api_key
    return None


def get_api_principal(
    creds: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
    x_api_key: Optional[str] = Security(_api_key_header),
    db: Session = Depends(get_db),
) -> ApiKey:
    token = _extract_token(creds, x_api_key)
    if not token or not token.startswith((KEY_PREFIX_LIVE, KEY_PREFIX_TEST)):
        raise HTTPException(status_code=401, detail="Missing or malformed API key")
    key = verify_api_key(db, token)
    if key is None:
        raise HTTPException(
            status_code=401, detail="Invalid, revoked, or expired API key"
        )
    return key


def require_scope(scope: str):
    """Dependency factory: 403 unless the key carries ``scope``."""

    def _dep(principal: ApiKey = Depends(get_api_principal)) -> ApiKey:
        if scope not in (principal.scopes or []):
            raise HTTPException(
                status_code=403,
                detail=f"API key missing required scope: {scope}",
            )
        return principal

    return _dep
