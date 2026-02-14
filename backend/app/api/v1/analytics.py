"""Thin router wrapper for analytics domain.

TODO(2026-04-30): Remove this compatibility module after import migration.
"""

from ...domains.assessments_runtime.analytics_routes import router

__all__ = ["router"]
