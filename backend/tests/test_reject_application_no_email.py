"""reject_application's Workable disqualify runs even without local email.

Codex flagged on PR #82: the Workable disqualify call was nested under
``if candidate_email`` so applications imported without a local email
were rejected in Tali but never moved in Workable.
"""

from __future__ import annotations

from unittest.mock import patch

from app.actions import reject_application
from app.actions.types import Actor
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


def _make_world(db, *, candidate_email: str | None):
    org = Organization(
        name=f"Reject Org {id(db)}",
        slug=f"reject-org-{id(db)}",
        workable_connected=True,
        workable_access_token="x",
        workable_subdomain="acme",
    )
    db.add(org)
    db.flush()
    user = User(
        organization_id=org.id,
        email=f"u-{id(db)}@x.test",
        hashed_password="x",
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email=candidate_email,
        full_name="Cand",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        cv_text="x",
        workable_candidate_id="wk-123",
    )
    db.add(app)
    db.flush()
    db.commit()
    return org, user, role, app


def test_workable_disqualify_called_even_when_candidate_email_missing(db):
    """Old behaviour skipped Workable when local email was empty."""
    org, user, role, app = _make_world(db, candidate_email=None)
    actor = Actor.recruiter(user)
    with patch(
        "app.actions.reject_application._try_workable_disqualify",
        return_value="handled",
    ) as wk, patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as email_mock:
        reject_application.run(
            db,
            actor,
            organization_id=org.id,
            application_id=int(app.id),
            reason="Bottom decile",
            idempotency_key="t1",
        )
    wk.assert_called_once()
    # No local email present, so the Taali-branded fallback shouldn't fire.
    email_mock.assert_not_called()


def test_taali_fallback_only_when_email_present_and_workable_failed(db):
    """When Workable disqualify fails non-retriably AND we have an email, send fallback."""
    org, user, role, app = _make_world(db, candidate_email="c@x.test")
    actor = Actor.recruiter(user)
    with patch(
        "app.actions.reject_application._try_workable_disqualify",
        return_value="fallback",
    ) as wk, patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as email_mock:
        reject_application.run(
            db,
            actor,
            organization_id=org.id,
            application_id=int(app.id),
            reason="Bottom decile",
            idempotency_key="t2",
        )
    wk.assert_called_once()
    email_mock.assert_called_once()
