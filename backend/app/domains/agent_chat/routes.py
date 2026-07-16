"""HTTP routes for the role-agent chat.

  GET  /api/v1/agent-chat/conversations                       sidebar (active agents + badges)
  GET  /api/v1/agent-chat/conversations/{role_id}/timeline    merged chat + questions + decisions
  POST /api/v1/agent-chat/conversations/{role_id}/messages    send a message → run the agent turn
  POST /api/v1/agent-chat/conversations/{role_id}/read        mark the thread read

The conversation is keyed by ``role_id`` (one shared thread per role's
agent) and created lazily on first access. Questions and decisions are
*projected* into the timeline; they're still answered / approved through the
existing ``/agent`` + ``/agent-decisions`` endpoints.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...agent_chat.draft_tasks import (
    apply_prepared_draft_revision,
    approve_draft,
    generate_prepared_draft_revision,
    prepare_draft_revision,
)
from ...agent_chat.engine import persist_user_message
from ...agent_chat.proactive import maybe_post_helper_briefing
from ...agent_chat.service import (
    conversation_agent_working,
    ensure_conversation,
    list_agent_conversations,
    mark_read,
    post_agent_message,
)
from ...agent_chat.timeline import build_timeline, serialize_message
from ...deps import get_current_user
from ...domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ...models.organization import Organization
from ...models.agent_conversation import AgentConversation
from ...models.role import Role
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...services.role_change_audit import (
    add_role_change_event,
    capture_role_change_snapshot,
)
from ...services.role_concurrency import bump_role_version
from .route_support import (
    ApproveDraftRequest,
    BulkMessageRequest,
    ReviseDraftRequest,
    SendMessageRequest,
    agent_meta as _agent_meta,
    assert_draft_role_version as _assert_draft_role_version,
    draft_conflict as _draft_conflict,
    draft_review_card as _draft_review_card,
    require_org as _require_org,
    require_role as _require_role,
)

logger = logging.getLogger("taali.agent_chat.routes")

router = APIRouter(prefix="/agent-chat", tags=["agent-chat"])


@router.get("/conversations")
def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _require_org(current_user)
    return {"agents": list_agent_conversations(db, organization_id=org_id, user=current_user)}


@router.post("/bulk-message")
def bulk_message(
    body: BulkMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fan one message out to several roles' agents at once.

    Each selected role's agent runs the message in ITS OWN thread (separate
    turn, separate audit) via a background job that paces the turns
    sequentially per org. Validates org-ownership of every role up front and
    reports any it dropped, then enqueues — returns immediately; the replies
    land in each role's thread as the job drains.
    """
    org_id = _require_org(current_user)
    role_ids = list(dict.fromkeys(int(x) for x in body.role_ids))  # de-dupe, keep order
    # Lock every selected role first, in deterministic order, then lock its
    # conversation. The accepted Role revision and pending turn receipt below
    # therefore commit atomically, while overlapping bulk/single sends share a
    # consistent Role -> conversation lock order.
    owned_roles = {
        int(r.id): r
        for r in db.query(Role)
        .filter(
            Role.organization_id == org_id,
            Role.id.in_(role_ids),
            Role.deleted_at.is_(None),
        )
        .order_by(Role.id.asc())
        .with_for_update(of=Role)
        .all()
    }
    owned_ids = [rid for rid in role_ids if rid in owned_roles]
    if not owned_ids:
        raise HTTPException(status_code=400, detail="No valid roles selected")

    accepted_ids: set[int] = set()
    busy_ids: set[int] = set()
    accepted_role_versions: dict[str, int] = {}
    # Every request acquires conversation row locks in the same order. Two
    # overlapping bulk sends with reversed UI selection order therefore cannot
    # deadlock each other in PostgreSQL.
    for rid in sorted(owned_ids):
        role = owned_roles[rid]
        conversation = ensure_conversation(db, organization_id=org_id, role=role)
        conversation = (
            db.query(AgentConversation)
            .filter(AgentConversation.id == int(conversation.id))
            .with_for_update()
            .one()
        )
        if conversation_agent_working(db, conversation):
            busy_ids.add(rid)
            continue
        user_row = persist_user_message(
            db=db,
            conversation=conversation,
            user=current_user,
            user_message=body.message.strip(),
        )
        mark_read(db, conversation=conversation, user=current_user)
        conversation.turn_message_id = int(user_row.id)
        accepted_role_version = int(role.version or 1)
        conversation.turn_accepted_role_version = accepted_role_version
        conversation.turn_status = "pending"
        conversation.turn_next_attempt_at = None
        conversation.turn_lease_until = None
        conversation.turn_error = None
        accepted_ids.add(rid)
        accepted_role_versions[str(rid)] = accepted_role_version
    # Preserve recruiter-provided ordering in the response and worker pacing.
    accepted = [rid for rid in owned_ids if rid in accepted_ids]
    busy = [rid for rid in owned_ids if rid in busy_ids]
    db.commit()
    if not accepted:
        raise HTTPException(status_code=409, detail="All selected agents are already working")

    from ...tasks.agent_chat_tasks import bulk_agent_message

    dispatched = True
    try:
        # The text is already durable in each conversation. Empty marks the new
        # receipt-aware contract and prevents compatibility fallback from
        # appending it again on an ambiguous duplicate publish.
        bulk_agent_message.delay(
            org_id,
            int(current_user.id),
            accepted,
            "",
            accepted_role_versions,
        )
    except Exception:
        # Every per-role user message + pending receipt is already committed.
        # Beat recovers them individually, whether this publish failed before or
        # ambiguously after broker acceptance.
        dispatched = False
        logger.exception("bulk agent-chat publish failed; durable turns will recover")
    return {
        "requested": len(role_ids),
        "accepted": len(accepted),
        "skipped": [rid for rid in role_ids if rid not in owned_roles],
        "busy": busy,
        "dispatch_pending": not dispatched,
    }


