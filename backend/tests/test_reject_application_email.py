"""Tests for the rejection-email behaviour of reject_application.run().

Covered:
- Email fires once on the first rejection
- Idempotent re-rejection does NOT re-fire (transition_outcome short-circuits)
- send_email=False suppresses the dispatch
- send_email=True with no Resend key configured short-circuits cleanly
- Email failure does not propagate (best-effort)
- Position falls back to candidate.position then a generic phrase
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.actions import reject_application
from app.actions.types import Actor
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


def _make_org(
    db,
    *,
    name: str = "Acme Hiring",
    workable_connected: bool = False,
) -> Organization:
    org = Organization(
        name=name,
        slug=f"acme-{id(db)}",
        workable_connected=workable_connected,
        workable_access_token=("token-xyz" if workable_connected else None),
        workable_subdomain=("acme-test" if workable_connected else None),
        workable_config=(
            {
                "granted_scopes": [
                    "r_candidates",
                    "r_jobs",
                    "w_candidates",
                ],
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
        organization_id=org.id,
        email=email,
        full_name=full_name,
        position=position,
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


def test_rejection_email_fires_with_role_name_as_position(db):
    org = _make_org(db, name="Acme Hiring")
    role = _make_role(db, org, name="Senior Backend Engineer")
    app = _make_application(db, org=org, role=role, email="alice@x.test", full_name="Alice")
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert mock_email.called
    kwargs = mock_email.call_args.kwargs
    assert kwargs["candidate_email"] == "alice@x.test"
    assert kwargs["candidate_name"] == "Alice"
    assert kwargs["org_name"] == "Acme Hiring"
    # Role name is the preferred position field.
    assert kwargs["position"] == "Senior Backend Engineer"


def test_rejection_email_falls_back_to_candidate_position_when_role_name_blank(db):
    """When role has no usable name, fall back to candidate.position."""
    org = _make_org(db)
    role = _make_role(db, org, name="")  # role.name is required NOT NULL but can be empty string
    app = _make_application(
        db, org=org, role=role, email="b@x.test", full_name="Bob",
        position="Frontend Engineer",
    )
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert mock_email.called
    assert mock_email.call_args.kwargs["position"] == "Frontend Engineer"


def test_rejection_email_falls_back_to_generic_when_role_and_position_blank(db):
    org = _make_org(db)
    role = _make_role(db, org, name="")
    app = _make_application(
        db, org=org, role=role, email="c@x.test", full_name="Cleo", position=None,
    )
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert mock_email.called
    assert "role" in mock_email.call_args.kwargs["position"].lower()


def test_rejection_email_does_not_fire_on_idempotent_re_reject(db):
    """transition_outcome is idempotent — re-rejecting an already-rejected
    application must not spam the candidate."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, email="d@x.test", full_name="D")
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        first_call_count = mock_email.call_count

        # Second call: already rejected
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert first_call_count == 1
    assert mock_email.call_count == 1  # didn't fire again


def test_send_email_false_suppresses_dispatch(db):
    """Caller can opt out of email even on a fresh rejection."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, email="e@x.test")
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
            send_email=False,
        )

    assert not mock_email.called


def test_no_email_when_candidate_has_no_email_address(db):
    """If the application's candidate has no email, dispatch is skipped silently."""
    org = _make_org(db)
    role = _make_role(db, org)
    candidate = Candidate(
        organization_id=org.id, email="", full_name="No Email", position="Eng"
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert not mock_email.called


def test_dispatch_short_circuits_when_resend_unconfigured(db, monkeypatch):
    """No RESEND_API_KEY → dispatcher is a no-op (doesn't enqueue the Celery task)."""
    from app.actions import reject_application as reject_module
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "RESEND_API_KEY", "")

    with patch(
        "app.components.notifications.tasks.send_application_rejected_email"
    ) as mock_celery:
        reject_module._dispatch_rejection_email(
            candidate_email="x@x.test",
            candidate_name="X",
            org_name="Org",
            position="Role",
        )

    assert not mock_celery.called


