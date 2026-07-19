"""Impact analysis for role-agent constraint changes — the "what happens
if…" engine behind the conversational agent.

Pure reads + deterministic math, no LLM. The agent's tools call these to
answer the recruiter's questions exactly:

* "what happens if I drop the threshold to 65?" → :func:`simulate_threshold`
* "what threshold brings 5 more candidates back?" → :func:`recommend_threshold`
* the actual commit (re-flow stored full-score decisions) lives in
  :func:`apply_threshold` and delegates to the deterministic cohort service.

The role's score threshold is the downstream role-fit boundary, not the cheap
Stage-1 prescreen gate.  Impact math therefore uses the same full-score signal
and threshold resolver as the decision-policy engine.  Stage-1 rejects have a
separate calibrated cutoff and are never retyped by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role


# Advance-family decision types a lowered threshold can supersede — mirrors
# ``pre_screen_decision_emitter._ADVANCE_DECISION_TYPES``.
_ADVANCE_DECISION_TYPES = frozenset(
    {"advance_to_interview", "send_assessment", "resend_assessment_invite"}
)


@dataclass
class CandidateRow:
    """One open application reduced to what the threshold math needs."""

    application_id: int
    candidate_name: str
    score: float | None  # cached role-fit score — the downstream signal
    recommendation: str | None
    pipeline_stage: str | None
    workable_stage: str | None
    bullhorn_status: str | None
    external_stage_normalized: str | None
    pending_decision_type: str | None  # None ⇒ no pending decision
    # Synced Workable recruiter comments/ratings [{author, created_at, body}],
    # newest first — only populated when load_open_candidates(with_comments=True);
    # None elsewhere so the threshold hot-path never pays for the JSON read.
    comments: list[dict[str, Any]] | None = None

    @property
    def has_pending_decision(self) -> bool:
        return self.pending_decision_type is not None

    @property
    def has_pending_advance(self) -> bool:
        return self.pending_decision_type in _ADVANCE_DECISION_TYPES


def _is_below(
    score: float | None, recommendation: str | None, threshold: float | None
) -> bool:
    """Return whether a stored full score falls below the role-fit boundary.

    Unscored candidates fail open: a stale Stage-1 recommendation must not be
    mistaken for a downstream role-fit verdict.
    """
    del recommendation  # retained in the row for candidate-list presentation
    return (
        score is not None
        and threshold is not None
        and float(score) < float(threshold)
    )


def effective_threshold(db: Session, role: Role) -> float | None:
    """The role-fit boundary consumed by the decision-policy engine."""
    from ..services.auto_threshold_service import resolve_role_fit_threshold

    return resolve_role_fit_threshold(db, role=role)


def load_open_candidates(
    db: Session, role: Role, *, with_comments: bool = False
) -> list[CandidateRow]:
    """Every open application on the role + its pending-decision type.

    One indexed query for the apps (joined to candidate for the display
    name) and one for the role's pending decisions, zipped in memory — no
    N+1.

    ``with_comments`` additionally selects the full Candidate so each row
    carries its synced Workable recruiter comments/ratings (via the canonical
    ``workable_recruiter_comments`` serializer). Off by default — the threshold
    math never needs comment text, so it keeps reading just the display name.
    """
    # Only pull the full Candidate (and its JSON comment/activity blobs) when the
    # caller actually wants comments; otherwise select just the display name.
    name_or_candidate = Candidate if with_comments else Candidate.full_name
    rows = (
        db.query(CandidateApplication, name_or_candidate)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.organization_id == int(role.organization_id),
            CandidateApplication.application_outcome == "open",
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )
    pending_by_app: dict[int, str] = {}
    for app_id, dtype in (
        db.query(AgentDecision.application_id, AgentDecision.decision_type)
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
        )
        .all()
    ):
        # First pending decision per app wins — apps have at most one
        # pending decision by the emitter's one-pending-per-app invariant.
        pending_by_app.setdefault(int(app_id), str(dtype))

    # Lazy import — keeps the Workable serializer (and its deps) off impact.py's
    # import path for the common, comment-free callers.
    if with_comments:
        from ..services.workable_context_service import workable_recruiter_comments

    out: list[CandidateRow] = []
    for app, name_or_cand in rows:
        if with_comments:
            candidate = name_or_cand
            full_name = candidate.full_name
            comments: list[dict[str, Any]] | None = workable_recruiter_comments(candidate)
        else:
            full_name = name_or_cand
            comments = None
        role_fit_score = getattr(app, "role_fit_score_cache_100", None)
        if role_fit_score is None:
            role_fit_score = app.cv_match_score
        out.append(
            CandidateRow(
                application_id=int(app.id),
                candidate_name=(full_name or "Unnamed candidate").strip()
                or "Unnamed candidate",
                score=float(role_fit_score)
                if role_fit_score is not None
                else None,
                recommendation=app.pre_screen_recommendation,
                pipeline_stage=app.pipeline_stage,
                workable_stage=app.workable_stage,
                bullhorn_status=app.bullhorn_status,
                external_stage_normalized=app.external_stage_normalized,
                pending_decision_type=pending_by_app.get(int(app.id)),
                comments=comments,
            )
        )
    return out


def _names(rows: list[CandidateRow], limit: int = 6) -> list[str]:
    return [r.candidate_name for r in rows[:limit]]


def stage_counts(rows: list[CandidateRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        key = r.pipeline_stage or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def split_by_threshold(
    rows: list[CandidateRow], threshold: float | None
) -> tuple[list[CandidateRow], list[CandidateRow]]:
    """(above_or_clear, below) at ``threshold``. ``above`` keeps anyone the
    gate doesn't reject (includes unscored candidates with no reject rec)."""
    below = [r for r in rows if _is_below(r.score, r.recommendation, threshold)]
    above = [r for r in rows if not _is_below(r.score, r.recommendation, threshold)]
    return above, below


