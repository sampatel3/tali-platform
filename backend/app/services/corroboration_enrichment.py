"""Shortlist-gated, generation-safe cross-source corroboration enrichment.

Graph and GitHub lookups stay off the scoring hot path. A durable JSON lease
deduplicates deliveries for one exact score/input generation; after the slow
calls, canonical row locks fence the result against newer role, candidate, or
score evidence before any detail can be persisted or influence a decision.
"""

from __future__ import annotations

import copy
import logging
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from ..components.scoring.freshness import capture_score_generation
from ..models.candidate_application import CandidateApplication
from .corroboration_generation import (
    CorroborationGeneration,
    CorroborationInputs,
    CorroborationLease,
    capture_corroboration_generation,
    capture_corroboration_inputs,
    claim_generation_lease,
    expected_score_attempt_is_latest,
    generation_is_current,
    lease_is_current,
    locate_corroboration,
    lock_corroboration_rows,
    update_lease_marker,
)

logger = logging.getLogger("taali.corroboration_enrichment")

_FLAGGED = {"review", "strong_review"}
_SLOW_SIGNAL_KEYS = (
    "graph_corroboration",
    "github",
    "graph_outcome_prior",
)


def should_enrich(application: CandidateApplication) -> bool:
    """Whether current, persisted evidence warrants the slow enrichment."""
    from ..platform.config import settings

    if not (
        settings.GRAPH_CORROBORATION_ENABLED
        or settings.GITHUB_CORROBORATION_ENABLED
        or settings.GRAPH_OUTCOME_PRIOR_ENABLED
    ):
        return False
    score = getattr(application, "cv_match_score", None)
    if score is None or float(score) < float(settings.CORROBORATION_ENRICH_MIN_SCORE):
        return False
    details = getattr(application, "cv_match_details", None)
    if not isinstance(details, dict):
        return False
    tri = (details.get("integrity_signals") or {}).get("triangulation") or {}
    return tri.get("verdict") in _FLAGGED


def _compute_signals(
    inputs: CorroborationInputs, db: Session
) -> dict[str, Any]:
    """Run external reads against an immutable snapshot; never mutate ORM rows."""
    from ..platform.config import settings
    from .external_corroboration import corroborate_github
    from .graph_corroboration import corroborate_candidate_stack

    signals: dict[str, Any] = {}
    graph = corroborate_candidate_stack(
        organization_id=inputs.locator.organization_id,
        cv_sections=inputs.cv_sections,
        min_observations=settings.GRAPH_CORROBORATION_MIN_OBSERVATIONS,
    )
    if graph is not None:
        signals["graph_corroboration"] = graph
    github = corroborate_github(
        cv_sections=inputs.cv_sections,
        social_profiles=inputs.social_profiles,
    )
    if github is not None:
        signals["github"] = github
    if settings.GRAPH_OUTCOME_PRIOR_ENABLED:
        from .graph_outcome_prior import (
            build_outcome_prior_shadow,
            fetch_outcome_prior,
        )

        application_snapshot = SimpleNamespace(
            id=inputs.locator.application_id,
            organization_id=inputs.locator.organization_id,
            role_id=inputs.locator.role_id,
            candidate_id=inputs.locator.candidate_id,
        )
        prior = build_outcome_prior_shadow(
            fetch_outcome_prior(application_snapshot, db),
            max_nudge=settings.GRAPH_OUTCOME_PRIOR_MAX_NUDGE,
        )
        if prior is not None:
            signals["graph_outcome_prior"] = prior
    return signals


def _current_or_captured_generation(
    db: Session,
    *,
    rows,
    expected: CorroborationGeneration | None,
) -> CorroborationGeneration | None:
    if expected is not None:
        return expected
    token = capture_score_generation(
        db,
        role=rows.role,
        application_id=int(rows.application.id),
    )
    if token is None:
        return None
    return capture_corroboration_generation(
        application=rows.application,
        candidate=rows.candidate,
        score_generation=token,
    )


def _invalidate_unfenced_score_if_needed(
    db: Session,
    *,
    rows,
    expected: CorroborationGeneration,
) -> bool:
    """Close a missed invalidation hole without overtaking a newer score job."""
    if not expected_score_attempt_is_latest(db, expected=expected):
        return False
    current = capture_corroboration_generation(
        application=rows.application,
        candidate=rows.candidate,
        score_generation=expected.score_generation,
    )
    from .role_intent_fingerprint import role_intent_fingerprint

    role_changed = (
        role_intent_fingerprint(rows.role, db=db)
        != expected.score_generation.role_intent_fingerprint
    )
    inputs_changed = (
        current.candidate_input_fingerprint
        != expected.candidate_input_fingerprint
        or current.evidence_fingerprint != expected.evidence_fingerprint
    )
    if not (role_changed or inputs_changed):
        return False
    from .cv_score_orchestrator import mark_application_scores_stale

    mark_application_scores_stale(
        db,
        int(rows.application.id),
        reason="corroboration_inputs_changed",
    )
    return True


def _finish_superseded(
    db: Session,
    *,
    rows,
    expected: CorroborationGeneration,
    lease: CorroborationLease | None = None,
) -> dict[str, Any]:
    if lease is not None and lease_is_current(rows.application, lease=lease):
        update_lease_marker(
            rows.application,
            lease=lease,
            status="superseded",
        )
    invalidated = _invalidate_unfenced_score_if_needed(
        db,
        rows=rows,
        expected=expected,
    )
    db.commit()
    return {
        "status": "superseded",
        "application_id": expected.locator.application_id,
        "score_invalidated": invalidated,
    }


