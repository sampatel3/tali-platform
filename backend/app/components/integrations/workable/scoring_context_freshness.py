"""Keep stored scores honest when Workable prompt context changes.

This module deliberately separates invalidation from re-scoring. A sync may
mark an existing result stale, but it never dispatches provider work. The
application-owned digest also avoids the historical multi-role loop caused by
the shared ``Candidate`` snapshot alternating between Workable applications.
"""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from ....models.candidate import Candidate
from ....models.candidate_application import CandidateApplication
from ....services.workable_context_service import format_workable_context


SCORING_CONTEXT_DIGEST_KEY = "workable_scoring_context_digest"
_DIGEST_PREFIX = "v1:"
_CONTEXT_SCORE_FIELDS = (
    "pre_screen_score_100",
    "genuine_pre_screen_score_100",
    "requirements_fit_score_100",
    "cv_match_score",
    "role_fit_score_cache_100",
)


def find_application_for_candidate(
    db: Session,
    *,
    candidate: Candidate,
    organization_id: int,
    role_id: int,
) -> CandidateApplication | None:
    """Return the canonical role application for an already-persisted candidate."""
    if candidate.id is None:
        return None
    return (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.candidate_id == candidate.id,
            CandidateApplication.role_id == role_id,
        )
        .first()
    )


def rendered_workable_scoring_context_digest(
    candidate: Candidate,
    application: CandidateApplication,
) -> str:
    """Hash the exact normalized text injected into scoring prompts."""
    rendered = format_workable_context(candidate, application)
    return _DIGEST_PREFIX + hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def prior_workable_scoring_context_digest(
    candidate: Candidate,
    application: CandidateApplication | None,
) -> str | None:
    """Return this application's prior context, isolated from shared-row churn."""
    if application is None:
        return None
    state = (
        application.integration_sync_state
        if isinstance(application.integration_sync_state, dict)
        else {}
    )
    stored = state.get(SCORING_CONTEXT_DIGEST_KEY)
    if (
        isinstance(stored, str)
        and stored.startswith(_DIGEST_PREFIX)
        and len(stored) == len(_DIGEST_PREFIX) + 64
    ):
        return stored
    return rendered_workable_scoring_context_digest(candidate, application)


def find_application_and_prior_context(
    db: Session,
    *,
    candidate: Candidate,
    organization_id: int,
    role_id: int,
) -> tuple[CandidateApplication | None, str | None]:
    """Resolve the canonical application before Workable mutates prompt inputs."""
    application = find_application_for_candidate(
        db,
        candidate=candidate,
        organization_id=organization_id,
        role_id=role_id,
    )
    return application, prior_workable_scoring_context_digest(candidate, application)


def capture_workable_cv_snapshot(
    db: Session, candidate: Candidate, organization_id: int
):
    """Capture existing effective CV inputs before a Workable upsert."""
    from ....services.candidate_cv_input_lifecycle import (
        capture_candidate_cv_input_snapshot,
    )

    return capture_candidate_cv_input_snapshot(
        db, candidate=candidate, organization_id=int(organization_id)
    )


def hold_changed_workable_cv_inputs(db: Session, candidate: Candidate, before) -> None:
    """Persist passive CV staleness without authorising related-role spend."""
    from ....services.candidate_cv_input_lifecycle import (
        invalidate_changed_candidate_cv_inputs,
    )

    invalidate_changed_candidate_cv_inputs(
        db, candidate=candidate, before=before, reason="workable_cv_changed"
    )


def invalidate_scores_for_workable_context_change(
    db: Session,
    *,
    candidate: Candidate,
    application: CandidateApplication,
    prior_digest: str | None,
    created_application: bool,
) -> str:
    """Invalidate a scored existing app iff its rendered context changed.

    The returned digest is persisted with this application's sync state. Raw
    payload differences that normalize to identical prompt text are no-ops.
    """
    current_digest = rendered_workable_scoring_context_digest(
        candidate, application
    )
    has_context_score = any(
        getattr(application, field, None) is not None
        for field in _CONTEXT_SCORE_FIELDS
    )
    context_changed = bool(
        not created_application
        and prior_digest is not None
        and prior_digest != current_digest
    )
    if context_changed and has_context_score:
        # Lazy import keeps the Workable integration boundary acyclic.
        from ....services.cv_score_orchestrator import (
            mark_application_scores_stale,
        )

        mark_application_scores_stale(
            db,
            int(application.id),
            reason="workable_context_changed",
        )
    return current_digest


__all__ = [
    "SCORING_CONTEXT_DIGEST_KEY",
    "capture_workable_cv_snapshot",
    "find_application_and_prior_context",
    "find_application_for_candidate",
    "invalidate_scores_for_workable_context_change",
    "hold_changed_workable_cv_inputs",
    "prior_workable_scoring_context_digest",
    "rendered_workable_scoring_context_digest",
]
