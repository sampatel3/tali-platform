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

logger = logging.getLogger("taali.prescreen_calibration")


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
    from ..cv_matching.schemas import Priority, RequirementInput, ScoringStatus

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
            CandidateApplication.pre_screen_score_100.isnot(None),
            CandidateApplication.pre_screen_score_100 < threshold,  # a reject
            Role.deleted_at.is_(None),
            ~CandidateApplication.id.in_(already_sampled),
        )
    )
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))
    rows = q.order_by(func.random()).limit(int(limit)).all()

    sampled = scored = failed = 0
    for app, role in rows:
        sampled += 1
        cv_text = (app.cv_text or "").strip()
        jd_text = (role.job_spec_text or "").strip()
        if not cv_text or not jd_text:
            failed += 1
            continue
        requirements = []
        for c in sorted((role.criteria or []), key=lambda c: getattr(c, "ordering", 0)):
            if getattr(c, "deleted_at", None) is not None:
                continue
            text = str(c.text or "").strip()
            if not text:
                continue
            bucket = str(getattr(c, "bucket", None) or ("must" if bool(c.must_have) else "preferred"))
            priority = Priority.MUST_HAVE if bucket in ("must", "constraint") else Priority.STRONG_PREFERENCE
            requirements.append(RequirementInput(id=f"crit_{int(c.id)}", requirement=text, priority=priority))

        metering = (
            {
                "organization_id": int(app.organization_id),
                "role_id": int(role.id),
                "entity_id": f"application:{app.id}",
            }
            if getattr(app, "organization_id", None)
            else None
        )
        try:
            out = run_cv_match(cv_text, jd_text, requirements, metering_context=metering)
        except Exception:  # pragma: no cover — run_cv_match shouldn't raise
            logger.exception("shadow cv_match raised for app=%s", app.id)
            failed += 1
            continue

        ok = out.scoring_status == ScoringStatus.OK
        evidence = app.pre_screen_evidence if isinstance(app.pre_screen_evidence, dict) else {}
        db.add(
            PrescreenCalibrationSample(
                organization_id=int(app.organization_id),
                role_id=int(role.id),
                application_id=int(app.id),
                pre_screen_score=evidence.get("llm_score_100"),
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
