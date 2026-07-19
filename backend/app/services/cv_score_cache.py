"""Deterministic cache inputs and persistence for CV scoring."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.cv_score_cache import CvScoreCache
from ..models.role import Role

_V3_PROMPT_VERSION = "cv_fit_v3_evidence_enriched"


def _criteria_payload(role: Role | None) -> list[dict]:
    if role is None:
        return []
    try:
        rows = list(role.criteria or [])
    except Exception:
        return []
    items: list[dict] = []
    for c in sorted(rows, key=lambda c: getattr(c, "ordering", 0)):
        if getattr(c, "deleted_at", None) is not None:
            continue
        items.append(
            {
                "id": int(c.id),
                "text": str(c.text or "").strip(),
                "must_have": bool(c.must_have),
                "bucket": str(
                    getattr(c, "bucket", None)
                    or ("must" if bool(c.must_have) else "preferred")
                ),
                "source": str(c.source or "recruiter"),
            }
        )
    return items


def compute_cache_key(
    *,
    cv_text: str,
    spec_description: str,
    spec_requirements: str,
    criteria: list[dict],
    prompt_version: str,
    model: str,
) -> str:
    """Hash the v4 (or v3) inputs into a deterministic cache key.

    ``bucket`` is included so a recruiter changing must → preferred
    invalidates the cache (the agent reasoning weights buckets differently)."""
    payload = {
        "cv": cv_text or "",
        "spec_description": spec_description or "",
        "spec_requirements": spec_requirements or "",
        "criteria": [
            {
                "id": int(c["id"]),
                "text": str(c.get("text") or ""),
                "must_have": bool(c.get("must_have")),
                "bucket": str(
                    c.get("bucket")
                    or ("must" if bool(c.get("must_have")) else "preferred")
                ),
            }
            for c in criteria
        ],
        "prompt_version": str(prompt_version),
        "model": str(model),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(serialized).hexdigest()


def get_cached_result(db: Session, cache_key: str) -> CvScoreCache | None:
    return db.query(CvScoreCache).filter(CvScoreCache.cache_key == cache_key).first()


def store_cached_result(
    db: Session,
    *,
    cache_key: str,
    prompt_version: str,
    model: str,
    score_100: float | None,
    result: dict,
) -> CvScoreCache:
    existing = get_cached_result(db, cache_key)
    if existing is not None:
        existing.hit_count = (existing.hit_count or 0) + 1
        existing.last_hit_at = datetime.now(timezone.utc)
        return existing
    row = CvScoreCache(
        cache_key=cache_key,
        prompt_version=prompt_version,
        model=model,
        score_100=score_100,
        result=result,
    )
    db.add(row)
    return row