def test_dispatch_enqueues_celery_task(db, monkeypatch):
    """RESEND_API_KEY set → dispatcher enqueues the Celery rejection task."""
    from app.actions import reject_application as reject_module
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "RESEND_API_KEY", "test-key")

    with patch(
        "app.components.notifications.tasks.send_application_rejected_email"
    ) as mock_celery:
        reject_module._dispatch_rejection_email(
            candidate_email="x@x.test",
            candidate_name="X",
            org_name="Org",
            position="Role",
        )

    assert mock_celery.delay.called


def test_dispatch_swallows_exceptions(db, monkeypatch):
    """Enqueue failures must not propagate — the rejection has already landed."""
    from app.actions import reject_application as reject_module
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "RESEND_API_KEY", "test-key")

    with patch(
        "app.components.notifications.tasks.send_application_rejected_email"
    ) as mock_celery:
        mock_celery.delay.side_effect = RuntimeError("broker down")
        # Should not raise.
        reject_module._dispatch_rejection_email(
            candidate_email="x@x.test",
            candidate_name="X",
            org_name="Org",
            position="Role",
        )


# ---------------------------------------------------------------------------
# Workable-first reject behavior
# ---------------------------------------------------------------------------


def test_workable_disqualify_success_suppresses_taali_email(db, monkeypatch):
    """When Workable disqualify succeeds, Taali email must NOT fire — the
    org's Workable rejection-stage workflow is responsible for notifying
    the candidate. This is the core behavior Sam's audit asked for."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role,
        email="alice@x.test",
        workable_candidate_id="wkbl_cand_001",
    )
    recruiter = _make_recruiter(db, org)

    fake_disqualify_result = {
        "success": True,
        "action": "disqualify",
        "code": "ok",
        "message": "Candidate disqualified in Workable",
        "config": {
            "actor_member_id": "member-123",
            "workable_disqualify_reason_id": "not_a_fit",
        },
    }

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value=fake_disqualify_result,
    ) as mock_workable, patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        db.commit()

    assert mock_workable.called, "Workable disqualify should have been attempted"
    assert mock_email.called is False, "Taali email must NOT fire when Workable handled it"

    db.refresh(app)
    assert app.application_outcome == "rejected"


def test_workable_disqualify_transient_failure_schedules_retry_no_email(db, monkeypatch):
    """A transient Workable API error (e.g. 429) schedules a background retry
    and does NOT send the Taali email now — the retry owns notification so the
    candidate isn't double-emailed if the retry later succeeds (Workable emails
    on its own disqualify workflow). The reject itself stays committed."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role,
        email="alice@x.test",
        workable_candidate_id="wkbl_cand_002",
    )
    recruiter = _make_recruiter(db, org)

    fake_failure = {
        "success": False,
        "action": "disqualify",
        "code": "api_error",
        "message": "Client error '429 Too Many Requests'",
    }

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value=fake_failure,
    ) as mock_workable, patch(
        "app.tasks.workable_tasks.retry_workable_disqualify_task"
    ) as mock_retry_task, patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        db.commit()

    assert mock_workable.called
    assert mock_retry_task.apply_async.called, "transient failure must schedule a retry"
    assert not mock_email.called, "no immediate email — the retry owns notification"

    db.refresh(app)
    assert app.application_outcome == "rejected"


def test_workable_disqualify_nonretriable_failure_falls_back_to_taali_email(db, monkeypatch):
    """A non-API failure (bad config / unlinked) won't self-heal, so the Taali
    email fires immediately as the safety net and no retry is scheduled."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role,
        email="alice@x.test",
        workable_candidate_id="wkbl_cand_003",
    )
    recruiter = _make_recruiter(db, org)

    fake_failure = {
        "success": False,
        "action": "disqualify",
        "code": "missing_candidate_id",
        "message": "Candidate is not linked to Workable",
    }

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value=fake_failure,
    ) as mock_workable, patch(
        "app.tasks.workable_tasks.retry_workable_disqualify_task"
    ) as mock_retry_task, patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        db.commit()

    assert mock_workable.called
    assert not mock_retry_task.apply_async.called
    assert mock_email.called, "non-retriable failure should fall back to the Taali email"

    db.refresh(app)
    assert app.application_outcome == "rejected"


def test_no_workable_candidate_id_skips_workable_path(db, monkeypatch):
    """Org has Workable connected, but the application predates the link
    (no workable_candidate_id) — must not call Workable, must send Taali."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role,
        email="alice@x.test",
        workable_candidate_id=None,
    )
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable"
    ) as mock_workable, patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert mock_workable.called is False
    assert mock_email.called


