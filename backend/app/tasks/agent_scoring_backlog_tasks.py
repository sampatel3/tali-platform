"""Bounded database work for the autonomous scoring-backlog Celery task."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import exists

from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..platform.database import SessionLocal


logger = logging.getLogger("app.tasks.agent_tasks")

SCORING_BACKLOG_PER_ROLE_CAP = 50
SCORING_BACKLOG_ROLE_CAP = 100


def run_agent_scoring_backlog_sweep(
    *,
    per_role_limit: int,
    role_limit: int,
    enqueue_scoring: Callable[..., int],
) -> dict[str, Any]:
    """Drain scoring backlogs without paying for an agent reasoning cycle."""

    per_role_limit = max(1, min(int(per_role_limit), SCORING_BACKLOG_PER_ROLE_CAP))
    role_limit = max(1, min(int(role_limit), SCORING_BACKLOG_ROLE_CAP))
    db = SessionLocal()
    touched = 0
    processed_roles = 0
    errors = 0
    try:
        has_backlog = exists().where(
            CandidateApplication.role_id == Role.id,
            CandidateApplication.organization_id == Role.organization_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            CandidateApplication.cv_match_score.is_(None),
            CandidateApplication.cv_text.isnot(None),
            CandidateApplication.cv_text != "",
        )
        roles = (
            db.query(Role)
            .filter(
                Role.agentic_mode_enabled.is_(True),
                Role.deleted_at.is_(None),
                Role.agent_paused_at.is_(None),
                has_backlog,
            )
            .order_by(Role.id.asc())
            .limit(role_limit)
            .all()
        )
        for role in roles:
            processed_roles += 1
            try:
                touched += enqueue_scoring(
                    db,
                    role=role,
                    limit=per_role_limit,
                    strict=False,
                )
            except Exception:
                errors += 1
                db.rollback()
                logger.exception(
                    "agent scoring backlog sweep failed role_id=%s", role.id
                )
        return {
            "status": "ok" if not errors else "partial",
            "roles": processed_roles,
            "enqueued": touched,
            "errors": errors,
            "per_role_limit": per_role_limit,
        }
    finally:
        db.close()


__all__ = [
    "SCORING_BACKLOG_PER_ROLE_CAP",
    "SCORING_BACKLOG_ROLE_CAP",
    "run_agent_scoring_backlog_sweep",
]
