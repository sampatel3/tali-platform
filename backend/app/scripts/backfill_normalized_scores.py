"""Recompute cached display scores after the ``<=10 → ×10`` inflation bug fix.

Background: the read-time score normalizer in role_support had a
``if numeric <= 10: numeric *= 10`` fallback that silently inflated
legitimate weak 0-100 scores — a candidate with ``role_fit_score =
9.6`` displayed as 96 in the gauge while the requirements panel
correctly showed "1 of 21 evidenced". The same bug lived in 5 places
(role_support, applications_routes filter, sync_service writer,
fit_matching_service, candidate_feedback_engine) and was fixed in the
same commit as this script.

The raw ``cv_match_score`` column is correct (the v3 runner always
emits 0-100). Only the cached columns it feeds —
``role_fit_score_cache_100``, ``taali_score_cache_100``,
``assessment_score_cache_100``, ``pre_screen_score_100`` — were
written through the buggy normalizer. This script:

1. Iterates every ``CandidateApplication`` and re-runs
   ``refresh_application_score_cache`` (pure Python, no Anthropic
   calls) so the cached columns are rewritten with the corrected
   normalizer.
2. Calls the existing ``backfill_existing_below_threshold`` so
   newly-correctly-below-50 candidates on agent-on roles get a
   pending ``skip_assessment_reject`` Decision Hub card.

Both steps are idempotent. Safe to run multiple times. Scope can be
limited to one organization via ``--organization-id``.
"""

from __future__ import annotations

import argparse
import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication

logger = logging.getLogger("taali.scripts.backfill_normalized_scores")


def refresh_cached_scores(
    db: Session,
    *,
    organization_id: Optional[int] = None,
    batch_size: int = 200,
) -> dict[str, int]:
    """Recompute the cached display columns for every application.

    Returns ``{"processed": N, "updated": N, "errors": N}``. Commits
    per batch so a single bad row doesn't roll back the whole pass.
    """
    from ..domains.assessments_runtime.role_support import (
        refresh_application_score_cache,
    )

    q = db.query(CandidateApplication.id)
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))
    ids = [row[0] for row in q.order_by(CandidateApplication.id.asc()).all()]

    processed = 0
    updated = 0
    errors = 0
    for start in range(0, len(ids), batch_size):
        chunk = ids[start : start + batch_size]
        apps = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id.in_(chunk))
            .all()
        )
        for app in apps:
            processed += 1
            try:
                before = (
                    app.taali_score_cache_100,
                    app.role_fit_score_cache_100,
                    app.assessment_score_cache_100,
                    app.pre_screen_score_100,
                )
                refresh_application_score_cache(app, db=db)
                after = (
                    app.taali_score_cache_100,
                    app.role_fit_score_cache_100,
                    app.assessment_score_cache_100,
                    app.pre_screen_score_100,
                )
                if before != after:
                    updated += 1
            except Exception:
                errors += 1
                logger.exception(
                    "refresh_application_score_cache failed for application_id=%s",
                    app.id,
                )
        try:
            db.commit()
        except Exception:
            db.rollback()
            errors += len(apps)
            logger.exception(
                "commit failed for batch starting at id=%s", chunk[0] if chunk else None
            )
        logger.info(
            "refresh batch %s/%s processed=%s updated=%s errors=%s",
            start + len(chunk),
            len(ids),
            processed,
            updated,
            errors,
        )

    return {"processed": processed, "updated": updated, "errors": errors}


def backfill_pre_screen_decisions(
    db: Session, *, organization_id: Optional[int] = None
) -> dict:
    """Queue Decision Hub cards for now-correctly-below-50 candidates.

    Reuses the canonical emitter at
    ``services.pre_screen_decision_emitter.backfill_existing_below_threshold``
    — idempotent, gated to agent-on roles only.
    """
    from ..services.pre_screen_decision_emitter import (
        backfill_existing_below_threshold,
    )

    return backfill_existing_below_threshold(db, organization_id=organization_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--organization-id",
        type=int,
        default=None,
        help="Restrict backfill to a single organization. Default: all orgs.",
    )
    parser.add_argument(
        "--skip-cache-refresh",
        action="store_true",
        help="Skip the score-cache refresh pass (only run the decision backfill).",
    )
    parser.add_argument(
        "--skip-decision-backfill",
        action="store_true",
        help="Skip the Decision Hub backfill (only refresh score caches).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        if not args.skip_cache_refresh:
            print(
                f"[backfill_normalized_scores] refreshing cached scores "
                f"(org={args.organization_id or 'ALL'})",
                flush=True,
            )
            stats = refresh_cached_scores(db, organization_id=args.organization_id)
            print(
                f"[backfill_normalized_scores] cache refresh complete: {stats}",
                flush=True,
            )
        if not args.skip_decision_backfill:
            print(
                f"[backfill_normalized_scores] queueing pre-screen reject decisions "
                f"(org={args.organization_id or 'ALL'})",
                flush=True,
            )
            stats = backfill_pre_screen_decisions(
                db, organization_id=args.organization_id
            )
            print(
                f"[backfill_normalized_scores] decision backfill complete: {stats}",
                flush=True,
            )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
