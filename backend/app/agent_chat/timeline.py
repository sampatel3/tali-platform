"""Unified timeline for a role-agent conversation.

Merges the three things the recruiter sees from a role's agent into one
chronological feed — the heart of "combine chat with the HITL decision feed":

  * chat messages (``AgentConversationMessage``, visible kinds only),
  * the agent's open questions (``AgentNeedsInput``),
  * the agent's queued decisions (``AgentDecision``).

Each item carries a ``kind`` discriminator so the UI renders a chat bubble,
a question card, or a decision card. Existing endpoints still answer the
questions and approve/override the decisions — this module only *reads* and
projects them into the conversation, it never duplicates that state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models.agent_conversation import (
    AgentConversation,
    AgentConversationMessage,
    MESSAGE_KIND_ACTION,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_EVENT,
    MESSAGE_KIND_PROACTIVE,
)
from ..services.reasoning_text import humanize_reasoning
from ..models.agent_decision import AgentDecision
from ..models.agent_needs_input import AgentNeedsInput
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from .recruiter_inputs import recruiter_input_contract


# How far back to surface already-resolved questions/decisions so the thread
# shows recent history without dragging the whole audit log in.
_RESOLVED_WINDOW_DAYS = 14
# Cap on decision cards in one timeline payload (the bulk queue can be large;
# the UI paginates the rest from the decisions endpoint).
_MAX_DECISIONS = 60

_VISIBLE_MESSAGE_KINDS = (
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_ACTION,
    MESSAGE_KIND_PROACTIVE,
    MESSAGE_KIND_EVENT,
)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def serialize_message(m: AgentConversationMessage) -> dict[str, Any]:
    return {
        "kind": "message",
        "id": f"msg-{m.id}",
        "message_id": int(m.id),
        "author": "agent" if m.author_role == "assistant" else "recruiter",
        "author_user_id": m.author_user_id,
        "text": m.text or "",
        "actions": m.actions or [],
        "message_kind": m.kind,
        "created_at": _iso(m.created_at),
    }


def serialize_needs_input(
    n: AgentNeedsInput, *, role_version: int | None = None
) -> dict[str, Any]:
    if n.resolved_at is not None:
        status = "answered"
    elif n.dismissed_at is not None:
        status = "dismissed"
    else:
        status = "open"
    contract = recruiter_input_contract(n)
    return {
        "kind": "needs_input",
        "id": f"needs-{n.id}",
        "needs_input_id": int(n.id),
        "role_version": role_version,
        "question_kind": n.kind,
        "prompt": n.prompt,
        "options": n.options or None,
        "response_schema": n.response_schema or None,
        "input_mode": contract["input_mode"],
        "can_answer": contract["can_answer"],
        "can_dismiss": contract["can_dismiss"],
        "rationale": n.rationale,
        "status": status,
        "response": n.response,
        "created_at": _iso(n.created_at),
        "resolved_at": _iso(n.resolved_at),
    }


def serialize_decision(
    d: AgentDecision, *, candidate_name: str | None, score: float | None
) -> dict[str, Any]:
    return {
        "kind": "decision",
        "id": f"decision-{d.id}",
        "decision_id": int(d.id),
        "application_id": d.application_id,
        "decision_type": d.decision_type,
        "recommendation": d.recommendation,
        "reasoning": humanize_reasoning(d.reasoning or ""),
        "confidence": float(d.confidence) if d.confidence is not None else None,
        "status": d.status,
        "candidate_name": candidate_name or "Unnamed candidate",
        "score": float(score) if score is not None else None,
        "resolution_note": d.resolution_note,
        "created_at": _iso(d.created_at),
        "resolved_at": _iso(d.resolved_at),
    }


def _messages(db: Session, conversation: AgentConversation) -> list[AgentConversationMessage]:
    return (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == conversation.id,
            AgentConversationMessage.kind.in_(_VISIBLE_MESSAGE_KINDS),
        )
        .order_by(AgentConversationMessage.created_at.asc(), AgentConversationMessage.id.asc())
        .all()
    )


def _needs_inputs(db: Session, role: Role, *, since: datetime) -> list[AgentNeedsInput]:
    # Open ones (always) + recently-resolved (within the window).
    return (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == int(role.id),
            (
                (AgentNeedsInput.resolved_at.is_(None) & AgentNeedsInput.dismissed_at.is_(None))
                | (AgentNeedsInput.created_at >= since)
            ),
        )
        .order_by(AgentNeedsInput.created_at.asc())
        .all()
    )


def _decisions(
    db: Session, role: Role, *, since: datetime
) -> list[tuple[AgentDecision, str | None, float | None]]:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(
            AgentDecision,
            Candidate.full_name,
            CandidateApplication.pre_screen_score_100,
        )
        .join(CandidateApplication, CandidateApplication.id == AgentDecision.application_id)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            AgentDecision.role_id == int(role.id),
            or_(
                and_(
                    AgentDecision.status == "pending",
                    or_(
                        AgentDecision.snoozed_until.is_(None),
                        AgentDecision.snoozed_until <= now,
                    ),
                ),
                and_(
                    AgentDecision.status != "pending",
                    AgentDecision.created_at >= since,
                ),
            ),
        )
        # Select the newest window first; ordering ascending before applying
        # the cap silently returned the oldest cards on high-volume roles.
        .order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc())
        .limit(_MAX_DECISIONS)
        .all()
    )
    # The merged conversation remains chronological. Reversing also restores
    # deterministic id order when several decisions share a timestamp.
    return [(d, name, score) for d, name, score in reversed(rows)]


def build_timeline(db: Session, *, conversation: AgentConversation, role: Role) -> list[dict[str, Any]]:
    """Merged, chronological timeline for the conversation."""
    since = datetime.now(timezone.utc) - timedelta(days=_RESOLVED_WINDOW_DAYS)

    items: list[dict[str, Any]] = []
    items.extend(serialize_message(m) for m in _messages(db, conversation))
    items.extend(
        serialize_needs_input(n, role_version=int(role.version or 1))
        for n in _needs_inputs(db, role, since=since)
    )
    items.extend(
        serialize_decision(d, candidate_name=name, score=score)
        for d, name, score in _decisions(db, role, since=since)
    )

    # Sort by created_at; items without a timestamp (shouldn't happen) sink
    # to the top so they're never lost.
    items.sort(key=lambda it: it.get("created_at") or "")
    return items


__all__ = [
    "build_timeline",
    "serialize_decision",
    "serialize_message",
    "serialize_needs_input",
]