@router.get("/conversations/{role_id}/timeline")
def get_timeline(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _require_org(current_user)
    role = _require_role(db, role_id, org_id)
    conversation = ensure_conversation(db, organization_id=org_id, role=role)

    # Make the working-check and user-message append one atomic claim. Without
    # this lock, two web replicas can both observe an idle conversation and
    # enqueue two paid/mutating agent turns over the same half-built history.
    # The lock is released by the commit immediately below; model work remains
    # asynchronous and does not hold a database connection hostage.
    conversation = (
        db.query(AgentConversation)
        .filter(AgentConversation.id == int(conversation.id))
        .with_for_update()
        .one()
    )
    # Speak first on a fresh or materially changed role without paying for a
    # model turn. The deterministic helper emits at most one suggested next
    # step and never changes recruiting state.
    try:
        # Helper generation is optional. Isolate any partial helper writes in a
        # savepoint so a failure cannot poison the outer transaction that owns
        # lazy conversation creation and the normal timeline read.
        with db.begin_nested():
            maybe_post_helper_briefing(db, conversation=conversation, role=role)
    except Exception:
        logger.exception(
            "optional helper briefing failed for role_id=%s; serving timeline",
            role.id,
        )
    timeline = build_timeline(db, conversation=conversation, role=role)
    working = conversation_agent_working(db, conversation)
    # A fetch is not an acknowledgement. The clients call POST /read only
    # after the selected thread has remained visibly open for a short dwell,
    # preventing auto-selection or background reads from consuming alerts.
    db.commit()
    return {
        "conversation_id": conversation.id,
        "role_id": role.id,
        "role_name": role.name,
        "agent": _agent_meta(role),
        "timeline": timeline,
        # Recomputed from persisted state, so the "agent is working…" indicator
        # survives navigation / an agent switch and resumes on return.
        "agent_working": working,
    }


@router.post("/conversations/{role_id}/messages")
def send_message(
    role_id: int,
    body: SendMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _require_org(current_user)
    # Acquire the Role lock before the conversation lock. The exact revision
    # accepted for this turn is persisted in the same transaction as the user
    # message and pending receipt, so post-commit edits cannot be silently
    # adopted by the asynchronous worker.
    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(org_id),
            Role.deleted_at.is_(None),
        )
        .with_for_update(of=Role)
        .one_or_none()
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    organization = (
        db.query(Organization).filter(Organization.id == org_id).first()
    )
    if organization is None:
        raise HTTPException(status_code=400, detail="Organization not found")
    conversation = ensure_conversation(db, organization_id=org_id, role=role)
    conversation = (
        db.query(AgentConversation)
        .filter(AgentConversation.id == int(conversation.id))
        .with_for_update()
        .one()
    )

    # One turn at a time PER agent: reject a second message while this agent is
    # still working on the previous one, rather than starting a second turn that
    # would replay a half-finished history and double-reply. This guard is
    # per-conversation, so you can still message OTHER agents concurrently (and
    # bulk-message fans out to many agents at once) — it only serialises a single
    # agent's own thread.
    if conversation_agent_working(db, conversation):
        raise HTTPException(
            status_code=409,
            detail="The agent is still working on your previous message — it'll reply in a moment.",
        )

    # Persist the recruiter's message synchronously and commit — it's durable the
    # instant they hit send, surviving navigation / an agent switch / a failed
    # turn. The slow, mutating model loop runs in a worker (run_agent_chat_turn);
    # the reply lands in the thread and the dock polls + notifies when it does.
    try:
        user_row = persist_user_message(
            db=db, conversation=conversation, user=current_user, user_message=body.message
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # The sender has read up to their own message; the agent's reply (posted by
    # the worker, later) then counts as unread → drives the reply notification.
    mark_read(db, conversation=conversation, user=current_user)
    user_payload = serialize_message(user_row)
    accepted_role_version = int(role.version or 1)
    conversation.turn_message_id = int(user_row.id)
    conversation.turn_accepted_role_version = accepted_role_version
    conversation.turn_status = "pending"
    conversation.turn_next_attempt_at = None
    conversation.turn_lease_until = None
    conversation.turn_error = None
    db.commit()

    # Build the response BEFORE enqueuing so it's identical under eager Celery
    # (tests) and prod: the POST returns the user message + "working", and the
    # reply is observed on the next timeline read.
    timeline = build_timeline(db, conversation=conversation, role=role)
    response = {
        "conversation_id": conversation.id,
        "role_id": role.id,
        "status": "accepted",
        "agent_working": True,
        "messages": [user_payload],
        "timeline": timeline,
        "agent": _agent_meta(role),
        "dispatch_pending": False,
    }

    from ...tasks.agent_chat_tasks import run_agent_chat_turn

    try:
        run_agent_chat_turn.delay(
            conversation_id=int(conversation.id),
            role_id=int(role.id),
            user_id=int(current_user.id),
            organization_id=int(org_id),
            turn_message_id=int(user_row.id),
            accepted_role_version=accepted_role_version,
        )
    except Exception:
        response["dispatch_pending"] = True
        logger.exception(
            "agent-chat publish failed/ambiguous conversation=%s; recovery will retry",
            conversation.id,
        )
    return response


@router.post("/conversations/{role_id}/draft-tasks/{task_id}/approve")
def approve_draft_task(
    role_id: int,
    task_id: int,
    body: ApproveDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve (activate) a generated draft from the chat. Narrates the outcome
    into the timeline so the recruiter sees the confirmation in-thread."""
    org_id = _require_org(current_user)
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    db.refresh(role)
    _assert_draft_role_version(db, role, body.expected_version)
    from_version = int(role.version or 1)
    before = capture_role_change_snapshot(role)
    result = approve_draft(db, role, task_id, user_id=int(current_user.id))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Approve failed")
    summary = result["summary"]
    try:
        to_version = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=before,
            action="role_draft_task_approved",
            actor_user_id=int(current_user.id),
            from_version=from_version,
            to_version=to_version,
            reason=f"Draft task {int(task_id)} approved from agent chat",
            allow_empty_changes=True,
        )
        conversation = ensure_conversation(db, organization_id=org_id, role=role)
        post_agent_message(
            db,
            conversation=conversation,
            text=f"Approved **{summary['name']}** — it's live and assignable now.",
        )
        timeline = build_timeline(db, conversation=conversation, role=role)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "ok": True,
        "role_id": role.id,
        "role_version": to_version,
        "summary": summary,
        "timeline": timeline,
    }


@router.post("/conversations/{role_id}/draft-tasks/{task_id}/revise")
def revise_draft_task(
    role_id: int,
    task_id: int,
    body: ReviseDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Structured-reject → revise: re-author the draft from the recruiter's
    multiple-choice feedback (one metered call) instead of deleting it, then
    re-present the revised draft as a fresh review card in the timeline."""
    org_id = _require_org(current_user)

    # Preflight authorization and stale-card validation are read-only. The
    # model call runs only after this transaction is closed, so it never holds
    # the shared Role row lock.
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
        lock_for_update=False,
    )
    _assert_draft_role_version(db, role, body.expected_version)
    api_key = str(getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip()
    prepared = prepare_draft_revision(
        db,
        role,
        task_id,
        answers=body.answers or {},
        note=body.note,
        api_key=api_key,
    )
    if not prepared.get("ok"):
        db.rollback()
        role = require_job_permission(
            db,
            current_user=current_user,
            role_id=role_id,
            permission=JobPermission.CONTROL_AGENT,
            lock_for_update=False,
        )
        conversation = ensure_conversation(db, organization_id=org_id, role=role)
        post_agent_message(
            db,
            conversation=conversation,
            text=f"I couldn't revise that draft — {prepared.get('error')} The original is unchanged.",
        )
        timeline = build_timeline(db, conversation=conversation, role=role)
        db.commit()
        return {
            "ok": False,
            "role_id": role.id,
            "role_version": int(role.version or 1),
            "error": prepared.get("error"),
            "errors": prepared.get("errors"),
            "timeline": timeline,
        }

    preparation = prepared["preparation"]
    db.rollback()
    generated = generate_prepared_draft_revision(preparation, api_key=api_key)
    if not generated.get("ok"):
        role = require_job_permission(
            db,
            current_user=current_user,
            role_id=role_id,
            permission=JobPermission.CONTROL_AGENT,
            lock_for_update=False,
        )
        conversation = ensure_conversation(db, organization_id=org_id, role=role)
        post_agent_message(
            db,
            conversation=conversation,
            text=f"I couldn't revise that draft — {generated.get('error')} The original is unchanged.",
        )
        timeline = build_timeline(db, conversation=conversation, role=role)
        db.commit()
        return {
            "ok": False,
            "role_id": role.id,
            "role_version": int(role.version or 1),
            "error": generated.get("error"),
            "errors": generated.get("errors"),
            "timeline": timeline,
        }

    # Re-authorize while holding the same lock as every shared-job mutation.
    # Membership removal or any intervening Role write wins over this prepared
    # model output.
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    db.refresh(role)
    _assert_draft_role_version(db, role, body.expected_version)
    from_version = int(role.version or 1)
    before = capture_role_change_snapshot(role)
    try:
        result = apply_prepared_draft_revision(
            db,
            role,
            preparation,
            spec=generated["spec"],
        )
        if result.get("conflict"):
            raise _draft_conflict(db, role)
        if not result.get("ok"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error") or "Revise failed",
            )

        summary = result["summary"]
        material = bool(result.get("material"))
        if material:
            to_version = bump_role_version(role)
            add_role_change_event(
                db,
                role=role,
                before=before,
                action="role_draft_task_revised",
                actor_user_id=int(current_user.id),
                from_version=from_version,
                to_version=to_version,
                reason=f"Draft task {int(task_id)} revised from agent chat",
                allow_empty_changes=True,
            )
            message = f"Revised **{summary['name']}** from your feedback — take another look."
        else:
            to_version = from_version
            message = f"**{summary['name']}** already matches that revision — nothing changed."

        conversation = ensure_conversation(db, organization_id=org_id, role=role)
        post_agent_message(
            db,
            conversation=conversation,
            text=message,
            actions=[_draft_review_card(role, summary)],
        )
        timeline = build_timeline(db, conversation=conversation, role=role)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "ok": True,
        "role_id": role.id,
        "role_version": to_version,
        "material": material,
        "summary": summary,
        "timeline": timeline,
    }


@router.post("/conversations/{role_id}/read")
def mark_conversation_read(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _require_org(current_user)
    role = _require_role(db, role_id, org_id)
    conversation = ensure_conversation(db, organization_id=org_id, role=role)
    mark_read(db, conversation=conversation, user=current_user)
    db.commit()
    return {"ok": True, "conversation_id": conversation.id}
