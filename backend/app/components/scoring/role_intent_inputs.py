"""Canonical RoleIntent projection used by every CV scoring path."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session, aliased

from ...models.role_intent import RoleIntent
from ...services.role_intent_text import (
    compact_role_intent_free_text,
    derive_latest_free_text,
)


def load_active_role_intent_with_predecessor(
    db: Session,
    *,
    role_id: int,
    at: datetime | None = None,
) -> tuple[RoleIntent, str | None] | None:
    """Load the active intent and its validated predecessor free text."""
    target = at if at is not None else datetime.now(timezone.utc)
    predecessor = aliased(RoleIntent)
    result = (
        db.query(RoleIntent, predecessor.free_text)
        .outerjoin(
            predecessor,
            and_(
                RoleIntent.superseded_id == predecessor.id,
                RoleIntent.role_id == predecessor.role_id,
                RoleIntent.organization_id == predecessor.organization_id,
            ),
        )
        .filter(
            RoleIntent.role_id == int(role_id),
            RoleIntent.valid_from <= target,
            (RoleIntent.valid_to.is_(None)) | (RoleIntent.valid_to > target),
        )
        .order_by(RoleIntent.version.desc(), RoleIntent.id.desc())
        .first()
    )
    if result is None:
        return None
    row, previous_free_text = result
    return row, previous_free_text


def build_role_intent_scoring_payload(
    *,
    version: int,
    structured: Mapping[str, Any] | None,
    free_text: str | None,
    latest_free_text: str | None,
) -> dict[str, Any]:
    """Build the bounded prompt/cache payload from a validated or DB record."""
    return {
        "version": int(version),
        "structured": dict(structured or {}),
        "free_text": (
            compact_role_intent_free_text(
                free_text,
                latest_free_text=latest_free_text,
            )
            if free_text is not None
            else None
        ),
    }


def active_role_intent_scoring_payload(
    db: Session, *, role_id: int
) -> dict[str, Any] | None:
    """Load the active RoleIntent as the bounded scoring prompt projection."""
    loaded = load_active_role_intent_with_predecessor(
        db,
        role_id=int(role_id),
    )
    if loaded is None:
        return None
    row, previous_free_text = loaded
    free_text = row.free_text
    return build_role_intent_scoring_payload(
        version=int(row.version),
        structured=(
            row.structured_fields
            if isinstance(row.structured_fields, dict)
            else {}
        ),
        free_text=free_text,
        latest_free_text=derive_latest_free_text(
            free_text,
            previous_free_text=previous_free_text,
        ),
    )


def active_role_intent_material_payload(
    db: Session, *, role_id: int
) -> dict[str, Any] | None:
    """Load the full active generation for in-flight scoring fingerprints."""
    loaded = load_active_role_intent_with_predecessor(
        db,
        role_id=int(role_id),
    )
    if loaded is None:
        return None
    row, _previous_free_text = loaded
    return {
        "version": int(row.version),
        "structured": (
            dict(row.structured_fields)
            if isinstance(row.structured_fields, dict)
            else {}
        ),
        "free_text": row.free_text,
    }


def render_role_intent_scoring_overlay(payload: Mapping[str, Any] | None) -> str:
    """Stable JSON text shared by prompts and their content-addressed caches."""
    if not payload:
        return ""
    return json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def append_role_intent_scoring_overlay(
    job_spec_text: str,
    payload: Mapping[str, Any] | None,
) -> str:
    """Append the canonical recruiter-intent block to a base job spec."""
    rendered = render_role_intent_scoring_overlay(payload)
    if not rendered:
        return job_spec_text
    return (
        f"{job_spec_text}\n\nRECRUITER INTENT FOR THIS ROLE:\n{rendered}"
    )


def job_spec_with_active_role_intent(
    db: Session | None, *, role_id: int | None, job_spec_text: str
) -> str:
    """Append the active bounded intent when a persisted role is available."""
    payload = (
        active_role_intent_scoring_payload(db, role_id=int(role_id))
        if db is not None and role_id is not None
        else None
    )
    return append_role_intent_scoring_overlay(job_spec_text, payload)


__all__ = [
    "active_role_intent_material_payload",
    "active_role_intent_scoring_payload",
    "append_role_intent_scoring_overlay",
    "build_role_intent_scoring_payload",
    "job_spec_with_active_role_intent",
    "load_active_role_intent_with_predecessor",
    "render_role_intent_scoring_overlay",
]
