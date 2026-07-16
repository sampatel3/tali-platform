"""Stable operator-facing boundary for Anthropic reconciliation failures."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .anthropic_reconciliation_service import reconcile_recent

logger = logging.getLogger(__name__)


def reconcile_recent_public(db: Session, *, days: int) -> dict:
    """Run reconciliation without exposing provider/SDK exception text."""

    try:
        summary = reconcile_recent(db, days=int(days))
    except Exception:
        logger.exception("Unexpected Anthropic reconciliation failure days=%s", days)
        return {
            "ok": False,
            "days": int(days),
            "error_code": "reconciliation_failed",
            "message": "Usage reconciliation could not complete. Retry later.",
        }
    if not isinstance(summary, dict):
        logger.error("Anthropic reconciliation returned an invalid summary")
        return {
            "ok": False,
            "days": int(days),
            "error_code": "reconciliation_invalid_result",
            "message": "Usage reconciliation could not complete. Retry later.",
        }
    if summary.get("error"):
        code = str(summary.get("error_code") or summary.get("error") or "reconciliation_failed")
        if code not in {"anthropic_usage_fetch_failed", "reconciliation_commit_failed"}:
            code = "reconciliation_failed"
        logger.error("Anthropic reconciliation did not complete error_code=%s", code)
        response = {
            "ok": False,
            "days": int(days),
            "error_code": code,
            "message": "Usage reconciliation could not complete. Retry later.",
        }
        if isinstance(summary.get("rows_attempted"), int):
            response["rows_attempted"] = summary["rows_attempted"]
        return response
    return {"ok": True, "days": int(days), **summary}


__all__ = ["reconcile_recent_public"]
