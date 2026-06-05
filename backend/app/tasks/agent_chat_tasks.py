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

# Backstop poll for the manual batch-score completion message. The status
# endpoint posts the chat summary the moment the frontend polls it through to
# completion; this task is the belt-and-suspenders for "recruiter closed the
# tab" — it confirms completion straight from the job rows and posts then.
# 60 × 60s = 1h covers a large fan-out; on timeout we stop silently (a false
# "completed" is worse than letting the next status poll report it).
_MAX_BATCH_ATTEMPTS = 60
_BATCH_POLL_SECONDS = 60


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


@celery_app.task(name="app.tasks.agent_chat_tasks.report_batch_score_complete")
def report_batch_score_complete(
    role_id: int,
    organization_id: int,
    conversation_id: int | None,
    token: str,
    started_at_iso: str,
    total: int,
    attempt: int = 0,
) -> dict:
    """Backstop reporter for a manual batch-score: poll the job rows and, once
    every targeted application has reached a terminal state, post the role-chat
    completion summary. Idempotent with the status-poll path via the Redis
    claim in ``batch_report``; gives up silently if completion can't be
    confirmed within the window (the next status poll will report it)."""
    if not conversation_id:
        return {"status": "no_conversation"}

    from datetime import datetime, timezone

    from sqlalchemy import func

    from ..agent_chat import batch_report
    from ..models.agent_conversation import AgentConversation
    from ..models.cv_score_job import (
        CvScoreJob,
        SCORE_JOB_DONE,
        SCORE_JOB_ERROR,
        SCORE_JOB_PENDING,
        SCORE_JOB_RUNNING,
    )
    from ..models.role import Role
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        started_at = datetime.fromisoformat(started_at_iso)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        def _count(*filters) -> int:
            return int(
                db.query(func.count(CvScoreJob.id))
                .filter(CvScoreJob.role_id == int(role_id), *filters)
                .scalar()
                or 0
            )

        pre_out = _count(CvScoreJob.cache_hit == "pre_screen_filtered", CvScoreJob.finished_at >= started_at)
        scored = _count(
            CvScoreJob.status == SCORE_JOB_DONE,
            CvScoreJob.cache_hit != "pre_screen_filtered",
            CvScoreJob.finished_at >= started_at,
        )
        errors = _count(CvScoreJob.status == SCORE_JOB_ERROR, CvScoreJob.finished_at >= started_at)

        done = int(total) > 0 and (scored + errors + pre_out) >= int(total)
        if not done and attempt < _MAX_BATCH_ATTEMPTS:
            report_batch_score_complete.apply_async(
                kwargs={
                    "role_id": int(role_id),
                    "organization_id": int(organization_id),
                    "conversation_id": int(conversation_id),
                    "token": token,
                    "started_at_iso": started_at_iso,
                    "total": int(total),
                    "attempt": attempt + 1,
                },
                countdown=_BATCH_POLL_SECONDS,
            )
            return {"status": "waiting", "attempt": attempt, "scored": scored}
        if not done:
            # Timed out without confirmed completion — let the status poll handle it.
            return {"status": "timed_out", "attempt": attempt}

        role = db.query(Role).filter(Role.id == int(role_id)).first()
        convo = (
            db.query(AgentConversation)
            .filter(AgentConversation.id == int(conversation_id))
            .first()
        )
        if role is None or convo is None:
            return {"status": "missing"}
        msg = batch_report.post_completion(
            db,
            conversation=convo,
            role=role,
            kind=batch_report.KIND_BATCH_SCORE,
            counts={"total": int(total), "scored": scored, "errors": errors, "pre_screened_out": pre_out},
            token=token,
            status="completed",
        )
        if msg is not None:
            db.commit()
        return {"status": "posted" if msg is not None else "already_reported"}
    except Exception:
        db.rollback()
        logger.exception("report_batch_score_complete failed for role_id=%s", role_id)
        return {"status": "error"}
    finally:
        db.close()


@celery_app.task(name="app.tasks.agent_chat_tasks.bulk_agent_message")
def bulk_agent_message(
    organization_id: int,
    user_id: int,
    role_ids: list[int],
    message: str,
) -> dict:
    """Fan one recruiter message out to each selected role's agent.

    Runs each role's turn SEQUENTIALLY in its OWN conversation (so the audit
    stays per-role) and COMMITS after each, so a later failure never rolls back
    an earlier role's recorded turn. Sequential pacing also bounds concurrent
    Anthropic spend — the same reason bulk-approve drains per-org. Re-screens a
    turn might propose stay opt-in, so a bulk 'salary is now AED 30k' applies
    the constraint per role and asks before spending, role by role.
    """
    from ..agent_chat.engine import run_agent_turn
    from ..agent_chat.service import ensure_conversation, get_owned_role
    from ..models.organization import Organization
    from ..models.user import User
    from ..platform.database import SessionLocal

    db = SessionLocal()
    ok: list[int] = []
    failed: list[dict] = []
    try:
        org = db.query(Organization).filter(Organization.id == int(organization_id)).first()
        user = db.query(User).filter(User.id == int(user_id)).first()
        if org is None or user is None:
            return {"status": "missing_org_or_user"}
        text = (message or "").strip()
        for rid in role_ids:
            try:
                role = get_owned_role(db, role_id=int(rid), organization_id=int(organization_id))
                if role is None:
                    failed.append({"role_id": int(rid), "error": "not_found"})
                    continue
                conversation = ensure_conversation(
                    db, organization_id=int(organization_id), role=role
                )
                run_agent_turn(
                    db=db, role=role, user=user, organization=org,
                    conversation=conversation, user_message=text,
                )
                db.commit()
                ok.append(int(rid))
            except Exception as exc:  # noqa: BLE001 — one role's failure can't sink the batch
                db.rollback()
                logger.warning("bulk_agent_message failed for role %s: %s", rid, exc, exc_info=True)
                failed.append({"role_id": int(rid), "error": type(exc).__name__})
        return {"status": "done", "ok": len(ok), "failed": len(failed), "failures": failed}
    finally:
        db.close()
