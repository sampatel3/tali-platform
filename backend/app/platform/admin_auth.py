"""Single fail-closed authentication boundary for operator-only routes."""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from .config import settings


_admin_secret_header = APIKeyHeader(
    name="X-Admin-Secret",
    scheme_name="AdminSecret",
    description="Dedicated operator secret for admin-only routes.",
    auto_error=False,
)


def verify_admin_secret(provided: str | None) -> None:
    expected = str(getattr(settings, "ADMIN_SECRET", "") or "")
    candidate = str(provided or "")
    if not expected or not candidate or not hmac.compare_digest(candidate, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


def require_admin_secret(
    x_admin_secret: str | None = Security(_admin_secret_header),
) -> None:
    verify_admin_secret(x_admin_secret)


__all__ = ["require_admin_secret", "verify_admin_secret"]