def simulate_threshold(
    db: Session, role: Role, simulated_threshold: float
) -> dict[str, Any]:
    """Project the effect of moving the score threshold to
    ``simulated_threshold`` — without committing anything.

    Returns the impact-card payload the UI renders and the agent narrates.
    """
    rows = load_open_candidates(db, role)
    current = effective_threshold(db, role)
    sim = float(simulated_threshold)

    cur_above, cur_below = split_by_threshold(rows, current)
    sim_above, sim_below = split_by_threshold(rows, sim)

    # Pending positive cards whose score band changes under the proposed cut.
    # The commit re-evaluates deterministic cards through the policy engine;
    # this score-only projection is deliberately described as an impact count,
    # not an exact promise that an LLM-authored card will be mutated.
    would_retract = [
        r
        for r in rows
        if r.has_pending_advance and _is_below(r.score, r.recommendation, sim)
    ]
    # Open, undecided candidates in the projected below-cutoff band. The
    # deterministic cohort service evaluates their complete stored policy
    # inputs before choosing a concrete decision type.
    would_reject = [
        r
        for r in sim_below
        if not r.has_pending_decision
    ]
    # Candidates the move newly lets through (were below current, clear of the
    # lower cutoff) — the "brings X back" set the recruiter cares about.
    newly_cleared = [
        r
        for r in cur_below
        if not _is_below(r.score, r.recommendation, sim)
    ]

    return {
        "type": "threshold_simulation",
        "current_threshold": current,
        "simulated_threshold": sim,
        "current_above": len(cur_above),
        "current_below": len(cur_below),
        "simulated_above": len(sim_above),
        "simulated_below": len(sim_below),
        "delta_above": len(sim_above) - len(cur_above),
        "newly_cleared_count": len(newly_cleared),
        "newly_cleared_sample": _names(
            sorted(newly_cleared, key=lambda r: (r.score or 0), reverse=True)
        ),
        "would_retract_count": len(would_retract),
        "would_retract_sample": _names(would_retract),
        "would_reject_count": len(would_reject),
        "pending_positive_below_count": len(would_retract),
        "undecided_below_count": len(would_reject),
        "total_open": len(rows),
    }


