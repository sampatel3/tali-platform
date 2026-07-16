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
# Candidate fields dropped from the snapshot before it is persisted. A public,
# no-auth report needs the recruiter's chosen evidence and scores, not database
# identifiers, private ATS links, or internal navigation URLs.
_SCRUB_FIELDS = (
    "application_id",
    "application_outcome",
    "ats_context",
    "auto_reject_state",
    "bullhorn_status",
    "candidate_id",
    "candidate_email",
    "candidate_phone",
    "created_at",
    "external_stage_normalized",
    "frontend_url",
    "pipeline_stage",
    "pipeline_stage_updated_at",
    "role_id",
    "workable_stage",
    "workable_profile_url",
)


def generate_report_token() -> str:
    return f"rpt_{secrets.token_urlsafe(24)}"


def report_public_url(token: str) -> str:
    return f"{_frontend_base()}/report/{token}"


def _scrub(snapshot: dict[str, Any]) -> dict[str, Any]:
    snap = copy.deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    snap.pop("rescore_candidate_ids", None)
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
    """Stage a scrubbed report and return its token-bearing row.

    The caller owns the transaction. This keeps a confirmed chat action's
    public report and consumed-confirmation receipt atomic: a worker crash
    cannot publish a link while leaving the same approval reusable.
    """
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
    db.flush()
    db.refresh(report)
    return report
