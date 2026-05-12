"""Graph writeback pipeline — Phase 6 of the multi-agent upgrade.

When a recruiter submits a teach/override with ``graph_write_hints``,
this package validates each hint, classifies its sensitivity, and
either:
  - auto-commits low-risk hints (HAS_SKILL, WORKED_AT, ...)
  - queues medium-risk hints for co-sign (SIMILAR_TO, HIGH_YIELD, ...)
  - blocks high-risk hints (anything touching protected attributes).

Failures here NEVER roll back the underlying ``decision_feedback`` row
— Postgres is the source of truth, the graph is a derived view.
"""

from .contracts import GraphWriteHint, ValidationResult, WritebackReport
from .pipeline import write_back_from_feedback
from .sensitivity import classify_hint, load_blocklist

__all__ = [
    "GraphWriteHint",
    "ValidationResult",
    "WritebackReport",
    "classify_hint",
    "load_blocklist",
    "write_back_from_feedback",
]
