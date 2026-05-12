"""Backfill: CV-match score historical Workable post-handover candidates.

Calibration-driven backfill. Cohort: every candidate whose Workable
stage indicates the recruiter advanced them past initial screening
(Technical Interview, Final Interview, Offer, plus recruiter-custom
stages like "First stage", "Technical", "Presentation"). Excludes
"Applied" and "Phone Screen" as too weak a signal to be worth the spend.

This script intentionally skips:
  - the pre-screen gate (we want a CV-match score regardless)
  - the post-score interview-pack generation (orchestrator's downstream
    Haiku call to pre-build interview questions — pure waste for backfill)
  - cache-write of taali_score_cache_100 / role_fit_score_cache (we run
    refresh_application_score_cache once at the end of each candidate to
    bring those in line with the new cv_match_score)

What it DOES per candidate, in 1 LLM call:
  1. Fetch CV from Workable if missing (best-effort, persists to bucket).
  2. Run cv_matching.runner.run_cv_match → CV match score + details.
  3. Write cv_match_score / cv_match_details / cv_match_scored_at.
  4. Refresh the score cache columns so the UI reflects the new score.
  5. Record a `score` usage_event so spend is visible in billing.

Runs candidates in parallel via ThreadPoolExecutor (LLM calls are
I/O-bound, so threads suffice).

Run from backend/:
  .venv/bin/python scripts/backfill_score_from_workable_history.py --dry-run
  .venv/bin/python scripts/backfill_score_from_workable_history.py --limit 5
  .venv/bin/python scripts/backfill_score_from_workable_history.py

Cost: ~$0.03-0.05 per candidate (one Haiku call). Idempotent — re-runs
skip candidates that already have a cv_match_score unless ``--force``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EXCLUDED_STAGES = {"applied", "phone_screen", "phone screen", ""}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CV-match score post-handover Workable candidates for calibration.",
    )
    parser.add_argument("--starred-only", action="store_true")
    parser.add_argument("--org-id", type=int, default=None)
    parser.add_argument("--role-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true",
                        help="Re-score candidates that already have a cv_match_score.")
    parser.add_argument("--workers", type=int, default=8,
                        help="Concurrent worker threads (default 8).")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _build_requirements(role) -> list:
    from app.cv_matching import Priority as V3Priority, RequirementInput
    out: list = []
    if role is None:
        return out
    for c in sorted(role.criteria or [], key=lambda c: getattr(c, "ordering", 0)):
        if getattr(c, "deleted_at", None) is not None:
            continue
        priority = (
            V3Priority.MUST_HAVE if bool(c.must_have) else V3Priority.STRONG_PREFERENCE
        )
        out.append(
            RequirementInput(
                id=f"crit_{int(c.id)}",
                requirement=str(c.text or "").strip(),
                priority=priority,
            )
        )
    return out


# Thread-local counters protected by a single lock.
_lock = threading.Lock()
_stats = {
    "scored": 0,
    "failed": 0,
    "skipped_no_cv": 0,
    "skipped_no_role_spec": 0,
}


def _bump(key: str, by: int = 1) -> None:
    with _lock:
        _stats[key] = _stats[key] + by


def _score_one(app_id: int, args: argparse.Namespace) -> None:
    """Process one candidate. Uses its own DB session (thread-safe)."""
    from app.cv_matching import MODEL_VERSION as V3_MODEL_VERSION
    from app.cv_matching.runner import ScoringStatus, run_cv_match
    from app.domains.assessments_runtime.applications_routes import (
        _try_fetch_cv_from_workable,
    )
    from app.domains.assessments_runtime.role_support import (
        refresh_application_score_cache,
    )
    from app.models.candidate_application import CandidateApplication
    from app.models.organization import Organization
    from app.platform.database import SessionLocal
    from app.services.claude_client_resolver import get_client_for_org as _resolve_anthropic_client
    from app.services.pricing_service import Feature
    from app.services.usage_metering_service import record_event as _meter_record_event

    db = SessionLocal()
    try:
        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == app_id)
            .first()
        )
        if app is None:
            _bump("failed")
            return

        role = app.role
        if role is None or not (role.job_spec_text or "").strip():
            _bump("skipped_no_role_spec")
            return

        # Best-effort CV fetch if missing.
        cv_text = (app.cv_text or "").strip()
        candidate = app.candidate
        if not cv_text and candidate:
            cv_text = (candidate.cv_text or "").strip()
            if cv_text:
                app.cv_text = cv_text
        if not cv_text and candidate is not None:
            org = (
                db.query(Organization)
                .filter(Organization.id == app.organization_id)
                .first()
            )
            if org is not None:
                try:
                    if _try_fetch_cv_from_workable(app, candidate, db, org):
                        cv_text = (app.cv_text or "").strip()
                        db.commit()
                except Exception as exc:  # pragma: no cover
                    print(f"  CV fetch failed app_id={app.id}: {exc}")
        if not cv_text:
            _bump("skipped_no_cv")
            db.commit()
            return

        org_client = _resolve_anthropic_client(
            getattr(app, "organization", None)
        )
        requirements = _build_requirements(role)
        archetype_meta = {
            "organization_id": app.organization_id,
            "role_id": app.role_id,
            "entity_id": f"application:{app.id}",
            "db": db,
        }

        output = run_cv_match(
            cv_text,
            (role.job_spec_text or "").strip(),
            requirements,
            client=org_client,
            metering_context=archetype_meta,
        )

        if output.scoring_status == ScoringStatus.FAILED:
            app.cv_match_score = None
            app.cv_match_details = {
                "error": output.error_reason or "cv_match failed",
                "trace_id": output.trace_id,
            }
            app.cv_match_scored_at = None
            db.commit()
            _bump("failed")
            print(f"  SCORE FAILED app_id={app.id}: {output.error_reason}")
            return

        app.cv_match_score = output.role_fit_score
        app.cv_match_details = output.model_dump(mode="json")
        app.cv_match_scored_at = datetime.now(timezone.utc)

        # Record the score-feature usage event so spend is visible.
        try:
            _meter_record_event(
                db,
                organization_id=int(app.organization_id),
                role_id=app.role_id,
                feature=Feature.SCORE,
                model=V3_MODEL_VERSION,
                input_tokens=int(getattr(output, "input_tokens", 0) or 0),
                output_tokens=int(getattr(output, "output_tokens", 0) or 0),
                cache_read_tokens=int(getattr(output, "cache_read_tokens", 0) or 0),
                cache_creation_tokens=int(getattr(output, "cache_creation_tokens", 0) or 0),
                cache_hit=bool(getattr(output, "cache_hit", False)),
                entity_id=f"application:{app.id}",
            )
        except Exception:  # pragma: no cover — metering must never block scoring
            pass

        # Sync cache columns (taali_score_cache_100 etc).
        try:
            refresh_application_score_cache(app, db=db)
        except Exception:
            pass

        db.commit()
        _bump("scored")
        if _stats["scored"] % 10 == 0:
            print(f"[backfill_score] scored={_stats['scored']} "
                  f"failed={_stats['failed']} "
                  f"skipped_no_cv={_stats['skipped_no_cv']}")
    except Exception as exc:  # pragma: no cover — best-effort
        _bump("failed")
        print(f"  EXCEPTION app_id={app_id}: {exc}")
        db.rollback()
    finally:
        db.close()


def main() -> int:
    args = parse_args()

    from app.models.candidate_application import CandidateApplication
    from app.models.role import Role
    from app.platform.config import settings
    from app.platform.database import SessionLocal

    if not settings.ANTHROPIC_API_KEY and not args.dry_run:
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        return 2

    started_at = time.time()

    db = SessionLocal()
    try:
        q = (
            db.query(CandidateApplication.id, CandidateApplication.workable_stage,
                     CandidateApplication.cv_match_score)
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
        rows = q.order_by(CandidateApplication.id.asc()).all()
    finally:
        db.close()

    cohort_ids: list[int] = []
    for app_id, ws, cv_score in rows:
        if (ws or "").strip().lower() in EXCLUDED_STAGES:
            continue
        if cv_score is not None and not args.force:
            continue
        cohort_ids.append(int(app_id))

    print(f"[backfill_score] candidates to score: {len(cohort_ids)} "
          f"(workers={args.workers})")
    if args.limit is not None:
        cohort_ids = cohort_ids[: int(args.limit)]
        print(f"[backfill_score] limit applied -> {len(cohort_ids)}")

    if args.dry_run:
        for app_id in cohort_ids[:20]:
            print(f"  would score app_id={app_id}")
        if len(cohort_ids) > 20:
            print(f"  ... and {len(cohort_ids) - 20} more")
        return 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(_score_one, a, args) for a in cohort_ids]
        for _ in concurrent.futures.as_completed(futures):
            pass

    elapsed = time.time() - started_at
    print(
        f"\n[backfill_score] DONE in {elapsed:.0f}s — "
        f"scored={_stats['scored']} failed={_stats['failed']} "
        f"skipped_no_cv={_stats['skipped_no_cv']} "
        f"skipped_no_spec={_stats['skipped_no_role_spec']}"
    )
    return 0 if _stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
