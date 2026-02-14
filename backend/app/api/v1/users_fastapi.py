"""Thin wrapper that re-exports FastAPI Users setup from identity domain.

TODO(2026-04-30): Remove this compatibility module after import migration.
"""

from ...domains.identity_access.users_fastapi import (
    UserRead,
    UserCreate,
    UserUpdate,
    auth_backend,
    fastapi_users,
    current_active_user,
)

__all__ = [
    "UserRead",
    "UserCreate",
    "UserUpdate",
    "auth_backend",
    "fastapi_users",
    "current_active_user",
]
