"""Celery tasks for the role-agent chat.

``report_rescreen_impact`` is the "feels instant" follow-up: after a
constraint edit triggers a re-screen, it waits for the role's re-score to
settle (bounded polling) and posts a proactive impact message back into the
conversation — qualified-pool delta + a one-word threshold recovery option.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .celery_app import celery_app

logger = logging.getLogger("taali.agent_chat")

# Bound the wait so a stuck/slow re-score can't poll forever. 12 × 30s ≈ 6 min,
# comfortably past a typical role re-screen; after that we report what we have.
_MAX_ATTEMPTS = 12
_POLL_SECONDS = 30
# Eight model rounds × a 120s Anthropic request timeout × one SDK retry has a
# 32-minute network ceiling. Give tool/DB work another 10 minutes, hard-stop a
# pathological turn at 45 minutes, and keep the durable lease five minutes
# beyond that hard stop so a healthy worker can never overlap Beat recovery.
_TURN_SOFT_LIMIT_SECONDS = 42 * 60
_TURN_HARD_LIMIT_SECONDS = 45 * 60
_TURN_LEASE = timedelta(minutes=50)
_TURN_DISPATCH_RETRY = timedelta(minutes=2)
_TURN_MAX_DISPATCH_RETRY = timedelta(minutes=30)


def _turn_dispatch_retry(attempt: int) -> timedelta:
    seconds = min(
        int(_TURN_DISPATCH_RETRY.total_seconds())
        * (2 ** min(max(0, int(attempt) - 1), 4)),
        int(_TURN_MAX_DISPATCH_RETRY.total_seconds()),
    )
    return timedelta(seconds=seconds)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


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


@celery_app.task(
    name="app.tasks.agent_chat_tasks.run_agent_chat_turn",
    soft_time_limit=_TURN_SOFT_LIMIT_SECONDS,
    time_limit=_TURN_HARD_LIMIT_SECONDS,
)
def run_agent_chat_turn(
    conversation_id: int,
    role_id: int,
    user_id: int,
    organization_id: int,
    turn_message_id: int | None = None,
    accepted_role_version: int | None = None,
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
    from ..models.agent_conversation import (
        AUTHOR_ROLE_ASSISTANT,
        AUTHOR_ROLE_USER,
        MESSAGE_KIND_ACTION,
        MESSAGE_KIND_CHAT,
        AgentConversation,
        AgentConversationMessage,
    )
    from ..models.organization import Organization
    from ..models.role import Role
    from ..models.user import User
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        # Claim the durable turn before model/tool work. This serialises an
        # ambiguous duplicate broker publish and gives a killed worker a bounded
        # lease that Beat can recover.
        conversation = (
            db.query(AgentConversation)
            .filter(AgentConversation.id == int(conversation_id))
            .with_for_update()
            .one_or_none()
        )
        if conversation is None:
            return {"status": "missing", "role_id": role_id}
        legacy_delivery = turn_message_id is None
        durable_role_version = (
            int(conversation.turn_accepted_role_version)
            if conversation.turn_accepted_role_version is not None
            else None
        )
        expected_message_id = int(turn_message_id or conversation.turn_message_id or 0)
        if legacy_delivery and expected_message_id <= 0:
            # A task published by the pre-receipt API has only the original
            # four bindings. Infer its last authored user row once so queued
            # work survives a worker-first rolling deploy; all new publishes
            # and Beat recovery carry an exact message id.
            legacy_message = (
                db.query(AgentConversationMessage)
                .filter(
                    AgentConversationMessage.conversation_id
                    == int(conversation.id),
                    AgentConversationMessage.organization_id
                    == int(organization_id),
                    AgentConversationMessage.role_id == int(role_id),
                    AgentConversationMessage.kind == MESSAGE_KIND_CHAT,
                    AgentConversationMessage.author_role == AUTHOR_ROLE_USER,
                    AgentConversationMessage.author_user_id == int(user_id),
                )
                .order_by(AgentConversationMessage.id.desc())
                .first()
            )
            expected_message_id = int(legacy_message.id) if legacy_message else 0
        if (
            turn_message_id is not None
            and conversation.turn_message_id is not None
            and int(conversation.turn_message_id) != int(turn_message_id)
        ):
            return {"status": "skipped", "reason": "superseded_turn", "role_id": role_id}
        lease = _as_utc(conversation.turn_lease_until)
        if conversation.turn_status == "running" and lease is not None and lease > _now():
            return {"status": "skipped", "reason": "turn_already_running", "role_id": role_id}
        if conversation.turn_status not in (None, "pending", "running"):
            return {"status": "skipped", "reason": "turn_already_closed", "role_id": role_id}
        role = db.query(Role).filter(Role.id == int(role_id)).one_or_none()
        user = db.query(User).filter(User.id == int(user_id)).one_or_none()
        org = (
            db.query(Organization)
            .filter(Organization.id == int(organization_id))
            .one_or_none()
        )
        turn_message = (
            db.query(AgentConversationMessage)
            .filter(
                AgentConversationMessage.id == expected_message_id,
                AgentConversationMessage.conversation_id == int(conversation.id),
                AgentConversationMessage.organization_id == int(organization_id),
                AgentConversationMessage.role_id == int(role_id),
                AgentConversationMessage.kind == MESSAGE_KIND_CHAT,
                AgentConversationMessage.author_role == AUTHOR_ROLE_USER,
                AgentConversationMessage.author_user_id == int(user_id),
            )
            .one_or_none()
            if expected_message_id > 0
            else None
        )
        context_matches = bool(
            role is not None
            and user is not None
            and org is not None
            and turn_message is not None
            and int(conversation.role_id) == int(role_id)
            and int(conversation.organization_id) == int(organization_id)
            and int(role.organization_id) == int(organization_id)
            and int(user.organization_id or 0) == int(organization_id)
        )
        if not context_matches:
            if legacy_delivery:
                # An old queued delivery can arrive after a newer recruiter's
                # turn owns the shared role conversation. It has no message id,
                # so fail closed without poisoning the newer pending receipt;
                # Beat will publish that receipt with exact bindings.
                return {
                    "status": "skipped",
                    "reason": "legacy_turn_context_mismatch",
                    "role_id": role_id,
                }
            # The delivery names the current turn but its security bindings do
            # not match the persisted author/message. That is not transient:
            # close it visibly instead of letting Beat republish it forever.
            post_agent_message(
                db,
                conversation=conversation,
                text=(
                    "I couldn't safely match that reply to its original request. "
                    "Please send the message again."
                ),
            )
            conversation.turn_status = "done"
            conversation.turn_lease_until = None
            conversation.turn_next_attempt_at = None
            conversation.turn_error = "turn_context_mismatch"
            db.commit()
            return {
                "status": "error",
                "reason": "turn_context_mismatch",
                "role_id": role_id,
            }
        if legacy_delivery:
            existing_reply = (
                db.query(AgentConversationMessage.id)
                .filter(
                    AgentConversationMessage.conversation_id
                    == int(conversation.id),
                    AgentConversationMessage.id > expected_message_id,
                    AgentConversationMessage.author_role == AUTHOR_ROLE_ASSISTANT,
                    AgentConversationMessage.kind.in_(
                        (MESSAGE_KIND_CHAT, MESSAGE_KIND_ACTION)
                    ),
                )
                .first()
            )
            if existing_reply is not None:
                # A pre-receipt worker may have committed its visible reply but
                # could not clear columns it did not know about. Adopt that
                # reply instead of charging for the same turn again.
                conversation.turn_message_id = expected_message_id
                conversation.turn_status = "done"
                conversation.turn_lease_until = None
                conversation.turn_next_attempt_at = None
                conversation.turn_error = None
                db.commit()
                return {
                    "status": "skipped",
                    "reason": "legacy_turn_already_replied",
                    "role_id": role_id,
                }
        conversation.turn_message_id = expected_message_id or conversation.turn_message_id
        conversation.turn_status = "running"
        conversation.turn_attempts = int(conversation.turn_attempts or 0) + 1
        claim_attempt = int(conversation.turn_attempts)
        conversation.turn_lease_until = _now() + _TURN_LEASE
        conversation.turn_next_attempt_at = None
        conversation.turn_error = None
        db.commit()

        # The claim commit releases the row lock. Reload every bound entity so
        # an admin deletion/org move in that narrow window cannot run a turn
        # with stale pre-commit ORM objects.
        conversation = db.get(AgentConversation, int(conversation_id))
        role = db.get(Role, int(role_id))
        user = db.get(User, int(user_id))
        org = db.get(Organization, int(organization_id))
        context_still_exists = bool(
            conversation is not None
            and role is not None
            and user is not None
            and org is not None
            and int(conversation.role_id) == int(role_id)
            and int(conversation.organization_id) == int(organization_id)
            and int(role.organization_id) == int(organization_id)
            and int(user.organization_id or 0) == int(organization_id)
        )
        if not context_still_exists:
            if conversation is not None:
                closed = (
                    db.query(AgentConversation)
                    .filter(
                        AgentConversation.id == int(conversation_id),
                        AgentConversation.turn_message_id == expected_message_id,
                        AgentConversation.turn_status == "running",
                        AgentConversation.turn_attempts == claim_attempt,
                    )
                    .update(
                        {
                            AgentConversation.turn_status: "done",
                            AgentConversation.turn_lease_until: None,
                            AgentConversation.turn_next_attempt_at: None,
                            AgentConversation.turn_error: "turn_context_missing",
                        },
                        synchronize_session=False,
                    )
                )
                if closed:
                    db.commit()
                else:
                    db.rollback()
            return {
                "status": "missing",
                "reason": "turn_context_missing",
                "role_id": role_id,
            }

        try:
            # New-format turns always trust the revision stored atomically with
            # the durable receipt, never a mutable broker argument. A NULL value
            # can only belong to a rolling-deploy/pre-column receipt and stays
            # fail-closed for mutations while read-only tools complete the turn.
            # Legacy deliveries retain their original compatibility fallback.
            effective_role_version = (
                (
                    int(durable_role_version)
                    if durable_role_version is not None
                    else (
                        int(accepted_role_version)
                        if accepted_role_version is not None
                        else int(role.version or 1)
                    )
                )
                if legacy_delivery
                else int(durable_role_version or 0)
            )
            run_agent_response(
                db=db,
                role=role,
                user=user,
                organization=org,
                conversation=conversation,
                accepted_role_version=effective_role_version,
            )
            closed = (
                db.query(AgentConversation)
                .filter(
                    AgentConversation.id == int(conversation_id),
                    AgentConversation.turn_message_id == expected_message_id,
                    AgentConversation.turn_status == "running",
                    AgentConversation.turn_attempts == claim_attempt,
                )
                .update(
                    {
                        AgentConversation.turn_status: "done",
                        AgentConversation.turn_lease_until: None,
                        AgentConversation.turn_next_attempt_at: None,
                        AgentConversation.turn_error: None,
                    },
                    synchronize_session=False,
                )
            )
            if not closed:
                # Recovery has already issued a newer owner generation. Roll
                # back this worker's staged reply/tool mutations so an expired
                # owner can never commit over the replacement turn.
                db.rollback()
                return {
                    "status": "skipped",
                    "reason": "turn_lease_lost",
                    "role_id": role_id,
                }
            db.commit()
            return {"status": "replied", "role_id": role_id}
        except Exception:
            db.rollback()
            logger.exception("run_agent_chat_turn failed for role_id=%s", role_id)
            # The user message is already committed by the web request — close the
            # turn with an error reply rather than leaving the recruiter in silence.
            closed = (
                db.query(AgentConversation)
                .filter(
                    AgentConversation.id == int(conversation_id),
                    AgentConversation.turn_message_id == expected_message_id,
                    AgentConversation.turn_status == "running",
                    AgentConversation.turn_attempts == claim_attempt,
                )
                .update(
                    {
                        AgentConversation.turn_status: "done",
                        AgentConversation.turn_lease_until: None,
                        AgentConversation.turn_next_attempt_at: None,
                        AgentConversation.turn_error: "turn_failed",
                    },
                    synchronize_session=False,
                )
            )
            if closed:
                conversation = db.get(AgentConversation, int(conversation_id))
                post_agent_message(
                    db,
                    conversation=conversation,
                    text="Sorry — I hit an error working on that. Please try again.",
                )
                conversation.last_message_at = datetime.now(timezone.utc)
                db.commit()
            else:
                db.rollback()
            return {"status": "error", "role_id": role_id}
    finally:
        db.close()


@celery_app.task(name="app.tasks.agent_chat_tasks.bulk_agent_message")
def bulk_agent_message(
    organization_id: int,
    user_id: int,
    role_ids: list[int],
    message: str,
    accepted_role_versions: dict[str, int] | None = None,
) -> dict:
    """Fan one recruiter message out to each selected role's agent.

    Runs each role's turn SEQUENTIALLY in its OWN conversation (so the audit
    stays per-role) and COMMITS after each, so a later failure never rolls back
    an earlier role's recorded turn. Sequential pacing also bounds concurrent
    Anthropic spend — the same reason bulk-approve drains per-org. Re-screens a
    turn might propose stay opt-in, so a bulk 'salary is now AED 30k' applies
    the constraint per role and asks before spending, role by role.
    """
    from ..models.agent_conversation import AgentConversation
    from ..models.user import User
    from ..platform.database import SessionLocal

    ok: list[int] = []
    failed: list[dict] = []
    with SessionLocal() as db:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if user is None or int(user.organization_id or 0) != int(organization_id):
            return {"status": "missing_org_or_user"}
        turns = {
            int(row.role_id): (
                int(row.id),
                int(row.turn_message_id) if row.turn_message_id is not None else None,
                (
                    int(row.turn_accepted_role_version)
                    if row.turn_accepted_role_version is not None
                    else None
                ),
            )
            for row in db.query(AgentConversation)
            .filter(
                AgentConversation.organization_id == int(organization_id),
                AgentConversation.role_id.in_([int(rid) for rid in role_ids]),
                AgentConversation.turn_status == "pending",
            )
            .all()
        }

    if not turns and (message or "").strip():
        # Compatibility for jobs published by the pre-receipt route during a
        # rolling deploy (and for the direct task API): those jobs carry the
        # text but have no pre-persisted turn. New route/recovery publishes use
        # an empty marker, so an ambiguous duplicate can never enter this path.
        from ..agent_chat.engine import run_agent_turn
        from ..agent_chat.service import ensure_conversation, get_owned_role
        from ..models.organization import Organization

        with SessionLocal() as db:
            org = db.get(Organization, int(organization_id))
            user = db.get(User, int(user_id))
            if org is None or user is None:
                return {"status": "missing_org_or_user"}
            for rid in role_ids:
                try:
                    role = get_owned_role(
                        db,
                        role_id=int(rid),
                        organization_id=int(organization_id),
                    )
                    if role is None:
                        failed.append({"role_id": int(rid), "error": "not_found"})
                        continue
                    conversation = ensure_conversation(
                        db, organization_id=int(organization_id), role=role
                    )
                    accepted_version = (
                        accepted_role_versions.get(str(int(rid)))
                        if accepted_role_versions is not None
                        else None
                    )
                    run_agent_turn(
                        db=db,
                        role=role,
                        user=user,
                        organization=org,
                        conversation=conversation,
                        user_message=message.strip(),
                        accepted_role_version=(
                            int(accepted_version)
                            if accepted_version is not None
                            else int(role.version or 1)
                        ),
                    )
                    db.commit()
                    ok.append(int(rid))
                except Exception as exc:  # noqa: BLE001 - isolate each role
                    db.rollback()
                    logger.exception("legacy bulk agent-chat turn failed role=%s", rid)
                    failed.append({"role_id": int(rid), "error": type(exc).__name__})
        return {"status": "done", "ok": len(ok), "failed": len(failed), "failures": failed}

    # Sequential direct task execution preserves the original bulk cost pacing;
    # each child independently claims its durable turn, so a duplicate bulk
    # delivery or Beat recovery cannot run the same paid turn concurrently.
    for rid in role_ids:
        turn = turns.get(int(rid))
        if turn is None:
            failed.append({"role_id": int(rid), "error": "not_pending"})
            continue
        try:
            accepted_version = (
                turn[2]
                if turn[2] is not None
                else (
                    accepted_role_versions.get(str(int(rid)))
                    if accepted_role_versions is not None
                    else None
                )
            )
            result = run_agent_chat_turn.run(
                conversation_id=turn[0],
                role_id=int(rid),
                user_id=int(user_id),
                organization_id=int(organization_id),
                turn_message_id=turn[1],
                accepted_role_version=(
                    int(accepted_version) if accepted_version is not None else None
                ),
            )
            if result.get("status") in ("replied", "skipped"):
                ok.append(int(rid))
            else:
                failed.append({"role_id": int(rid), "error": result.get("status")})
        except Exception as exc:  # noqa: BLE001 - another pending turn can continue
            logger.exception("bulk agent-chat turn failed role=%s", rid)
            failed.append({"role_id": int(rid), "error": type(exc).__name__})
    return {"status": "done", "ok": len(ok), "failed": len(failed), "failures": failed}


@celery_app.task(name="app.tasks.agent_chat_tasks.recover_agent_chat_turns")
def recover_agent_chat_turns(limit: int = 100) -> dict:
    """Re-publish pending turns and reclaim expired worker leases."""
    from ..models.agent_conversation import AgentConversation, AgentConversationMessage
    from ..platform.database import SessionLocal

    now = _now()
    with SessionLocal() as db:
        stale_ids = [
            int(row[0])
            for row in (
                db.query(AgentConversation.id)
                .filter(
                    AgentConversation.turn_status == "running",
                    AgentConversation.turn_lease_until <= now,
                )
                .order_by(AgentConversation.id.asc())
                .limit(max(1, int(limit)))
                .all()
            )
        ]
        stale_recovered = 0
        for conversation_id in stale_ids:
            # Recheck the lease/status in the UPDATE itself. A worker may have
            # finished after the scan while we waited for its row lock; an ORM
            # assignment based on the stale snapshot could otherwise overwrite
            # that freshly committed ``done`` state back to ``pending``.
            stale_recovered += (
                db.query(AgentConversation)
                .filter(
                    AgentConversation.id == conversation_id,
                    AgentConversation.turn_status == "running",
                    AgentConversation.turn_lease_until <= now,
                )
                .update(
                    {
                        AgentConversation.turn_status: "pending",
                        AgentConversation.turn_lease_until: None,
                        AgentConversation.turn_next_attempt_at: now,
                        AgentConversation.turn_error: "worker_interrupted",
                        # Fence the expired worker immediately, before a
                        # replacement delivery even reaches the broker.
                        AgentConversation.turn_attempts: (
                            AgentConversation.turn_attempts + 1
                        ),
                    },
                    synchronize_session=False,
                )
            )
        db.commit()

        rows = (
            db.query(AgentConversation, AgentConversationMessage.author_user_id)
            .join(
                AgentConversationMessage,
                AgentConversationMessage.id == AgentConversation.turn_message_id,
            )
            .filter(
                AgentConversation.turn_status == "pending",
                (
                    AgentConversation.turn_next_attempt_at.is_(None)
                    | (AgentConversation.turn_next_attempt_at <= now)
                ),
            )
            .order_by(AgentConversation.id.asc())
            .limit(max(1, int(limit)))
            .all()
        )
        payloads = []
        for conversation, author_user_id in rows:
            if author_user_id is None or conversation.turn_message_id is None:
                continue
            prior_attempts = int(conversation.turn_attempts or 0)
            dispatch_attempt = prior_attempts + 1
            next_dispatch_at = now + _turn_dispatch_retry(dispatch_attempt)
            claimed = (
                db.query(AgentConversation)
                .filter(
                    AgentConversation.id == int(conversation.id),
                    AgentConversation.turn_message_id
                    == int(conversation.turn_message_id),
                    AgentConversation.turn_status == "pending",
                    AgentConversation.turn_attempts == prior_attempts,
                    (
                        AgentConversation.turn_next_attempt_at.is_(None)
                        | (AgentConversation.turn_next_attempt_at <= now)
                    ),
                )
                .update(
                    {
                        AgentConversation.turn_next_attempt_at: next_dispatch_at,
                        AgentConversation.turn_attempts: dispatch_attempt,
                    },
                    synchronize_session=False,
                )
            )
            if claimed:
                payloads.append(
                    {
                        "conversation_id": int(conversation.id),
                        "role_id": int(conversation.role_id),
                        "user_id": int(author_user_id),
                        "organization_id": int(conversation.organization_id),
                        "turn_message_id": int(conversation.turn_message_id),
                        "accepted_role_version": (
                            int(conversation.turn_accepted_role_version)
                            if conversation.turn_accepted_role_version is not None
                            else None
                        ),
                    }
                )
        # Commit the bounded retry reservation before broker I/O. Concurrent
        # Beat pods and the following tick then cannot flood duplicate work.
        db.commit()

    kicked = publish_failed = 0
    for payload in payloads:
        try:
            # Re-publish the exact durable turn identity. Regrouping by role
            # would re-read whichever pending message currently owns that role
            # and could execute a newer recruiter's message under the old user.
            run_agent_chat_turn.delay(**payload)
            kicked += 1
        except Exception:
            publish_failed += 1
            logger.exception(
                "agent-chat recovery publish failed conversation=%s",
                payload["conversation_id"],
            )
    return {
        "scanned": len(payloads),
        "stale_recovered": stale_recovered,
        "kicked": kicked,
        "publish_failed": publish_failed,
    }
