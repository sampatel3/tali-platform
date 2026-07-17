"""State transitions for one persisted related-role evaluation."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from ..models.sister_role_evaluation import (
    SISTER_EVAL_PENDING,
    SISTER_EVAL_UNSCORABLE,
    SisterRoleEvaluation,
)


def archive_evaluation_result(evaluation: SisterRoleEvaluation) -> None:
    if (
        evaluation.scored_at is None
        and evaluation.role_fit_score is None
        and not evaluation.details
    ):
        return
    history = list(evaluation.history or [])
    snapshot = {
        "status": evaluation.status,
        "role_fit_score": evaluation.role_fit_score,
        "summary": evaluation.summary,
        "spec_fingerprint": evaluation.spec_fingerprint,
        "cv_fingerprint": evaluation.cv_fingerprint,
        "model_version": evaluation.model_version,
        "prompt_version": evaluation.prompt_version,
        "trace_id": evaluation.trace_id,
        "cache_hit": bool(evaluation.cache_hit),
        "scored_at": (
            evaluation.scored_at.isoformat() if evaluation.scored_at else None
        ),
    }
    artifact_fields = (
        "role_fit_score",
        "summary",
        "spec_fingerprint",
        "cv_fingerprint",
        "model_version",
        "prompt_version",
        "trace_id",
        "scored_at",
    )
    if history and all(
        history[-1].get(key) == snapshot.get(key) for key in artifact_fields
    ):
        return
    history.append(snapshot)
    evaluation.history = history[-20:]


def reset_evaluation_for_rescore(
    evaluation: SisterRoleEvaluation,
    *,
    role_id: int,
    application_id: int,
    cv_text: str,
    job_spec: str,
) -> bool:
    """Invalidate exactly one role score; return whether it can be dispatched."""

    if (
        int(evaluation.role_id) != int(role_id)
        or int(evaluation.source_application_id) != int(application_id)
    ):
        raise ValueError("Related evaluation does not match role/application")
    cv_text = str(cv_text or "").strip()
    job_spec = str(job_spec or "").strip()
    archive_evaluation_result(evaluation)
    evaluation.spec_fingerprint = _text_fingerprint(job_spec)
    evaluation.cv_fingerprint = _text_fingerprint(cv_text) if cv_text else None
    evaluation.role_fit_score = None
    evaluation.summary = None
    evaluation.details = None
    evaluation.cache_hit = False
    evaluation.attempts = 0
    evaluation.next_attempt_at = None
    evaluation.dispatch_attempted_at = None
    evaluation.last_error_code = None
    evaluation.queued_at = datetime.now(timezone.utc)
    evaluation.started_at = None
    evaluation.scored_at = None
    if not cv_text or not job_spec:
        evaluation.status = SISTER_EVAL_UNSCORABLE
        evaluation.error_message = (
            "No CV text available" if not cv_text else "No job specification available"
        )
        return False
    evaluation.status = SISTER_EVAL_PENDING
    evaluation.error_message = None
    return True


def _text_fingerprint(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


__all__ = ["archive_evaluation_result", "reset_evaluation_for_rescore"]
