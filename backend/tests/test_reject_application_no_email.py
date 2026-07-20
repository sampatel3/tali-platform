"""reject_application's Workable disqualify runs even without local email.

Codex flagged on PR #82: the Workable disqualify call was nested under
``if candidate_email`` so applications imported without a local email
were rejected in Taali but never moved in Workable.
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
    """The Workable disqualify must run regardless of the local email — it's how
    the candidate is notified (via Workable's own workflow). Taali emails no one."""
    org, user, role, app = _make_world(db, candidate_email=None)
    actor = Actor.recruiter(user)
    with patch(
        "app.actions.reject_application._try_workable_disqualify",
        return_value="handled",
    ) as wk, patch(
        "app.components.notifications.email_client.resend.Emails.send"
    ) as mock_resend:
        reject_application.run(
            db,
            actor,
            organization_id=org.id,
            application_id=int(app.id),
            reason="Bottom decile",
            idempotency_key="t1",
        )
    wk.assert_called_once()
    assert wk.call_args.kwargs["reason"] == "Bottom decile"
    # Taali never emails the candidate about the job.
    mock_resend.assert_not_called()


def test_no_candidate_email_even_when_workable_falls_back(db):
    """When Workable can't be written (returns "fallback"), the reject lands
    locally and Taali still sends the candidate NO email — job comms belong to
    the ATS, not Taali."""
    org, user, role, app = _make_world(db, candidate_email="c@x.test")
    actor = Actor.recruiter(user)
    with patch(
        "app.actions.reject_application._try_workable_disqualify",
        return_value="fallback",
    ) as wk, patch(
        "app.components.notifications.email_client.resend.Emails.send"
    ) as mock_resend:
        reject_application.run(
            db,
            actor,
            organization_id=org.id,
            application_id=int(app.id),
            reason="Bottom decile",
            idempotency_key="t2",
        )
    wk.assert_called_once()
    mock_resend.assert_not_called()


def test_notify_rejection_reports_only_confirmed_ats_write(db):
    org, user, _, app = _make_world(db, candidate_email="c@x.test")
    actor = Actor.recruiter(user)

    with patch(
        "app.actions.reject_application._try_bullhorn_reject",
        return_value=None,
    ), patch(
        "app.actions.reject_application._try_workable_disqualify",
        side_effect=("handled", "fallback", "retry_scheduled"),
    ):
        assert reject_application.notify_rejection(
            db, app=app, actor=actor, reason="Bottom decile"
        ) is True
        assert reject_application.notify_rejection(
            db, app=app, actor=actor, reason="Bottom decile"
        ) is False
        assert reject_application.notify_rejection(
            db, app=app, actor=actor, reason="Bottom decile"
        ) is False


def test_decision_hub_rejection_does_not_add_a_workable_disqualify_note(
    db, monkeypatch
):
    org, user, _, app = _make_world(db, candidate_email="c@x.test")
    monkeypatch.setattr(reject_application.settings, "MVP_DISABLE_WORKABLE", False)

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value={"success": True, "config": {}, "message": "Disqualified"},
    ) as disqualify:
        result = reject_application._try_workable_disqualify(
            db,
            app=app,
            org=org,
            actor=Actor.recruiter(user),
            reason=None,
        )

    assert result == "handled"
    assert disqualify.call_args.kwargs["reason"] is None


def test_already_disqualified_workable_application_is_not_written_or_reported_as_moved(db):
    org, user, _, app = _make_world(db, candidate_email="c@x.test")
    app.workable_disqualified = True
    db.flush()

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable"
    ) as disqualify:
        result = reject_application._try_workable_disqualify(
            db,
            app=app,
            org=org,
            actor=Actor.recruiter(user),
            reason=None,
        )

    assert result == "already_disqualified"
    disqualify.assert_not_called()

    with patch(
        "app.actions.reject_application._try_bullhorn_reject",
        return_value=None,
    ):
        assert reject_application.notify_rejection(
            db,
            app=app,
            actor=Actor.recruiter(user),
            reason=None,
        ) is False


def test_notify_rejection_reports_confirmed_bullhorn_write(db):
    _, user, _, app = _make_world(db, candidate_email="c@x.test")
    actor = Actor.recruiter(user)

    with patch(
        "app.actions.reject_application._try_bullhorn_reject",
        return_value=True,
    ), patch(
        "app.actions.reject_application._try_workable_disqualify"
    ) as workable:
        assert reject_application.notify_rejection(
            db, app=app, actor=actor, reason="Bottom decile"
        ) is True

    workable.assert_not_called()

    with patch(
        "app.actions.reject_application._try_bullhorn_reject",
        return_value=False,
    ), patch(
        "app.actions.reject_application._try_workable_disqualify"
    ) as workable:
        assert reject_application.notify_rejection(
            db, app=app, actor=actor, reason="Bottom decile"
        ) is False

    workable.assert_not_called()


def test_exact_target_bullhorn_rejection_is_not_reported_as_new_movement(db):
    from app.components.integrations.bullhorn.provider import BullhornProvider
    from app.models.candidate_application_event import CandidateApplicationEvent

    org, user, _, app = _make_world(db, candidate_email="c@x.test")
    app.bullhorn_job_submission_id = "submission-9"
    provider = BullhornProvider(org, db)

    with patch(
        "app.components.integrations.resolver.resolve_application_ats_provider",
        return_value=provider,
    ), patch.object(
        provider,
        "reject_application",
        return_value={
            "success": True,
            "skipped": True,
            "code": "already_at_target",
            "config": {"remote_status": "Client Rejected"},
        },
    ):
        moved = reject_application._try_bullhorn_reject(
            db,
            app=app,
            org=org,
            actor=Actor.recruiter(user),
            reason=None,
        )

    assert moved is False
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "bullhorn_rejected",
        )
        .count()
        == 0
    )

def test_notify_rejection_reports_unlinked_application_as_unconfirmed(db):
    _, user, _, app = _make_world(db, candidate_email="c@x.test")
    app.workable_candidate_id = None
    db.flush()

    assert reject_application.notify_rejection(
        db,
        app=app,
        actor=Actor.recruiter(user),
        reason="Bottom decile",
    ) is False
