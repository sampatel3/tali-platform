"""reject_application: the ATS owns candidate job comms — Taali never emails.

Policy (see ``app/actions/reject_application.py`` module docstring): rejecting a
candidate disqualifies them in Workable — whose own disqualify-stage workflow
notifies the candidate — and Taali sends NO candidate-facing email, not even a
fallback when Workable can't be written. Taali only ever emails candidates
about the assessment (invite / expiry reminder / feedback), never about a
hiring decision.

These tests lock that policy:
- the candidate-rejection email capability no longer exists, and
- rejecting attempts the Workable disqualify + records the audit events, and
- when Workable can't be used, the reject still lands locally with no email.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.actions import reject_application
from app.actions.types import Actor
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


def _make_org(db, *, name: str = "Acme Hiring", workable_connected: bool = False) -> Organization:
    org = Organization(
        name=name,
        slug=f"acme-{id(db)}",
        workable_connected=workable_connected,
        workable_access_token=("token-xyz" if workable_connected else None),
        workable_subdomain=("acme-test" if workable_connected else None),
        workable_config=(
            {
                "granted_scopes": ["r_candidates", "r_jobs", "w_candidates"],
                "workable_actor_member_id": "member-123",
                "workable_disqualify_reason_id": "not_a_fit",
            }
            if workable_connected
            else {}
        ),
    )
    db.add(org)
    db.flush()
    return org


def _make_role(db, org: Organization, *, name: str = "Senior Backend") -> Role:
    role = Role(organization_id=org.id, name=name, source="manual")
    db.add(role)
    db.flush()
    return role


def _make_application(
    db,
    *,
    org: Organization,
    role: Role | None,
    email: str = "alice@x.test",
    full_name: str = "Alice",
    position: str | None = "Engineer",
    workable_candidate_id: str | None = None,
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=org.id, email=email, full_name=full_name, position=position,
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id if role else None,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        workable_candidate_id=workable_candidate_id,
    )
    db.add(app)
    db.flush()
    return app


def _make_recruiter(db, org: Organization) -> User:
    user = User(
        email=f"r-{id(db)}@x.test",
        hashed_password="x",
        full_name="Recruiter",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------------------
# Policy: the candidate-rejection email capability is gone for good.
# ---------------------------------------------------------------------------


def test_no_rejection_email_method_on_email_service():
    from app.components.notifications.email_client import EmailService

    assert not hasattr(EmailService, "send_application_rejected")


def test_no_rejection_email_celery_task():
    from app.components.notifications import tasks as notification_tasks

    assert not hasattr(notification_tasks, "send_application_rejected_email")


def test_reject_module_has_no_email_dispatcher():
    # The fallback-email dispatcher was removed entirely.
    assert not hasattr(reject_application, "_dispatch_rejection_email")


# ---------------------------------------------------------------------------
# Rejecting never sends a candidate email — even when Workable can't be used.
# ---------------------------------------------------------------------------


def test_reject_unlinked_app_sends_no_candidate_email(db, monkeypatch):
    """Org connected to Workable but the app predates the link (no
    workable_candidate_id) → Workable is skipped, the reject lands locally,
    and NO candidate email is sent."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, workable_candidate_id=None)
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable"
    ) as mock_workable, patch(
        "app.components.notifications.email_client.resend.Emails.send"
    ) as mock_resend:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        db.commit()

    assert mock_workable.called is False  # unlinked → no Workable write
    assert mock_resend.called is False  # and no Taali email
    db.refresh(app)
    assert app.application_outcome == "rejected"


def test_reject_org_not_connected_sends_no_candidate_email(db, monkeypatch):
    """App is linked to Workable but the org's connection lapsed → Workable is
    skipped and the candidate is not emailed by Taali."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=False)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, workable_candidate_id="wkbl_x")
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable"
    ) as mock_workable, patch(
        "app.components.notifications.email_client.resend.Emails.send"
    ) as mock_resend:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        db.commit()

    assert mock_workable.called is False
    assert mock_resend.called is False
    db.refresh(app)
    assert app.application_outcome == "rejected"


# ---------------------------------------------------------------------------
# Workable-first behaviour preserved — the ATS owns candidate notification.
# ---------------------------------------------------------------------------


def test_workable_disqualify_success_records_event_no_email(db, monkeypatch):
    """Successful Workable disqualify records the audit event (Workable's own
    workflow emails the candidate) and Taali sends nothing."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, workable_candidate_id="wkbl_cand_001")
    recruiter = _make_recruiter(db, org)

    result = {
        "success": True,
        "action": "disqualify",
        "code": "ok",
        "message": "Candidate disqualified in Workable",
        "config": {"actor_member_id": "member-123", "workable_disqualify_reason_id": "not_a_fit"},
    }
    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value=result,
    ) as mock_workable, patch(
        "app.components.notifications.email_client.resend.Emails.send"
    ) as mock_resend:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        db.commit()

    assert mock_workable.called
    assert mock_resend.called is False
    events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "workable_disqualified",
        )
        .all()
    )
    assert len(events) == 1
    assert (events[0].event_metadata or {}).get("workable_candidate_id") == "wkbl_cand_001"


