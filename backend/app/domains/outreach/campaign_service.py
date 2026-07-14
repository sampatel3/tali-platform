"""Outreach campaign service — audience resolution, serialization, rollups.

The policy engine that sits under ``campaign_routes``. Keeps the route module
thin (size gate) and puts the exclusion rails + serialization in one testable
place. No LLM calls, no sends — those live in ``outreach_tasks``.

Audience exclusion rails (hard — encode, don't debate):
- suppressed emails (global OR org-scoped) are never added,
- candidates with an open application PAST the ``sourced`` stage are in-process,
  not outbound targets (``sourced`` leads ARE reachable — that's the point),
- duplicate emails within the campaign are collapsed,
- rows with no usable email are skipped.
Hard cap 200 recipients per campaign.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.outreach_campaign import (
    CAMPAIGN_STATUS_ARCHIVED,
    MESSAGE_STATUS_APPROVED,
    MESSAGE_STATUS_DRAFT,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_PENDING,
    OutreachCampaign,
    OutreachMessage,
)
from ...services.email_suppression_service import normalize_email, suppressed_set
from .campaign_concurrency_service import (
    claim_ready_revision,
    get_owned_campaign,
)
from .campaign_destination_service import (
    default_brief,
    resolve_campaign_destination,
    resolve_job_page_token,
)

AUDIENCE_CAP = 200

# Per-message draft cost (USD) shown in the generate cost-confirm. Mirrors the
# SOURCING_OUTREACH_DRAFT estimate: one cheap Haiku call per recipient.
COST_PER_DRAFT_USD = 0.006


# --------------------------------------------------------------------------- #
# Audience resolution
# --------------------------------------------------------------------------- #


def _open_application_emails(db: Session, org_id: int) -> set[str]:
    """Normalized emails of candidates with an ACTIVE-PIPELINE open application.

    In-process people are not outbound targets. ``sourced`` applications are
    excluded from this rail: a sourced row is a pre-application lead (outcome is
    ``open`` only because nothing has happened yet) and is the whole point of
    outreach. A candidate is blocked only if they hold an open application in a
    real evaluation stage (``applied`` and beyond). One query joins the open
    non-sourced applications to their candidates' emails."""
    rows = (
        db.query(Candidate.email)
        .join(CandidateApplication, CandidateApplication.candidate_id == Candidate.id)
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.application_outcome == "open",
            CandidateApplication.pipeline_stage != "sourced",
            Candidate.email.isnot(None),
        )
        .all()
    )
    return {normalize_email(e) for (e,) in rows if e and normalize_email(e)}


def _open_application_candidate_ids(db: Session, org_id: int) -> set[int]:
    """Candidate ids with an ACTIVE-PIPELINE open application in this org.

    Catches selected sourced applications whose candidate is already active on
    another role, even when the application email snapshot differs. Mirrors
    ``_open_application_emails``: ``sourced`` rows are NOT a block — only open
    applications past the sourced stage are in-process."""
    rows = (
        db.query(CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.application_outcome == "open",
            CandidateApplication.pipeline_stage != "sourced",
            CandidateApplication.candidate_id.isnot(None),
        )
        .all()
    )
    return {int(cid) for (cid,) in rows}


def _resolve_application_recipients(
    db: Session,
    org_id: int,
    application_ids: list[int],
    *,
    campaign_role_id: Optional[int],
) -> list[dict[str, Any]]:
    if not application_ids:
        return []
    rows = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id.in_(set(application_ids)),
            CandidateApplication.organization_id == org_id,
        )
        .all()
    )
    out: list[dict[str, Any]] = []
    for app in rows:
        cand = app.candidate
        email = normalize_email(cand.email) if cand and cand.email else ""
        out.append(
            {
                "candidate_id": cand.id if cand else None,
                "source_application_id": app.id,
                "recipient_name": (cand.full_name if cand else None),
                "email": email,
                "ref": {"id": app.id, "kind": "application"},
                # A role-scoped campaign must never spend that role's budget
                # drafting against another role's application. Keep the row in
                # the result so the caller can report the exclusion honestly.
                "blocked_reason": (
                    "wrong_role"
                    if campaign_role_id is not None
                    and int(app.role_id) != int(campaign_role_id)
                    else None
                ),
            }
        )
    return out


