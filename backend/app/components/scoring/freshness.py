"""Decision safety checks for persisted candidate scores."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...models.cv_score_job import CvScoreJob, SCORE_JOB_DONE
from ...models.role import ROLE_KIND_STANDARD


SCORE_BACKED_STANDARD_DECISION_TYPES = frozenset(
    {
        "advance_to_interview",
        "reject",
        "skip_assessment_reject",
        "send_assessment",
        "escalate_low_confidence",
    }
)

_ROLE_INTENT_CACHE_PREFIX = "role-intent:"


@dataclass(frozen=True)
class ScoreAttempt:
    """Latest durable score attempt and its generation provenance."""

    application_id: int
    job_id: int
    status: str
    role_id: int | None
    cache_key: str | None


def _score_attempt_provenance_matches(
    attempt: ScoreAttempt,
    *,
    application_id: int,
    role_id: int,
    role_intent_fingerprint: str,
) -> bool:
    """Validate the attempt's application, role, and modern cache key.

    Rows whose cache key starts with ``role-intent:`` are modern and must carry
    both an exact Role foreign key and the exact generation fingerprint.
    Missing or older non-role-intent cache keys remain a bounded compatibility
    path, but an explicitly different Role is never accepted.
    """
    if int(attempt.application_id) != int(application_id):
        return False
    if attempt.role_id is not None and int(attempt.role_id) != int(role_id):
        return False
    cache_key = str(attempt.cache_key or "")
    if not cache_key.startswith(_ROLE_INTENT_CACHE_PREFIX):
        return True
    return bool(
        attempt.role_id is not None
        and int(attempt.role_id) == int(role_id)
        and cache_key.removeprefix(_ROLE_INTENT_CACHE_PREFIX)
        == str(role_intent_fingerprint)
    )


@dataclass(frozen=True)
class ScoreGenerationToken:
    """Exact standard-role score generation consumed by a verdict.

    ``job_id=None`` is an explicit, bounded legacy generation.  A missing
    token is represented by ``None`` at call sites and is never equivalent.
    """

    application_id: int
    role_id: int
    job_id: int | None
    role_intent_fingerprint: str

    def as_fingerprint(self) -> dict[str, Any]:
        return {
            "application_id": int(self.application_id),
            "role_id": int(self.role_id),
            "job_id": int(self.job_id) if self.job_id is not None else None,
            "role_intent_fingerprint": str(self.role_intent_fingerprint),
        }


def score_generation_from_fingerprint(
    fingerprint: Any,
) -> ScoreGenerationToken | None:
    """Rehydrate a token persisted in ``AgentDecision.input_fingerprint``."""
    if not isinstance(fingerprint, dict):
        return None
    payload = fingerprint.get("score_generation")
    if not isinstance(payload, dict):
        return None
    try:
        job_id = payload.get("job_id")
        return ScoreGenerationToken(
            application_id=int(payload["application_id"]),
            role_id=int(payload["role_id"]),
            job_id=int(job_id) if job_id is not None else None,
            role_intent_fingerprint=str(payload["role_intent_fingerprint"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def latest_score_attempts(
    db: Session, application_ids: Iterable[int]
) -> dict[int, ScoreAttempt]:
    """Return each application's latest score attempt in one query.

    ``CvScoreJob`` is append-only, so its primary key is the causal attempt
    order. ``queued_at`` is intentionally excluded: Python and database
    timestamp writers have different precision and can invert adjacent rows.
    """
    ids = list(dict.fromkeys(int(application_id) for application_id in application_ids))
    if not ids:
        return {}
    row_number = (
        func.row_number()
        .over(
            partition_by=CvScoreJob.application_id,
            order_by=CvScoreJob.id.desc(),
        )
        .label("rn")
    )
    ranked = (
        db.query(
            CvScoreJob.id,
            CvScoreJob.application_id,
            CvScoreJob.status,
            CvScoreJob.role_id,
            CvScoreJob.cache_key,
            row_number,
        )
        .filter(CvScoreJob.application_id.in_(ids))
        .subquery()
    )
    rows = (
        db.query(
            ranked.c.id,
            ranked.c.application_id,
            ranked.c.status,
            ranked.c.role_id,
            ranked.c.cache_key,
        )
        .filter(ranked.c.rn == 1)
        .all()
    )
    return {
        int(application_id): ScoreAttempt(
            application_id=int(application_id),
            job_id=int(job_id),
            status=str(status),
            role_id=int(role_id) if role_id is not None else None,
            cache_key=str(cache_key) if cache_key is not None else None,
        )
        for job_id, application_id, status, role_id, cache_key in rows
    }


def application_has_persisted_score(application: Any) -> bool:
    """Whether a no-job legacy row contains an actual persisted score.

    ``no CvScoreJob`` predates the append-only attempt ledger and is allowed
    only when there is a numeric pre-screen/CV score to consume. A truly cold
    application must use the canonical scorer; letting an ephemeral policy
    call decide it would have no durable generation marker to fence against a
    concurrent RoleIntent edit.
    """
    return any(
        getattr(application, field, None) is not None
        for field in (
            "pre_screen_score_100",
            "genuine_pre_screen_score_100",
            "cv_match_score",
            "role_fit_score_cache_100",
        )
    )


def capture_score_generations(
    db: Session,
    *,
    role: Any,
    application_ids: Iterable[int],
) -> dict[int, ScoreGenerationToken]:
    """Capture eligible generations before callers hydrate verdict inputs.

    The attempt ledger is read first.  This ordering makes every concurrent
    change fail safe: an application refreshed after this snapshot can only
    produce a token mismatch at the later locked boundary, never relabel an
    old verdict as belonging to the new generation.
    """
    ids = list(dict.fromkeys(int(application_id) for application_id in application_ids))
    if not ids or getattr(role, "id", None) is None:
        return {}
    attempts = latest_score_attempts(db, ids)
    from ...services.role_intent_fingerprint import role_intent_fingerprint

    role_fingerprint = role_intent_fingerprint(role, db=db)
    role_id = int(role.id)
    tokens: dict[int, ScoreGenerationToken] = {}
    for application_id, attempt in attempts.items():
        if attempt.status != SCORE_JOB_DONE or not _score_attempt_provenance_matches(
            attempt,
            application_id=application_id,
            role_id=role_id,
            role_intent_fingerprint=role_fingerprint,
        ):
            continue
        tokens[application_id] = ScoreGenerationToken(
            application_id=application_id,
            role_id=role_id,
            job_id=attempt.job_id,
            role_intent_fingerprint=role_fingerprint,
        )
    legacy_ids = [
        application_id for application_id in ids if application_id not in attempts
    ]
    if legacy_ids:
        from ...models.candidate_application import CandidateApplication

        rows = (
            db.query(
                CandidateApplication.id,
                CandidateApplication.pre_screen_score_100,
                CandidateApplication.genuine_pre_screen_score_100,
                CandidateApplication.cv_match_score,
                CandidateApplication.role_fit_score_cache_100,
            )
            .filter(CandidateApplication.id.in_(legacy_ids))
            .all()
        )
        for row in rows:
            if any(value is not None for value in row[1:]):
                application_id = int(row[0])
                tokens[application_id] = ScoreGenerationToken(
                    application_id=application_id,
                    role_id=role_id,
                    job_id=None,
                    role_intent_fingerprint=role_fingerprint,
                )
    return tokens


def capture_score_generation(
    db: Session, *, role: Any, application_id: int
) -> ScoreGenerationToken | None:
    """Singular wrapper for producer paths."""
    return capture_score_generations(
        db,
        role=role,
        application_ids=[int(application_id)],
    ).get(int(application_id))


def score_generation_is_current(
    db: Session,
    *,
    expected: ScoreGenerationToken | None,
    locked_role: Any,
    application: Any,
) -> bool:
    """Validate an eligible token while the caller holds Role then App locks."""
    current_attempt = (
        latest_score_attempts(db, [int(application.id)]).get(int(application.id))
        if getattr(application, "id", None) is not None
        else None
    )
    return score_generation_matches_observed(
        db,
        expected=expected,
        role=locked_role,
        application=application,
        current_attempt=current_attempt,
    )


def score_generation_matches_observed(
    db: Session,
    *,
    expected: ScoreGenerationToken | None,
    role: Any,
    application: Any,
    current_attempt: ScoreAttempt | None,
    current_role_intent_fingerprint: str | None = None,
) -> bool:
    """Compare a token with a caller's already-observed latest attempt."""
    if expected is None:
        return False
    if (
        getattr(application, "id", None) is None
        or getattr(application, "role_id", None) is None
        or int(application.id) != int(expected.application_id)
        or int(application.role_id) != int(expected.role_id)
        or getattr(role, "id", None) is None
        or int(role.id) != int(expected.role_id)
    ):
        return False
    if expected.job_id is None:
        attempt_matches = current_attempt is None and application_has_persisted_score(
            application
        )
    else:
        attempt_matches = bool(
            current_attempt is not None
            and current_attempt.job_id == int(expected.job_id)
            and current_attempt.status == SCORE_JOB_DONE
            and _score_attempt_provenance_matches(
                current_attempt,
                application_id=int(expected.application_id),
                role_id=int(expected.role_id),
                role_intent_fingerprint=str(expected.role_intent_fingerprint),
            )
        )
    if not attempt_matches:
        return False
    from ...services.role_intent_fingerprint import role_intent_fingerprint

    current_fingerprint = current_role_intent_fingerprint or role_intent_fingerprint(
        role, db=db
    )
    return current_fingerprint == str(expected.role_intent_fingerprint)