def _lock_current_rows(db: Session, *, application_id: int):
    locator = locate_corroboration(db, application_id=application_id)
    if locator is None:
        db.rollback()
        return None, None
    # End the unlocked locator read before the canonical lock sequence.
    db.rollback()
    return locator, lock_corroboration_rows(db, locator=locator)


def _finalize_signals(
    db: Session,
    *,
    expected: CorroborationGeneration,
    lease: CorroborationLease,
    signals: dict[str, Any],
) -> dict[str, Any]:
    _locator, rows = _lock_current_rows(
        db, application_id=expected.locator.application_id
    )
    if rows is None:
        db.rollback()
        return {
            "status": "superseded",
            "application_id": expected.locator.application_id,
        }
    if (
        not generation_is_current(db, rows=rows, expected=expected)
        or not lease_is_current(rows.application, lease=lease)
    ):
        return _finish_superseded(
            db,
            rows=rows,
            expected=expected,
            lease=lease,
        )

    from .fraud_detection import aggregate_triangulation, build_integrity_warnings

    details = copy.deepcopy(rows.application.cv_match_details or {})
    raw_signals = details.get("integrity_signals")
    integrity = copy.deepcopy(raw_signals) if isinstance(raw_signals, dict) else {}
    for key in _SLOW_SIGNAL_KEYS:
        integrity.pop(key, None)
    integrity.update(copy.deepcopy(signals))
    integrity["triangulation"] = aggregate_triangulation(integrity)
    integrity["warnings"] = build_integrity_warnings(integrity)
    details["integrity_signals"] = integrity
    rows.application.cv_match_details = details
    marker_status = "done" if signals else "no_signal"
    update_lease_marker(
        rows.application,
        lease=lease,
        status=marker_status,
    )
    db.commit()
    return {
        "status": "ok" if signals else "no_signal",
        "application_id": expected.locator.application_id,
        "verdict": integrity["triangulation"].get("verdict"),
        "triangulation": integrity["triangulation"],
    }


def _mark_retry_wait(
    db: Session,
    *,
    expected: CorroborationGeneration,
    lease: CorroborationLease,
) -> None:
    try:
        db.rollback()
        _locator, rows = _lock_current_rows(
            db, application_id=expected.locator.application_id
        )
        if (
            rows is not None
            and generation_is_current(db, rows=rows, expected=expected)
            and lease_is_current(rows.application, lease=lease)
        ):
            update_lease_marker(
                rows.application,
                lease=lease,
                status="retry_wait",
                error_code="provider_enrichment_failed",
            )
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        logger.exception(
            "failed to persist corroboration retry lease app=%s",
            expected.locator.application_id,
        )


def run_corroboration_enrichment(
    db: Session,
    *,
    application_id: int,
    expected_generation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Claim, compute and commit one exact enrichment generation."""
    expected = (
        CorroborationGeneration.from_payload(expected_generation)
        if expected_generation is not None
        else None
    )
    if expected_generation is not None and expected is None:
        return {"status": "invalid_generation", "application_id": application_id}

    locator, rows = _lock_current_rows(db, application_id=application_id)
    if rows is None or locator is None:
        db.rollback()
        return {"status": "missing", "application_id": application_id}
    if expected is not None and locator != expected.locator:
        db.rollback()
        return {"status": "superseded", "application_id": application_id}
    expected = _current_or_captured_generation(
        db,
        rows=rows,
        expected=expected,
    )
    if expected is None:
        db.rollback()
        return {"status": "missing_generation", "application_id": application_id}
    if not generation_is_current(db, rows=rows, expected=expected):
        return _finish_superseded(db, rows=rows, expected=expected)
    if not should_enrich(rows.application):
        db.rollback()
        return {"status": "skipped", "application_id": application_id}

    claim = claim_generation_lease(rows.application, generation=expected)
    if claim.status != "claimed" or claim.lease is None:
        if claim.status == "retry_exhausted":
            db.commit()
        else:
            db.rollback()
        return {
            "status": claim.status,
            "application_id": application_id,
            "retry_after_seconds": claim.retry_after_seconds,
        }
    inputs = capture_corroboration_inputs(rows)
    lease = claim.lease
    db.commit()  # release the lease locks before every external call

    try:
        signals = _compute_signals(inputs, db)
        # GRAPH_OUTCOME_PRIOR may have opened a read transaction. End it before
        # re-entering Organization -> Role -> Candidate -> Application locks.
        db.rollback()
        return _finalize_signals(
            db,
            expected=expected,
            lease=lease,
            signals=signals,
        )
    except Exception:  # fail open, but retain one bounded recovery attempt
        logger.warning(
            "corroboration enrichment failed for app=%s",
            application_id,
            exc_info=True,
        )
        _mark_retry_wait(db, expected=expected, lease=lease)
        return {
            "status": "retry_wait",
            "application_id": application_id,
            "retry_after_seconds": 60,
        }


__all__ = [
    "capture_corroboration_generation",
    "run_corroboration_enrichment",
    "should_enrich",
]