def resolve_audience(
    db: Session,
    *,
    campaign: OutreachCampaign,
    application_ids: list[int],
) -> dict[str, Any]:
    """Resolve recipients, apply the exclusion rails, create pending messages.

    Returns ``{added, skipped: [{email|id, reason}]}``. Raises HTTP 413 (via the
    caller) when the resulting audience would exceed ``AUDIENCE_CAP``. Rails run
    in a fixed order: missing_email → suppressed → open_application → duplicate
    (dup covers both within-batch and already-in-campaign)."""
    from fastapi import HTTPException

    org_id = campaign.organization_id
    candidates = _resolve_application_recipients(
        db,
        org_id,
        application_ids,
        campaign_role_id=campaign.role_id,
    )

    # Bulk suppression check (no N+1) + open-application set.
    all_emails = [c["email"] for c in candidates if c["email"]]
    suppressed = suppressed_set(db, emails=all_emails, organization_id=org_id)
    open_emails = _open_application_emails(db, org_id)
    open_candidate_ids = _open_application_candidate_ids(db, org_id)

    # Emails already in this campaign — the UniqueConstraint would reject them,
    # so filter up front and report as duplicates.
    existing_emails = {
        e
        for (e,) in db.query(OutreachMessage.email)
        .filter(OutreachMessage.campaign_id == campaign.id)
        .all()
    }
    current_count = len(existing_emails)

    added_rows: list[OutreachMessage] = []
    skipped: list[dict[str, Any]] = []
    seen_in_batch: set[str] = set()

    for c in candidates:
        email = c["email"]
        ref_id = c["ref"]["id"]
        if c.get("blocked_reason"):
            skipped.append({"id": ref_id, "email": email or None, "reason": c["blocked_reason"]})
            continue
        if not email:
            skipped.append({"id": ref_id, "reason": "missing_email"})
            continue
        if email in suppressed:
            skipped.append({"email": email, "reason": "suppressed"})
            continue
        if email in open_emails or (
            c.get("candidate_id") and int(c["candidate_id"]) in open_candidate_ids
        ):
            skipped.append({"email": email, "reason": "open_application"})
            continue
        if email in seen_in_batch or email in existing_emails:
            skipped.append({"email": email, "reason": "duplicate"})
            continue
        seen_in_batch.add(email)
        added_rows.append(
            OutreachMessage(
                campaign_id=campaign.id,
                organization_id=org_id,
                candidate_id=c["candidate_id"],
                source_application_id=c["source_application_id"],
                recipient_name=c["recipient_name"],
                email=email,
            )
        )

    if current_count + len(added_rows) > AUDIENCE_CAP:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Audience would exceed the {AUDIENCE_CAP}-recipient cap "
                f"(have {current_count}, adding {len(added_rows)}). Narrow the list."
            ),
        )

    for row in added_rows:
        db.add(row)
    db.commit()

    return {"added": len(added_rows), "skipped": skipped}


# --------------------------------------------------------------------------- #
# Serialization + rollups
# --------------------------------------------------------------------------- #


def compute_counts(db: Session, campaign_id: int) -> dict[str, int]:
    """Denormalized rollup of message states for a campaign."""
    from sqlalchemy import func as sa_func

    rows = (
        db.query(OutreachMessage.status, sa_func.count(OutreachMessage.id))
        .filter(OutreachMessage.campaign_id == campaign_id)
        .group_by(OutreachMessage.status)
        .all()
    )
    by_status = {status: int(n) for status, n in rows}
    audience = sum(by_status.values())
    # Drafted = anything that has a draft or moved past it.
    drafted = sum(
        n
        for s, n in by_status.items()
        if s
        not in ("pending", "drafting")
    )
    return {
        "audience": audience,
        "drafted": drafted,
        "sendable": by_status.get("draft", 0) + by_status.get("approved", 0),
        "approved": by_status.get("approved", 0),
        "sent": by_status.get("sent", 0)
        + by_status.get("delivered", 0)
        + by_status.get("opened", 0)
        + by_status.get("clicked", 0)
        + by_status.get("interested", 0),
        "delivered": by_status.get("delivered", 0)
        + by_status.get("opened", 0)
        + by_status.get("clicked", 0)
        + by_status.get("interested", 0),
        "opened": by_status.get("opened", 0)
        + by_status.get("clicked", 0)
        + by_status.get("interested", 0),
        "clicked": by_status.get("clicked", 0) + by_status.get("interested", 0),
        "interested": by_status.get("interested", 0),
        "bounced": by_status.get("bounced", 0),
        "failed": by_status.get("failed", 0),
    }


def refresh_counts(db: Session, campaign: OutreachCampaign) -> dict[str, int]:
    counts = compute_counts(db, campaign.id)
    campaign.counts = counts
    db.commit()
    return counts


def serialize_message(m: OutreachMessage) -> dict[str, Any]:
    return {
        "id": m.id,
        "campaign_id": m.campaign_id,
        "candidate_id": m.candidate_id,
        "source_application_id": m.source_application_id,
        "recipient_name": m.recipient_name,
        "email": m.email,
        "subject": m.subject,
        "body": m.body,
        "status": m.status,
        "error": m.error,
        "sent_at": m.sent_at.isoformat() if m.sent_at else None,
        "delivered_at": m.delivered_at.isoformat() if m.delivered_at else None,
        "opened_at": m.opened_at.isoformat() if m.opened_at else None,
        "clicked_at": m.clicked_at.isoformat() if m.clicked_at else None,
        "interested_at": m.interested_at.isoformat() if m.interested_at else None,
    }


