"""Thin router wrapper for role/application assessment domain.

TODO(2026-04-30): Remove this compatibility module after import migration.
"""

from ...domains.assessments_runtime.roles_routes import router

__all__ = ["router"]
