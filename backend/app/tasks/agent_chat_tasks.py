"""Celery tasks for the role-agent chat.

``report_rescreen_impact`` is the "feels instant" follow-up: after a
constraint edit triggers a re-screen, it waits for the role's re-score to
settle (bounded polling) and posts a proactive impact message back into the
conversation — qualified-pool delta + a one-word threshold recovery option.
"""

from __future__ import annotations

import logging

from .celery_app import celery_app

logger = logging.getLogger("taali.agent_chat")

# Bound the wait so a stuck/slow re-score can't poll forever. 12 × 30s ≈ 6 min,
# comfortably past a typical role re-screen; after that we report what we have.
_MAX_ATTEMPTS = 12
_POLL_SECONDS = 30


@celery_app.task(name="app.tasks.agent_chat_tasks.report_rescreen_impact")
def report_rescreen_impact(
    conversation_id: int,
    role_id: int,
    baseline_qualified: int,
    attempt: int = 0,
) -> dict:
    from ..agent_chat.rescreen_report import count_inflight_score_jobs, post_rescreen_impact
    from ..models.agent_conversation import AgentConversation
    from ..models.role import Role
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == int(role_id)).first()
        conversation = (
            db.query(AgentConversation)
            .filter(AgentConversation.id == int(conversation_id))
            .first()
        )
        if role is None or conversation is None:
            return {"status": "missing", "role_id": role_id, "conversation_id": conversation_id}

        in_flight = count_inflight_score_jobs(db, int(role_id))
        if in_flight > 0 and attempt < _MAX_ATTEMPTS:
            # Still re-scoring — check back shortly. The attempt cap also bounds
            # recursion under eager execution (tests).
            report_rescreen_impact.apply_async(
                kwargs={
                    "conversation_id": int(conversation_id),
                    "role_id": int(role_id),
                    "baseline_qualified": int(baseline_qualified),
                    "attempt": attempt + 1,
                },
                countdown=_POLL_SECONDS,
            )
            return {"status": "waiting", "in_flight": in_flight, "attempt": attempt}

        post_rescreen_impact(
            db,
            conversation=conversation,
            role=role,
            baseline_qualified=int(baseline_qualified),
        )
        db.commit()
        return {"status": "posted", "role_id": role_id, "timed_out": in_flight > 0}
    except Exception:
        db.rollback()
        logger.exception("report_rescreen_impact failed for role_id=%s", role_id)
        return {"status": "error", "role_id": role_id}
    finally:
        db.close()
