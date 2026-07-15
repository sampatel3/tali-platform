"""Autonomously source a bounded audience and prepare outreach drafts.

This is intentionally the last action available to the role agent.  It may
discover people, create ``sourced`` applications, build a campaign, and enqueue
drafting under the role budget.  It can never approve or send a message: the
ready campaign is the single outbound HITL owned by an authenticated user.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.outreach_campaign import (
    CAMPAIGN_STATUS_DRAFT,
    CAMPAIGN_STATUS_GENERATING,
    CAMPAIGN_STATUS_READY,
    CAMPAIGN_STATUS_SENDING,
    MESSAGE_STATUS_PENDING,
    OutreachCampaign,
    OutreachMessage,
)
from ..models.role import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FILLED,
    JOB_STATUS_FILLED_EXTERNAL,
    ROLE_KIND_STANDARD,
)
from ..services.application_events import on_application_created
from ..services.email_suppression_service import normalize_email, suppressed_set
from .source_candidates import run as source_candidate
from .types import ACTOR_AGENT, Actor


MAX_AGENT_OUTREACH_RECIPIENTS = 25
_ACTIVE_CAMPAIGN_STATUSES = (
    CAMPAIGN_STATUS_DRAFT,
    CAMPAIGN_STATUS_GENERATING,
    CAMPAIGN_STATUS_READY,
    CAMPAIGN_STATUS_SENDING,
)


@dataclass(frozen=True)
class PrepareSourcedOutreachResult:
    status: str
    campaign_id: int | None = None
    sourced: int = 0
    audience_added: int = 0
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        from ..services.sourcing_capability_service import (
            linkedin_sourcing_capability,
        )

        return {
            "status": self.status,
            "campaign_id": self.campaign_id,
            "sourced": self.sourced,
            "audience_added": self.audience_added,
            "skipped": self.skipped,
            "send_requires_human_approval": True,
            "external_sourcing": linkedin_sourcing_capability(),
        }


def _dedupe_ids(values: Iterable[int]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for raw in values:
        value = int(raw)
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out[:MAX_AGENT_OUTREACH_RECIPIENTS]


def _active_agent_campaign(db: Session, *, role_id: int, organization_id: int):
    return (
        db.query(OutreachCampaign)
        .filter(
            OutreachCampaign.organization_id == int(organization_id),
            OutreachCampaign.role_id == int(role_id),
            OutreachCampaign.origin == "agent",
            OutreachCampaign.status.in_(_ACTIVE_CAMPAIGN_STATUSES),
        )
        .order_by(OutreachCampaign.id.desc())
        .first()
    )


def _eligible_discovered_candidates(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    candidate_ids: list[int],
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    if not candidate_ids:
        return [], []
    candidates = (
        db.query(Candidate)
        .filter(
            Candidate.id.in_(candidate_ids),
            Candidate.organization_id == int(organization_id),
            Candidate.deleted_at.is_(None),
        )
        .all()
    )
    by_id = {int(candidate.id): candidate for candidate in candidates}

    existing_apps = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.candidate_id.in_(candidate_ids),
            or_(
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.role_id == int(role_id),
                CandidateApplication.application_outcome == "hired",
            ),
        )
        .all()
    )
    blocked_candidates: dict[int, str] = {}
    for app in existing_apps:
        cid = int(app.candidate_id)
        if int(app.role_id) == int(role_id):
            if app.deleted_at is not None:
                blocked_candidates[cid] = "previously_removed"
            elif str(app.pipeline_stage or "").lower() != "sourced":
                blocked_candidates[cid] = "already_on_role"
        elif (
            app.deleted_at is None
            and app.application_outcome == "open"
            and str(app.pipeline_stage or "").lower() != "sourced"
        ):
            blocked_candidates[cid] = "open_application"
        elif app.application_outcome == "hired":
            blocked_candidates[cid] = "hired"

    contacted_ids = {
        int(cid)
        for (cid,) in (
            db.query(OutreachMessage.candidate_id)
            .join(OutreachCampaign, OutreachCampaign.id == OutreachMessage.campaign_id)
            .filter(
                OutreachCampaign.organization_id == int(organization_id),
                OutreachMessage.candidate_id.in_(candidate_ids),
            )
            .all()
        )
        if cid is not None
    }
    # Historical outreach messages may predate candidate/application linkage.
    # Email is therefore part of the same organization-wide contact history
    # rail; discovery must not make an old recipient look new merely because a
    # legacy row has no IDs.
    contacted_emails = {
        normalize_email(value)
        for (value,) in (
            db.query(OutreachMessage.email)
            .filter(
                OutreachMessage.organization_id == int(organization_id),
                OutreachMessage.email.isnot(None),
                OutreachMessage.email != "",
            )
            .all()
        )
        if value and normalize_email(value)
    }
    emails = [
        normalize_email(candidate.email)
        for candidate in candidates
        if candidate.email and normalize_email(candidate.email)
    ]
    suppressed = suppressed_set(
        db, emails=emails, organization_id=int(organization_id)
    )

    eligible: list[Candidate] = []
    skipped: list[dict[str, Any]] = []
    for candidate_id in candidate_ids:
        candidate = by_id.get(int(candidate_id))
        if candidate is None:
            skipped.append({"candidate_id": candidate_id, "reason": "not_found"})
            continue
        email = normalize_email(candidate.email) if candidate.email else ""
        reason = blocked_candidates.get(int(candidate.id))
        if reason is None and not email:
            reason = "missing_email"
        if reason is None and candidate.marketing_consent is False:
            reason = "no_marketing_consent"
        if reason is None and email in suppressed:
            reason = "suppressed"
        if reason is None and int(candidate.id) in contacted_ids:
            reason = "already_contacted"
        if reason is None and email in contacted_emails:
            reason = "already_contacted"
        if reason:
            skipped.append(
                {"candidate_id": int(candidate.id), "email": email or None, "reason": reason}
            )
            continue
        eligible.append(candidate)
    return eligible, skipped


def _uncontacted_sourced_application_ids(
    db: Session, *, role_id: int, organization_id: int
) -> list[int]:
    contacted_applications = (
        db.query(OutreachMessage.source_application_id)
        .filter(
            OutreachMessage.organization_id == int(organization_id),
            OutreachMessage.source_application_id.isnot(None),
        )
        .scalar_subquery()
    )
    contacted_candidates = (
        db.query(OutreachMessage.candidate_id)
        .filter(
            OutreachMessage.organization_id == int(organization_id),
            OutreachMessage.candidate_id.isnot(None),
        )
        .scalar_subquery()
    )
    contacted_emails = (
        db.query(func.lower(func.trim(OutreachMessage.email)))
        .filter(
            OutreachMessage.organization_id == int(organization_id),
            OutreachMessage.email.isnot(None),
            OutreachMessage.email != "",
        )
        .scalar_subquery()
    )
    rows = (
        db.query(CandidateApplication.id)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.role_id == int(role_id),
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.pipeline_stage == "sourced",
            CandidateApplication.application_outcome == "open",
            Candidate.email.isnot(None),
            Candidate.email != "",
            Candidate.marketing_consent.isnot(False),
            ~CandidateApplication.id.in_(contacted_applications),
            ~CandidateApplication.candidate_id.in_(contacted_candidates),
            ~func.lower(func.trim(Candidate.email)).in_(contacted_emails),
        )
        .order_by(CandidateApplication.id.asc())
        .limit(MAX_AGENT_OUTREACH_RECIPIENTS)
        .all()
    )
    return [int(row[0]) for row in rows]


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    role_id: int,
    candidate_ids: Iterable[int] = (),
) -> PrepareSourcedOutreachResult:
    # Lazy imports keep ``role_budget_gate -> agent_runtime -> tool_registry``
    # bootstrapping acyclic. Both modules eventually import the shared action
    # catalogue, so importing them while this module is first loaded creates a
    # partially-initialized package during test/app startup.
    from ..domains.outreach import campaign_service
    from ..services.role_budget_gate import can_spend_on_role

    if actor.type != ACTOR_AGENT or actor.agent_run_id is None:
        raise HTTPException(status_code=403, detail="Agent actor is required")

    from ..domains.assessments_runtime.role_support import get_role

    role = get_role(int(role_id), int(organization_id), db)
    if not role.agentic_mode_enabled or role.agent_paused_at is not None:
        return PrepareSourcedOutreachResult(status="agent_not_running")
    if str(getattr(role, "role_kind", ROLE_KIND_STANDARD)) != ROLE_KIND_STANDARD:
        return PrepareSourcedOutreachResult(status="unsupported_sister_role")
    if role.job_status in {
        JOB_STATUS_FILLED,
        JOB_STATUS_FILLED_EXTERNAL,
        JOB_STATUS_CANCELLED,
    }:
        return PrepareSourcedOutreachResult(status="role_closed")
    if not can_spend_on_role(db, role=role):
        db.commit()  # persist the budget gate's auto-pause, when applicable
        return PrepareSourcedOutreachResult(status="role_budget_exhausted")

    destination = campaign_service.resolve_campaign_destination(db, int(role.id))
    # Outbound must lead somewhere the candidate can actually apply.  Native
    # roles need a published JobPage; external roles need a validated provider
    # URL. A generic click/thanks page is not a substitute for an agent-run
    # sourcing flow, so missing publication is one deterministic setup blocker.
    if destination.get("status") != "ready":
        return PrepareSourcedOutreachResult(status="application_destination_required")

    active = _active_agent_campaign(
        db, role_id=int(role.id), organization_id=int(organization_id)
    )
    if active is not None and active.status != CAMPAIGN_STATUS_DRAFT:
        return PrepareSourcedOutreachResult(
            status=str(active.status), campaign_id=int(active.id)
        )

    requested_ids = _dedupe_ids(candidate_ids)
    candidates, skipped = _eligible_discovered_candidates(
        db,
        role_id=int(role.id),
        organization_id=int(organization_id),
        candidate_ids=requested_ids,
    )
    sourced_results = []
    for candidate in candidates:
        try:
            result = source_candidate(
                db,
                actor,
                organization_id=int(organization_id),
                role_id=int(role.id),
                candidate_id=int(candidate.id),
                source_name="internal_talent_pool",
                allow_reactivation=False,
            )
            sourced_results.append(result)
        except HTTPException as exc:
            skipped.append(
                {
                    "candidate_id": int(candidate.id),
                    "reason": "source_blocked",
                    "detail": str(exc.detail),
                }
            )
    db.flush()

    application_ids = _uncontacted_sourced_application_ids(
        db, role_id=int(role.id), organization_id=int(organization_id)
    )
    campaign = active
    existing_pending = 0
    if campaign is not None:
        existing_pending = (
            db.query(OutreachMessage.id)
            .filter(
                OutreachMessage.campaign_id == int(campaign.id),
                OutreachMessage.status == MESSAGE_STATUS_PENDING,
            )
            .count()
        )
    if not application_ids and existing_pending <= 0:
        db.commit()
        return PrepareSourcedOutreachResult(
            status="no_reachable_candidates",
            sourced=sum(1 for result in sourced_results if result.created_or_reactivated),
            skipped=skipped,
        )

    if campaign is None:
        digest = hashlib.sha256(
            ",".join(str(value) for value in sorted(application_ids)).encode("utf-8")
        ).hexdigest()[:20]
        campaign = OutreachCampaign(
            organization_id=int(organization_id),
            role_id=int(role.id),
            name=f"Taali agent outreach · {role.name}",
            brief=campaign_service.default_brief(role.name, role.job_spec_text),
            job_page_token=destination.get("job_page_token"),
            destination_url=destination.get("destination_url"),
            destination_provider=destination.get("provider"),
            origin="agent",
            prepared_by_agent_run_id=int(actor.agent_run_id),
            idempotency_key=f"agent-outreach:{int(role.id)}:{digest}",
        )
        db.add(campaign)
        db.flush()

    audience = (
        campaign_service.resolve_audience(
            db,
            campaign=campaign,
            application_ids=application_ids,
        )
        if application_ids
        else {"added": 0, "skipped": []}
    )
    all_skipped = [*skipped, *(audience.get("skipped") or [])]
    audience_added = int(audience.get("added") or 0)
    pending_count = (
        db.query(OutreachMessage.id)
        .filter(
            OutreachMessage.campaign_id == int(campaign.id),
            OutreachMessage.status == MESSAGE_STATUS_PENDING,
        )
        .count()
    )
    if pending_count <= 0:
        return PrepareSourcedOutreachResult(
            status="no_reachable_candidates",
            campaign_id=int(campaign.id),
            sourced=sum(1 for result in sourced_results if result.created_or_reactivated),
            skipped=all_skipped,
        )

    claimed = (
        db.query(OutreachCampaign)
        .filter(
            OutreachCampaign.id == int(campaign.id),
            OutreachCampaign.organization_id == int(organization_id),
            OutreachCampaign.status == CAMPAIGN_STATUS_DRAFT,
        )
        .update(
            {OutreachCampaign.status: CAMPAIGN_STATUS_GENERATING},
            synchronize_session=False,
        )
    )
    if claimed != 1:
        db.rollback()
        winner = _active_agent_campaign(
            db, role_id=int(role.id), organization_id=int(organization_id)
        )
        return PrepareSourcedOutreachResult(
            status=str(winner.status) if winner is not None else "claim_conflict",
            campaign_id=int(winner.id) if winner is not None else None,
            skipped=all_skipped,
        )
    db.commit()

    # The row must be visible before Celery receives the id.  If broker publish
    # fails, put the campaign back in draft so the next idempotent agent cycle
    # can retry instead of stranding a false "generating" state.
    try:
        from ..tasks.outreach_tasks import generate_campaign_drafts

        generate_campaign_drafts.delay(int(campaign.id))
    except Exception as exc:  # noqa: BLE001 - durable compensation
        live = db.get(OutreachCampaign, int(campaign.id))
        if live is not None and live.status == CAMPAIGN_STATUS_GENERATING:
            live.status = CAMPAIGN_STATUS_DRAFT
            db.commit()
        return PrepareSourcedOutreachResult(
            status="draft_enqueue_failed",
            campaign_id=int(campaign.id),
            sourced=sum(1 for result in sourced_results if result.created_or_reactivated),
            audience_added=audience_added,
            skipped=[*all_skipped, {"reason": "enqueue_failed", "detail": str(exc)[:300]}],
        )

    for result in sourced_results:
        if not result.created_or_reactivated:
            continue
        app = db.get(CandidateApplication, int(result.application_id))
        if app is not None:
            on_application_created(
                app,
                score=False,
                allow_paid_work=False,
                parse_origin=None,
            )
    return PrepareSourcedOutreachResult(
        status=CAMPAIGN_STATUS_GENERATING,
        campaign_id=int(campaign.id),
        sourced=sum(1 for result in sourced_results if result.created_or_reactivated),
        audience_added=audience_added,
        skipped=all_skipped,
    )
