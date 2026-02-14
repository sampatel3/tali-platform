"""Thin router wrapper for candidates/documents domain.

TODO(2026-04-30): Remove this compatibility module after import migration.
"""

from ...domains.candidates_documents.routes import router

__all__ = ["router"]
