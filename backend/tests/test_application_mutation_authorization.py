"""Application-first mutation authorization contracts on the SQLite suite."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import update

from app.domains.assessments_runtime import application_mutation_authorization
from app.domains.assessments_runtime import related_role_actions
from app.domains.assessments_runtime.application_mutation_authorization import (
    require_application_job_permission,
)
from app.domains.assessments_runtime.job_authorization import JobPermission
from app.domains.assessments_runtime.related_role_actions import (
    require_application_outcome_action,
    require_related_role_application_action,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.user import User
from app.schemas.role import RoleFamilyResponse, RoleReference


def _authorization_world(db, *, suffix: str):
    organization = Organization(
        name=f"Application authorization {suffix}",
        slug=f"application-authorization-{suffix}",
    )
    db.add(organization)
    db.flush()
    role = Role(
        organization_id=int(organization.id),
        name="Canonical role",
        source="manual",
    )
    candidate = Candidate(
        organization_id=int(organization.id),
        email=f"candidate-{suffix}@example.test",
        full_name="Authorization Candidate",
    )
    owner = User(
        email=f"owner-{suffix}@example.test",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        organization_id=int(organization.id),
        role="owner",
    )
    db.add_all([role, candidate, owner])
    db.flush()
    application = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        status="applied",
        pipeline_stage="review",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.commit()
    return organization, role, application, owner


def test_default_mutation_authorization_refreshes_an_identity_mapped_application(db):
    organization, _role, application, owner = _authorization_world(
        db, suffix="refresh"
    )
    assert application.application_outcome == "open"

    db.execute(
        update(CandidateApplication)
        .where(CandidateApplication.id == int(application.id))
        .values(application_outcome="withdrawn")
        .execution_options(synchronize_session=False)
    )
    assert application.application_outcome == "open"

    authorized = require_application_job_permission(
        db,
        current_user=owner,
        application_id=int(application.id),
        permission=JobPermission.EDIT_ROLE,
    )

    assert authorized is application
    assert int(authorized.organization_id) == int(organization.id)
    assert authorized.application_outcome == "withdrawn"


def test_related_authorization_rejects_a_freshly_closed_stale_application(db):
    organization, role, application, owner = _authorization_world(
        db, suffix="related-refresh"
    )
    related_role = Role(
        organization_id=int(organization.id),
        name="Related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(role.id),
    )
    db.add(related_role)
    db.commit()

    db.execute(
        update(CandidateApplication)
        .where(CandidateApplication.id == int(application.id))
        .values(application_outcome="withdrawn")
        .execution_options(synchronize_session=False)
    )
    assert application.application_outcome == "open"

    with pytest.raises(HTTPException) as exc_info:
        require_related_role_application_action(
            db,
            current_user=owner,
            related_role_id=int(related_role.id),
            application=application,
        )

    assert exc_info.value.status_code == 409
    assert "closed shared ATS application" in exc_info.value.detail
    assert application.application_outcome == "withdrawn"


def test_reject_authorization_locks_application_before_current_role_family(db):
    organization, role, application, owner = _authorization_world(
        db, suffix="reject-family-order"
    )
    related_role = Role(
        organization_id=int(organization.id),
        name="Related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(role.id),
    )
    db.add(related_role)
    db.commit()
    expected = RoleFamilyResponse(
        owner=RoleReference(id=int(role.id), name=str(role.name)),
        related=[
            RoleReference(id=int(related_role.id), name=str(related_role.name))
        ],
    )
    call_order: list[str] = []
    lock_application = related_role_actions.lock_application_for_mutation
    lock_families = related_role_actions.lock_current_role_families
    authorize_role = related_role_actions.require_job_permission

    def record_application_lock(*args, **kwargs):
        call_order.append("application")
        return lock_application(*args, **kwargs)

    def record_family_lock(*args, **kwargs):
        call_order.append("family")
        return lock_families(*args, **kwargs)

    def record_role_authorization(*args, **kwargs):
        call_order.append(
            "role_lock" if kwargs.get("lock_for_update", True) else "role_read"
        )
        return authorize_role(*args, **kwargs)

    with (
        patch.object(
            related_role_actions,
            "lock_application_for_mutation",
            side_effect=record_application_lock,
        ),
        patch.object(
            related_role_actions,
            "lock_current_role_families",
            side_effect=record_family_lock,
        ),
        patch.object(
            related_role_actions,
            "require_job_permission",
            side_effect=record_role_authorization,
        ),
    ):
        authorized = require_application_outcome_action(
            db,
            current_user=owner,
            application_id=int(application.id),
            acting_role_id=None,
            target_outcome="rejected",
            expected_role_family=expected,
        )

    assert authorized is application
    assert call_order[:4] == ["application", "role_read", "family", "role_lock"]


def test_explicit_nonlocking_assessment_precheck_keeps_its_existing_path(db):
    _organization, _role, application, owner = _authorization_world(
        db, suffix="assessment-precheck"
    )

    with (
        patch.object(
            application_mutation_authorization,
            "lock_application_for_mutation",
        ) as application_lock,
        patch.object(
            application_mutation_authorization,
            "get_application",
            wraps=application_mutation_authorization.get_application,
        ) as application_load,
        patch.object(
            application_mutation_authorization,
            "require_job_permission",
            wraps=application_mutation_authorization.require_job_permission,
        ) as role_authorization,
    ):
        authorized = require_application_job_permission(
            db,
            current_user=owner,
            application_id=int(application.id),
            permission=JobPermission.CONTROL_AGENT,
            lock_for_update=False,
        )

    assert authorized is application
    application_lock.assert_not_called()
    application_load.assert_called_once_with(
        int(application.id), int(owner.organization_id), db
    )
    assert role_authorization.call_args.kwargs["lock_for_update"] is False