def serialize_campaign(
    campaign: OutreachCampaign,
    *,
    counts: Optional[dict[str, int]] = None,
    messages: Optional[list[OutreachMessage]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": campaign.id,
        "organization_id": campaign.organization_id,
        "role_id": campaign.role_id,
        "name": campaign.name,
        "status": campaign.status,
        "brief": campaign.brief,
        "job_page_token": campaign.job_page_token,
        "destination_url": getattr(campaign, "destination_url", None),
        "destination_provider": getattr(campaign, "destination_provider", None),
        "origin": getattr(campaign, "origin", "manual") or "manual",
        "prepared_by_agent_run_id": getattr(campaign, "prepared_by_agent_run_id", None),
        "approved_by_user_id": getattr(campaign, "approved_by_user_id", None),
        "approved_at": (
            campaign.approved_at.isoformat()
            if getattr(campaign, "approved_at", None)
            else None
        ),
        "review_revision": int(getattr(campaign, "review_revision", 0) or 0),
        "counts": counts if counts is not None else (campaign.counts or {}),
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
        "updated_at": campaign.updated_at.isoformat() if campaign.updated_at else None,
    }
    if messages is not None:
        payload["messages"] = [serialize_message(m) for m in messages]
    return payload


def approvable_draft_ids(db: Session, campaign_id: int) -> list[int]:
    """Ids of messages currently in ``draft`` — the approve-all target set."""
    return [
        mid
        for (mid,) in db.query(OutreachMessage.id)
        .filter(
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.status == MESSAGE_STATUS_DRAFT,
        )
        .all()
    ]


def approved_count(db: Session, campaign_id: int) -> int:
    return (
        db.query(OutreachMessage)
        .filter(
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.status == MESSAGE_STATUS_APPROVED,
        )
        .count()
    )


def approve_and_send_estimate(
    db: Session, campaign_id: int, org_id: int
) -> dict[str, Any]:
    """What one campaign-level "approve & send all" would actually do.

    ``sendable`` = every message still in ``draft`` or ``approved`` — the set
    the batch approves and enqueues. Suppression is re-checked here (bulk, no
    N+1) so the confirmation is honest about how many will really go out; the
    send task re-checks it again at send time. ``rejected`` (drafted then
    rejected back to ``pending``) and ``failed`` drafts are reported as excluded
    so the recruiter sees exactly who is left out."""
    from ...services.email_suppression_service import suppressed_set

    rows = (
        db.query(OutreachMessage)
        .filter(
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.status.in_(
                [MESSAGE_STATUS_DRAFT, MESSAGE_STATUS_APPROVED]
            ),
        )
        .order_by(OutreachMessage.id.asc())
        .all()
    )
    sendable_emails = [row.email for row in rows if row.email]
    suppressed = suppressed_set(db, emails=sendable_emails, organization_id=org_id)
    suppressed_excluded = sum(1 for email in sendable_emails if email in suppressed)
    review_revision = int(
        db.query(OutreachCampaign.review_revision)
        .filter(
            OutreachCampaign.id == campaign_id,
            OutreachCampaign.organization_id == org_id,
        )
        .scalar()
        or 0
    )

    # Bind the consequential approval to the exact recipient + rendered draft
    # snapshot the UI reviewed. A count alone cannot detect two recipients
    # swapping or a message body changing while the number stays constant.
    review_snapshot = [
        {
            "id": int(row.id),
            "email": row.email,
            "subject": row.subject or "",
            "body": row.body or "",
            "status": row.status,
            "suppressed": bool(row.email in suppressed),
        }
        for row in rows
    ]
    review_token = hashlib.sha256(
        json.dumps(
            {
                "review_revision": review_revision,
                "messages": review_snapshot,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    from sqlalchemy import func as sa_func

    def _count(status: str) -> int:
        return int(
            db.query(sa_func.count(OutreachMessage.id))
            .filter(
                OutreachMessage.campaign_id == campaign_id,
                OutreachMessage.status == status,
            )
            .scalar()
            or 0
        )

    sendable_count = len(rows)
    return {
        "sendable_count": sendable_count,
        "will_send": sendable_count - suppressed_excluded,
        "suppressed_excluded": suppressed_excluded,
        "rejected_excluded": _count(MESSAGE_STATUS_PENDING),
        "failed_excluded": _count(MESSAGE_STATUS_FAILED),
        "review_token": review_token,
        "review_revision": review_revision,
    }


def is_archived(campaign: OutreachCampaign) -> bool:
    return campaign.status == CAMPAIGN_STATUS_ARCHIVED