def test_workable_transient_failure_schedules_retry_no_email(db, monkeypatch):
    """A transient api_error (e.g. 429) schedules a bounded background retry to
    push the disqualify through; Taali still sends no candidate email."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, workable_candidate_id="wkbl_cand_002")
    recruiter = _make_recruiter(db, org)

    failure = {
        "success": False,
        "action": "disqualify",
        "code": "api_error",
        "message": "Client error '429 Too Many Requests'",
    }
    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value=failure,
    ), patch(
        "app.tasks.workable_tasks.retry_workable_disqualify_task"
    ) as mock_retry, patch(
        "app.components.notifications.email_client.resend.Emails.send"
    ) as mock_resend:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        db.commit()

    assert mock_retry.apply_async.called
    assert mock_resend.called is False
    db.refresh(app)
    assert app.application_outcome == "rejected"


def test_workable_nonretriable_failure_records_event_no_email(db, monkeypatch):
    """A non-API failure won't self-heal — record it and stop. No retry, and the
    candidate is NOT emailed (was previously a Taali fallback email)."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, workable_candidate_id="wkbl_cand_003")
    recruiter = _make_recruiter(db, org)

    failure = {
        "success": False,
        "action": "disqualify",
        "code": "missing_candidate_id",
        "message": "Candidate is not linked to Workable",
    }
    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value=failure,
    ), patch(
        "app.tasks.workable_tasks.retry_workable_disqualify_task"
    ) as mock_retry, patch(
        "app.components.notifications.email_client.resend.Emails.send"
    ) as mock_resend:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        db.commit()

    assert mock_retry.apply_async.called is False
    assert mock_resend.called is False
    failed = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "workable_writeback_failed",
        )
        .all()
    )
    assert len(failed) == 1


def test_mvp_disable_skips_workable_no_email(db, monkeypatch):
    """MVP_DISABLE_WORKABLE short-circuits the integration — no Workable call,
    no candidate email; the local reject still stands."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", True)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, workable_candidate_id="wkbl_cand_006")
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable"
    ) as mock_workable, patch(
        "app.components.notifications.email_client.resend.Emails.send"
    ) as mock_resend:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        db.commit()

    assert mock_workable.called is False
    assert mock_resend.called is False
    db.refresh(app)
    assert app.application_outcome == "rejected"


def test_idempotent_re_reject_disqualifies_once(db, monkeypatch):
    """Two uncommitted calls emit one transition and one ATS side effect."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, workable_candidate_id="wkbl_cand_008")
    recruiter = _make_recruiter(db, org)
    initial_version = int(app.version)

    result = {"success": True, "action": "disqualify", "code": "ok", "config": {}}
    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value=result,
    ) as mock_workable:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        first = mock_workable.call_count
        # Second call: already rejected → transition_outcome short-circuits.
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert first == 1
    assert mock_workable.call_count == 1
    assert app.application_outcome == "rejected"
    assert int(app.version) == initial_version + 1

    db.flush()
    event_types = [
        event_type
        for (event_type,) in (
            db.query(CandidateApplicationEvent.event_type)
            .filter(CandidateApplicationEvent.application_id == app.id)
            .all()
        )
    ]
    assert event_types.count("pipeline_initialized") == 1
    assert event_types.count("application_outcome_changed") == 1
    assert event_types.count("workable_disqualified") == 1


def test_strict_workable_failure_can_roll_back_flushed_rejection(db, monkeypatch):
    """The idempotency flush must not commit a failed strict write-back."""
    from app.platform.config import settings as cfg
    from app.services.workable_actions_service import WorkableWritebackError

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, workable_candidate_id="wkbl_cand_009")
    recruiter = _make_recruiter(db, org)
    db.commit()
    original_state = (
        app.status,
        int(app.version),
        app.application_outcome_updated_at,
    )

    provider_error = WorkableWritebackError(
        action="disqualify",
        code="api_error",
        message="Workable unavailable",
        retriable=True,
    )
    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        side_effect=provider_error,
    ):
        with pytest.raises(WorkableWritebackError):
            reject_application.run(
                db,
                Actor.recruiter(recruiter),
                organization_id=int(org.id),
                application_id=int(app.id),
            )

    db.rollback()
    db.refresh(app)
    assert app.application_outcome == "open"
    assert (
        app.status,
        int(app.version),
        app.application_outcome_updated_at,
    ) == original_state
    assert (
        db.query(CandidateApplicationEvent)
        .filter(CandidateApplicationEvent.application_id == app.id)
        .count()
        == 0
    )


def test_workable_exception_does_not_break_rejection(db, monkeypatch):
    """If the Workable client raises unexpectedly, the reject must still commit;
    the candidate is not emailed by Taali."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, workable_candidate_id="wkbl_cand_007")
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        side_effect=RuntimeError("network down"),
    ), patch(
        "app.components.notifications.email_client.resend.Emails.send"
    ) as mock_resend:
        # Must not raise.
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        db.commit()

    assert mock_resend.called is False
    db.refresh(app)
    assert app.application_outcome == "rejected"
