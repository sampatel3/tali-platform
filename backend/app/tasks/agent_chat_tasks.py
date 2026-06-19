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


@celery_app.task(name="app.tasks.agent_chat_tasks.run_agent_chat_turn")
def run_agent_chat_turn(
    conversation_id: int,
    role_id: int,
    user_id: int,
    organization_id: int,
) -> dict:
    """Run the agent's response for a single conversation turn.

    The web request has already persisted (and committed) the recruiter's
    message; this runs the slow, mutating model loop and posts the reply. Split
    out so the request returns immediately — the message is durable on send and
    the reply lands asynchronously, surfaced by the dock's poll + a notification.

    Always closes the turn with an assistant message: on an unexpected failure we
    roll back the partial turn and post a plain error reply, so the recruiter is
    never left looking at their own message with no response.
    """
    from ..agent_chat.engine import run_agent_response
    from ..agent_chat.service import post_agent_message
    from ..models.agent_conversation import AgentConversation
    from ..models.organization import Organization
    from ..models.role import Role
    from ..models.user import User
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.id == int(role_id)).first()
        user = db.query(User).filter(User.id == int(user_id)).first()
        org = (
            db.query(Organization)
            .filter(Organization.id == int(organization_id))
            .first()
        )
        conversation = (
            db.query(AgentConversation)
            .filter(AgentConversation.id == int(conversation_id))
            .first()
        )
        if role is None or user is None or org is None or conversation is None:
            return {"status": "missing", "role_id": role_id}

        try:
            run_agent_response(
                db=db, role=role, user=user, organization=org, conversation=conversation
            )
            db.commit()
            return {"status": "replied", "role_id": role_id}
        except Exception:
            db.rollback()
            logger.exception("run_agent_chat_turn failed for role_id=%s", role_id)
            # The user message is already committed by the web request — close the
            # turn with an error reply rather than leaving the recruiter in silence.
            conversation = (
                db.query(AgentConversation)
                .filter(AgentConversation.id == int(conversation_id))
                .first()
            )
            if conversation is not None:
                post_agent_message(
                    db,
                    conversation=conversation,
                    text="Sorry — I hit an error working on that. Please try again.",
                )
                from datetime import datetime, timezone

                conversation.last_message_at = datetime.now(timezone.utc)
                db.commit()
            return {"status": "error", "role_id": role_id}
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
