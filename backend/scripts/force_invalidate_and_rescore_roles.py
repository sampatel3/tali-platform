"""One-off: force-invalidate + rescore every application on the given roles.

Use case: roles that had must-have / constraint criteria added BEFORE
the score-invalidation hooks shipped (PRs #209 + #211 on 2026-05-21).
Those candidates never had their stale ``pre_screen_score_100`` /
``cv_match_score`` reset, so the UI keeps showing scores that don't
reflect the new constraints — e.g. a 65k AED salary expectation
candidate showing as "Strong match — 87" on a role whose new
constraint says salary must be below 40k.

The script:
1. For each role, calls ``mark_role_scores_stale(role_id)`` — NULLs
   every score field on scored apps + adds a ``status=stale``
   ``CvScoreJob`` row.
2. For each invalidated app, calls ``enqueue_score(app, force=True)``
   to create a pending CvScoreJob the worker picks up immediately
   (rather than waiting up to 30 min for the beat sweeper).
3. Reports counts so you can spot-check before the worker drains.

Usage:
    DATABASE_URL=... ANTHROPIC_API_KEY=... \\
        python -m scripts.force_invalidate_and_rescore_roles --role-ids 110 111 112 113
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("force_rescore")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role-ids",
        type=int,
        nargs="+",
        required=True,
        help="Role IDs to invalidate + rescore.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without writing.",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        log.error("DATABASE_URL not set — refusing to run.")
        return 2

    from app.models.candidate_application import CandidateApplication
    from app.platform.database import SessionLocal
    from app.services.cv_score_orchestrator import (
        enqueue_score,
        mark_role_scores_stale,
    )

    db = SessionLocal()
    try:
        for role_id in args.role_ids:
            apps = (
                db.query(CandidateApplication)
                .filter(
                    CandidateApplication.role_id == role_id,
                    CandidateApplication.deleted_at.is_(None),
                )
                .all()
            )
            scored_apps = [
                a
                for a in apps
                if (a.pre_screen_score_100 is not None or a.cv_match_score is not None)
            ]
            log.info(
                "role=%s total=%d scored=%d (will invalidate + enqueue)",
                role_id, len(apps), len(scored_apps),
            )
            if args.dry_run:
                continue

            invalidated = mark_role_scores_stale(db, role_id)
            log.info("role=%s invalidated %d apps", role_id, invalidated)

            enqueued = 0
            skipped = 0
            for app in scored_apps:
                # Re-pull from session because invalidation flushed it.
                db.refresh(app)
                try:
                    job = enqueue_score(db, app, force=True)
                    if job is not None:
                        enqueued += 1
                    else:
                        skipped += 1
                except Exception:
                    log.exception("enqueue_score raised for app=%s", app.id)
                    skipped += 1
                # Tiny breather to avoid spiking the worker queue.
                time.sleep(0.02)
            db.commit()
            log.info(
                "role=%s enqueued=%d skipped=%d", role_id, enqueued, skipped,
            )

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
