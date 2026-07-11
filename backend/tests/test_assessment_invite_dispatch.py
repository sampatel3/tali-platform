"""Tests for dispatch_assessment_invite — the Taali-email + Workable-hybrid
dispatcher used by both the recruiter create-assessment route and the
agent's send_assessment action.

Behaviour matrix (from the 2026-05-07 restructure):
- Always send Taali email (Taali is the only source of the unique link).
- ALSO move candidate in Workable + post activity note WHEN:
  * MVP_DISABLE_WORKABLE is False
  * org.workable_connected + access_token + subdomain are set
  * assessment.workable_candidate_id is set
  * config.invite_stage_name is non-empty
  * config.workable_writeback is true  (read-only opt-out honored)
- invite_channel records what happened: manual | workable_hybrid | workable_partial
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.domains.integrations_notifications.invite_flow import dispatch_assessment_invite
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_org(
    db,
    *,
    name: str = "Acme",
    workable_connected: bool = False,
    invite_stage_name: str = "",
    workable_writeback: bool = False,
    workflow_mode: str = "manual",
) -> Organization:
    config = {
        "workable_writeback": workable_writeback,
        "workflow_mode": workflow_mode,
        "invite_stage_name": invite_stage_name,
        "granted_scopes": ["r_candidates", "r_jobs", "w_candidates"],
        "workable_actor_member_id": "member-x",
    }
    org = Organization(
        name=name,
        slug=f"org-{id(db)}",
        workable_connected=workable_connected,
        workable_access_token=("tk-1" if workable_connected else None),
        workable_subdomain=("acme" if workable_connected else None),
        workable_config=config,
    )
    db.add(org)
    db.flush()
    return org


def _make_assessment(
    db,
    *,
    org: Organization,
    workable_candidate_id: str | None = None,
) -> Assessment:
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    task = Task(
        name="Test Task",
        task_key=f"task-{id(db)}",
        organization_id=org.id,
        is_active=True,
    )
    db.add(task)
    db.flush()
    candidate = Candidate(
        organization_id=org.id, email="alice@x.test", full_name="Alice"
    )
    db.add(candidate)
    db.flush()
    a = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        role_id=role.id,
        token="tok-abc",
        duration_minutes=60,
        expires_at=datetime.now(timezone.utc),
        workable_candidate_id=workable_candidate_id,
    )
    db.add(a)
    db.flush()
    return a


@pytest.fixture
def patched_email():
    """Patch the inner email sender so the function never hits Resend."""
    with patch(
        "app.domains.integrations_notifications.invite_flow._send_taali_invite_email"
    ) as mock_email:
        yield mock_email


@pytest.fixture
def patched_workable():
    """Patch both Workable side calls. Defaults to success for both."""
    with patch(
        "app.domains.integrations_notifications.invite_flow.move_candidate_in_workable"
    ) as mock_move, patch(
        "app.domains.integrations_notifications.invite_flow.build_workable_adapter"
    ) as mock_adapter_factory:
        mock_move.return_value = {
            "success": True,
            "action": "move",
            "code": "ok",
            "config": {"actor_member_id": "member-x"},
        }
        adapter = mock_adapter_factory.return_value
        adapter.post_candidate_comment.return_value = {"success": True}
        yield {"move": mock_move, "adapter_factory": mock_adapter_factory}


# ---------------------------------------------------------------------------
# Always-send-Taali-email (the new invariant)
# ---------------------------------------------------------------------------


def test_taali_email_fires_when_workable_not_connected(db, patched_email):
    org = _make_org(db, workable_connected=False)
    a = _make_assessment(db, org=org)

    channel = dispatch_assessment_invite(
        assessment=a,
        org=org,
        candidate_email="alice@x.test",
        candidate_name="Alice",
        position="Senior Backend",
    )

    assert patched_email.called
    assert channel == "manual"
    assert a.invite_channel == "manual"
    assert a.invite_sent_at is not None


def test_taali_email_fires_even_when_workable_connected(db, patched_email, patched_workable, monkeypatch):
    """The big behavior change: previously workable_preferred_fallback_manual
    suppressed the Taali email; now it ALWAYS fires."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(
        db,
        workable_connected=True,
        invite_stage_name="Phone Screen",
        workable_writeback=True,
        workflow_mode="workable_hybrid",
    )
    a = _make_assessment(db, org=org, workable_candidate_id="wkbl_001")

    channel = dispatch_assessment_invite(
        assessment=a,
        org=org,
        candidate_email="alice@x.test",
        candidate_name="Alice",
        position="Senior Backend",
    )

    # Email was sent (the new invariant).
    assert patched_email.called
    # Workable was also updated.
    assert patched_workable["move"].called
    assert channel == "workable_hybrid"
    assert a.invite_channel == "workable_hybrid"


