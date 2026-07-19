"""Pre-screen score calibration — data collection (reject inference).

The pre-screen gate filters candidates below a threshold and never full-scores
them, so we never observe the full score in the rejected region — exactly where
we'd need it to know whether the gate is wrong. This module closes that gap: a
backend-only job takes a RANDOM sample of pre-screen rejects, runs full cv_match
on them in **shadow mode** (the result is stored ONLY in
``prescreen_calibration_samples`` — never on the application, never shown to a
recruiter), and records the ``(pre_screen_score → full_score)`` pair. The
calibrator (part B) trains on these pairs.
"""
from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.prescreen_calibration_sample import PrescreenCalibrationSample
from ..models.role import Role
from ..platform.config import settings
from .provider_error_evidence import safe_provider_error_code

logger = logging.getLogger("taali.prescreen_calibration")
PRESCREEN_SHADOW_SCORE_MAX_LIMIT = 50


def sample_and_shadow_score_rejects(
    db: Session, *, limit: int = 50, organization_id: int | None = None
) -> dict:
    """Shadow-score up to ``limit`` random pre-screen rejects.

    A reject = open, pre-screened, scored below the gate threshold, not yet
    full-scored, and not already sampled. For each we run full cv_match and
    store the result ONLY in ``prescreen_calibration_samples`` — the
    application's ``cv_match_*`` fields are never touched, so nothing surfaces
    to the recruiter. Returns ``{"sampled": int, "scored": int, "failed": int}``.
    """
    from ..cv_matching.runner import run_cv_match
    from ..cv_matching.schemas import ScoringStatus
    from .prescreen_gate_calibration import GATE_CEILING
    from .role_requirement_service import (
        build_scoring_requirements,
        resolve_role_job_spec,
    )

    limit = int(limit)
    if not 1 <= limit <= PRESCREEN_SHADOW_SCORE_MAX_LIMIT:
        raise ValueError(
            f"limit must be between 1 and {PRESCREEN_SHADOW_SCORE_MAX_LIMIT}"
        )
    threshold = float(settings.PRE_SCREEN_THRESHOLD)
    already_sampled = db.query(PrescreenCalibrationSample.application_id)
    q = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            CandidateApplication.cv_match_score.is_(None),  # never full-scored
            CandidateApplication.pre_screen_run_at.isnot(None),  # was pre-screened
            CandidateApplication.cv_match_scored_at.isnot(None),  # gate decided
            CandidateApplication.genuine_pre_screen_score_100.isnot(None),
            CandidateApplication.genuine_pre_screen_score_100
            < max(threshold, float(GATE_CEILING)),
            Role.deleted_at.is_(None),
            ~CandidateApplication.id.in_(already_sampled),
        )
    )
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))
    # Fetch a bounded surplus because a dynamically-lowered stamped threshold
    # may make some rows ineligible after the portable Python-side check.
    rows = q.order_by(func.random()).limit(limit * 5).all()

    sampled = scored = failed = 0
    for app, role in rows:
        if sampled >= limit:
            break
        evidence = app.pre_screen_evidence if isinstance(app.pre_screen_evidence, dict) else {}
        try:
            enforced_threshold = float(
                evidence.get("gate_threshold_enforced", threshold)
            )
        except (TypeError, ValueError):
            enforced_threshold = threshold
        genuine_score = float(app.genuine_pre_screen_score_100)
        if genuine_score >= enforced_threshold:
            continue
        sampled += 1
        cv_text = (app.cv_text or "").strip()
        jd_text = resolve_role_job_spec(
            role,
            db=db,
            agent_name="cv_scoring",
        )
        if not cv_text or not jd_text:
            failed += 1
            continue
        requirements = build_scoring_requirements(role)

        metering = (
            {
                "organization_id": int(app.organization_id),
                "role_id": int(role.id),
                "entity_id": f"application:{app.id}",
            }
            if getattr(app, "organization_id", None)
            else None
        )
        # Shadow-score with the SAME engine the org uses in prod, so the
        # (pre_screen -> full_score) pair the gate calibrator trains on matches
        # what survivors actually get. Orgs default to the holistic Sonnet engine
        # (2026-06); a Haiku-v18 shadow score would mis-train the gate cut.
        try:
            workable_context = ""
            try:
                from .workable_context_service import format_workable_context

                workable_context = (
                    format_workable_context(
                        candidate=getattr(app, "candidate", None), application=app
                    )
                    or ""
                )
            except Exception:  # pragma: no cover — proceed without context
                workable_context = ""
            from .cv_score_orchestrator import _holistic_enabled_for

            if _holistic_enabled_for(app):
                from ..cv_matching.holistic import run_holistic_match
                from .claude_client_resolver import get_client_for_org

                org_client = get_client_for_org(getattr(app, "organization", None))
                out = run_holistic_match(
                    cv_text,
                    jd_text,
                    client=org_client,
                    metering_context=metering,
                    workable_context=workable_context or None,
                )
            else:
                out = run_cv_match(
                    cv_text,
                    jd_text,
                    requirements,
                    metering_context=metering,
                    workable_context=workable_context or None,
                )
        except Exception as exc:  # pragma: no cover — runners shouldn't raise
            logger.warning(
                "shadow score failed app_id=%s error_code=%s",
                app.id,
                safe_provider_error_code(exc, operation="prescreen_shadow_score"),
            )
            failed += 1
            continue

        ok = out.scoring_status == ScoringStatus.OK
        raw_pre_score = evidence.get("llm_score_100")
        if raw_pre_score is None:
            raw_pre_score = genuine_score
        db.add(
            PrescreenCalibrationSample(
                organization_id=int(app.organization_id),
                role_id=int(role.id),
                application_id=int(app.id),
                pre_screen_score=float(raw_pre_score),
                full_cv_match_score=out.role_fit_score if ok else None,
                full_recommendation=(
                    getattr(out.recommendation, "value", str(out.recommendation or "")) if ok else None
                ),
                scoring_status="ok" if ok else "failed",
            )
        )
        # Shadow-only: deliberately do NOT write app.cv_match_score/details.
        db.commit()  # per-row so one failure doesn't lose the batch
        scored += 1 if ok else 0
        failed += 0 if ok else 1
    return {"sampled": sampled, "scored": scored, "failed": failed}
