"""Durable fan-out of one recruiter message to several role agents."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...agent_chat.engine import persist_user_message
from ...agent_chat.service import (
    conversation_agent_working,
    ensure_conversation,
    mark_read,
)
from ...deps import get_current_user
from ...models.agent_conversation import AgentConversation
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db
from .route_support import (
    BulkMessageRequest,
    require_org as _require_org,
)

router = APIRouter()
logger = logging.getLogger("taali.agent_chat.routes")

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
