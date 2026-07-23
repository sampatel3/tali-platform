"""Legacy idempotency compatibility for immutable pipeline events."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domains.assessments_runtime.pipeline_event_service import (
    existing_idempotent_event,
)
from app.domains.assessments_runtime.pipeline_service import (
    append_application_event,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.services.related_role_action_service import (
    transition_related_role_stage_action,
)


EVENT_TIME = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _legacy_event_world(db):
    organization = Organization(
        name="Legacy event idempotency",
        slug=f"legacy-event-idempotency-{id(db)}",
    )
    db.add(organization)
    db.flush()
    owner = Role(
        organization_id=int(organization.id),
        name="ATS owner",
        job_spec_text="Owner role specification.",
    )
    related = Role(
        organization_id=int(organization.id),
        name="Independent related role",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=None,
        job_spec_text="Related role specification.",
    )
    db.add_all([owner, related])
    db.flush()
    related.ats_owner_role_id = int(owner.id)
    candidate = Candidate(
        organization_id=int(organization.id),
        email=f"legacy-event-{id(db)}@example.com",
        full_name="Legacy Event Candidate",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(candidate.id),
        role_id=int(owner.id),
        source="manual",
        pipeline_stage="review",
        application_outcome="open",
    )
    db.add(application)
    db.flush()
    membership = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        candidate_id=int(candidate.id),
        source_application_id=int(application.id),
        ats_application_id=int(application.id),
        status="done",
        pipeline_stage="review",
        spec_fingerprint="a" * 64,
        created_at=EVENT_TIME - timedelta(days=1),
    )
    db.add(membership)
    db.flush()
    return owner, related, application, membership


def test_legacy_owner_event_replay_returns_existing_instead_of_colliding(db):
    owner, _related, application, _membership = _legacy_event_world(db)
    legacy = CandidateApplicationEvent(
        organization_id=int(application.organization_id),
        application_id=int(application.id),
        role_id=None,
        event_type="pipeline_stage_changed",
        actor_type="recruiter",
        to_stage="advanced",
        idempotency_key="legacy-owner-retry",
        created_at=EVENT_TIME,
    )
    db.add(legacy)
    db.flush()

    replay = append_application_event(
        db,
        app=application,
        role_id=int(owner.id),
        event_type="pipeline_stage_changed",
        actor_type="recruiter",
        to_stage="advanced",
        idempotency_key="legacy-owner-retry",
    )
    db.flush()

    assert replay is legacy
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == int(application.id),
            CandidateApplicationEvent.idempotency_key == "legacy-owner-retry",
        )
        .count()
        == 1
    )


def test_legacy_related_event_is_idempotent_only_for_its_grounded_role(db):
    owner, related, application, _membership = _legacy_event_world(db)
    legacy = CandidateApplicationEvent(
        organization_id=int(application.organization_id),
        application_id=int(application.id),
        role_id=None,
        event_type="role_pipeline_stage_changed",
        actor_type="recruiter",
        to_stage="advanced",
        event_metadata={"acting_role_id": int(related.id)},
        idempotency_key="legacy-related-retry",
        created_at=EVENT_TIME,
    )
    db.add(legacy)
    db.flush()

    assert (
        existing_idempotent_event(
            db,
            application_id=int(application.id),
            role_id=int(owner.id),
            idempotency_key="legacy-related-retry",
        )
        is None
    )
    assert (
        existing_idempotent_event(
            db,
            application_id=int(application.id),
            role_id=int(related.id),
            idempotency_key="legacy-related-retry",
        )
        is legacy
    )


def test_related_action_replay_cannot_reapply_a_legacy_event(db):
    _owner, related, application, membership = _legacy_event_world(db)
    membership.version = 4
    legacy = CandidateApplicationEvent(
        organization_id=int(application.organization_id),
        application_id=int(application.id),
        role_id=None,
        event_type="role_pipeline_stage_changed",
        actor_type="recruiter",
        from_stage="review",
        to_stage="advanced",
        event_metadata={"acting_role_id": int(related.id)},
        idempotency_key="legacy-related-action-retry",
        created_at=EVENT_TIME,
    )
    db.add(legacy)
    db.flush()

    replay = transition_related_role_stage_action(
        db,
        application=application,
        acting_role_id=int(related.id),
        to_stage="advanced",
        source="recruiter",
        actor_type="recruiter",
        idempotency_key="legacy-related-action-retry",
    )
    db.flush()

    assert replay is not None
    assert replay.changed is False
    assert membership.pipeline_stage == "review"
    assert membership.version == 4
