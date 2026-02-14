"""Thin router wrapper for assessments runtime domain.

TODO(2026-04-30): Remove this compatibility module after import migration.
"""

from ...domains.assessments_runtime.routes import router

__all__ = ["router"]
