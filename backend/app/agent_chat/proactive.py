"""Deterministic, low-noise proactive helper briefings for role Agent Chat.

The role agent already creates decisions and structured recruiter questions,
but a fresh conversation otherwise waits for the recruiter to speak first.
This module selects one useful next step from live role state and persists it as
an assistant message without an LLM call, credit spend, or recruiting mutation.

Briefings are suggestions only. Clicking a quick reply fills the composer; all
state-changing work still goes through the existing preview/confirmation rails.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..candidate_search.role_scope import resolve_candidate_role_scope
from ..models.agent_conversation import (
    AUTHOR_ROLE_USER,
    AgentConversation,
    AgentConversationMessage,
    MESSAGE_KIND_ACTION,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_EVENT,
    MESSAGE_KIND_PROACTIVE,
)
from ..models.agent_decision import AgentDecision
from ..models.agent_needs_input import AgentNeedsInput
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.role_criterion import RoleCriterion
from ..models.sister_role_evaluation import SisterRoleEvaluation
from ..services.needs_input_membership import (
    apply_live_logical_needs_input_scope,
)
from .draft_tasks import count_role_drafts
from .health import role_health_check
from .service import conversation_agent_working, post_agent_message

logger = logging.getLogger("taali.agent_chat.proactive")

HELPER_CARD_TYPE = "helper_prompt"
HELPER_STOP_PREFIX = "proactive_briefing:"
HELPER_COOLDOWN = timedelta(hours=6)

HELPER_TOOL_DEFINITION: dict[str, Any] = {
    "name": "get_helper_briefing",
    "description": (
        "Return the single highest-value next step for THIS role from live open "
        "questions, pending decisions, draft tasks, agent state and role health. "
        "Use when the recruiter asks what needs attention or what to do next. It "
        "is read-only and returns editable suggested prompts; never imply that a "
        "suggestion has already run."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_QUESTION_PRIORITY = {
    "missing_job_spec": 100,
    "monthly_budget_missing": 95,
    "confirm_material_change": 90,
    "missing_cv": 85,
    "cv_unreadable": 84,
    "task_assignment_missing": 80,
    "intent_slot_missing": 75,
    "intent_clarification": 70,
    "threshold_ambiguous": 65,
    "candidate_tie_break": 60,
}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _open_questions(db: Session, role: Role) -> list[AgentNeedsInput]:
    rows = (
        apply_live_logical_needs_input_scope(
            db,
            db.query(AgentNeedsInput),
            organization_id=int(role.organization_id),
        )
        .filter(
            AgentNeedsInput.role_id == int(role.id),
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .all()
    )
    return sorted(
        rows,
        key=lambda row: (
            -_QUESTION_PRIORITY.get(str(row.kind), 0),
            _aware(row.created_at) or datetime.min.replace(tzinfo=timezone.utc),
            int(row.id),
        ),
    )


def _live_pending_decision_query(db: Session, role: Role):
    scope = resolve_candidate_role_scope(
        db,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
    )
    query = (
        db.query(AgentDecision)
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .filter(
            AgentDecision.organization_id == int(role.organization_id),
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
        )
    )
    return scope.scope_visible_roster(query)


def _unsnoozed_decisions(db: Session, role: Role) -> list[AgentDecision]:
    now = datetime.now(timezone.utc)
    return (
        _live_pending_decision_query(db, role)
        .filter(
            or_(
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= now,
            ),
        )
        .order_by(AgentDecision.created_at.asc(), AgentDecision.id.asc())
        .limit(100)
        .all()
    )


def _unsnoozed_decision_count(db: Session, role: Role) -> int:
    now = datetime.now(timezone.utc)
    return int(
        _live_pending_decision_query(db, role)
        .with_entities(func.count(AgentDecision.id))
        .filter(
            or_(
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= now,
            ),
        )
        .scalar()
        or 0
    )


def _source_signal(db: Session, role: Role) -> tuple[str, int]:
    """Cheap material-state token used to avoid running health checks on polls."""

    question_count, question_max = (
        apply_live_logical_needs_input_scope(
            db,
            db.query(func.count(AgentNeedsInput.id), func.max(AgentNeedsInput.id)),
            organization_id=int(role.organization_id),
        )
        .filter(
            AgentNeedsInput.role_id == int(role.id),
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .one()
    )
    now = datetime.now(timezone.utc)
    decision_count, decision_max = (
        _live_pending_decision_query(db, role)
        .with_entities(func.count(AgentDecision.id), func.max(AgentDecision.id))
        .filter(
            or_(
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= now,
            ),
        )
        .one()
    )
    scope = resolve_candidate_role_scope(
        db,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
    )
    if scope.is_related:
        app_count, app_max_id, app_updated = (
            db.query(
                func.count(SisterRoleEvaluation.id),
                func.max(SisterRoleEvaluation.id),
                func.max(SisterRoleEvaluation.updated_at),
            )
            .filter(
                SisterRoleEvaluation.organization_id == int(role.organization_id),
                SisterRoleEvaluation.role_id == int(role.id),
                SisterRoleEvaluation.application_outcome == "open",
                SisterRoleEvaluation.deleted_at.is_(None),
            )
            .one()
        )
    else:
        app_count, app_max_id, app_updated = (
            db.query(
                func.count(CandidateApplication.id),
                func.max(CandidateApplication.id),
                func.max(CandidateApplication.updated_at),
            )
            .filter(
                CandidateApplication.organization_id == int(role.organization_id),
                CandidateApplication.role_id == int(role.id),
                CandidateApplication.application_outcome == "open",
                CandidateApplication.deleted_at.is_(None),
            )
            .one()
        )
    criterion_count, criterion_max, criterion_updated = (
        db.query(
            func.count(RoleCriterion.id),
            func.max(RoleCriterion.id),
            func.max(RoleCriterion.updated_at),
        )
        .filter(
            RoleCriterion.role_id == int(role.id),
            RoleCriterion.deleted_at.is_(None),
        )
        .one()
    )
    try:
        draft_count = count_role_drafts(db, role)
    except Exception:  # pragma: no cover - helper must not break the thread
        logger.warning("draft count failed for proactive role=%s", role.id, exc_info=True)
        draft_count = 0
    signal = _digest(
        {
            "role_id": int(role.id),
            "role_updated": role.updated_at,
            "agent_enabled": bool(role.agentic_mode_enabled),
            "paused_at": role.agent_paused_at,
            "last_run_at": role.agent_last_run_at,
            "threshold": role.score_threshold,
            "questions": [int(question_count or 0), int(question_max or 0)],
            "decisions": [int(decision_count or 0), int(decision_max or 0)],
            "applications": [int(app_count or 0), int(app_max_id or 0), app_updated],
            "criteria": [int(criterion_count or 0), int(criterion_max or 0), criterion_updated],
            "drafts": int(draft_count),
        }
    )
    return signal, int(draft_count)


def _health_copy(finding: dict[str, Any]) -> tuple[str, str, list[dict[str, str]]]:
    """Render derived facts without copying raw criterion/CV text into history."""

    kind = str(finding.get("type") or "")
    if kind == "calibration_drift":
        summary = "Your recent overrides suggest that my recommendations are drifting from how you decide."
        question = "Should I inspect the threshold and must-haves to find the mismatch?"
        prompts = [
            {"label": "Review calibration", "prompt": "Review my recent overrides and identify the strongest calibration issue."},
            {"label": "Test thresholds", "prompt": "Simulate whether a different threshold would better match my recent decisions."},
        ]
    elif kind in {"threshold_too_strict", "threshold_too_loose"}:
        qualified = int(finding.get("qualified") or 0)
        total = int(finding.get("total_open") or 0)
        threshold = finding.get("threshold")
        summary = f"The current cut-off of {threshold} clears {qualified} of {total} open candidates."
        question = "Want me to simulate a better-balanced threshold before changing anything?"
        prompts = [
            {"label": "Recommend a threshold", "prompt": "Recommend a better threshold and show the impact without changing it."},
            {"label": "Show edge cases", "prompt": "Show me the candidates closest to the current threshold."},
        ]
    elif kind in {"dead_requirement", "unverifiable_requirement", "redundant_requirement"}:
        assessed = int(finding.get("assessed") or 0)
        met = finding.get("met")
        unknown = finding.get("unknown")
        if kind == "dead_requirement":
            summary = f"One must-have is met by only {int(met or 0)} of {assessed} assessed candidates."
        elif kind == "unverifiable_requirement":
            summary = f"One requirement cannot be verified for {int(unknown or 0)} of {assessed} assessed candidates."
        else:
            summary = "One requirement is adding little or no signal to the ranking."
        question = "Should I show its exact pool impact before you decide whether to edit it?"
        prompts = [
            {"label": "Show the impact", "prompt": "Show me the top role-health finding and the exact criterion impact. Do not change it."},
            {"label": "Review requirements", "prompt": "Review the must-haves and tell me which one I should reconsider first."},
        ]
    elif kind == "stale_scores":
        count = int(finding.get("stale_count") or 0)
        cost = finding.get("est_cost_usd")
        summary = f"{count} candidate score{'s are' if count != 1 else ' is'} from an older scoring engine."
        question = f"Want a scoped re-score preview{f' (about ${cost} for all)' if cost is not None else ''}?"
        prompts = [
            {"label": "Preview re-score", "prompt": "Preview re-scoring the stale candidates. Show scopes and cost; do not run it."},
            {"label": "Show stale candidates", "prompt": "Which candidates have stale scores, ranked by current score?"},
        ]
    else:
        pending = int(finding.get("pending") or 0)
        summary = f"{pending} decisions are waiting for review." if pending else "I found one role-health issue worth reviewing."
        question = "Want the short version and the safest next step?"
        prompts = [{"label": "Show me", "prompt": "Show me the top role-health issue and recommend one next step."}]
    return summary, question, prompts


def build_helper_briefing(
    db: Session,
    role: Role,
    *,
    source_signal: str | None = None,
    draft_count: int | None = None,
) -> dict[str, Any]:
    """Return one grounded helper question plus editable quick replies."""

    if source_signal is None or draft_count is None:
        source_signal, draft_count = _source_signal(db, role)
    questions = _open_questions(db, role)
    decisions = _unsnoozed_decisions(db, role)
    decision_count = _unsnoozed_decision_count(db, role)

    topic: str
    priority: str
    focus_id: int | None = None
    if questions:
        row = questions[0]
        topic, priority, focus_id = "open_question", "attention", int(row.id)
        summary = (
            f"I need your steer on {len(questions)} open question"
            f"{'s' if len(questions) != 1 else ''} before I can keep moving this role."
        )
        question = str(row.prompt or "Please answer the first question in this thread.").strip()[:700]
        prompts = [
            {"label": "Explain why", "prompt": "Explain why you need this answer and what it will change before I decide."},
            {"label": "List all questions", "prompt": "List every open question for this role in priority order."},
        ]
    elif role.agent_paused_at is not None:
        topic, priority = "agent_paused", "attention"
        reason = str(role.agent_paused_reason or "the role needs a recruiter check").strip()[:240]
        summary = f"The agent is paused: {reason}."
        question = "Want me to review what is waiting before you decide whether to resume it?"
        prompts = [
            {"label": "Review waiting work", "prompt": "Review what is waiting while the agent is paused. Do not resume it yet."},
            {"label": "Check role health", "prompt": "Run a read-only role health check and tell me what to fix first."},
        ]
    elif decisions:
        escalation = next(
            (row for row in decisions if row.decision_type == "escalate_low_confidence"),
            None,
        )
        focus = escalation or decisions[0]
        focus_id = int(focus.id)
        topic = "low_confidence_decision" if escalation else "pending_decisions"
        priority = "attention"
        if escalation:
            summary = "I have a low-confidence candidate decision that needs recruiter judgment."
            question = "Should I explain the disagreement and the available choices?"
        else:
            summary = f"I have {decision_count} recommendation{'s' if decision_count != 1 else ''} waiting for review."
            question = "Want a short summary, or should we work through the highest-priority one?"
        prompts = [
            {"label": "Summarise decisions", "prompt": "Summarise the pending decisions by action and flag anything stale."},
            {"label": "Start with one", "prompt": "Show me the highest-priority pending decision and explain the trade-off."},
        ]
    elif int(draft_count or 0) > 0:
        topic, priority = "draft_tasks", "suggestion"
        summary = f"{int(draft_count)} generated assessment draft{'s are' if int(draft_count) != 1 else ' is'} ready for review."
        question = "Want me to bring the drafts into this thread?"
        prompts = [{"label": "Show drafts", "prompt": "Show me the draft assessment tasks awaiting review."}]
    elif not bool(role.agentic_mode_enabled):
        topic, priority = "agent_off", "suggestion"
        summary = "Automation is off, but I can still analyse this role and help you decide what to change."
        question = "Should I review role health, the candidate pool, or explain what turning the agent on would do?"
        prompts = [
            {"label": "Review role health", "prompt": "Review this role's health without turning the agent on."},
            {"label": "Review the pool", "prompt": "Summarise the candidate pool and tell me who needs attention."},
            {"label": "Explain activation", "prompt": "Explain what the agent would do if I turned it on. Do not activate it."},
        ]
    else:
        health = role_health_check(db, role)
        # The legacy health scanner counts snoozed decisions in its backlog.
        # Never nag about cards the live timeline intentionally hides.
        finding = next(
            (
                item
                for item in (health.get("findings") or ([health.get("top_finding")] if health.get("top_finding") else []))
                if item and (item.get("type") != "decision_backlog" or decision_count > 0)
            ),
            None,
        )
        if finding:
            topic, priority = str(finding.get("type") or "role_health"), "suggestion"
            summary, question, prompts = _health_copy(finding)
            focus_id = int(finding["criterion_id"]) if finding.get("criterion_id") else None
        elif int(health.get("open_candidates") or 0) == 0:
            topic, priority = "empty_pool", "suggestion"
            summary = "I don't see an open candidate pool for this role yet."
            question = "Want me to check the setup, or explain what I will do when applications arrive?"
            prompts = [
                {"label": "Check setup", "prompt": "Check this role's setup and tell me what is missing before candidates arrive."},
                {"label": "Explain the workflow", "prompt": "Explain what this agent will do as new applications arrive."},
            ]
        else:
            topic, priority = "all_clear", "suggestion"
            open_count = int(health.get("open_candidates") or 0)
            summary = f"I reviewed this role: {open_count} open candidate{'s' if open_count != 1 else ''}, with no urgent policy issue."
            question = "Should I shortlist the strongest candidates or look for edge cases near the cut-off?"
            prompts = [
                {"label": "Shortlist strongest", "prompt": "Show me the strongest candidates for this role and ground every ranking."},
                {"label": "Find edge cases", "prompt": "Show me candidates closest to the current cut-off and explain the trade-offs."},
            ]

    fingerprint = _digest(
        {
            "source_signal": source_signal,
            "topic": topic,
            "focus_id": focus_id,
            "summary": summary,
            "question": question,
        }
    )
    return {
        "type": HELPER_CARD_TYPE,
        "title": "Suggested next step",
        "summary": summary,
        "question": question,
        "suggestions": prompts[:3],
        "priority": priority,
        "topic": topic,
        "focus_id": focus_id,
        "fingerprint": fingerprint,
        "source_signal": source_signal,
    }


def _helper_card(row: AgentConversationMessage | None) -> dict[str, Any] | None:
    if row is None or not isinstance(row.actions, list):
        return None
    return next(
        (
            dict(card)
            for card in row.actions
            if isinstance(card, dict) and card.get("type") == HELPER_CARD_TYPE
        ),
        None,
    )


def maybe_post_helper_briefing(
    db: Session,
    *,
    conversation: AgentConversation,
    role: Role,
) -> AgentConversationMessage | None:
    """Post one fresh/materially-changed helper prompt, with anti-nagging rails."""

    # Serialize with interactive sends in production, but never make a
    # timeline read wait for a worker that already owns the conversation row.
    # The helper is optional; if the row is busy, the next poll can try again.
    # SQLite ignores FOR UPDATE, but its test connection is single-writer.
    locked = (
        db.query(AgentConversation)
        .filter(
            AgentConversation.id == int(conversation.id),
            AgentConversation.organization_id == int(role.organization_id),
            AgentConversation.role_id == int(role.id),
        )
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if locked is None or conversation_agent_working(db, locked):
        return None

    visible_kinds = (
        MESSAGE_KIND_CHAT,
        MESSAGE_KIND_ACTION,
        MESSAGE_KIND_PROACTIVE,
        MESSAGE_KIND_EVENT,
    )
    recent = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == int(locked.id),
            AgentConversationMessage.kind.in_(visible_kinds),
        )
        .order_by(AgentConversationMessage.created_at.desc(), AgentConversationMessage.id.desc())
        .limit(100)
        .all()
    )
    latest = recent[0] if recent else None
    if latest is not None and latest.author_role == AUTHOR_ROLE_USER:
        return None
    latest_helper_row = next((row for row in recent if _helper_card(row)), None)
    latest_helper = _helper_card(latest_helper_row)

    source_signal, draft_count = _source_signal(db, role)
    if latest_helper and latest_helper.get("source_signal") == source_signal:
        return None

    now = datetime.now(timezone.utc)
    latest_at = _aware(latest.created_at) if latest is not None else None
    if latest_at is not None and now - latest_at < HELPER_COOLDOWN:
        # The one immediate progression we allow is moving from one answered
        # recruiter question to the next. All other changed-state suggestions
        # wait for the cooldown; their source-of-truth cards remain visible.
        if not latest_helper or latest_helper.get("topic") != "open_question":
            return None
        current_questions = _open_questions(db, role)
        next_id = int(current_questions[0].id) if current_questions else None
        if next_id is None or next_id == latest_helper.get("focus_id"):
            return None

    card = build_helper_briefing(
        db,
        role,
        source_signal=source_signal,
        draft_count=draft_count,
    )
    if latest_helper and latest_helper.get("fingerprint") == card["fingerprint"]:
        return None
    text = f"{card['summary']} {card['question']}"
    return post_agent_message(
        db,
        conversation=locked,
        text=text,
        actions=[card],
        kind=MESSAGE_KIND_PROACTIVE,
        stop_reason=f"{HELPER_STOP_PREFIX}{card['fingerprint']}",
    )


__all__ = [
    "HELPER_CARD_TYPE",
    "HELPER_TOOL_DEFINITION",
    "build_helper_briefing",
    "maybe_post_helper_briefing",
]
