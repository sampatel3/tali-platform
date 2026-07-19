"""Role-context snapshot and post-submission agent dispatch helpers."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ...models.assessment import Assessment
from ...models.role import ROLE_KIND_SISTER, Role


logger = logging.getLogger("app.components.assessments.service")


def load_submission_role_kind(
    db: Session,
    assessment: Assessment,
    *,
    role_id: int | None,
) -> str | None:
    """Snapshot dispatch context before submission detaches the ORM row."""

    if role_id is None:
        return None
    return (
        db.query(Role.role_kind)
        .filter(
            Role.id == role_id,
            Role.organization_id == int(assessment.organization_id),
            Role.deleted_at.is_(None),
        )
        .scalar()
    )


def wake_role_agent_after_assessment(assessment: Assessment) -> bool:
    """Best-effort, bounded wake-up for the assessment's role agent.

    The cohort task owns the canonical enabled/paused and concurrent-run guards,
    plus idempotent scoring/decision materialisation. This hook therefore does
    exactly one dispatch when a role is present and never changes the already
    committed submission outcome if the broker is unavailable.
    """

    role_id = getattr(assessment, "role_id", None)
    if role_id is None:
        return False
    try:
        if (
            str(getattr(assessment, "_submission_role_kind", "") or "")
            == ROLE_KIND_SISTER
        ):
            from ...tasks.sister_role_tasks import related_role_agent_cycle

            related_role_agent_cycle.delay(int(role_id))
            return True
        from ...tasks.agent_tasks import agent_cohort_tick_role

        agent_cohort_tick_role.delay(int(role_id), activation=False)
        return True
    except Exception:
        logger.exception(
            "Failed to enqueue post-assessment agent cycle assessment_id=%s role_id=%s",
            getattr(assessment, "id", None),
            role_id,
        )
        return False


__all__ = ["load_submission_role_kind", "wake_role_agent_after_assessment"]
