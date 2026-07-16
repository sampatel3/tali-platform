"""Single fail-closed authentication boundary for operator-only routes."""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from .config import settings


def verify_admin_secret(provided: str | None) -> None:
    expected = str(getattr(settings, "ADMIN_SECRET", "") or "")
    candidate = str(provided or "")
    if not expected or not candidate or not hmac.compare_digest(candidate, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


def require_admin_secret(
    x_admin_secret: str | None = Header(default=None, alias="X-Admin-Secret"),
) -> None:
    verify_admin_secret(x_admin_secret)


__all__ = ["require_admin_secret", "verify_admin_secret"]
