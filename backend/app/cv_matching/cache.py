"""Cache adapter over the ``cv_score_cache`` table.

Cache key:

    sha256(cv_text + jd_text + json(requirements) + prompt_version + model_version)

Bumping ``PROMPT_VERSION`` invalidates the cache cleanly — every entry
keys on it, so old rows become unreachable and a fresh score regenerates.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .schemas import CVMatchOutput

if TYPE_CHECKING:
    from .schemas import RequirementInput

logger = logging.getLogger("taali.cv_match.cache")


def compute_cache_key(
    *,
    cv_text: str,
    jd_text: str,
    requirements: "list[RequirementInput]",
    prompt_version: str,
    model_version: str,
) -> str:
    """Stable SHA256 over normalized inputs."""
    payload = {
        "cv": cv_text or "",
        "jd": jd_text or "",
        "requirements": [
            r.model_dump(mode="json") for r in (requirements or [])
        ],
        "prompt_version": prompt_version,
        "model_version": model_version,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def get(cache_key: str) -> CVMatchOutput | None:
    """Lookup a cached output. Returns None on miss or schema drift."""
    try:
        from ..platform.database import SessionLocal
        from ..models.cv_score_cache import CvScoreCache
    except Exception as exc:
        logger.debug("Cache get skipped (no DB): %s", exc)
        return None

    session = SessionLocal()
    try:
        row = session.query(CvScoreCache).filter_by(cache_key=cache_key).one_or_none()
        if row is None:
            return None
        try:
            output = CVMatchOutput.model_validate(row.result or {})
        except Exception as exc:
            logger.warning(
                "Cache hit but row failed schema validation (key=%s): %s",
                cache_key[:16],
                exc,
            )
            return None
        try:
            row.hit_count = (row.hit_count or 0) + 1
            row.last_hit_at = datetime.now(timezone.utc)
            session.commit()
        except Exception:  # pragma: no cover — defensive
            session.rollback()
        return output
    finally:
        session.close()


def set(cache_key: str, output: CVMatchOutput) -> None:
    """Persist a CVMatchOutput. No-op if row already exists or run failed."""
    from .schemas import ScoringStatus

    if output.scoring_status != ScoringStatus.OK:
        return

    try:
        from ..platform.database import SessionLocal
        from ..models.cv_score_cache import CvScoreCache
    except Exception as exc:
        logger.debug("Cache set skipped (no DB): %s", exc)
        return

    session = SessionLocal()
    try:
        existing = (
            session.query(CvScoreCache).filter_by(cache_key=cache_key).one_or_none()
        )
        if existing is not None:
            return

        row = CvScoreCache(
            cache_key=cache_key,
            prompt_version=output.prompt_version,
            model=output.model_version,
            score_100=output.role_fit_score,
            result=output.model_dump(mode="json"),
            hit_count=0,
        )
        session.add(row)
        session.commit()
    except Exception as exc:
        logger.warning("Cache write failed for key=%s: %s", cache_key[:16], exc)
        session.rollback()
    finally:
        session.close()
