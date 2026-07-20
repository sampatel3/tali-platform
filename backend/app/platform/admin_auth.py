"""Fail-closed authentication boundary for operator-only routes."""

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


def require_admin_secret(
    x_admin_secret: str | None = Security(_admin_secret_header),
) -> None:
    """Require the dedicated admin secret without falling back to JWT keys."""
    expected = str(getattr(settings, "ADMIN_SECRET", "") or "").strip()
    provided = str(x_admin_secret or "").strip()
    matches = hmac.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    )
    if not expected or not matches:
        raise HTTPException(status_code=403, detail="Forbidden")


__all__ = ["require_admin_secret"]
