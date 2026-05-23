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

_DEFAULT_TARGET_STAGES = frozenset({"final_interview", "offer"})


def _target_stages_from_argv(argv: list[str]) -> frozenset[str]:
    for i, arg in enumerate(argv):
        if arg == "--stages" and i + 1 < len(argv):
            return frozenset(normalize_pipeline_key(s) for s in argv[i + 1].split(",") if s.strip())
        if arg.startswith("--stages="):
            return frozenset(normalize_pipeline_key(s) for s in arg.split("=", 1)[1].split(",") if s.strip())
    return _DEFAULT_TARGET_STAGES


def score_advanced_for_training(db: Session, *, target_stages: frozenset[str], apply: bool) -> dict:
    candidates = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.pipeline_stage == "advanced",
            CandidateApplication.pipeline_stage_source == "sync",
            CandidateApplication.application_outcome == "open",
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.cv_match_score.is_(None),
        )
        .all()
    )
    targets = [
        app for app in candidates
        if normalize_pipeline_key(app.workable_stage) in target_stages
    ]

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
            f"app_id={app.id} role_id={app.role_id} candidate_id={app.candidate_id} "
            f"stage={app.workable_stage!r}"
        )

        if not spec.strip():
            summary["skipped_no_spec"] += 1
            print(f"  SKIP (no job spec) {label}", flush=True)
            continue

        if not cv_text:
            if not apply:
                print(f"  would fetch CV + score {label}", flush=True)
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
            except Exception:  # noqa: BLE001 — report and continue
                logger.exception("CV fetch failed for app_id=%s", app.id)
                fetched = False
            if fetched:
                db.commit()
                summary["cv_fetched"] += 1
                cv_text = (app.cv_text or (candidate.cv_text if candidate else "") or "").strip()
            else:
                db.rollback()
            if not cv_text:
                summary["skipped_no_cv"] += 1
                print(f"  SKIP (no CV; Workable fetch failed) {label}", flush=True)
                continue

        if not apply:
            print(f"  would score {label}", flush=True)
            continue

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
            print(
                f"  SCORED {label} cv_match_score={app.cv_match_score} "
                f"taali={app.taali_score_cache_100} job_status={job.status}",
                flush=True,
            )
        except Exception:  # noqa: BLE001 — report and continue
            db.rollback()
            summary["errored"] += 1
            logger.exception("scoring failed for app_id=%s", app.id)
            print(f"  ERROR {label} (see traceback in logs)", flush=True)

    return summary


def main() -> int:
    from ..platform.database import SessionLocal

    argv = sys.argv[1:]
    apply = "--apply" in argv
    target_stages = _target_stages_from_argv(argv)

    db = SessionLocal()
    try:
        print(
            f"[score_advanced_for_training] mode={'APPLY' if apply else 'DRY-RUN'} "
            f"target_stages={sorted(target_stages)}",
            flush=True,
        )
        summary = score_advanced_for_training(db, target_stages=target_stages, apply=apply)
        print(f"[score_advanced_for_training] {summary}", flush=True)
        if not apply and summary["matched"]:
            print("  (dry run — re-run with --apply to score)", flush=True)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
