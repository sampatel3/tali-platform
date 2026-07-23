"""Immutable input snapshots persisted on queued agent decisions."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from ..components.scoring.freshness import ScoreGenerationToken


def capture_input_fingerprint(
    db: Session,
    *,
    application_id: int,
    role_id: int,
    evidence: dict[str, Any] | None = None,
    score_generation: ScoreGenerationToken | None = None,
) -> tuple[dict, str | None, str | None]:
    """Snapshot cited inputs and indexed criteria/CV fingerprints."""
    try:
        from ..models.candidate_application import CandidateApplication
        from ..models.role import Role
        from ..models.role_feedback_note import RoleFeedbackNote
        from .decision_staleness import criteria_content_fingerprint

        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == application_id)
            .one_or_none()
        )
        role = db.query(Role).filter(Role.id == role_id).one_or_none()
        if app is None or role is None:
            return ({}, None, None)

        criteria_fp = criteria_content_fingerprint(db, int(role_id))
        cv_text = (app.cv_text or "").strip()
        cv_fp = hashlib.sha256(cv_text.encode("utf-8")).hexdigest() if cv_text else None
        last_note_id = (
            db.query(RoleFeedbackNote.id)
            .filter(RoleFeedbackNote.role_id == role_id)
            .order_by(RoleFeedbackNote.id.desc())
            .first()
        )

        def _to_float(value):
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        role_fit_score = getattr(app, "cv_match_score", None)
        pre_screen_score = getattr(app, "pre_screen_score_100", None)
        assessment_score = getattr(app, "assessment_score_cache_100", None)
        taali_score = getattr(app, "taali_score_cache_100", None)
        pre_screen_cutoff = getattr(role, "pre_screen_cutoff_score_100", None)
        from .related_role_application_runtime import (
            related_role_evaluation_for_application,
        )

        # Physical row ownership is not the logical-role boundary. A candidate
        # may apply directly to a related role, making ``app.role_id`` equal to
        # ``role_id`` while the live SisterRoleEvaluation still owns that
        # role's score, stage, and freshness lifecycle.
        evaluation = related_role_evaluation_for_application(
            db,
            role_id=int(role_id),
            application=app,
        )
        if evaluation is not None:
            role_fit_score = getattr(evaluation, "role_fit_score", None)
            frozen = evidence if isinstance(evidence, dict) else {}
            role_fit_score = frozen.get("role_fit_score", role_fit_score)
            assessment_score = frozen.get("assessment_score")
            taali_score = frozen.get(
                "taali_score",
                assessment_score if assessment_score is not None else role_fit_score,
            )
            pre_screen_score = None
            pre_screen_cutoff = None
            evaluation_cv_fp = (
                frozen.get("evaluation_cv_fingerprint")
                or getattr(evaluation, "cv_fingerprint", None)
            )
            if evaluation_cv_fp:
                cv_fp = str(evaluation_cv_fp)

        fingerprint = {
            "criteria_fingerprint": criteria_fp,
            "cv_fingerprint": cv_fp,
            "cv_uploaded_at": (
                app.cv_uploaded_at.isoformat()
                if getattr(app, "cv_uploaded_at", None) is not None
                else None
            ),
            "pre_screen_score_at_emit": _to_float(pre_screen_score),
            "assessment_score_at_emit": _to_float(assessment_score),
            "cv_match_score_at_emit": _to_float(role_fit_score),
            "taali_score_at_emit": _to_float(taali_score),
            "pre_screen_cutoff_at_emit": _to_float(pre_screen_cutoff),
            "last_recruiter_note_id": int(last_note_id[0]) if last_note_id else None,
        }
        if score_generation is not None:
            fingerprint["score_generation"] = score_generation.as_fingerprint()
        return (fingerprint, criteria_fp, cv_fp)
    except Exception:
        # Preserve the already-validated score token even if auxiliary capture
        # fails; later approval must not lose the exact generation boundary.
        logging.getLogger("taali.actions.queue_decision").warning(
            "input fingerprint capture failed for app=%s role=%s",
            application_id,
            role_id,
            exc_info=True,
        )
        fallback = (
            {"score_generation": score_generation.as_fingerprint()}
            if score_generation is not None
            else {}
        )
        return (fallback, None, None)


__all__ = ["capture_input_fingerprint"]
