"""Retry-on-failure for the Workable disqualify writeback (issue #2).

When the synchronous reject path's disqualify call fails on a transient API
error (typically a 429), Tali's local outcome is already 'rejected' — without a
retry, Tali and Workable drift permanently. These cover the retry task's
success, idempotency, give-up, and retry-scheduling branches.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.tasks.workable_tasks import retry_workable_disqualify_task


def _seed(db, *, outcome: str = "rejected", email: str = "cand@x.test"):
    org = Organization(
        name="O",
        slug=f"o-{id(db)}",
        workable_connected=True,
        workable_access_token="tok",
        workable_subdomain="acme",
        workable_config={
            "granted_scopes": ["w_candidates"],
            "workable_actor_member_id": "m1",
            "workable_disqualify_reason_id": "r1",
        },
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="R", source="manual")
    db.add(role)
    db.flush()
    cand = Candidate(organization_id=org.id, email=email, full_name="Cand")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome=outcome,
        source="manual",
        workable_candidate_id="wkbl_1",
    )
    db.add(app)
    db.flush()
    # The task opens its own SessionLocal — it only sees committed rows.
    db.commit()
    return org, role, app


_SUCCESS = {
    "success": True,
    "action": "disqualify",
    "code": "ok",
    "config": {"actor_member_id": "m1", "workable_disqualify_reason_id": "r1"},
}


def test_retry_task_success_records_event_no_email(db):
    org, role, app = _seed(db, outcome="rejected")
    app_id = int(app.id)
    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value=_SUCCESS,
    ) as mock_dq, patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        out = retry_workable_disqualify_task.run(application_id=app_id)
    assert out["status"] == "ok"
    assert mock_dq.called
    assert not mock_email.called, "success defers candidate email to Workable's workflow"


def test_retry_task_skips_when_not_rejected(db):
    """Recruiter overrode the reject between attempts — don't disqualify."""
    org, role, app = _seed(db, outcome="open")
    app_id = int(app.id)
    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
    ) as mock_dq:
        out = retry_workable_disqualify_task.run(application_id=app_id)
    assert out["status"] == "skipped"
    assert out["reason"] == "not_rejected"
    assert not mock_dq.called


def test_retry_task_skips_when_already_disqualified(db):
    """A prior attempt already landed — idempotent no-op."""
    org, role, app = _seed(db, outcome="rejected")
    app_id = int(app.id)
    db.add(
        CandidateApplicationEvent(
            application_id=app_id,
            organization_id=int(org.id),
            event_type="workable_disqualified",
            actor_type="system",
        )
    )
    db.commit()
    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
    ) as mock_dq:
        out = retry_workable_disqualify_task.run(application_id=app_id)
    assert out["status"] == "skipped"
    assert out["reason"] == "already_disqualified"
    assert not mock_dq.called


def test_retry_task_nonretriable_failure_emails_and_gives_up(db):
    """A non-API failure won't self-heal — record it and notify the candidate."""
    org, role, app = _seed(db, outcome="rejected", email="cand@x.test")
    app_id = int(app.id)
    failure = {
        "success": False,
        "action": "disqualify",
        "code": "missing_candidate_id",
        "message": "no link",
    }
    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value=failure,
    ) as mock_dq, patch(
        "app.actions.reject_application._dispatch_rejection_email"
    ) as mock_email:
        out = retry_workable_disqualify_task.run(application_id=app_id)
    assert out["status"] == "failed"
    assert mock_dq.called
    assert mock_email.called, "candidate must still be notified when Workable can't be used"


def test_retry_task_transient_failure_reschedules(db, monkeypatch):
    """A transient api_error with retries remaining calls self.retry()."""
    org, role, app = _seed(db, outcome="rejected")
    app_id = int(app.id)
    failure = {
        "success": False,
        "action": "disqualify",
        "code": "api_error",
        "message": "Client error '429 Too Many Requests'",
    }

    class _RetrySignal(Exception):
        pass

    monkeypatch.setattr(
        retry_workable_disqualify_task,
        "retry",
        MagicMock(side_effect=_RetrySignal()),
    )
    with patch(
        "app.services.workable_actions_service.disqualify_candidate_in_workable",
        return_value=failure,
    ) as mock_dq:
        with pytest.raises(_RetrySignal):
            retry_workable_disqualify_task.run(application_id=app_id)
    assert mock_dq.called
    assert retry_workable_disqualify_task.retry.called
