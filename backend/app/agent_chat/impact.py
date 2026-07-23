"""Impact analysis for role-agent constraint changes — the "what happens
if…" engine behind the conversational agent.

Pure reads + deterministic math, no LLM. The agent's tools call these to
answer the recruiter's questions exactly:

* "what happens if I drop the threshold to 65?" → :func:`simulate_threshold`
* "what threshold brings 5 more candidates back?" → :func:`recommend_threshold`
* the actual commit lives in :func:`apply_threshold`, which routes ordinary
  and related roles through the canonical role-aware reconciler.

The gate is ``pre_screen_score_100`` vs the cutoff — the same signal the
deterministic reject path uses (mirrored in :func:`_is_below`, kept in lockstep
with ``pre_screen_decision_emitter._below_threshold``). Simulating with the
same gate the commit uses means the projection matches what actually happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..candidate_search.application_role_scope import application_outcome_expression
from ..candidate_search.role_scope import resolve_candidate_role_scope
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
    score: float | None  # pre_screen_score_100 — the gating signal
    recommendation: str | None
    pipeline_stage: str | None
    workable_stage: str | None
    bullhorn_status: str | None
    external_stage_normalized: str | None
    ats_context: dict[str, Any]
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
    """Deterministic below-threshold test — lockstep with
    ``pre_screen_decision_emitter._below_threshold``.

    Numeric score is authoritative against the cutoff; with no score the
    'Below threshold' recommendation (must-have miss / invalidated score) is
    the reject signal regardless of the numeric cutoff.
    """
    if score is not None:
        return threshold is not None and float(score) < float(threshold)
    return (recommendation or "").strip().lower() == "below threshold"


def effective_threshold(db: Session, role: Role) -> float | None:
    """The 0-100 cutoff the logical role's decision runtime uses."""
    from ..services.role_threshold_reconciliation import effective_role_threshold

    return effective_role_threshold(db, role=role)


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
    role_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
    )
    query = (
        db.query(CandidateApplication, name_or_candidate)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.organization_id == int(role.organization_id),
        )
    )
    query = role_scope.scope_visible_roster(query).filter(
        application_outcome_expression(role_scope) == "open"
    )
    rows = query.all()
    evaluations = role_scope.evaluation_map(
        db,
        application_ids=[int(application.id) for application, _ in rows],
    )
    adapter = role_scope.row_adapter(evaluations)
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
    for source_app, name_or_cand in rows:
        app = adapter(source_app) if adapter is not None else source_app
        ats_context = getattr(app, "ats_context", None)
        if not isinstance(ats_context, dict):
            from ..services.ats_context_service import application_ats_context

            ats_context = application_ats_context(app)
        provider = str(ats_context.get("provider") or "native")
        raw_stage = ats_context.get("raw_stage")
        if with_comments:
            candidate = name_or_cand
            full_name = candidate.full_name
            comments: list[dict[str, Any]] | None = workable_recruiter_comments(candidate)
        else:
            full_name = name_or_cand
            comments = None
        out.append(
            CandidateRow(
                application_id=int(app.id),
                candidate_name=(full_name or "Unnamed candidate").strip()
                or "Unnamed candidate",
                score=float(app.pre_screen_score_100)
                if app.pre_screen_score_100 is not None
                else None,
                recommendation=app.pre_screen_recommendation,
                pipeline_stage=app.pipeline_stage,
                workable_stage=(
                    str(raw_stage) if provider == "workable" and raw_stage else None
                ),
                bullhorn_status=(
                    str(raw_stage) if provider == "bullhorn" and raw_stage else None
                ),
                external_stage_normalized=(
                    str(ats_context["normalized_stage"])
                    if ats_context.get("normalized_stage")
                    else None
                ),
                ats_context=ats_context,
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

    # Pending advance cards that a commit would retract (now below the new
    # cutoff). Mirrors retract_advances_below_threshold's filter.
    would_retract = [
        r
        for r in rows
        if r.has_pending_advance and _is_below(r.score, r.recommendation, sim)
    ]
    # Open candidates a commit would newly card as reject (below + no pending
    # decision yet). Mirrors reconcile's emit loop.
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
    """Commit a score-threshold change and reconcile the decision queue.

    Sets ``role.score_threshold`` then runs the same canonical role-aware
    reconciler as the role PATCH and autonomous cycle. Ordinary roles read
    CandidateApplication state; related roles read SisterRoleEvaluation state.
    No re-scoring occurs; only the role-local verdict moves.

    Returns the impact card with before/after counts.
    """
    from ..services.role_threshold_reconciliation import (
        reconcile_role_threshold_decisions,
    )

    before = effective_threshold(db, role)
    role.score_threshold = (
        int(round(new_threshold)) if new_threshold is not None else None
    )
    db.flush()
    after = effective_threshold(db, role)

    reconciled = reconcile_role_threshold_decisions(
        db,
        role=role,
        organization_id=int(organization_id),
        threshold=after,
    )
    db.flush()

    rows = load_open_candidates(db, role)
    above, below = split_by_threshold(rows, after)
    return {
        "type": "threshold_change",
        "before_threshold": before,
        "after_threshold": after,
        "discarded_advances": int(
            reconciled.get(
                "discarded_advances",
                reconciled.get("threshold_discarded_advances", 0),
            )
        ),
        "created_rejects": int(
            reconciled.get("created_rejects", reconciled.get("reject", 0))
        ),
        "reconcile_discarded": int(
            reconciled.get(
                "reconcile_discarded",
                reconciled.get("threshold_discarded", 0),
            )
        ),
        "above_after": len(above),
        "below_after": len(below),
        "total_open": len(rows),
    }
