"""
Shared dependencies. Re-exports get_current_user from FastAPI-Users.
"""

from fastapi import Depends, HTTPException

from .domains.identity_access.users_fastapi import (
    current_active_user as get_current_user,
    current_active_user_optional as get_optional_current_user,
)
from .models.user import User


def require_org_owner(current_user: User = Depends(get_current_user)) -> User:
    """Gate privileged workspace and integration mutations to owners."""
    if getattr(current_user, "role", None) != "owner":
        raise HTTPException(
            status_code=403,
            detail="Only a workspace owner can do this",
        )
    return current_user


__all__ = ["get_current_user", "get_optional_current_user", "require_org_owner"]
