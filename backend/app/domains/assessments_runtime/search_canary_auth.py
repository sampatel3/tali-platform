"""Least-privilege authentication for the production search canary.

The canary key is not a recruiter session token. It has one internal scope and
is accepted only by ``GET /api/v1/applications`` for two exact, read-only
requests: fixture inventory and the canonical grounded search.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ...deps import get_optional_current_user
from ...models.api_key import (
    KEY_PREFIX_LIVE,
    ApiKey,
    SCOPE_INTERNAL_SEARCH_CANARY_READ,
)
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db
from ...services.api_key_service import hash_token


SEARCH_CANARY_API_KEY_NAME = "__TAALI_SEARCH_CANARY_V1_READ_ONLY__"
SEARCH_CANARY_ROLE_NAME = "__TAALI_SEARCH_CANARY_V1_DO_NOT_EDIT__"
SEARCH_CANARY_QUERY = "candidates based in UAE with Python and PostgreSQL"

_ALLOWED_QUERY_KEYS = frozenset(
    {
        "role_id",
        "application_outcome",
        "assessment_status",
        "nl_query",
        "view",
        "rerank",
        "provider_mode",
        "include_stage_counts",
        "include_cv_text",
        "limit",
        "offset",
    }
)


@dataclass(frozen=True)
class SearchCanaryPrincipal:
    """Minimal principal shape consumed by the applications list route."""

    organization_id: int
    api_key_id: int
    id: int = 0
    role: str = "service"


def _deny(status_code: int, message: str) -> None:
    raise HTTPException(status_code=status_code, detail=message)


def _bearer_token(request: Request) -> str | None:
    raw = (request.headers.get("authorization") or "").strip()
    if not raw:
        return None
    parts = raw.split()
    if len(parts) != 2 or parts[0].casefold() != "bearer":
        return None
    return parts[1]


def _is_live_key(key: ApiKey) -> bool:
    if key.revoked_at is not None:
        return False
    expires_at = key.expires_at
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def _require_exact_request(request: Request, *, role_id: int) -> None:
    params = request.query_params
    if set(params.keys()) - _ALLOWED_QUERY_KEYS:
        _deny(403, "Search canary key is not valid for this request")
    if any(len(params.getlist(key)) != 1 for key in params.keys()):
        _deny(403, "Search canary key is not valid for this request")

    exact_values = {
        "role_id": str(role_id),
        "application_outcome": "all",
        "view": "list",
        "rerank": "false",
        "provider_mode": "forbid",
        "include_stage_counts": "false",
        "include_cv_text": "false",
        "limit": "50",
        "offset": "0",
    }
    if any(params.get(key) != value for key, value in exact_values.items()):
        _deny(403, "Search canary key is not valid for this request")

    if "nl_query" in params:
        if (
            params.get("nl_query") != SEARCH_CANARY_QUERY
            or params.get("assessment_status") != "completed"
        ):
            _deny(403, "Search canary key is not valid for this request")
    elif "assessment_status" in params:
        _deny(403, "Search canary key is not valid for this request")


async def get_applications_search_principal(
    request: Request,
    db: Session = Depends(get_db),
    regular_user: User | None = Depends(get_optional_current_user),
) -> User | SearchCanaryPrincipal:
    """Authenticate a recruiter session or the exact read-only canary key."""

    if regular_user is not None:
        return regular_user

    token = _bearer_token(request)
    if not token or not token.startswith(KEY_PREFIX_LIVE):
        _deny(401, "Not authenticated")
    key = (
        db.query(ApiKey)
        .filter(ApiKey.hashed_secret == hash_token(token))
        .first()
    )
    if (
        key is None
        or not _is_live_key(key)
        or set(key.scopes or []) != {SCOPE_INTERNAL_SEARCH_CANARY_READ}
        or key.name != SEARCH_CANARY_API_KEY_NAME
    ):
        _deny(401, "Not authenticated")

    try:
        role_id = int(request.query_params.get("role_id") or 0)
    except (TypeError, ValueError):
        _deny(403, "Search canary key is not valid for this request")
    role_exists = (
        db.query(Role.id)
        .filter(
            Role.id == role_id,
            Role.organization_id == key.organization_id,
            Role.name == SEARCH_CANARY_ROLE_NAME,
        )
        .first()
        is not None
    )
    if not role_exists:
        _deny(403, "Search canary key is not valid for this request")
    _require_exact_request(request, role_id=role_id)
    return SearchCanaryPrincipal(
        organization_id=int(key.organization_id),
        api_key_id=int(key.id),
    )


__all__ = [
    "SEARCH_CANARY_API_KEY_NAME",
    "SEARCH_CANARY_QUERY",
    "SEARCH_CANARY_ROLE_NAME",
    "SearchCanaryPrincipal",
    "get_applications_search_principal",
]
