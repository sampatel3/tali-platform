"""Content-hash cache for parsed CVs.

Backed by ``cv_parse_cache`` table (see migration 045). Key:
``sha256(cv_text + prompt_version + model_version)``. Same content +
same prompt version + same model = same cached output, no re-parse.

Failed parses are NOT cached — caller can retry with fresh inputs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from .schemas import ParsedCV

logger = logging.getLogger("taali.cv_parsing.cache")


def compute_cache_key(
    *,
    cv_text: str,
    prompt_version: str,
    model_version: str,
) -> str:
    payload = {
        "cv_text": cv_text or "",
        "prompt_version": prompt_version,
        "model_version": model_version,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def get(cache_key: str) -> ParsedCV | None:
    """Return cached ParsedCV or None on miss / schema drift."""
    try:
        from ..models.cv_parse_cache import CvParseCache
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("cache.get skipped (no DB): %s", exc)
        return None

    session = SessionLocal()
    try:
        row = session.query(CvParseCache).filter_by(cache_key=cache_key).one_or_none()
        if row is None:
            return None
        try:
            parsed = ParsedCV.model_validate(row.result or {})
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
        return parsed
    finally:
        session.close()


def set(cache_key: str, parsed: ParsedCV) -> None:
    """Persist a successful parse. No-op for failed parses or duplicates."""
    if parsed.parse_failed:
        return
    try:
        from ..models.cv_parse_cache import CvParseCache
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("cache.set skipped (no DB): %s", exc)
        return

    session = SessionLocal()
    try:
        existing = (
            session.query(CvParseCache).filter_by(cache_key=cache_key).one_or_none()
        )
        if existing is not None:
            return
        row = CvParseCache(
            cache_key=cache_key,
            prompt_version=parsed.prompt_version,
            model=parsed.model_version,
            result=parsed.model_dump(mode="json"),
            hit_count=0,
        )
        session.add(row)
        session.commit()
    except Exception as exc:
        logger.warning("cache.set failed for key=%s: %s", cache_key[:16], exc)
        session.rollback()
    finally:
        session.close()
