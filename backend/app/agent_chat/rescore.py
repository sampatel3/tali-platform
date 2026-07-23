"""Scoped re-score tool — refresh a subset of a role's OLD-engine scores to the
current holistic engine, driven by the recruiter's natural-language steer in the
role chat.

Making v2.1.0 the platform default (PR #618) does NOT re-score existing
candidates — a re-score is a real spend, so it stays opt-in. When the agent is
switched on for a role still carrying stale v1.x scores, it surfaces the count
and offers a re-score; the recruiter steers the scope ("all", "top 10", "only
those below 60", "leave them") and the agent previews the cost before running
anything. The spend only happens on an explicit ``confirm=True`` call.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..candidate_search.application_role_scope import application_outcome_expression
from ..candidate_search.role_scope import resolve_candidate_role_scope
from ..cv_matching.holistic import HOLISTIC_ENGINE_VERSION, resolve_engine_version
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..services.workable_actions_service import workable_job_syncable

logger = logging.getLogger("taali.agent_chat.rescore")

# Honest per-candidate cost of the two-call holistic engine (see
# scoring_recalibration_project: ~$0.083/candidate).
_RESCORE_COST_PER_CANDIDATE_USD = 0.083

_SCOPES = {"all", "top_n", "above_threshold", "below_threshold", "none"}


def find_stale_scored(db: Session, role: Role) -> list[dict[str, Any]]:
    """Open candidates on the role whose stored score is from an OLD engine,
    highest current-score first. Each row keeps the live app object for enqueue.

    Uses the org-aware :func:`score_is_outdated` so this never offers a re-score
    that would just reproduce the same legacy score (an org not on the holistic
    engine has no newer engine to move to)."""
    from ..services.cv_score_orchestrator import score_is_outdated

    role_scope = resolve_candidate_role_scope(
        db,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
    )
    query = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == int(role.organization_id)
    )
    apps = role_scope.scope_visible_roster(query).filter(
        application_outcome_expression(role_scope) == "open"
    ).all()
    evaluations = role_scope.evaluation_map(
        db,
        application_ids=[int(application.id) for application in apps],
    )
    adapter = role_scope.row_adapter(evaluations)
    out: list[dict[str, Any]] = []
    for source_application in apps:
        application = (
            adapter(source_application) if adapter is not None else source_application
        )
        score = getattr(application, "cv_match_score", None)
        if score is not None and score_is_outdated(application):
            out.append(
                {
                    "application_id": int(application.id),
                    "score": float(score),
                    "engine_version": resolve_engine_version(
                        application.cv_match_details
                        if isinstance(application.cv_match_details, dict)
                        else {}
                    ),
                    "_app": source_application,
                    "_evaluation": evaluations.get(int(source_application.id)),
                }
            )
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def stale_scores_summary(db: Session, role: Role) -> dict[str, Any] | None:
    """Compact heads-up the agent surfaces on activation: how many candidates
    are on the old engine + the score range. ``None`` when there are none."""
    stale = find_stale_scored(db, role)
    if not stale:
        return None
    scores = [s["score"] for s in stale]
    return {
        "stale_count": len(stale),
        "score_min": round(min(scores), 1),
        "score_max": round(max(scores), 1),
        "est_cost_all_usd": round(len(stale) * _RESCORE_COST_PER_CANDIDATE_USD, 2),
        "engine_versions": sorted({s["engine_version"] for s in stale}),
    }


def _select(
    stale: list[dict[str, Any]], scope: str, limit: int, threshold: float | None
) -> list[dict[str, Any]]:
    if scope == "none":
        return []
    if scope == "all":
        return stale
    if scope == "top_n":
        return stale[: max(1, int(limit))]
    if scope == "above_threshold":
        return [s for s in stale if threshold is not None and s["score"] >= threshold]
    if scope == "below_threshold":
        return [s for s in stale if threshold is not None and s["score"] < threshold]
    return []


def rescore_candidates(
    db: Session,
    role: Role,
    *,
    scope: str = "all",
    limit: int = 10,
    threshold: float | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview (``confirm=False``) or run (``confirm=True``) a scoped re-score of
    the role's OLD-engine candidates with the current holistic engine.

    Scope: ``all`` · ``top_n`` (by current score, uses ``limit``) ·
    ``above_threshold`` / ``below_threshold`` (uses ``threshold``) · ``none``.
    The preview never spends; it returns the matched count + $ estimate so the
    agent can show it and ask. Only ``confirm=True`` enqueues the re-scores.
    """
    scope = (scope or "all").strip().lower()
    if scope not in _SCOPES:
        return {"ok": False, "error": f"Unknown scope {scope!r}; use one of {sorted(_SCOPES)}."}
    if scope in ("above_threshold", "below_threshold") and threshold is None:
        return {"ok": False, "error": "Give me a score threshold for that scope (0–100)."}

    # Don't re-score a dead req. A closed/archived Workable job can't be hired
    # into, so refreshing its scores only burns credits (2026-06 cost audit:
    # closed/archived roles drove a large share of wasted holistic scoring).
    is_related = bool(
        str(getattr(role, "role_kind", "") or "") == ROLE_KIND_SISTER
        or getattr(role, "ats_owner_role_id", None) is not None
    )
    if not is_related and not workable_job_syncable(role):
        return {
            "type": "rescore",
            "stale_total": 0,
            "message": (
                "This role's Workable job is closed/archived — skipping re-score "
                "(no one can be hired into it)."
            ),
        }

    stale = find_stale_scored(db, role)
    if not stale:
        return {
            "type": "rescore",
            "stale_total": 0,
            "message": "Every scored candidate on this role is already on the current engine — nothing to re-score.",
        }

    selected = _select(stale, scope, limit, threshold)
    count = len(selected)
    est = round(count * _RESCORE_COST_PER_CANDIDATE_USD, 2)

    if not confirm:
        return {
            "type": "rescore_preview",
            "stale_total": len(stale),
            "scope": scope,
            "selected_count": count,
            "est_cost_usd": est,
            "needs_confirmation": count > 0,
            "message": (
                f"{count} of {len(stale)} old-engine candidates match — re-scoring them to "
                f"v{HOLISTIC_ENGINE_VERSION} costs roughly ${est} (~$0.083 each). "
                "Want me to run it?"
            ),
        }

    queued = 0
    reset_count = 0
    waiting_count = 0
    unscorable_count = 0
    skipped_current_count = 0
    decisions_invalidated = 0
    if is_related:
        from ..services.related_role_rescreen_service import (
            RelatedRoleRescreenUnavailableError,
            rescreen_related_role_candidates,
        )

        try:
            outcome = rescreen_related_role_candidates(
                db,
                role,
                reason="agent_chat:old_engine_rescore",
                application_ids=[int(item["application_id"]) for item in selected],
                # A score can finish between preview and the locked mutation.
                # Do not erase a result that is current by confirmation time.
                only_outdated=True,
            )
        except RelatedRoleRescreenUnavailableError as exc:
            db.rollback()
            return {
                "type": "rescore",
                "stale_total": len(stale),
                "rescoring_count": 0,
                "queued_count": 0,
                "message": str(exc),
            }
        reset_count = int(outcome.reset_count)
        queued = int(outcome.queued_count)
        waiting_count = int(outcome.waiting_count)
        unscorable_count = int(outcome.unscorable_count)
        skipped_current_count = int(outcome.skipped_current_count)
        decisions_invalidated = int(outcome.decisions_invalidated)
    else:
        from ..services.cv_score_orchestrator import enqueue_score

        for item in selected:
            try:
                if enqueue_score(
                    db,
                    item["_app"],
                    force=True,
                    bypass_pre_screen=True,
                    requires_active_agent=False,
                ):
                    queued += 1
            except Exception:  # pragma: no cover — one bad app must not abort the batch
                logger.exception(
                    "rescore enqueue failed app=%s",
                    item["application_id"],
                )
        db.commit()
        reset_count = queued
    return {
        "type": "rescore_started",
        "rescoring_count": queued + waiting_count,
        "invalidated_count": reset_count,
        "queued_count": queued,
        "waiting_count": waiting_count,
        "unscorable_count": unscorable_count,
        "skipped_current_count": skipped_current_count,
        "decisions_invalidated": decisions_invalidated,
        "scope": scope,
        "est_cost_usd": round(
            (queued + waiting_count) * _RESCORE_COST_PER_CANDIDATE_USD,
            2,
        ),
        "message": (
            f"Reset {reset_count} candidates for v{HOLISTIC_ENGINE_VERSION}: "
            f"{queued} queued, {waiting_count} waiting for queue recovery, and "
            f"{unscorable_count} missing scoreable evidence. "
            "Role-local actionable decisions based on the old score were discarded; "
            "resolved candidates and other roles were left unchanged."
        ),
    }