def test_org_not_connected_skips_workable_path(db, monkeypatch):
    """Application is linked to Workable but org's connection lapsed —
    must not call Workable, must send Taali."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=False)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role,
        email="alice@x.test",
        workable_candidate_id="wkbl_cand_003",
    )
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable"
    ) as mock_workable, patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert mock_workable.called is False
    assert mock_email.called


def test_workable_disqualify_records_audit_event_on_success(db, monkeypatch):
    """Successful Workable disqualify records a workable_disqualified event
    with the Workable response metadata, mirroring the manual outcome PATCH."""
    from app.models.candidate_application_event import CandidateApplicationEvent
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role,
        email="alice@x.test",
        workable_candidate_id="wkbl_cand_004",
    )
    recruiter = _make_recruiter(db, org)

    fake_disqualify_result = {
        "success": True,
        "action": "disqualify",
        "code": "ok",
        "message": "Candidate disqualified in Workable",
        "config": {
            "actor_member_id": "member-xyz",
            "workable_disqualify_reason_id": "not_a_fit",
        },
    }

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value=fake_disqualify_result,
    ), patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ):
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
    db.commit()

    events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "workable_disqualified",
        )
        .all()
    )
    assert len(events) == 1
    meta = events[0].event_metadata or {}
    assert meta.get("workable_candidate_id") == "wkbl_cand_004"
    assert meta.get("workable_actor_member_id") == "member-xyz"
    assert meta.get("source") == "reject_application"


def test_workable_disqualify_failure_records_writeback_failed_event(db, monkeypatch):
    """A failed Workable call leaves a workable_writeback_failed audit row
    so the recruiter can see why the platform fell back to a Taali email."""
    from app.models.candidate_application_event import CandidateApplicationEvent
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role,
        email="alice@x.test",
        workable_candidate_id="wkbl_cand_005",
    )
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value={
            "success": False,
            "action": "disqualify",
            "code": "api_error",
            "message": "Workable timed out",
        },
    ), patch("app.actions.reject_application._dispatch_rejection_email"):
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
    db.commit()

    failed_events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "workable_writeback_failed",
        )
        .all()
    )
    assert len(failed_events) == 1
    assert "timed out" in (failed_events[0].reason or "").lower()


def test_workable_disabled_via_setting_skips_workable_path(db, monkeypatch):
    """MVP_DISABLE_WORKABLE flag short-circuits the integration — used in
    test environments and when Workable is administratively disabled."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", True)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role,
        email="alice@x.test",
        workable_candidate_id="wkbl_cand_006",
    )
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable"
    ) as mock_workable, patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert mock_workable.called is False
    assert mock_email.called  # Taali fallback


def test_workable_disqualify_exception_does_not_break_rejection(db, monkeypatch):
    """If the Workable client raises unexpectedly, the reject must still
    commit and the candidate should still get the Taali fallback email."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(db, workable_connected=True)
    role = _make_role(db, org)
    app = _make_application(
        db, org=org, role=role,
        email="alice@x.test",
        workable_candidate_id="wkbl_cand_007",
    )
    recruiter = _make_recruiter(db, org)

    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        side_effect=RuntimeError("network down"),
    ), patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        # Must not raise.
        reject_application.run(
            db,
            Actor.recruiter(recruiter),
            organization_id=int(org.id),
            application_id=int(app.id),
        )
        db.commit()

    assert mock_email.called  # fallback fires
    db.refresh(app)
    assert app.application_outcome == "rejected"
