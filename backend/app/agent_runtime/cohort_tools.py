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

from sqlalchemy import func, or_, select
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

    intent_gaps = _intent_gaps(
        role,
        recent_answers=_recent_resolved_answers(
            db, organization_id=organization_id, role_id=role_id
        ),
    )

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

    # The recruiter's most recent answer to a threshold_ambiguous question
    # is the effective threshold for this cycle when role.score_threshold
    # is unset. We surface it via ``effective_score_threshold`` (and the
    # similar treatment for monthly budget) so the agent can use the
    # recruiter's number without us having to rewrite their role config
    # behind their back. role.score_threshold remains a recruiter-owned
    # setting they can change on the role page.
    effective_threshold = role.score_threshold
    effective_budget = role.monthly_usd_budget_cents

    # The single role-fit boundary the decision engine actually rejects /
    # advances on (auto mode → dynamic; manual → role.score_threshold).
    # This is the authoritative cutoff — the agent must reason against it,
    # not role.reject_threshold (legacy/unused by the engine). Lazy import
    # to avoid a circular at module load.
    try:
        from ..services.auto_threshold_service import resolve_role_fit_threshold

        effective_role_fit_threshold = resolve_role_fit_threshold(db, role=role)
    except Exception:  # pragma: no cover — never break the survey on threshold resolution
        effective_role_fit_threshold = None
    if effective_threshold is None or effective_budget is None:
        recent = _recent_resolved_answers(
            db, organization_id=organization_id, role_id=role_id
        )
        if effective_threshold is None:
            t = recent.get("threshold_ambiguous")
            if t is not None:
                try:
                    effective_threshold = max(0, min(100, int(float(str(t)))))
                except (TypeError, ValueError):
                    pass
        if effective_budget is None:
            b = recent.get("monthly_budget_missing")
            if b is not None:
                try:
                    n = float(str(b).strip().lstrip("$").replace(",", ""))
                    effective_budget = int(n * 100) if n <= 1000 else int(n)
                except (TypeError, ValueError):
                    pass

    from ..services.agent_policy_settings import effective_agent_policy

    return {
        "role_id": int(role.id),
        "role_name": role.name,
        "agentic_mode_enabled": bool(role.agentic_mode_enabled),
        "agent_paused_at": role.agent_paused_at.isoformat() if role.agent_paused_at else None,
        "auto_reject": bool(getattr(role, "auto_reject", False)),
        "auto_reject_pre_screen": bool(getattr(role, "auto_reject_pre_screen", False)),
        "auto_promote": bool(getattr(role, "auto_promote", False)),
        "auto_send_assessment": getattr(role, "auto_send_assessment", None),
        "auto_resend_assessment": getattr(role, "auto_resend_assessment", None),
        "auto_advance": getattr(role, "auto_advance", None),
        "auto_skip_assessment": bool(getattr(role, "auto_skip_assessment", False)),
        "agent_effective_policy": effective_agent_policy(role),
        "monthly_usd_budget_cents": role.monthly_usd_budget_cents,
        "score_threshold": role.score_threshold,
        # Effective values fold in the recruiter's latest answer when the
        # role config column is null. The agent should triage against
        # these, not the raw column.
        "effective_score_threshold": effective_threshold,
        # The engine's authoritative role-fit reject/advance boundary. Reason
        # against THIS, not role.reject_threshold (which the engine ignores).
        "effective_role_fit_threshold": effective_role_fit_threshold,
        "effective_monthly_budget_cents": effective_budget,
        "counts": counts,
        "intent_gaps": intent_gaps,
        # Shape of the recruiter's intent — lets the agent judge whether
        # the captured intent is "rich enough" to triage well, beyond the
        # blunt zero-must-haves rule that intent_gaps uses. The agent can
        # open an intent_clarification question when it spots a thin
        # dimension (e.g. must-haves listed but no seniority signal).
        "role_intent_shape": _role_intent_shape(role),
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


def _role_intent_shape(role: Role) -> dict[str, Any]:
    """Per-bucket counts + a few example chips so the agent can judge
    whether captured intent is rich enough to triage."""
    from ..models.role_criterion import CRITERION_SOURCE_DERIVED

    # Canonical bucket literals are singular ("constraint") per
    # role_criterion model. Earlier I used "constraints" (plural) here
    # which silently dropped every constraint chip (Codex #190).
    by_bucket: dict[str, list[str]] = {"must": [], "preferred": [], "constraint": []}
    for c in role.criteria or []:
        if c.deleted_at is not None:
            continue
        if c.source == CRITERION_SOURCE_DERIVED:
            continue
        text = (c.text or "").strip()
        if not text:
            continue
        bucket = getattr(c, "bucket", None)
        if bucket in by_bucket:
            by_bucket[bucket].append(text)
    return {
        "must_count": len(by_bucket["must"]),
        "preferred_count": len(by_bucket["preferred"]),
        # Surfaced to the agent under the plural name for readability;
        # the underlying bucket literal is singular.
        "constraints_count": len(by_bucket["constraint"]),
        "must_examples": by_bucket["must"][:5],
        "preferred_examples": by_bucket["preferred"][:5],
        "constraints_examples": by_bucket["constraint"][:5],
        "has_job_spec": bool((role.job_spec_text or "").strip())
        or bool((role.description or "").strip()),
    }


def _recent_resolved_answers(
    db: Session, *, organization_id: int, role_id: int
) -> dict[str, Any]:
    """Latest resolved answer value per config-gap kind for this role.

    Returns ``{kind: value}`` for the most-recently-resolved
    AgentNeedsInput row of each canonical kind. Lets the survey expose
    "effective" config that folds in a recruiter answer when the role
    column itself is still null.
    """
    rows = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.organization_id == organization_id,
            AgentNeedsInput.role_id == role_id,
            AgentNeedsInput.resolved_at.isnot(None),
            AgentNeedsInput.kind.in_(("threshold_ambiguous", "monthly_budget_missing")),
        )
        .order_by(AgentNeedsInput.resolved_at.desc())
        .all()
    )
    out: dict[str, Any] = {}
    for r in rows:
        if r.kind in out:
            continue
        if not isinstance(r.response, dict):
            continue
        v = r.response.get("value")
        if v is None:
            continue
        # Only consider an answer "useful" when it parses into the
        # role's numeric type — otherwise we'd suppress the gap (per
        # _intent_gaps) without producing an effective_* value, leaving
        # the agent permanently blocked on send/advance triage. Codex
        # #187: "around fifty" should NOT close the threshold gap.
        if r.kind == "threshold_ambiguous":
            try:
                int(float(str(v)))
            except (TypeError, ValueError):
                continue
        elif r.kind == "monthly_budget_missing":
            try:
                float(str(v).strip().lstrip("$").replace(",", ""))
            except (TypeError, ValueError):
                continue
        out[r.kind] = v
    return out


