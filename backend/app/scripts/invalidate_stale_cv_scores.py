"""One-shot invalidation of CV match scores generated under an older
``PROMPT_VERSION``.

When the cv_match prompt version is bumped (e.g. cv_match_v6 → v7), the
scoring cache is invalidated automatically via cache key, but the
``cv_match_score`` already persisted on each ``CandidateApplication``
row stays in place — so the "Score N new CVs" widget shows 0 even
though every score on the role is now stale.

This script nulls out ``cv_match_score`` and ``cv_match_scored_at`` for
any application whose cached score was produced under a different
``scoring_version`` than the current ``PROMPT_VERSION``. Recruiters
then see those candidates in the rescore widget and can trigger a
fresh score on demand.

Idempotent: re-running with no version drift is a fast no-op (the
``WHERE`` clause matches zero rows). Safe to run on every web boot,
which is how it gets wired in ``railway_start.py``.

The pre-screen pipeline is intentionally not touched here — its prompt
version (``PRE_SCREEN_PROMPT_VERSION``) is a separate concern with its
own cache + persistence path.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def invalidate_stale_cv_match_scores(db: Session, current_prompt_version: str) -> int:
    """Null out ``cv_match_score`` for rows whose cached
    ``cv_match_details.scoring_version`` differs from the current
    ``PROMPT_VERSION``. Returns the number of affected rows.

    Skips rows that are already unscored (``cv_match_score IS NULL``)
    so pre-screen-filtered candidates and never-scored applications
    aren't disturbed.
    """
    if not (current_prompt_version or "").strip():
        return 0
    result = db.execute(
        text(
            """
            UPDATE candidate_applications
            SET cv_match_score = NULL,
                cv_match_scored_at = NULL
            WHERE cv_match_score IS NOT NULL
              AND COALESCE(cv_match_details->>'scoring_version', '') <> :current_version
            """
        ),
        {"current_version": current_prompt_version},
    )
    db.commit()
    return int(result.rowcount or 0)


def main() -> int:
    """CLI entrypoint so this can be run ad-hoc via ``python -m
    app.scripts.invalidate_stale_cv_scores`` if needed (one-off
    invalidation outside the web boot path)."""
    from ..cv_matching import PROMPT_VERSION
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        affected = invalidate_stale_cv_match_scores(db, PROMPT_VERSION)
        print(
            f"[invalidate_stale_cv_scores] current={PROMPT_VERSION} affected={affected}",
            flush=True,
        )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
