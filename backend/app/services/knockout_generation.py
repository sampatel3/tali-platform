"""Content-addressed generations for recruiter-authored knockout questions."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from ..models.screening_question import ScreeningQuestion


KNOCKOUT_GENERATION_KEY = "knockout_generation"
KNOCKOUT_GENERATION_UNAVAILABLE = "unavailable"
_GENERATION_PREFIX = "v1:"
_logger = logging.getLogger("taali.knockout_generation")


def capture_knockout_generation(
    db: Session, *, organization_id: int, role_id: int
) -> str:
    """Hash the complete active candidate-facing screening configuration."""
    rows = (
        db.query(
            ScreeningQuestion.id,
            ScreeningQuestion.prompt,
            ScreeningQuestion.kind,
            ScreeningQuestion.options,
            ScreeningQuestion.required,
            ScreeningQuestion.knockout,
            ScreeningQuestion.knockout_expected,
            ScreeningQuestion.position,
        )
        .filter(
            ScreeningQuestion.organization_id == int(organization_id),
            ScreeningQuestion.role_id == int(role_id),
            ScreeningQuestion.is_active.is_(True),
        )
        .order_by(ScreeningQuestion.position.asc(), ScreeningQuestion.id.asc())
        .all()
    )
    payload = [
        {
            "id": int(row.id),
            "prompt": str(row.prompt or ""),
            "kind": str(row.kind or ""),
            "options": row.options,
            "required": bool(row.required),
            "knockout": bool(row.knockout),
            "knockout_expected": row.knockout_expected,
            "position": int(row.position or 0),
        }
        for row in rows
    ]
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return _GENERATION_PREFIX + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def stored_knockout_generation(fingerprint: object) -> str | None:
    """Return the stored token; missing remains the explicit legacy path."""
    if not isinstance(fingerprint, dict) or KNOCKOUT_GENERATION_KEY not in fingerprint:
        return None
    value = fingerprint.get(KNOCKOUT_GENERATION_KEY)
    return str(value) if isinstance(value, str) else KNOCKOUT_GENERATION_UNAVAILABLE


def is_knockout_decision(decision: Any) -> bool:
    """Whether this card is based on native intake rather than a CV score."""
    evidence = getattr(decision, "evidence", None)
    return bool(
        stored_knockout_generation(getattr(decision, "input_fingerprint", None))
        is not None
        or (
            isinstance(evidence, dict)
            and str(evidence.get("source") or "") == "knockout_screening"
        )
    )


def _is_valid_generation(value: str) -> bool:
    digest = value.removeprefix(_GENERATION_PREFIX)
    return (
        value.startswith(_GENERATION_PREFIX)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


def knockout_generation_drift(
    db: Session,
    decision: Any,
    cache: dict[int, str] | None = None,
) -> tuple[str, dict[str, str]] | None:
    """Describe screening-config drift; missing tokens remain legacy-compatible."""
    stored = stored_knockout_generation(
        getattr(decision, "input_fingerprint", None)
    )
    if stored is None:
        return None
    if not _is_valid_generation(stored):
        return (
            "screening_questions_unavailable",
            {"at_emit": stored, "current": KNOCKOUT_GENERATION_UNAVAILABLE},
        )
    role_id: int | None = None
    try:
        role_id = int(decision.role_id)
        if cache is not None and role_id in cache:
            current = cache[role_id]
        else:
            current = capture_knockout_generation(
                db,
                organization_id=int(decision.organization_id),
                role_id=role_id,
            )
            if cache is not None:
                cache[role_id] = current
    except Exception:
        _logger.warning(
            "screening generation verification failed decision_id=%s",
            getattr(decision, "id", None),
            exc_info=True,
        )
        current = KNOCKOUT_GENERATION_UNAVAILABLE
        if cache is not None and role_id is not None:
            cache[role_id] = current
    if not _is_valid_generation(current):
        return (
            "screening_questions_unavailable",
            {"at_emit": stored, "current": current},
        )
    if current != stored:
        return "screening_questions_changed", {"at_emit": stored, "current": current}
    return None


__all__ = [
    "KNOCKOUT_GENERATION_KEY",
    "KNOCKOUT_GENERATION_UNAVAILABLE",
    "capture_knockout_generation",
    "is_knockout_decision",
    "knockout_generation_drift",
    "stored_knockout_generation",
]
