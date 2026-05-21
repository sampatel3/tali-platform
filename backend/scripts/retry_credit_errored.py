"""Re-enqueue applications whose last scoring attempt errored on
"Anthropic credit balance is too low" — now that credits are restored.

Targets the apps that hit the credit ceiling during the post-PR-211
invalidate-rescore wave on 2026-05-21. Without this they'd sit blank
forever (the sweeper only picks up ``stale`` jobs, not ``error`` jobs).
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
log = logging.getLogger("retry_credit_errored")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role-ids", type=int, nargs="+", required=True,
        help="Re-enqueue credit-errored apps on these roles.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        log.error("DATABASE_URL not set")
        return 2

    from sqlalchemy import desc
    from app.models.candidate_application import CandidateApplication
    from app.models.cv_score_job import CvScoreJob
    from app.platform.database import SessionLocal
    from app.services.cv_score_orchestrator import enqueue_score

    db = SessionLocal()
    try:
        # Find apps whose LATEST job errored on credits.
        apps = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.role_id.in_(args.role_ids),
                CandidateApplication.deleted_at.is_(None),
            )
            .all()
        )
        targets = []
        for app in apps:
            latest = (
                db.query(CvScoreJob)
                .filter(CvScoreJob.application_id == app.id)
                .order_by(desc(CvScoreJob.queued_at))
                .first()
            )
            if latest is None:
                continue
            if latest.status != "error":
                continue
            if "credit balance" not in (latest.error_message or ""):
                continue
            targets.append(app)
        log.info("Found %d credit-errored apps across roles %s", len(targets), args.role_ids)
        if args.dry_run:
            return 0

        enqueued = 0
        for app in targets:
            try:
                job = enqueue_score(db, app, force=True)
                if job is not None:
                    enqueued += 1
            except Exception:
                log.exception("enqueue raised for app=%s", app.id)
            time.sleep(0.05)
        db.commit()
        log.info("Re-enqueued %d apps", enqueued)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
