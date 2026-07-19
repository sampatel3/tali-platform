"""Backfill TAALI score cache columns on candidate_applications.

Run from backend/:
  .venv/bin/python scripts/backfill_application_score_cache.py --batch-size 1000
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Ensure backend app package imports resolve when running from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.domains.assessments_runtime.role_support import refresh_application_score_cache
from app.models.candidate_application import CandidateApplication
from app.platform.database import SessionLocal


logger = logging.getLogger("taali.scripts.backfill_application_score_cache")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill candidate application score cache columns")
    parser.add_argument("--batch-size", type=int, default=1000, help="Row batch size (default: 1000)")
    parser.add_argument("--org-id", type=int, default=None, help="Optional organization id filter")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute cache for all rows (default only fills missing score_cached_at)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute only; do not persist updates")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    from app.platform.logging import setup_logging

    setup_logging()
    db = SessionLocal()
    started_at = time.time()
    processed = 0
    updated = 0
    failed = 0
    last_id = 0

    try:
        total_query = db.query(CandidateApplication).filter(CandidateApplication.deleted_at.is_(None))
        if args.org_id is not None:
            total_query = total_query.filter(CandidateApplication.organization_id == args.org_id)
        if not args.force:
            total_query = total_query.filter(CandidateApplication.score_cached_at.is_(None))
        total_target = int(total_query.count())
        logger.info(
            "Score cache backfill start target=%s batch_size=%s organization_id=%s force=%s dry_run=%s",
            total_target,
            args.batch_size,
            args.org_id,
            bool(args.force),
            bool(args.dry_run),
        )

        while True:
            batch_query = (
                db.query(CandidateApplication)
                .filter(
                    CandidateApplication.deleted_at.is_(None),
                    CandidateApplication.id > last_id,
                )
            )
            if args.org_id is not None:
                batch_query = batch_query.filter(CandidateApplication.organization_id == args.org_id)
            if not args.force:
                batch_query = batch_query.filter(CandidateApplication.score_cached_at.is_(None))
            rows = (
                batch_query
                .order_by(CandidateApplication.id.asc())
                .limit(args.batch_size)
                .all()
            )
            if not rows:
                break

            for app in rows:
                last_id = max(last_id, int(app.id))
                processed += 1
                try:
                    with db.begin_nested():
                        refresh_application_score_cache(app, db=db)
                    updated += 1
                except Exception as exc:
                    # Roll back only the nested transaction for this row.
                    db.rollback()
                    failed += 1
                    logger.warning(
                        "Score cache backfill failed application_id=%s error_type=%s",
                        app.id,
                        type(exc).__name__,
                    )

                if processed % 500 == 0:
                    elapsed = max(0.1, time.time() - started_at)
                    rate = processed / elapsed
                    logger.info(
                        "Score cache backfill progress processed=%s updated=%s failed=%s rate_per_second=%.1f",
                        processed,
                        updated,
                        failed,
                        rate,
                    )

            if args.dry_run:
                db.rollback()
            else:
                db.commit()
    finally:
        db.close()

    elapsed = max(0.1, time.time() - started_at)
    logger.info(
        "Score cache backfill complete processed=%s updated=%s failed=%s elapsed_seconds=%.1f",
        processed,
        updated,
        failed,
        elapsed,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
