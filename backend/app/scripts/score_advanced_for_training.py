"""Score late-stage `advanced` candidates for model-refinement training data.

Candidates who have left Tali's flow (`pipeline_stage == 'advanced'`) are
normally frozen — no scoring (see role_support.is_resolved + the enqueue_score
guard). But for the highest-signal late stages (Workable "final interview" /
"offer") we DO want a Tali prediction on record, so that when their outcome
lands (hired/rejected) there is a (score, outcome) pair to calibrate against.

This is a deliberate, targeted one-off. It:
  * selects advanced (sync-advanced, still open) applications whose current
    Workable stage is in the target set and which have NO cv_match_score yet;
  * runs the exact production scoring core (``_execute_scoring_v3`` +
    ``refresh_application_score_cache``) with ``force_full_score=True``,
    bypassing the resolved-freeze guard on purpose;
  * is PURE scoring — it writes the score fields and goes through the metered
    Anthropic client, but emits no agent decisions, emails, or Workable changes.

It intentionally does NOT build interview-support packs (an extra Haiku call we
don't need for training).

Candidates with no stored CV text get their CV fetched from Workable first
(the resolved-freeze means the normal sync won't fetch it). The fetched CV is
committed before scoring so a scoring error doesn't discard it. Candidates whose
Workable CV fetch fails are reported and skipped.

Run this in the prod environment (e.g. ``railway ssh``) so the Workable + Anthropic
calls have stable connectivity — high-criteria scores can time out from a laptop.

Usage::

    python -m app.scripts.score_advanced_for_training            # dry run
    python -m app.scripts.score_advanced_for_training --apply    # score for real
    python -m app.scripts.score_advanced_for_training --apply --stages final_interview,offer
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.cv_score_job import CvScoreJob, SCORE_JOB_PENDING
from ..domains.assessments_runtime.pipeline_service import normalize_pipeline_key

logger = logging.getLogger(__name__)

def _target_stages_from_argv(argv: list[str]) -> frozenset[str] | None:
    """Return the stage filter, or None to cover ALL `advanced` candidates."""
    for i, arg in enumerate(argv):
        if arg == "--stages" and i + 1 < len(argv):
            return frozenset(normalize_pipeline_key(s) for s in argv[i + 1].split(",") if s.strip())
        if arg.startswith("--stages="):
            return frozenset(normalize_pipeline_key(s) for s in arg.split("=", 1)[1].split(",") if s.strip())
    return None


def score_advanced_for_training(
    db: Session,
    *,
    target_stages: frozenset[str] | None = None,
    apply: bool,
    limit: int | None = None,
) -> dict:
    # All `advanced` (= terminal / decided) candidates that lack a Tali score.
    # advanced means the hiring decision is made — a Tali hand-back, or a
    # Workable offer/hired/reject — so scoring them gives the cv_match
    # calibrator a (score -> outcome) training pair: positives (offer/hired)
    # and negatives (reject/disqualify). Already-scored candidates are skipped
    # (cv_match_score NOT NULL), so this never re-scores.
    candidates = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.pipeline_stage == "advanced",
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.cv_match_score.is_(None),
        )
        .all()
    )
    targets = [
        app for app in candidates
        if target_stages is None
        or normalize_pipeline_key(app.workable_stage) in target_stages
    ]
    if limit is not None:
        targets = targets[: max(0, int(limit))]

    summary = {
        "matched": len(targets),
        "scored": 0,
        "cv_fetched": 0,
        "skipped_no_cv": 0,
        "skipped_no_spec": 0,
        "errored": 0,
        "applied": apply,
    }

    # Imported lazily so a dry run needs no scoring/Anthropic imports.
    from ..services.cv_score_orchestrator import _execute_scoring_v3
    from ..domains.assessments_runtime.role_support import refresh_application_score_cache
    from ..domains.assessments_runtime.applications_routes import _try_fetch_cv_from_workable
    from ..models.organization import Organization

    for app in targets:
        role = app.role
        candidate = app.candidate
        cv_text = (app.cv_text or (candidate.cv_text if candidate else "") or "").strip()
        spec = (role.job_spec_text if role else "") or ""
        label = (
            f"application_id={app.id} role_id={app.role_id} candidate_id={app.candidate_id}"
        )

        if not spec.strip():
            summary["skipped_no_spec"] += 1
            logger.info("Advanced training score skipped stage=no_job_spec %s", label)
            continue

        if not cv_text:
            if not apply:
                logger.info("Advanced training score planned stage=fetch_cv_and_score %s", label)
                continue
            # Fetch the CV from Workable and persist it BEFORE scoring, so a
            # later scoring error doesn't roll back the fetched CV.
            org = (
                db.query(Organization)
                .filter(Organization.id == app.organization_id)
                .first()
            )
            fetched = False
            try:
                if org is not None and candidate is not None:
                    fetched = bool(_try_fetch_cv_from_workable(app, candidate, db, org))
            except Exception as exc:  # noqa: BLE001 — report and continue
                logger.error(
                    "Advanced training CV fetch failed application_id=%s error_type=%s",
                    app.id,
                    type(exc).__name__,
                )
                fetched = False
            if fetched:
                db.commit()
                summary["cv_fetched"] += 1
                cv_text = (app.cv_text or (candidate.cv_text if candidate else "") or "").strip()
            else:
                db.rollback()
            if not cv_text:
                summary["skipped_no_cv"] += 1
                logger.info("Advanced training score skipped stage=no_cv %s", label)
                continue

        if not apply:
            logger.info("Advanced training score planned stage=score %s", label)
            continue

        # ``_execute_scoring_v3`` reads ``application.cv_text`` directly. When the
        # CV only lives at the candidate level, copy the resolved text onto the
        # application first, otherwise it's scored as "missing CV" despite the
        # gate above passing on the candidate fallback.
        if not (app.cv_text or "").strip() and cv_text:
            app.cv_text = cv_text

        try:
            job = CvScoreJob(
                application_id=app.id,
                role_id=app.role_id,
                status=SCORE_JOB_PENDING,
            )
            db.add(job)
            db.flush()
            _execute_scoring_v3(db, application=app, job=job, force_full_score=True)
            refresh_application_score_cache(app, db=db)
            db.commit()
            summary["scored"] += 1
            logger.info(
                "Advanced training score complete %s cv_match_score=%s taali_score=%s job_status=%s",
                label,
                app.cv_match_score,
                app.taali_score_cache_100,
                job.status,
            )
        except Exception as exc:  # noqa: BLE001 — report and continue
            db.rollback()
            summary["errored"] += 1
            logger.error(
                "Advanced training scoring failed application_id=%s error_type=%s",
                app.id,
                type(exc).__name__,
            )

    return summary


def main() -> int:
    from ..platform.database import SessionLocal
    from ..platform.logging import setup_logging

    argv = sys.argv[1:]
    apply = "--apply" in argv
    target_stages = _target_stages_from_argv(argv)
    limit = None
    for i, arg in enumerate(argv):
        if arg == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])

    setup_logging()
    db = SessionLocal()
    try:
        stages_label = "ALL advanced" if target_stages is None else sorted(target_stages)
        logger.info(
            "Advanced training score start mode=%s target_stages=%s limit=%s",
            "apply" if apply else "dry_run",
            stages_label,
            limit,
        )
        summary = score_advanced_for_training(
            db, target_stages=target_stages, apply=apply, limit=limit
        )
        logger.info("Advanced training score summary=%s", summary)
        if not apply and summary["matched"]:
            logger.info("Advanced training score dry run; re-run with --apply to score")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
