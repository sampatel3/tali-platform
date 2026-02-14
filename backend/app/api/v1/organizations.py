"""Thin router wrapper for identity/organization domain.

TODO(2026-04-30): Remove this compatibility module after import migration.
"""

from ...domains.identity_access.organization_routes import router

__all__ = ["router"]
