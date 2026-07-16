"""Shared reset and dispatch lifecycle for related-role job-spec changes."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..models.role import Role
from .sister_role_service import ensure_sister_evaluations

logger = logging.getLogger("taali.related_role_specs")


def reset_related_role_spec_evaluations(
    db: Session, role: Role
) -> dict[str, int | float]:
    """Archive scores, rebuild the roster, and report the fresh scoring scope."""
    counts = ensure_sister_evaluations(db, role, reset_existing=True)
    return {
        "count": int(counts.get("pending") or 0),
        "est_cost_usd": 0.0,
    }


def dispatch_related_role_spec_scoring(role: Role) -> None:
    """Best-effort scoring kick; Beat recovers the committed pending rows."""
    from ..tasks.sister_role_tasks import score_sister_role

    try:
        score_sister_role.apply_async(args=[role.id], queue="scoring")
    except Exception as exc:  # pragma: no cover - durable pending rows recover
        logger.error(
            "Related-role spec scoring kick unavailable role_id=%s "
            "error_code=queue_unavailable error_type=%s",
            role.id,
            type(exc).__name__,
        )
