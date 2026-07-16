from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.role_change_event import RoleChangeEvent
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from tests.conftest import auth_headers


def test_delete_related_role_archives_without_losing_scores_or_history(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner = Role(
        organization_id=user.organization_id,
        name="Original platform role",
        source="manual",
    )
    candidate = Candidate(
        organization_id=user.organization_id,
        email="retained-related-score@example.test",
        full_name="Retained Candidate",
        cv_text="Python and distributed systems",
    )
    db.add_all([owner, candidate])
    db.flush()
    application = CandidateApplication(
        organization_id=user.organization_id,
        role_id=owner.id,
        candidate_id=candidate.id,
        source="manual",
        application_outcome="open",
    )
    related = Role(
        organization_id=user.organization_id,
        name="Related reliability role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
        agentic_mode_enabled=True,
    )
    db.add_all([application, related])
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=user.organization_id,
        role_id=related.id,
        source_application_id=application.id,
        status="done",
        spec_fingerprint="spec-retained",
        cv_fingerprint="cv-retained",
        role_fit_score=91.5,
        summary="Retain this current score.",
        details={"strengths": ["Python", "reliability"]},
        history=[
            {"role_fit_score": 77.0, "summary": "Retain this prior score."}
        ],
    )
    db.add(evaluation)
    db.commit()
    related_id = int(related.id)
    evaluation_id = int(evaluation.id)
    original_history = evaluation.history

    response = client.delete(
        f"/api/v1/roles/{related_id}",
        params={"expected_version": int(related.version)},
        headers=headers,
    )

    assert response.status_code == 204, response.text
    db.expire_all()
    retained_role = db.get(Role, related_id)
    retained_evaluation = db.get(SisterRoleEvaluation, evaluation_id)
    assert retained_role is not None
    assert retained_role.deleted_at is not None
    assert retained_role.agentic_mode_enabled is False
    assert retained_evaluation is not None
    assert retained_evaluation.role_id == related_id
    assert retained_evaluation.role_fit_score == 91.5
    assert retained_evaluation.summary == "Retain this current score."
    assert retained_evaluation.history == original_history
    audit = (
        db.query(RoleChangeEvent)
        .filter(
            RoleChangeEvent.role_id == related_id,
            RoleChangeEvent.action == "role_deleted",
        )
        .one()
    )
    assert audit.reason == "role archived to preserve related-role scoring history"
    assert audit.changes["deleted_at"]["after"] is not None


def test_delete_original_role_with_related_child_is_rejected_without_changes(
    client, db
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner = Role(
        organization_id=user.organization_id,
        name="Original empty role",
        source="manual",
    )
    related = Role(
        organization_id=user.organization_id,
        name="Related empty role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role=owner,
    )
    db.add_all([owner, related])
    db.commit()
    owner_id = int(owner.id)
    related_id = int(related.id)

    response = client.delete(
        f"/api/v1/roles/{owner_id}",
        params={"expected_version": int(owner.version)},
        headers=headers,
    )

    assert response.status_code == 400, response.text
    assert "related roles" in response.json()["detail"].lower()
    db.expire_all()
    assert db.get(Role, owner_id) is not None
    assert db.get(Role, related_id) is not None
    assert db.get(Role, related_id).ats_owner_role_id == owner_id


def test_loaded_related_collection_cannot_be_orphaned_by_orm_parent_delete(db):
    organization = Organization(
        name="ORM retention test organization",
        slug="orm-related-retention-test",
    )
    db.add(organization)
    db.flush()
    owner = Role(
        organization_id=int(organization.id),
        name="ORM-protected original role",
        source="manual",
    )
    related = Role(
        organization_id=int(organization.id),
        name="ORM-protected related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role=owner,
    )
    db.add_all([owner, related])
    db.commit()
    owner_id = int(owner.id)
    related_id = int(related.id)

    # Loading the collection is the important adversarial case: ordinary
    # passive deletes may otherwise convert the child FK to NULL before the
    # database can enforce its RESTRICT constraint.
    assert [int(item.id) for item in owner.sister_roles] == [related_id]
    db.delete(owner)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    db.expire_all()
    retained_owner = db.get(Role, owner_id)
    retained_related = db.get(Role, related_id)
    assert retained_owner is not None
    assert retained_related is not None
    assert retained_related.ats_owner_role_id == owner_id
