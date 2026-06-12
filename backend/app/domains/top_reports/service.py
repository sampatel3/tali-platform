"""Mint + scrub helpers for shareable top-candidate reports."""
from __future__ import annotations

import copy
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ...mcp.urls import _frontend_base
from ...models.top_candidates_report import TopCandidatesReport

REPORT_TTL = timedelta(days=30)
# Candidate fields dropped from the snapshot before it is persisted — a
# shareable, no-auth report should not carry direct contact PII.
_SCRUB_FIELDS = ("candidate_email", "candidate_phone")


def generate_report_token() -> str:
    return f"rpt_{secrets.token_urlsafe(24)}"


def report_public_url(token: str) -> str:
    return f"{_frontend_base()}/report/{token}"


def _scrub(snapshot: dict[str, Any]) -> dict[str, Any]:
    snap = copy.deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    for c in snap.get("candidates") or []:
        if isinstance(c, dict):
            for field in _SCRUB_FIELDS:
                c.pop(field, None)
    return snap


def create_report(
    db: Session,
    *,
    organization_id: int,
    created_by_user_id: int | None,
    role_id: int | None,
    query: str,
    snapshot: dict[str, Any],
) -> TopCandidatesReport:
    """Persist a scrubbed snapshot and return the report row (token minted)."""
    report = TopCandidatesReport(
        organization_id=organization_id,
        created_by_user_id=created_by_user_id,
        role_id=role_id,
        token=generate_report_token(),
        query=query,
        snapshot=_scrub(snapshot),
        expires_at=datetime.now(timezone.utc) + REPORT_TTL,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report