# ---------------------------------------------------------------------------
# Workable handoff eligibility — each gate
# ---------------------------------------------------------------------------


def test_workable_handoff_skipped_when_invite_stage_blank(db, patched_email, patched_workable, monkeypatch):
    """Hybrid mode + connected + linked, but no invite_stage_name → email-only."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(
        db,
        workable_connected=True,
        invite_stage_name="",
        workable_writeback=True,
    )
    a = _make_assessment(db, org=org, workable_candidate_id="wkbl_002")

    channel = dispatch_assessment_invite(
        assessment=a,
        org=org,
        candidate_email="alice@x.test",
        candidate_name="Alice",
        position="Backend",
    )

    assert patched_email.called
    assert patched_workable["move"].called is False
    assert channel == "manual"


def test_workable_handoff_skipped_when_candidate_not_linked(db, patched_email, patched_workable, monkeypatch):
    """Hybrid mode + connected + stage set, but assessment isn't linked
    (manual recruiter-created) → email only."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(
        db,
        workable_connected=True,
        invite_stage_name="Invited",
        workable_writeback=True,
    )
    a = _make_assessment(db, org=org, workable_candidate_id=None)

    channel = dispatch_assessment_invite(
        assessment=a,
        org=org,
        candidate_email="alice@x.test",
        candidate_name="Alice",
        position="Backend",
    )

    assert patched_email.called
    assert patched_workable["move"].called is False
    assert channel == "manual"


def test_workable_handoff_skipped_when_writeback_disabled(db, patched_email, patched_workable, monkeypatch):
    """Read-only mode (``workable_writeback`` False) = explicit recruiter
    opt-out from Workable side effects on assessment send. Honor it even
    when everything else is wired (incl. the w_candidates scope)."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(
        db,
        workable_connected=True,
        invite_stage_name="Invited",
        workable_writeback=False,
    )
    a = _make_assessment(db, org=org, workable_candidate_id="wkbl_003")

    channel = dispatch_assessment_invite(
        assessment=a,
        org=org,
        candidate_email="alice@x.test",
        candidate_name="Alice",
        position="Backend",
    )

    assert patched_email.called
    assert patched_workable["move"].called is False
    assert channel == "manual"


def test_workable_handoff_skipped_when_globally_disabled(db, patched_email, patched_workable, monkeypatch):
    """MVP_DISABLE_WORKABLE flag short-circuits regardless of org config."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", True)

    org = _make_org(
        db,
        workable_connected=True,
        invite_stage_name="Invited",
        workable_writeback=True,
    )
    a = _make_assessment(db, org=org, workable_candidate_id="wkbl_004")

    channel = dispatch_assessment_invite(
        assessment=a,
        org=org,
        candidate_email="alice@x.test",
        candidate_name="Alice",
        position="Backend",
    )

    assert patched_email.called
    assert patched_workable["move"].called is False
    assert channel == "manual"


# ---------------------------------------------------------------------------
# Workable handoff failure modes
# ---------------------------------------------------------------------------


