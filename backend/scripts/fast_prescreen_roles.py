"""Fast path: run pre-screen synchronously for every blank app on the
given roles. Pre-screen is Haiku (~1.5s) so 300 apps drain in ~8 min,
vs ~2.5 hours through the worker queue (where each task does
pre-screen + Sonnet v3 cv_match, ~60s per app).

After this lands scores via execute_pre_screen_only, candidates that
violate the salary / hard-constraint are immediately reflected in the
UI. The orchestrator's normal v3 pass can fill in the full cv_match
score over the next 30 min via the existing queue + sweeper.

Usage:
    DATABASE_URL=... ANTHROPIC_API_KEY=... \\
        python -m scripts.fast_prescreen_roles --role-ids 110 111 112 113
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
log = logging.getLogger("fast_prescreen")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role-ids", type=int, nargs="+", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        log.error("DATABASE_URL not set")
        return 2

    from app.models.candidate_application import CandidateApplication
    from app.platform.database import SessionLocal
    from app.services.claude_client_resolver import get_shared_client
    from app.services.pre_screening_service import execute_pre_screen_only

    db = SessionLocal()
    try:
        # Every app on the target roles that needs pre-screen — keyed
        # on ``pre_screen_run_at IS NULL`` (the staleness signal under
        # the new "honest stale" semantics, where invalidated apps
        # keep their prior score value but null this timestamp). Old
        # filter on ``pre_screen_score_100 IS NULL`` would silently
        # skip every stale app and falsely report success.
        apps = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.role_id.in_(args.role_ids),
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.pre_screen_run_at.is_(None),
                CandidateApplication.cv_text.isnot(None),
            )
            .all()
        )
        log.info("Found %d apps needing pre-screen across roles %s", len(apps), args.role_ids)
        if args.dry_run:
            return 0

        ok = errored = skipped = 0
        for i, app in enumerate(apps, start=1):
            org_id = int(getattr(app, "organization_id", 0) or 0) or None
            try:
                client = get_shared_client(organization_id=org_id)
                result = execute_pre_screen_only(app, db=db, client=client)
            except Exception as exc:
                log.exception("app=%s raised: %s", app.id, exc)
                errored += 1
                continue
            status = result.get("status")
            score = result.get("score")
            if status == "ok":
                ok += 1
                db.commit()
                if i % 25 == 0 or score is None or (score or 0) < 50:
                    log.info(
                        "[%d/%d] app=%s role=%s score=%s decision=%s",
                        i, len(apps), app.id, app.role_id, score, result.get("decision"),
                    )
            elif status == "skipped":
                skipped += 1
                db.commit()
            else:
                errored += 1
                db.rollback()
                log.warning(
                    "[%d/%d] app=%s role=%s ERROR: %s",
                    i, len(apps), app.id, app.role_id, str(result.get("reason"))[:80],
                )
            # Polite pacing — Haiku is fast but the metered client also
            # writes usage rows. Let's not hammer.
            time.sleep(0.03)
        log.info("Done. ok=%d errored=%d skipped=%d total=%d", ok, errored, skipped, len(apps))
        return 0 if errored < len(apps) // 2 else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
