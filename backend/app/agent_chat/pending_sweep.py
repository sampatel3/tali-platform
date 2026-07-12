"""Toggle-on sweep offer for pending pre-screen reject cards.

Flipping ``auto_reject`` / ``auto_reject_pre_screen`` ON only changes how
FUTURE pre-screen failures are handled — cards already sitting in the
Decision Hub stay pending. They were surfaced under the old policy, may be
days old, and approving them writes to Workable, so the toggle must not
silently execute that backlog. Instead the agent posts a confirm card into
the role's chat: one explicit click applies the new policy to the existing
queue (through the normal serialized approve path), or the recruiter keeps
the cards for manual review.

The offer card carries no decision ids — the apply endpoint re-queries the
role's CURRENT pending cards at click time, so a stale offer can never
approve rows that have since been actioned, retracted, or re-carded.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models.agent_conversation import (
    AUTHOR_ROLE_ASSISTANT,
    AgentConversation,
    AgentConversationMessage,
    MESSAGE_KIND_ACTION,
)
from ..models.agent_decision import AgentDecision
from ..models.role import Role
from .service import ensure_conversation, post_agent_message

SWEEP_CARD_TYPE = "pending_reject_sweep"

SWEEP_STATUS_OFFERED = "offered"
SWEEP_STATUS_APPLIED = "applied"
SWEEP_STATUS_DISMISSED = "dismissed"


def pending_pre_screen_reject_ids(db: Session, role: Role) -> list[int]:
    """Ids of this role's pending ``skip_assessment_reject`` cards, oldest first."""
    rows = (
        db.query(AgentDecision.id)
        .filter(
            AgentDecision.organization_id == int(role.organization_id),
            AgentDecision.role_id == int(role.id),
            AgentDecision.decision_type == "skip_assessment_reject",
            AgentDecision.status == "pending",
        )
        .order_by(AgentDecision.id.asc())
        .all()
    )
    return [int(r[0]) for r in rows]


def _sweep_card(msg: AgentConversationMessage) -> dict[str, Any] | None:
    for card in msg.actions or []:
        if isinstance(card, dict) and card.get("type") == SWEEP_CARD_TYPE:
            return card
    return None


def find_open_sweep_offer(
    db: Session, conversation: AgentConversation
) -> AgentConversationMessage | None:
    """The newest still-unresolved sweep offer in this thread, if any."""
    msgs = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == int(conversation.id),
            AgentConversationMessage.author_role == AUTHOR_ROLE_ASSISTANT,
            AgentConversationMessage.kind == MESSAGE_KIND_ACTION,
        )
        .order_by(AgentConversationMessage.id.desc())
        .all()
    )
    for msg in msgs:
        card = _sweep_card(msg)
        if card is not None and card.get("status") == SWEEP_STATUS_OFFERED:
            return msg
    return None


def resolve_sweep_offer(
    msg: AgentConversationMessage, *, status: str, applied_count: int | None = None
) -> None:
    """Mark the offer card resolved in place so the buttons collapse to an
    outcome line on the next timeline read. Caller commits."""
    actions = list(msg.actions or [])
    for i, card in enumerate(actions):
        if isinstance(card, dict) and card.get("type") == SWEEP_CARD_TYPE:
            updated = dict(card)
            updated["status"] = status
            if applied_count is not None:
                updated["applied_count"] = int(applied_count)
            actions[i] = updated
    msg.actions = actions
    flag_modified(msg, "actions")


def offer_pending_reject_sweep(db: Session, *, role: Role) -> bool:
    """Post the confirm card if this role has pending pre-screen reject cards
    and no unresolved offer already in the thread. Flushes; caller commits.
    Returns True when a new offer was posted."""
    pending = pending_pre_screen_reject_ids(db, role)
    if not pending:
        return False
    conversation = ensure_conversation(
        db, organization_id=int(role.organization_id), role=role
    )
    if find_open_sweep_offer(db, conversation) is not None:
        return False
    n = len(pending)
    post_agent_message(
        db,
        conversation=conversation,
        text=(
            f"Auto-reject for pre-screen fails is now on — it applies to new "
            f"candidates from here. Heads-up: {n} pre-screen reject"
            f"{'s were' if n != 1 else ' was'} already waiting in your review "
            f"queue when you turned it on. Want me to apply it to "
            f"{'them' if n != 1 else 'that one'} too? Anything Workable "
            f"refuses comes straight back to the queue."
        ),
        actions=[
            {
                "type": SWEEP_CARD_TYPE,
                "role_id": int(role.id),
                "pending_count": n,
                "status": SWEEP_STATUS_OFFERED,
            }
        ],
    )
    return True


__all__ = [
    "SWEEP_CARD_TYPE",
    "SWEEP_STATUS_APPLIED",
    "SWEEP_STATUS_DISMISSED",
    "SWEEP_STATUS_OFFERED",
    "find_open_sweep_offer",
    "offer_pending_reject_sweep",
    "pending_pre_screen_reject_ids",
    "resolve_sweep_offer",
]