def test_workable_partial_when_stage_move_fails(db, patched_email, monkeypatch):
    """If the Workable stage move fails, email is already out — record
    workable_partial channel so observability sees the divergence."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(
        db,
        workable_connected=True,
        invite_stage_name="Invited",
        workable_writeback=True,
    )
    a = _make_assessment(db, org=org, workable_candidate_id="wkbl_005")

    with patch(
        "app.domains.integrations_notifications.invite_flow.move_candidate_in_workable",
        return_value={"success": False, "code": "api_error", "message": "500"},
    ), patch(
        "app.domains.integrations_notifications.invite_flow.build_workable_adapter"
    ) as mock_adapter_factory:
        adapter = mock_adapter_factory.return_value
        adapter.post_candidate_comment.return_value = {"success": True}
        channel = dispatch_assessment_invite(
            assessment=a,
            org=org,
            candidate_email="alice@x.test",
            candidate_name="Alice",
            position="Backend",
        )

    assert patched_email.called
    assert channel == "workable_partial"
    assert a.invite_channel == "workable_partial"


def test_workable_partial_when_activity_post_fails(db, patched_email, monkeypatch):
    """Stage move OK but activity post fails → still partial. Recruiter
    won't see the activity in Workable but candidate did get the email."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(
        db,
        workable_connected=True,
        invite_stage_name="Invited",
        workable_writeback=True,
    )
    a = _make_assessment(db, org=org, workable_candidate_id="wkbl_006")

    with patch(
        "app.domains.integrations_notifications.invite_flow.move_candidate_in_workable",
        return_value={"success": True, "config": {"actor_member_id": "member-x"}},
    ), patch(
        "app.domains.integrations_notifications.invite_flow.build_workable_adapter"
    ) as mock_adapter_factory:
        adapter = mock_adapter_factory.return_value
        adapter.post_candidate_comment.return_value = {"success": False}
        channel = dispatch_assessment_invite(
            assessment=a,
            org=org,
            candidate_email="alice@x.test",
            candidate_name="Alice",
            position="Backend",
        )

    assert channel == "workable_partial"


def test_workable_exception_does_not_break_email_dispatch(db, patched_email, monkeypatch):
    """If the Workable client raises, the email is still already sent and
    the function returns a workable_partial channel, not an exception."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)

    org = _make_org(
        db,
        workable_connected=True,
        invite_stage_name="Invited",
        workable_writeback=True,
    )
    a = _make_assessment(db, org=org, workable_candidate_id="wkbl_007")

    with patch(
        "app.domains.integrations_notifications.invite_flow.move_candidate_in_workable",
        side_effect=RuntimeError("network down"),
    ):
        # Must not raise.
        channel = dispatch_assessment_invite(
            assessment=a,
            org=org,
            candidate_email="alice@x.test",
            candidate_name="Alice",
            position="Backend",
        )

    assert patched_email.called
    assert channel == "workable_partial"


# ---------------------------------------------------------------------------
# Activity note content
# ---------------------------------------------------------------------------


def test_workable_activity_note_contains_assessment_link(db, patched_email, monkeypatch):
    """The activity note posted to Workable should include the unique
    assessment link so recruiters can preview what the candidate received."""
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(cfg, "FRONTEND_URL", "https://app.taali.test")

    org = _make_org(
        db,
        workable_connected=True,
        invite_stage_name="Invited",
        workable_writeback=True,
    )
    a = _make_assessment(db, org=org, workable_candidate_id="wkbl_008")

    with patch(
        "app.domains.integrations_notifications.invite_flow.move_candidate_in_workable",
        return_value={"success": True, "config": {"actor_member_id": "member-x"}},
    ), patch(
        "app.domains.integrations_notifications.invite_flow.build_workable_adapter"
    ) as mock_adapter_factory:
        adapter = mock_adapter_factory.return_value
        adapter.post_candidate_comment.return_value = {"success": True}

        dispatch_assessment_invite(
            assessment=a,
            org=org,
            candidate_email="alice@x.test",
            candidate_name="Alice",
            position="Backend",
        )

        post_args = adapter.post_candidate_comment.call_args
        candidate_id_arg, member_id_arg, body = post_args.args
        assert candidate_id_arg == "wkbl_008"
        assert member_id_arg == "member-x"
        assert "Alice" in body
        assert "alice@x.test" in body
        assert f"https://app.taali.test/assessment/{a.id}" in body
        assert "tok-abc" in body  # token is present
