"""
Shared dependencies. Re-exports get_current_user from FastAPI-Users.
"""

from .api.v1.users_fastapi import current_active_user as get_current_user

__all__ = ["get_current_user"]
