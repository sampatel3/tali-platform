"""One-off: re-run pre-screen for applications whose pre_screen_evidence
shows ``decision: 'error'`` (most commonly from Anthropic credit
exhaustion during the post-deploy first-sync wave on 2026-05-20).

The orchestrator's current design treats LLM errors as "maybe" and
falls through to v3 cv_match scoring, then mirrors that v3 score into
``pre_screen_score_100`` via ``refresh_pre_screening_fields``. As a
result, candidates whose pre-screen LLM call errored show as
"passing" pre-screen with high scores — even when they violate hard
constraints the LLM never got to see.

This script:
1. Finds every CandidateApplication with ``pre_screen_evidence.decision
   == 'error'``.
2. Clears the stale scoring fields (pre_screen_score_100,
   pre_screen_evidence, cv_match_score, cv_match_details,
   cv_match_scored_at) so refresh helpers don't mirror leftover v3
   data back into pre-screen.
3. Calls ``execute_pre_screen_only`` synchronously — Haiku is cheap
   and fast, ~1 sec per call. No Celery needed.
4. Reports the new score + reason inline so a human can spot-check.

Idempotent: re-running picks up any that errored again.
Safe to run while traffic is live (writes are per-row + flushed).

Usage (with prod DATABASE_URL exported):
    cd backend && python -m scripts.rescreen_errored_apps [--limit N] [--app-id ID]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from sqlalchemy import or_, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("rescreen")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit", type=int, default=None, help="Process at most N apps (for testing)."
    )
    parser.add_argument(
        "--app-id",
        type=int,
        default=None,
        help="Re-screen only this specific application id (skip the errored-only filter).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List affected apps without re-screening.",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        log.error("DATABASE_URL not set — refusing to run.")
        return 2

    # Late import so SQLAlchemy picks up DATABASE_URL.
    from app.models.candidate_application import CandidateApplication
    from app.platform.database import SessionLocal
    from app.services.claude_client_resolver import get_shared_client
    from app.services.pre_screening_service import execute_pre_screen_only

    db = SessionLocal()
    try:
        q = db.query(CandidateApplication).filter(
            CandidateApplication.deleted_at.is_(None)
        )
        if args.app_id is not None:
            q = q.filter(CandidateApplication.id == args.app_id)
        else:
            # JSON ``->>`` lookup. Match either the structured decision
            # field or the substring in summary for older shapes.
            q = q.filter(
                or_(
                    text("pre_screen_evidence->>'decision' = 'error'"),
                    text("pre_screen_evidence::text LIKE '%credit balance is too low%'"),
                    text("pre_screen_evidence::text LIKE '%claude_call_failed%'"),
                )
            )
        if args.limit:
            q = q.limit(args.limit)

        apps = q.all()
        log.info("Found %d candidate applications to re-screen.", len(apps))
        if args.dry_run:
            for app in apps:
                log.info(
                    "  app=%s role=%s candidate=%s current_pre_screen=%s cv_match=%s",
                    app.id, app.role_id, app.candidate_id,
                    app.pre_screen_score_100, app.cv_match_score,
                )
            return 0

        ok, errored, skipped = 0, 0, 0
        for i, app in enumerate(apps, start=1):
            # Clear stale scoring fields so the snapshot helpers don't
            # mirror leftover v3 data back into pre-screen output.
            app.pre_screen_score_100 = None
            app.pre_screen_evidence = None
            app.pre_screen_recommendation = None
            app.cv_match_score = None
            app.cv_match_details = None
            app.cv_match_scored_at = None
            app.requirements_fit_score_100 = None
            app.rank_score = None
            db.flush()

            # Use the metered wrapper so the ``metering`` kwarg the
            # runner passes to ``messages.create`` is handled correctly
            # (raw anthropic.Anthropic rejects the kwarg).
            org_id = int(getattr(app, "organization_id", 0) or 0) or None
            try:
                client = get_shared_client(organization_id=org_id)
                result = execute_pre_screen_only(app, db=db, client=client)
            except Exception as exc:  # noqa: BLE001
                log.exception("app=%s rescore call raised: %s", app.id, exc)
                errored += 1
                continue

            status = result.get("status")
            score = result.get("score")
            reason = (result.get("reason") or "")[:140]

            if status == "ok":
                ok += 1
                db.commit()
                log.info(
                    "[%d/%d] app=%s OK score=%s decision=%s reason=%s",
                    i, len(apps), app.id, score, result.get("decision"), reason,
                )
            elif status == "skipped":
                skipped += 1
                db.commit()
                log.info(
                    "[%d/%d] app=%s SKIPPED reason=%s",
                    i, len(apps), app.id, result.get("reason"),
                )
            else:
                errored += 1
                db.rollback()
                log.warning(
                    "[%d/%d] app=%s ERROR reason=%s",
                    i, len(apps), app.id, result.get("reason"),
                )
            # Tiny breather between calls to be polite to Anthropic.
            time.sleep(0.05)

        log.info("Done. ok=%d errored=%d skipped=%d total=%d", ok, errored, skipped, len(apps))
        return 0 if errored == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