def _intent_gaps(
    role: Role, *, recent_answers: dict[str, Any] | None = None
) -> list[str]:
    """Return human-readable list of role-config gaps the agent should
    ask the recruiter about before running cycles in earnest.

    Recruiter intent lives in ``role_criteria`` rows (bucketed
    must / preferred / constraints) since alembic 068 retired
    ``Role.additional_requirements``. "no must-have requirements" means
    the role has zero non-derived criteria in the must bucket — the
    agent can't apply hard requirements without them.

    A gap is considered closed if the recruiter has answered the
    corresponding question, even when the role column itself is still
    null — the answer is the working value for this cycle. Without this
    the agent re-asks the same question forever.
    """
    from ..models.role_criterion import CRITERION_SOURCE_DERIVED

    answers = recent_answers or {}
    gaps: list[str] = []
    if (role.monthly_usd_budget_cents is None or role.monthly_usd_budget_cents <= 0) and "monthly_budget_missing" not in answers:
        gaps.append("monthly_usd_budget_cents is unset")
    if role.score_threshold is None and "threshold_ambiguous" not in answers:
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

    BUG-1: triage states also exclude apps the recruiter has already said
    "no" to — a discarded/overridden decision whose cited inputs are
    unchanged. That suppression releases the moment a new score /
    assessment / CV / criteria edit / recruiter note makes a fresh verdict
    legitimate (see ``decision_staleness``), so the agent stops re-queuing a
    verdict the human just rejected without locking the candidate out
    forever.
    """
    if state not in COHORT_STATES:
        return []
    q = _state_query(db, organization_id=organization_id, role_id=role_id, state=state)
    if q is None:
        return []

    is_triage = state in ("ready_for_assessment_decision", "ready_for_advance_decision")
    if is_triage:
        pending_ids = select(AgentDecision.application_id).where(
                AgentDecision.organization_id == organization_id,
                AgentDecision.role_id == role_id,
                AgentDecision.status == "pending",
        )
        q = q.filter(~CandidateApplication.id.in_(pending_ids))
    if state == "ready_for_assessment_decision":
        q = q.order_by(CandidateApplication.cv_match_score.desc().nullslast())
    elif state == "ready_for_advance_decision":
        q = q.order_by(CandidateApplication.assessment_score_cache_100.desc().nullslast())

    if not is_triage:
        rows = q.with_entities(CandidateApplication.id).limit(int(limit)).all()
        return [int(r[0]) for r in rows]

    suppressed = _human_suppressed_app_ids(
        db, organization_id=organization_id, role_id=role_id
    )
    if not suppressed:
        rows = q.with_entities(CandidateApplication.id).limit(int(limit)).all()
        return [int(r[0]) for r in rows]

    # Over-read so the post-filter still returns a full page when some of the
    # top-ranked rows are suppressed. At most ``len(suppressed)`` rows can be
    # dropped, so reading that many extra guarantees ``limit`` survivors when
    # that many unsuppressed apps exist (no starvation of lower-ranked apps).
    over_read = int(limit) + len(suppressed)
    rows = q.with_entities(CandidateApplication.id).limit(over_read).all()
    out: list[int] = []
    for (app_id,) in rows:
        if int(app_id) in suppressed:
            continue
        out.append(int(app_id))
        if len(out) >= int(limit):
            break
    return out


def _human_suppressed_app_ids(
    db: Session, *, organization_id: int, role_id: int
) -> set[int]:
    """App ids in this role with a *live* discarded/overridden decision.

    "Live" = the most-recently-resolved discarded/overridden decision for the
    app has cited inputs that have NOT materially changed since (per
    ``decision_staleness``). Such apps are kept out of the triage cohort so
    the agent doesn't re-surface a verdict the recruiter already rejected.
    The set releases an app the instant its inputs drift, letting the agent
    re-decide on fresh information.
    """
    from ..services.decision_staleness import (
        StalenessCache,
        is_human_suppression_live,
    )

    rows = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == organization_id,
            AgentDecision.role_id == role_id,
            AgentDecision.status.in_(("discarded", "overridden")),
        )
        .order_by(
            AgentDecision.application_id.asc(),
            AgentDecision.resolved_at.desc().nullslast(),
            AgentDecision.id.desc(),
        )
        .all()
    )
    cache = StalenessCache()
    suppressed: set[int] = set()
    seen: set[int] = set()
    for decision in rows:
        app_id = int(decision.application_id)
        if app_id in seen:
            continue  # only the most-recent resolution per app matters
        seen.add(app_id)
        try:
            if is_human_suppression_live(db, decision, cache=cache):
                suppressed.add(app_id)
        except Exception:  # pragma: no cover — never break the survey
            logger.warning(
                "human-suppression check failed app=%s role=%s",
                app_id, role_id, exc_info=True,
            )
            suppressed.add(app_id)  # fail safe toward honouring the human "no"
    return suppressed


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

    # A6: explicit exclusion of pipeline_stage='advanced' from every
    # active-pipeline state. ``application_outcome == "open"`` doesn't
    # cover advanced (an advanced candidate can still be outcome=open
    # while they're awaiting downstream interview/offer in the customer's
    # ATS). The later states (ready_for_*, in_assessment) list explicit
    # pipeline_stage IN clauses that exclude 'advanced' implicitly, but
    # the early states need the explicit guard so Workable sync surprises
    # can't slip an advanced candidate back into the scoring pipeline.
    if state == "needs_cv_fetch":
        return base.filter(
            or_(
                CandidateApplication.cv_text.is_(None),
                CandidateApplication.cv_text == "",
            ),
            CandidateApplication.cv_file_url.isnot(None),
            CandidateApplication.application_outcome == "open",
            CandidateApplication.pipeline_stage != "advanced",
        )
    if state == "needs_pre_screen":
        return base.filter(
            CandidateApplication.cv_text.isnot(None),
            CandidateApplication.cv_text != "",
            CandidateApplication.pre_screen_score_100.is_(None),
            CandidateApplication.application_outcome == "open",
            CandidateApplication.pipeline_stage != "advanced",
        )
    if state == "needs_score":
        return base.filter(
            CandidateApplication.pre_screen_score_100.isnot(None),
            CandidateApplication.pre_screen_score_100 >= 50,
            CandidateApplication.cv_match_score.is_(None),
            CandidateApplication.application_outcome == "open",
            CandidateApplication.pipeline_stage != "advanced",
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
