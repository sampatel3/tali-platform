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

from ..cv_matching.holistic import HOLISTIC_ENGINE_VERSION, resolve_engine_version
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
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

    apps = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.application_outcome == "open",
            CandidateApplication.cv_match_score.isnot(None),
        )
        .all()
    )
    out: list[dict[str, Any]] = []
    for a in apps:
        if score_is_outdated(a):
            out.append(
                {
                    "application_id": int(a.id),
                    "score": float(a.cv_match_score),
                    "engine_version": resolve_engine_version(
                        a.cv_match_details if isinstance(a.cv_match_details, dict) else {}
                    ),
                    "_app": a,
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
    reuse_active_jobs: bool = False,
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
    if not workable_job_syncable(role):
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

    from ..services.cv_score_orchestrator import enqueue_score

    queued = 0
    for s in selected:
        try:
            if enqueue_score(
                db,
                s["_app"],
                # A pending confirmed-command replay finishes any remaining
                # candidates without creating a second active paid job for one
                # already accepted before the chat worker crashed. Ordinary
                # explicit rescores retain the established force-refresh path.
                force=not reuse_active_jobs,
                bypass_pre_screen=True,
                requires_active_agent=False,
            ):
                queued += 1
        except Exception:  # pragma: no cover — one bad app must not abort the batch
            logger.exception("rescore enqueue failed app=%s", s["application_id"])
    db.commit()
    return {
        "type": "rescore_started",
        "rescoring_count": queued,
        "scope": scope,
        "est_cost_usd": round(queued * _RESCORE_COST_PER_CANDIDATE_USD, 2),
        "message": (
            f"Re-scoring {queued} candidates to v{HOLISTIC_ENGINE_VERSION}. Scores refresh as "
            "they complete; any pending decision whose verdict flips gets auto-corrected "
            "(gated/advanced ones stay in your queue for review)."
        ),
    }
