"""Add existing or newly-resolved people to a role as sourced leads.

The recruiter route and autonomous sourcing tools share this action so the
identity, idempotency, audit, and no-scoring rules cannot drift.  A sourced
lead is deliberately pre-application: it has no paid parsing/scoring work and
never enters the evaluation decision queue until the person actually applies.

The caller owns the transaction and the cheap ``on_application_created``
notification.  Keeping the notification outside this action lets callers
commit the new row before a Celery worker attempts to read it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..components.assessments.repository import utcnow
from ..domains.assessments_runtime.pipeline_service import (
    initialize_pipeline_event_if_missing,
)
from ..domains.assessments_runtime.role_support import get_role
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..services.candidate_identity_service import normalize_phone, resolve_candidate
from .types import Actor


@dataclass(frozen=True)
class SourceCandidateResult:
    application_id: int
    candidate_id: int
    status: str  # created | reactivated | existing

    @property
    def created_or_reactivated(self) -> bool:
        return self.status in {"created", "reactivated"}

    def as_dict(self) -> dict:
        return {
            "application_id": self.application_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
        }


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    role_id: int,
    candidate_id: Optional[int] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    name: Optional[str] = None,
    position: Optional[str] = None,
    linkedin: Optional[str] = None,
    source_name: Optional[str] = None,
    allow_reactivation: bool = False,
) -> SourceCandidateResult:
    """Idempotently put one person on ``role_id`` at ``pipeline_stage=sourced``.

    ``candidate_id`` is the autonomous internal-rediscovery path.  Identity
    fields are the integration/manual-fallback path.  At least one path is
    required, and every lookup is organization-scoped.
    """

    role = get_role(role_id, organization_id, db)
    normalized_email = (email or "").strip().lower() or None
    normalized_phone = (phone or "").strip() or None

    candidate: Candidate | None = None
    if candidate_id is not None:
        candidate = (
            db.query(Candidate)
            .filter(
                Candidate.id == int(candidate_id),
                Candidate.organization_id == int(organization_id),
                Candidate.deleted_at.is_(None),
            )
            .first()
        )
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
    elif normalized_email or normalized_phone:
        candidate = resolve_candidate(
            db,
            int(organization_id),
            email=normalized_email,
            phone=normalized_phone,
        )
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide candidate_id, email address, or phone number.",
        )

    if candidate is None:
        candidate = Candidate(
            organization_id=int(organization_id),
            email=normalized_email,
            full_name=(name or "").strip() or None,
            position=(position or "").strip() or None,
            phone=normalized_phone,
            phone_normalized=normalize_phone(normalized_phone),
            profile_url=(linkedin or "").strip() or None,
            lead_source=(source_name or "sourced").strip() or "sourced",
        )
        db.add(candidate)
        db.flush()
    else:
        # Sourcing may enrich empty identity fields, but must never overwrite a
        # candidate-owned or ATS-synced value with discovery-provider data.
        if not candidate.email and normalized_email:
            candidate.email = normalized_email
        if not candidate.phone and normalized_phone:
            candidate.phone = normalized_phone
            candidate.phone_normalized = normalize_phone(normalized_phone)
        if not candidate.full_name and (name or "").strip():
            candidate.full_name = name.strip()
        if not candidate.position and (position or "").strip():
            candidate.position = position.strip()
        if not candidate.profile_url and (linkedin or "").strip():
            candidate.profile_url = linkedin.strip()
        if not candidate.lead_source:
            candidate.lead_source = (source_name or "sourced").strip() or "sourced"

    existing = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.candidate_id == int(candidate.id),
            CandidateApplication.role_id == int(role.id),
        )
        .first()
    )
    if existing is not None and existing.deleted_at is None:
        return SourceCandidateResult(
            application_id=int(existing.id),
            candidate_id=int(candidate.id),
            status="existing",
        )

    if existing is not None and not allow_reactivation:
        # Soft deletion may represent erasure, a do-not-source request, or a
        # terminal recruiter removal.  Autonomous discovery must never reverse
        # it.  The recruiter fallback can opt in explicitly after reviewing the
        # record, preserving the legacy route behaviour.
        raise HTTPException(
            status_code=409,
            detail="Candidate was previously removed from this role and requires manual review",
        )

    now = utcnow()
    source_label = (source_name or "sourced").strip() or "sourced"
    sourcing_refs = {
        "provider": source_label,
        "sourced_at": now.isoformat(),
        "actor_type": actor.type,
        "agent_run_id": actor.agent_run_id,
    }
    if existing is not None:
        existing.deleted_at = None
        existing.status = "sourced"
        existing.pipeline_stage = "sourced"
        existing.pipeline_stage_source = actor.type
        existing.pipeline_stage_updated_at = now
        existing.application_outcome = "open"
        existing.application_outcome_updated_at = now
        existing.source = "sourced"
        existing.source_strategy = "sourced"
        existing.source_name = source_label
        existing.external_refs = {
            **(existing.external_refs if isinstance(existing.external_refs, dict) else {}),
            "sourcing": sourcing_refs,
        }
        existing.auto_reject_state = None
        existing.auto_reject_reason = None
        existing.auto_reject_triggered_at = None
        app = existing
        result_status = "reactivated"
    else:
        app = CandidateApplication(
            organization_id=int(organization_id),
            candidate_id=int(candidate.id),
            role_id=int(role.id),
            status="sourced",
            pipeline_stage="sourced",
            pipeline_stage_updated_at=now,
            pipeline_stage_source=actor.type,
            application_outcome="open",
            application_outcome_updated_at=now,
            source="sourced",
            source_strategy="sourced",
            source_name=source_label,
            external_refs={"sourcing": sourcing_refs},
        )
        db.add(app)
        result_status = "created"

    db.flush()
    initialize_pipeline_event_if_missing(
        db,
        app=app,
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason="Candidate sourced for role",
    )
    return SourceCandidateResult(
        application_id=int(app.id),
        candidate_id=int(candidate.id),
        status=result_status,
    )
