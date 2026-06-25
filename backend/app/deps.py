"""
Shared dependencies. Re-exports get_current_user from FastAPI-Users + RBAC.
"""

from fastapi import Depends, HTTPException, status

from .domains.identity_access.users_fastapi import (
    current_active_user as get_current_user,
    current_active_user_optional as get_optional_current_user,
)
from .models.user import User

__all__ = ["get_current_user", "get_optional_current_user", "require_role"]


def require_role(*allowed_roles: str):
    """Dependency factory requiring the current user's role to be one of
    ``allowed_roles`` (else 403). With no roles given, any authenticated user
    passes. Roles default to 'admin' (migration 123) so pre-RBAC users are
    unaffected; RBAC is rolled out write-route by write-route as endpoints opt in.
    """

    def _dep(current_user: User = Depends(get_current_user)) -> User:
        if allowed_roles and getattr(current_user, "role", None) not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your role does not permit this action",
            )
        return current_user

    return _dep
