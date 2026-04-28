"""Cache adapter over the existing ``cv_score_cache`` table.

Cache key (per handover):

    sha256(cv_text + jd_text + json(requirements) + prompt_version + model_version)

This shape differs from the legacy v4 cache key (which hashes
spec_description/spec_requirements/criteria_id-list). They share the same
DB table but never collide because content hashing is collision-free at the
SHA256 level and the prompt_version differs (`cv_match_v3.0` vs
`cv_match_v4`).

TTL: 30 days is documented as configurable in ``calibration.md`` but no
sweep job exists yet — rows are immutable until a future cleanup task adds
LRU eviction on top of the existing ``hit_count`` / ``last_hit_at`` columns.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

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


def get(
    cache_key: str,
    *,
    result_schema: type[BaseModel] = CVMatchOutput,
) -> Any | None:
    """Lookup a cached output. Returns None on miss or schema drift.

    Returns None if:
    - the row doesn't exist
    - the JSON in the row fails to round-trip through ``result_schema``
      (defensive: this catches schema drift between cache writers)

    ``result_schema`` defaults to ``CVMatchOutput`` (v3) for backwards
    compatibility. The v4 runner passes ``CVMatchOutputV4``. Cache rows
    written under one schema are not rehydratable under the other; the
    cache key includes ``prompt_version``, so v3 and v4 rows live under
    separate keys and never cross.
    """
    try:
        from ..platform.database import SessionLocal
        from ..models.cv_score_cache import CvScoreCache
    except Exception as exc:
        # In lightweight test contexts the DB is not wired up; treat as miss.
        logger.debug("Cache get skipped (no DB): %s", exc)
        return None

    session = SessionLocal()
    try:
        row = session.query(CvScoreCache).filter_by(cache_key=cache_key).one_or_none()
        if row is None:
            return None
        try:
            output = result_schema.model_validate(row.result or {})
        except Exception as exc:
            logger.warning(
                "Cache hit but row failed schema validation (key=%s, schema=%s): %s",
                cache_key[:16],
                result_schema.__name__,
                exc,
            )
            return None
        # Bump hit counter so future LRU sweep can prefer recently-used rows.
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
    """Persist a CVMatchOutput. No-op if the row already exists.

    Failed runs are not cached (callers can retry). Successful runs are
    immutable: re-running the same inputs after a cache hit returns the
    identical row, so write-once is safe.
    """
    from .schemas import ScoringStatus

    if output.scoring_status != ScoringStatus.OK:
        return  # don't poison the cache with failed runs

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
