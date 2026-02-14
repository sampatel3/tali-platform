"""Thin router wrapper for tasks/repository domain.

TODO(2026-04-30): Remove this compatibility module after import migration.
"""

from ...domains.tasks_repository.routes import router

__all__ = ["router"]