def recommend_threshold(
    db: Session,
    role: Role,
    *,
    target_additional: int | None = None,
    target_total: int | None = None,
) -> dict[str, Any]:
    """Recommend a score threshold.

    * ``target_total`` — a cutoff that clears ~this many open candidates.
    * ``target_additional`` — clears ~this many *more* than the current cutoff.
    * neither — relax to recover the cluster sitting just under the current
      cutoff (default 'open it up a little' suggestion).

    The recommended cutoff is the score of the boundary candidate (so
    ``score >= cutoff`` includes them), clamped to [0, current).
    """
    rows = load_open_candidates(db, role)
    current = effective_threshold(db, role)
    cur_above, cur_below = split_by_threshold(rows, current)

    # Scored candidates currently below the cutoff, best first — the pool a
    # lower threshold can recover.
    recoverable = sorted(
        [r for r in cur_below if r.score is not None],
        key=lambda r: r.score or 0.0,
        reverse=True,
    )

    rationale: str
    if target_total is not None:
        want_extra = max(0, int(target_total) - len(cur_above))
    elif target_additional is not None:
        want_extra = max(0, int(target_additional))
    else:
        # No explicit target: recover everyone within 8 points below the
        # current cutoff (a natural 'loosen slightly' band), or the single
        # best recoverable candidate if the band is empty.
        if current is not None:
            band = [r for r in recoverable if (r.score or 0) >= float(current) - 8.0]
            want_extra = len(band) if band else (1 if recoverable else 0)
        else:
            want_extra = 0

    if not recoverable or want_extra <= 0:
        return {
            "type": "threshold_recommendation",
            "current_threshold": current,
            "recommended_threshold": current,
            "current_above": len(cur_above),
            "projected_above": len(cur_above),
            "projected_additional": 0,
            "added_sample": [],
            "rationale": (
                "No scored candidates sit below the current cutoff — lowering "
                "the threshold wouldn't bring anyone new through right now."
                if not recoverable
                else "The current cutoff already clears the requested volume."
            ),
        }

    # Boundary candidate: the want_extra-th best recoverable one (clamped to
    # what's available). The cutoff is their score so >= includes them.
    idx = min(want_extra, len(recoverable)) - 1
    boundary = recoverable[idx]
    recommended = float(boundary.score or 0.0)
    # Floor it a touch so equal-score ties at the boundary all clear.
    recommended = max(0.0, round(recommended, 1))

    added = [r for r in recoverable if (r.score or 0.0) >= recommended]
    if current is not None:
        rationale = (
            f"Dropping the cutoff from {current:.0f} to {recommended:.0f} clears "
            f"{len(added)} more candidate(s) currently sitting just below it."
        )
    else:
        rationale = (
            f"Setting a cutoff of {recommended:.0f} clears {len(added)} "
            "candidate(s)."
        )

    return {
        "type": "threshold_recommendation",
        "current_threshold": current,
        "recommended_threshold": recommended,
        "current_above": len(cur_above),
        "projected_above": len(cur_above) + len(added),
        "projected_additional": len(added),
        "added_sample": _names(added),
        "rationale": rationale,
    }


def apply_threshold(
    db: Session, role: Role, new_threshold: float | None, *, organization_id: int
) -> dict[str, Any]:
    """Commit a role-fit-threshold change and re-flow stored-score decisions.

    Sets ``role.score_threshold`` then runs the canonical deterministic cohort
    path. Existing deterministic full-score cards are re-evaluated and open,
    scored applications are decided against the new boundary without an LLM
    call or re-scoring. Stage-1 prescreen cards are intentionally untouched.

    Returns the impact card with before/after counts.
    """
    from ..services.bulk_decision_service import decide_role_cohort

    before = effective_threshold(db, role)
    role.score_threshold = int(round(new_threshold)) if new_threshold is not None else None
    # An explicit number is a recruiter-pinned boundary. Without switching out
    # of auto mode the saved value is silently ignored by runtime resolution.
    # Clearing the pin returns the role to automatic threshold selection.
    role.auto_reject_threshold_mode = (
        "manual" if new_threshold is not None else "auto"
    )
    db.flush()
    after = effective_threshold(db, role)

    # The role is already scoped to the caller's organization; keep the
    # explicit argument as a defence-in-depth contract for direct callers.
    if int(role.organization_id) != int(organization_id):
        raise ValueError("Role does not belong to the requested organization")
    reconciled = decide_role_cohort(db, role=role)

    rows = load_open_candidates(db, role)
    above, below = split_by_threshold(rows, after)
    return {
        "type": "threshold_change",
        "before_threshold": before,
        "after_threshold": after,
        "reconciled_decisions": int(reconciled.get("reconciled_discarded", 0)),
        "created_decisions": int(reconciled.get("created", 0)),
        "created_rejects": int(reconciled.get("reject", 0)),
        "created_positive_decisions": int(
            reconciled.get("send_assessment", 0)
            + reconciled.get("advance_to_interview", 0)
        ),
        "above_after": len(above),
        "below_after": len(below),
        "total_open": len(rows),
    }
