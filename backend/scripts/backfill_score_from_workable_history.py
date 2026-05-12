"""Backfill: score historical Workable post-handover candidates.

Background: Workable holds the recruiter's actual decisions. Every
candidate the recruiter has moved past initial screening (Technical
Interview, Final Interview, Offer, plus any custom advanced stages
like "First stage", "Technical", "Presentation") is high-signal
training data for our scoring models — yet many of these were
imported before Tali was wired up to the role and never received a
Taali score.

This script:
  1. Finds candidates in the post-handover Workable cohort
     (excludes "Applied" and "Phone Screen" by default).
  2. Fetches the CV from Workable when missing.
  3. Runs the standard CV-score orchestrator with
     ``force_full_score=True`` so we get a full v4/v9 Taali score
     regardless of the pre-screen gate.

Once these rows have ``taali_score_cache_100`` and ``pre_screen_score_100``
populated AND ``pipeline_stage='advanced'`` (set by the companion
``backfill_advanced_stage.py``), ``auto_threshold_service`` will start
anchoring its recommended threshold on the recruiter-validated cohort
automatically — that's the calibration win.

Run from backend/:
  .venv/bin/python scripts/backfill_score_from_workable_history.py --dry-run
  .venv/bin/python scripts/backfill_score_from_workable_history.py --starred-only --limit 5   # smoke
  .venv/bin/python scripts/backfill_score_from_workable_history.py                            # full

Cost: roughly $0.05-0.15 per scored candidate (v4 CV match on Sonnet
plus optional pre-screen). The script commits per-row so partial runs
are safe.

Idempotent: re-runs skip candidates that already have a Taali score
unless ``--force`` is passed.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Workable stages we *exclude* — too weak a signal to be worth the spend.
# The cohort is "everything else with a non-empty workable_stage".
EXCLUDED_STAGES = {"applied", "phone_screen", "phone screen", ""}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score post-handover Workable candidates so auto-threshold "
        "and per-role calibration anchor on recruiter-validated history.",
    )
    parser.add_argument("--starred-only", action="store_true",
                        help="Limit to roles with starred_for_auto_sync=True.")
    parser.add_argument("--org-id", type=int, default=None)
    parser.add_argument("--role-id", type=int, default=None,
                        help="Only score one role (for testing).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after N candidates (for smoke testing).")
    parser.add_argument("--force", action="store_true",
                        help="Re-score candidates that already have a Taali score.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List the cohort + projected scoring calls; no LLM spend.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    from app.domains.assessments_runtime.applications_routes import (
        _try_fetch_cv_from_workable,
    )
    from app.models.candidate_application import CandidateApplication
    from app.models.cv_score_job import CvScoreJob, SCORE_JOB_PENDING
    from app.models.organization import Organization
    from app.models.role import Role
    from app.platform.config import settings
    from app.platform.database import SessionLocal
    from app.services.cv_score_orchestrator import _execute_scoring

    if not settings.ANTHROPIC_API_KEY and not args.dry_run:
        print("ERROR: ANTHROPIC_API_KEY not set; cannot score.", file=sys.stderr)
        return 2

    db = SessionLocal()
    started_at = time.time()
    scanned = 0
    skipped_no_role_spec = 0
    skipped_no_cv = 0
    skipped_already_scored = 0
    scored = 0
    failed = 0

    try:
        q = (
            db.query(CandidateApplication)
            .join(Role, Role.id == CandidateApplication.role_id)
            .filter(CandidateApplication.deleted_at.is_(None))
            .filter(CandidateApplication.workable_stage.isnot(None))
            .filter(CandidateApplication.workable_stage != "")
        )
        if args.starred_only:
            q = q.filter(Role.starred_for_auto_sync.is_(True))
        if args.org_id is not None:
            q = q.filter(CandidateApplication.organization_id == args.org_id)
        if args.role_id is not None:
            q = q.filter(CandidateApplication.role_id == args.role_id)

        all_apps = q.order_by(CandidateApplication.id.asc()).all()
        cohort = [
            a for a in all_apps
            if (a.workable_stage or "").strip().lower() not in EXCLUDED_STAGES
        ]
        print(f"[backfill_score] candidates in cohort: {len(cohort)}")
        if args.limit is not None:
            cohort = cohort[: int(args.limit)]
            print(f"[backfill_score] limit applied -> {len(cohort)}")

        # Cache org rows so we don't refetch on every iteration.
        orgs_by_id: dict[int, Organization] = {}

        for app in cohort:
            scanned += 1

            # Skip if already scored unless --force.
            if app.taali_score_cache_100 is not None and not args.force:
                skipped_already_scored += 1
                continue

            role = app.role
            if role is None or not (role.job_spec_text or "").strip():
                skipped_no_role_spec += 1
                continue

            if args.dry_run:
                print(
                    f"  would score app_id={app.id} role_id={app.role_id} "
                    f"workable_stage={app.workable_stage!r} "
                    f"has_cv_text={bool((app.cv_text or '').strip())}"
                )
                continue

            # Best-effort CV fetch if missing.
            cv_text = (app.cv_text or "").strip()
            candidate = app.candidate
            if not cv_text and candidate:
                cv_text = (candidate.cv_text or "").strip()
                if cv_text:
                    app.cv_text = cv_text
            if not cv_text:
                org = orgs_by_id.get(app.organization_id)
                if org is None:
                    org = (
                        db.query(Organization)
                        .filter(Organization.id == app.organization_id)
                        .first()
                    )
                    if org is not None:
                        orgs_by_id[app.organization_id] = org
                if org is not None and candidate is not None:
                    try:
                        ok = _try_fetch_cv_from_workable(app, candidate, db, org)
                        if ok:
                            cv_text = (app.cv_text or "").strip()
                    except Exception as exc:  # pragma: no cover — best-effort
                        print(f"  CV fetch failed app_id={app.id}: {exc}")

            if not cv_text:
                skipped_no_cv += 1
                db.commit()  # persist any side-effects from a partial fetch
                continue

            # Run the scoring orchestrator synchronously. Bypasses the
            # per-org credit reserve and per-role monthly cap (both live in
            # enqueue_score upstream) — backfill spend is intentional and
            # still emits usage_events so it's visible in billing.
            try:
                job = CvScoreJob(
                    application_id=app.id,
                    role_id=app.role_id,
                    status=SCORE_JOB_PENDING,
                )
                db.add(job)
                db.flush()
                _execute_scoring(
                    db,
                    application=app,
                    job=job,
                    force_full_score=True,
                )
                db.commit()
                scored += 1
                if scored % 10 == 0:
                    elapsed = time.time() - started_at
                    print(
                        f"[backfill_score] scored={scored} failed={failed} "
                        f"elapsed={elapsed:.0f}s"
                    )
            except Exception as exc:  # pragma: no cover — best-effort
                failed += 1
                print(f"  SCORE FAILED app_id={app.id}: {exc}")
                db.rollback()

    finally:
        db.close()

    elapsed = time.time() - started_at
    print(
        f"\n[backfill_score] DONE in {elapsed:.0f}s — scanned={scanned} "
        f"scored={scored} failed={failed} "
        f"skipped_already={skipped_already_scored} skipped_no_cv={skipped_no_cv} "
        f"skipped_no_spec={skipped_no_role_spec} "
        f"(dry_run={args.dry_run})"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
