"""Backfill: advance Tali ``pipeline_stage`` for candidates already past
Workable's handover point.

Background: until this PR, the Workable sync only updated
``workable_stage`` on existing rows — never ``pipeline_stage``. So
candidates who got moved to Phone Screen / Interview / Technical
Interview / Offer / Hired in Workable kept their stale Tali stage
(``applied`` / ``review`` / etc.).

This script finds those rows and advances them to ``advanced``, the new
post-handover Tali bucket. Forward-only — never demotes.

Run from backend/:
  .venv/bin/python scripts/backfill_advanced_stage.py --starred-only --dry-run
  .venv/bin/python scripts/backfill_advanced_stage.py --starred-only
  .venv/bin/python scripts/backfill_advanced_stage.py            # all roles

Idempotent: re-running is a no-op once everyone is at ``advanced``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.domains.assessments_runtime.pipeline_service import (
    map_legacy_status_to_pipeline,
    should_auto_advance_to_advanced,
    transition_stage,
)
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.platform.database import SessionLocal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Forward-advance Tali pipeline_stage to 'advanced' for "
        "candidates already past Workable's handover point.",
    )
    parser.add_argument(
        "--starred-only",
        action="store_true",
        help="Limit to roles with starred_for_auto_sync=True.",
    )
    parser.add_argument(
        "--org-id", type=int, default=None,
        help="Optional organization id filter.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="Commit batch size (default 500).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would change without persisting.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    started_at = time.time()
    scanned = 0
    eligible = 0
    advanced = 0
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

        total = q.count()
        print(f"[backfill_advanced_stage] candidates to scan: {total}")
        if total == 0:
            return 0

        last_id = 0
        while True:
            batch = (
                q.filter(CandidateApplication.id > last_id)
                .order_by(CandidateApplication.id.asc())
                .limit(args.batch_size)
                .all()
            )
            if not batch:
                break
            for app in batch:
                scanned += 1
                last_id = app.id
                mapped, _ = map_legacy_status_to_pipeline(app.workable_stage or "")
                if mapped != "advanced":
                    continue
                if not should_auto_advance_to_advanced(app.pipeline_stage):
                    continue
                eligible += 1
                if args.dry_run:
                    print(
                        f"  would advance app_id={app.id} "
                        f"(role_id={app.role_id}, workable_stage={app.workable_stage!r}, "
                        f"current_pipeline_stage={app.pipeline_stage!r})"
                    )
                    continue
                try:
                    transition_stage(
                        db,
                        app=app,
                        to_stage="advanced",
                        source="sync",
                        actor_type="sync",
                        reason="Backfill: Workable already past handover point",
                        metadata={"workable_stage": app.workable_stage},
                        idempotency_key=f"backfill_advance:{app.id}",
                    )
                    advanced += 1
                except Exception as exc:  # pragma: no cover — best-effort
                    failed += 1
                    print(f"  FAIL app_id={app.id}: {exc}")
                    db.rollback()
                    continue
            if not args.dry_run:
                db.commit()
            print(
                f"[backfill_advanced_stage] scanned={scanned}/{total} "
                f"eligible={eligible} advanced={advanced} failed={failed}"
            )
    finally:
        db.close()

    elapsed = time.time() - started_at
    print(
        f"[backfill_advanced_stage] DONE in {elapsed:.1f}s — "
        f"scanned={scanned} eligible={eligible} advanced={advanced} failed={failed} "
        f"(dry_run={args.dry_run})"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