def application_score_status_allows_decision(
    application: Any, status: str | None
) -> bool:
    """Apply latest-attempt freshness plus the bounded legacy exception."""
    if status is not None:
        return status == SCORE_JOB_DONE
    return application_has_persisted_score(application)


def application_scores_allow_decision(
    db: Session,
    application_id: int,
    *,
    application: Any | None = None,
    role: Any | None = None,
) -> bool:
    """Whether persisted scores and their provenance may drive a decision."""
    if application is None:
        from ...models.candidate_application import CandidateApplication

        application = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == int(application_id))
            .one_or_none()
        )
    if application is None:
        return False
    if (
        getattr(application, "id", None) is None
        or int(application.id) != int(application_id)
    ):
        return False

    attempt = latest_score_attempts(db, [int(application_id)]).get(
        int(application_id)
    )
    if attempt is None:
        return application_has_persisted_score(application)
    if (
        attempt.status != SCORE_JOB_DONE
        or getattr(application, "role_id", None) is None
    ):
        return False

    if role is None:
        from ...models.role import Role

        role = (
            db.query(Role)
            .filter(Role.id == int(application.role_id))
            .one_or_none()
        )
    if role is None or getattr(role, "id", None) is None:
        return False
    if int(role.id) != int(application.role_id):
        return False

    from ...services.role_intent_fingerprint import role_intent_fingerprint

    return bool(
        _score_attempt_provenance_matches(
            attempt,
            application_id=int(application_id),
            role_id=int(role.id),
            role_intent_fingerprint=role_intent_fingerprint(role, db=db),
        )
    )


def standard_owner_score_guard_applies(
    *,
    application_role_id: int,
    decision_role_id: int,
    role_kind: str | None,
    decision_type: str,
) -> bool:
    """Whether a decision consumes the standard application's score state.

    Related-role decisions have their own ``SisterRoleEvaluation`` lifecycle
    and must not be blocked by the ATS owner's ``CvScoreJob`` rows.
    """
    return bool(
        int(application_role_id) == int(decision_role_id)
        and str(role_kind or ROLE_KIND_STANDARD) == ROLE_KIND_STANDARD
        and str(decision_type) in SCORE_BACKED_STANDARD_DECISION_TYPES
    )


__all__ = [
    "capture_score_generation",
    "capture_score_generations",
    "application_scores_allow_decision",
    "application_has_persisted_score",
    "application_score_status_allows_decision",
    "latest_score_attempts",
    "SCORE_BACKED_STANDARD_DECISION_TYPES",
    "ScoreAttempt",
    "ScoreGenerationToken",
    "score_generation_from_fingerprint",
    "score_generation_is_current",
    "score_generation_matches_observed",
    "standard_owner_score_guard_applies",
]
