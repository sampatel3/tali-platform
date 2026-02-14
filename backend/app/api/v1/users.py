"""Thin router wrapper for identity/team domain.

TODO(2026-04-30): Remove this compatibility module after import migration.
"""

from ...domains.identity_access.user_routes import router

__all__ = ["router"]
