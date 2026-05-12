"""Cohort-survey tools the orchestrator calls during a role tick.

These are the *eyes* the orchestrator uses to see the role's state in
one shot — instead of paging through 405 individual ``get_application``
calls, it asks ``survey_role_state`` once and gets the counts of
candidates in each pipeline state. From there it decides what work is
worth doing this cycle (auto-execute the cheap deterministic stuff,
queue decisions for human approval, ask the recruiter when input is
genuinely missing).

Per OpenAI's "Practical guide to building agents" §Tools, these are
**Data tools** + small **Action tools** — no new orchestration layer.
The agent stays a single-agent loop with a sharper tool surface.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.agent_needs_input import AgentNeedsInput
from ..models.candidate_application import CandidateApplication
from ..models.role import Role


logger = logging.getLogger("taali.agent_runtime.cohort_tools")


# Names of the application "states" the orchestrator reasons about.
# Each maps to a concrete query in ``find_apps_in_state``.
COHORT_STATES = (
    "needs_cv_fetch",                  # cv_text NULL but cv_file_url set
    "needs_pre_screen",                # cv_text present, pre_screen_score_100 NULL
    "needs_score",                     # pre_screen passed, cv_match_score NULL
    "ready_for_assessment_decision",   # scored above-ish, no assessment yet
    "in_assessment",                   # assessment sent, no completed score
    "ready_for_advance_decision",      # assessment_score present, no decision queued
    "rejected",                        # outcome=rejected
    "hired",                           # outcome=hired
)


# ---------------------------------------------------------------------------
# survey_role_state — counts per state, plus role-level flags
# ---------------------------------------------------------------------------


def survey_role_state(db: Session, *, organization_id: int, role_id: int) -> dict[str, Any]:
    """Return a single dict the orchestrator uses to plan its cycle.

    Counts per state + role-level config gaps + open recruiter
    questions. Cheap (one COUNT query per state, plus a couple of
    role/JSON reads). The orchestrator should call this exactly once
    at the top of each cycle.
    """
    role = (
        db.query(Role)
        .filter(Role.id == role_id, Role.organization_id == organization_id)
        .one_or_none()
    )
    if role is None:
        return {"error": f"role {role_id} not found"}

    counts: dict[str, int] = {}
    for state in COHORT_STATES:
        counts[state] = _count_in_state(db, organization_id=organization_id, role_id=role_id, state=state)

    intent_gaps = _intent_gaps(role)

    open_questions = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.organization_id == organization_id,
            AgentNeedsInput.role_id == role_id,
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .order_by(AgentNeedsInput.created_at.desc())
        .all()
    )

    return {
        "role_id": int(role.id),
        "role_name": role.name,
        "agentic_mode_enabled": bool(role.agentic_mode_enabled),
        "agent_paused_at": role.agent_paused_at.isoformat() if role.agent_paused_at else None,
        "auto_reject": bool(getattr(role, "auto_reject", False)),
        "auto_promote": bool(getattr(role, "auto_promote", False)),
        "monthly_usd_budget_cents": role.monthly_usd_budget_cents,
        "score_threshold": role.score_threshold,
        "counts": counts,
        "intent_gaps": intent_gaps,
        "open_recruiter_questions": [
            {
                "id": int(q.id),
                "kind": q.kind,
                "prompt": q.prompt,
                "created_at": q.created_at.isoformat() if q.created_at else None,
            }
            for q in open_questions
        ],
    }


def _intent_gaps(role: Role) -> list[str]:
    """Return human-readable list of role-config gaps the agent should
    ask the recruiter about before running cycles in earnest.

    Recruiter intent lives in ``role_criteria`` rows (bucketed
    must / preferred / constraints) since alembic 068 retired
    ``Role.additional_requirements``. "no must-have requirements" means
    the role has zero non-derived criteria in the must bucket — the
    agent can't apply hard requirements without them.
    """
    from ..models.role_criterion import CRITERION_SOURCE_DERIVED

    gaps: list[str] = []
    if role.monthly_usd_budget_cents is None or role.monthly_usd_budget_cents <= 0:
        gaps.append("monthly_usd_budget_cents is unset")
    if role.score_threshold is None:
        gaps.append("score_threshold is unset")
    if not (role.job_spec_text or "").strip() and not (role.description or "").strip():
        gaps.append("no job spec attached")
    must_chips = [
        c for c in (role.criteria or [])
        if c.deleted_at is None
        and c.source != CRITERION_SOURCE_DERIVED
        and getattr(c, "bucket", None) == "must"
        and (c.text or "").strip()
    ]
    if not must_chips:
        gaps.append("no must-have requirements captured")
    return gaps


# ---------------------------------------------------------------------------
# find_apps_in_state — id list for one state
# ---------------------------------------------------------------------------


def find_apps_in_state(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    state: str,
    limit: int = 50,
) -> list[int]:
    """Return up to ``limit`` application ids in the given state.

    Used by the orchestrator after ``survey_role_state`` tells it
    where the work is — e.g. "47 apps need_pre_screen, give me the
    first 25".

    For triage states (``ready_for_assessment_decision`` /
    ``ready_for_advance_decision``), applications that already have a
    pending AgentDecision are excluded — otherwise the agent re-picks
    the same top candidate every cycle, hits the dedup branch in
    _tool_send_assessment, and never advances down the list. Triage
    states also sort by cv_match_score so high-signal candidates land
    at the top regardless of insertion order.
    """
    if state not in COHORT_STATES:
        return []
    q = _state_query(db, organization_id=organization_id, role_id=role_id, state=state)
    if q is None:
        return []
    if state in ("ready_for_assessment_decision", "ready_for_advance_decision"):
        pending_subq = (
            db.query(AgentDecision.application_id)
            .filter(
                AgentDecision.organization_id == organization_id,
                AgentDecision.role_id == role_id,
                AgentDecision.status == "pending",
            )
            .subquery()
        )
        q = q.filter(~CandidateApplication.id.in_(pending_subq))
    if state == "ready_for_assessment_decision":
        q = q.order_by(CandidateApplication.cv_match_score.desc().nullslast())
    elif state == "ready_for_advance_decision":
        q = q.order_by(CandidateApplication.assessment_score_cache_100.desc().nullslast())
    rows = q.with_entities(CandidateApplication.id).limit(int(limit)).all()
    return [int(r[0]) for r in rows]


def _count_in_state(
    db: Session, *, organization_id: int, role_id: int, state: str
) -> int:
    q = _state_query(db, organization_id=organization_id, role_id=role_id, state=state)
    if q is None:
        return 0
    return int(q.with_entities(func.count(CandidateApplication.id)).scalar() or 0)


def _state_query(
    db: Session, *, organization_id: int, role_id: int, state: str
):
    """Build the SQL query that defines a state. One place so the
    counts and id-lists never drift apart.
    """
    base = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == organization_id,
        CandidateApplication.role_id == role_id,
        CandidateApplication.deleted_at.is_(None),
    )

    if state == "needs_cv_fetch":
        return base.filter(
            or_(
                CandidateApplication.cv_text.is_(None),
                CandidateApplication.cv_text == "",
            ),
            CandidateApplication.cv_file_url.isnot(None),
            CandidateApplication.application_outcome == "open",
        )
    if state == "needs_pre_screen":
        return base.filter(
            CandidateApplication.cv_text.isnot(None),
            CandidateApplication.cv_text != "",
            CandidateApplication.pre_screen_score_100.is_(None),
            CandidateApplication.application_outcome == "open",
        )
    if state == "needs_score":
        return base.filter(
            CandidateApplication.pre_screen_score_100.isnot(None),
            CandidateApplication.pre_screen_score_100 >= 50,
            CandidateApplication.cv_match_score.is_(None),
            CandidateApplication.application_outcome == "open",
        )
    if state == "ready_for_assessment_decision":
        return base.filter(
            CandidateApplication.cv_match_score.isnot(None),
            CandidateApplication.pipeline_stage.in_(["applied", "review"]),
            CandidateApplication.application_outcome == "open",
        )
    if state == "in_assessment":
        return base.filter(
            CandidateApplication.pipeline_stage == "in_assessment",
            CandidateApplication.application_outcome == "open",
        )
    if state == "ready_for_advance_decision":
        return base.filter(
            CandidateApplication.assessment_score_cache_100.isnot(None),
            CandidateApplication.pipeline_stage.in_(["in_assessment", "review"]),
            CandidateApplication.application_outcome == "open",
        )
    if state == "rejected":
        return base.filter(CandidateApplication.application_outcome == "rejected")
    if state == "hired":
        return base.filter(CandidateApplication.application_outcome == "hired")
    return None


# ---------------------------------------------------------------------------
# read_pending_recruiter_inputs
# ---------------------------------------------------------------------------


def read_pending_recruiter_inputs(
    db: Session, *, organization_id: int, role_id: int
) -> list[dict[str, Any]]:
    """Return open + recently-resolved questions the agent has asked.

    The orchestrator calls this each cycle so it can:
      - skip re-asking questions that are already open,
      - read freshly-resolved answers and act on them.

    Limits to 25 most recent rows; older resolved rows are noise.
    """
    rows = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.organization_id == organization_id,
            AgentNeedsInput.role_id == role_id,
        )
        .order_by(AgentNeedsInput.created_at.desc())
        .limit(25)
        .all()
    )
    return [
        {
            "id": int(r.id),
            "kind": r.kind,
            "prompt": r.prompt,
            "options": r.options,
            "rationale": r.rationale,
            "status": (
                "resolved" if r.resolved_at else "dismissed" if r.dismissed_at else "open"
            ),
            "response": r.response,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


__all__ = [
    "COHORT_STATES",
    "find_apps_in_state",
    "read_pending_recruiter_inputs",
    "survey_role_state",
]
