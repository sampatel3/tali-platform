"""Reject — CV gap.

Covers the single-candidate reject helper (Workable-gated, mirrors
auto-reject) and the bulk route that rejects a role's CV-gap cohort. Both
the ``missing_cv`` (no file) and ``cv_unreadable`` (file present, no text)
cards can reject — each its own cohort, with a cause-specific reason.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.agent_runtime import data_readiness
from app.models.agent_needs_input import AgentNeedsInput
from app.models.background_job_run import BackgroundJobRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.job_hiring_team import JobHiringTeam
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.services import application_automation_service as svc
from app.services import cv_gap_rejection as cv_gap_svc
from app.services import cv_gap_rejection_batch as cv_gap_batch
from app.services import cv_gap_rejection_batch_support as cv_gap_support
from app.services.cv_gap_rejection_receipt import cv_gap_rejection_receipt
from app.services.workable_actions_service import WorkableWritebackError
from tests.conftest import auth_headers

REJECT_URL = "/api/v1/agent-needs-input/{id}/reject-cv-gap"


def _seed_org(db, *, workable=False) -> Organization:
    org = Organization(
        name="O",
        slug=f"o-{uuid.uuid4().hex[:10]}",
        workable_connected=workable,
        workable_access_token="tok" if workable else None,
        workable_subdomain="sub" if workable else None,
    )
    db.add(org)
    db.flush()
    return org


def _seed_role(db, org, *, name="Backend") -> Role:
    role = Role(
        organization_id=org.id,
        name=name,
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
        job_spec_text="Requirements\n- Python\n",
    )
    db.add(role)
    db.flush()
    return role


def _seed_app(
    db,
    org,
    role,
    *,
    cv_text=None,
    cv_file_url=None,
    workable_id=None,
) -> CandidateApplication:
    cand = Candidate(
        organization_id=org.id,
        email=f"c-{uuid.uuid4().hex[:10]}@x.test",
        full_name="C",
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        cv_text=cv_text,
        cv_file_url=cv_file_url,
        workable_candidate_id=workable_id,
    )
    db.add(app)
    db.flush()
    return app


def _reget(db, app_id) -> CandidateApplication:
    return db.query(CandidateApplication).filter(CandidateApplication.id == app_id).first()


# ---------------------------------------------------------------------------
# reject_for_cv_gap helper
# ---------------------------------------------------------------------------

def test_reject_for_cv_gap_writes_to_workable_then_rejects(db):
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role, workable_id="wk-1")

    with patch.object(
        svc,
        "disqualify_candidate_in_workable",
        return_value={"success": True, "action": "disqualify"},
    ) as mock_dq:
        result = svc.reject_for_cv_gap(
            db=db, org=org, app=app, role=role, actor_type="recruiter", actor_id=1
        )

    assert result["performed"] is True
    assert result["workable_written"] is True
    mock_dq.assert_called_once()
    assert app.application_outcome == "rejected"


def test_reject_for_cv_gap_passes_reason_to_workable(db):
    """The caller's reason flows into the Workable disqualify call + result,
    so the unreadable card records 'CV could not be read', not 'no CV'."""
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role, workable_id="wk-r")

    with patch.object(
        svc,
        "disqualify_candidate_in_workable",
        return_value={"success": True, "action": "disqualify"},
    ) as mock_dq:
        result = svc.reject_for_cv_gap(
            db=db, org=org, app=app, role=role, actor_type="recruiter",
            actor_id=1, reason="CV could not be read",
        )

    assert result["reason"] == "CV could not be read"
    assert mock_dq.call_args.kwargs["reason"] == "CV could not be read"


def test_reject_for_cv_gap_workable_failure_leaves_candidate_open(db):
    org = _seed_org(db, workable=True)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role, workable_id="wk-2")

    with patch.object(
        svc,
        "disqualify_candidate_in_workable",
        return_value={"success": False, "message": "boom", "code": "api_error"},
    ):
        result = svc.reject_for_cv_gap(
            db=db, org=org, app=app, role=role, actor_type="recruiter", actor_id=1
        )

    # Workable write failed → local outcome stays open (no silent divergence).
    assert result["performed"] is False
    assert "boom" in result["reason"]
    assert app.application_outcome == "open"


def test_reject_for_cv_gap_unlinked_rejects_locally_only(db):
    org = _seed_org(db, workable=False)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role, workable_id=None)

    with patch.object(svc, "disqualify_candidate_in_workable") as mock_dq:
        result = svc.reject_for_cv_gap(
            db=db, org=org, app=app, role=role, actor_type="recruiter", actor_id=1
        )

    assert result["performed"] is True
    assert result["workable_written"] is False
    mock_dq.assert_not_called()
    assert app.application_outcome == "rejected"


def test_reject_for_cv_gap_bullhorn_failure_never_falls_through_to_local_reject(db):
    org = _seed_org(db, workable=False)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role, workable_id=None)
    app.bullhorn_job_submission_id = "submission-1"

    with patch.object(
        cv_gap_svc,
        "bullhorn_reject_outcome",
        return_value="failed",
    ), patch.object(cv_gap_svc, "disqualify_candidate_in_workable") as workable:
        result = cv_gap_svc.reject_for_cv_gap(
            db=db,
            org=org,
            app=app,
            role=role,
            actor_type="recruiter",
            actor_id=1,
            disqualify_fn=workable,
        )

    assert result == {
        "performed": False,
        "reason": "Bullhorn did not accept the rejection",
        "bullhorn_written": False,
    }
    workable.assert_not_called()
    assert app.application_outcome == "open"


# ---------------------------------------------------------------------------
# POST /agent-needs-input/{id}/reject-cv-gap
# ---------------------------------------------------------------------------

def _org_for_user(db, email) -> Organization:
    user = db.query(User).filter(User.email == email).first()
    return db.query(Organization).filter(Organization.id == user.organization_id).first()


def _open_card(db, role, kind) -> AgentNeedsInput:
    return (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == role.id,
            AgentNeedsInput.kind == kind,
            AgentNeedsInput.resolved_at.is_(None),
        )
        .one()
    )


def _preview(client, headers, row: AgentNeedsInput) -> dict:
    response = client.get(
        f"/api/v1/agent-needs-input/{int(row.id)}/reject-cv-gap-preview",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _proof_body(preview: dict) -> dict:
    return {
        "application_ids": preview["application_ids"],
        "expected_owner_role_version": preview["expected_owner_role_version"],
        "expected_role_family": preview["expected_role_family"],
    }


def _accept_without_worker(client, headers, row, preview):
    with patch(
        "app.agent_runtime.cv_gap_rejection_routes.enqueue_workable_op",
        return_value=701,
    ) as enqueue:
        response = client.post(
            REJECT_URL.format(id=row.id),
            headers=headers,
            json=_proof_body(preview),
        )
    assert response.status_code == 202, response.text
    assert response.json() == {
        "job_run_id": 701,
        "status": "queued",
        "accepted_count": len(preview["application_ids"]),
        "application_ids": preview["application_ids"],
    }
    return enqueue.call_args.kwargs["payload"]


def _attach_running_job(db, org, payload):
    progress = cv_gap_batch.initial_cv_gap_rejection_progress(
        payload["application_ids"]
    )
    run = BackgroundJobRun(
        kind="workable_op",
        scope_kind="org",
        scope_id=int(org.id),
        organization_id=int(org.id),
        status="running",
        counters={"op_type": "reject_cv_gap", "progress": progress},
    )
    db.add(run)
    db.commit()
    return {**payload, "_job_run_id": int(run.id)}, run


def test_answer_setting_keeps_request_contract_and_bumps_role_version(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    role.score_threshold = 55
    row = AgentNeedsInput(
        organization_id=org.id,
        role_id=role.id,
        kind="threshold_ambiguous",
        prompt="Use 30 as the bar?",
    )
    db.add(row)
    db.commit()
    starting_version = int(role.version or 1)

    resp = client.post(
        f"/api/v1/agent-needs-input/{row.id}/answer",
        headers=headers,
        json={
            "response": {"value": "30"},
            "expected_version": starting_version,
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "resolved"
    db.expire_all()
    stored_role = db.query(Role).filter(Role.id == role.id).one()
    assert stored_role.score_threshold == 30
    assert stored_role.version == starting_version + 1


def test_answer_setting_rejects_a_stale_question_card(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    role.score_threshold = 55
    row = AgentNeedsInput(
        organization_id=org.id,
        role_id=role.id,
        kind="threshold_ambiguous",
        prompt="Use 30 as the bar?",
    )
    db.add(row)
    db.commit()
    stale_version = int(role.version or 1)

    # Simulate another user's settings edit after this question was rendered.
    role.score_threshold = 70
    role.version = stale_version + 1
    db.commit()

    resp = client.post(
        f"/api/v1/agent-needs-input/{row.id}/answer",
        headers=headers,
        json={
            "response": {"value": "30"},
            "expected_version": stale_version,
        },
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "ROLE_VERSION_CONFLICT"
    db.expire_all()
    stored_role = db.query(Role).filter(Role.id == role.id).one()
    stored_row = db.query(AgentNeedsInput).filter(AgentNeedsInput.id == row.id).one()
    assert stored_role.score_threshold == 70
    assert stored_row.resolved_at is None


def test_reject_missing_cv_card_rejects_file_less_only(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    a1 = _seed_app(db, org, role)                       # file-less → rejected
    a2 = _seed_app(db, org, role)                       # file-less → rejected
    unreadable = _seed_app(db, org, role, cv_file_url="s3://b/scan.png")  # file present
    has_cv = _seed_app(db, org, role, cv_text="real cv")                 # control
    db.commit()

    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")

    preview = _preview(client, headers, row)
    assert preview["application_ids"] == sorted([int(a1.id), int(a2.id)])
    payload = _accept_without_worker(client, headers, row, preview)

    # The request only queues a durable operation; provider/local effects are
    # deferred to the serialized worker.
    assert _reget(db, a1.id).application_outcome == "open"
    result = cv_gap_batch.run_cv_gap_rejection_batch(
        db, int(org.id), payload
    )
    assert result["progress"]["rejected_count"] == 2
    assert result["progress"]["failure_count"] == 0

    db.expire_all()
    assert _reget(db, a1.id).application_outcome == "rejected"
    assert _reget(db, a2.id).application_outcome == "rejected"
    # The unreadable + has-CV candidates are untouched by the missing_cv card.
    assert _reget(db, unreadable.id).application_outcome == "open"
    assert _reget(db, has_cv.id).application_outcome == "open"
    # Nothing file-less left → the card auto-resolves.
    assert db.query(AgentNeedsInput).filter(AgentNeedsInput.id == row.id).first().resolved_at is not None


def test_reject_unreadable_card_rejects_unreadable_cohort_only(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    u1 = _seed_app(db, org, role, cv_file_url="s3://b/scan1.png")  # unreadable → rejected
    u2 = _seed_app(db, org, role, cv_file_url="s3://b/scan2.png")  # unreadable → rejected
    file_less = _seed_app(db, org, role)                          # missing_cv cohort
    has_cv = _seed_app(db, org, role, cv_text="real cv")          # control
    db.commit()

    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "cv_unreadable")

    preview = _preview(client, headers, row)
    assert preview["application_ids"] == sorted([int(u1.id), int(u2.id)])
    payload = _accept_without_worker(client, headers, row, preview)
    result = cv_gap_batch.run_cv_gap_rejection_batch(
        db, int(org.id), payload
    )
    assert result["progress"]["rejected_count"] == 2
    assert result["progress"]["failure_count"] == 0

    db.expire_all()
    assert _reget(db, u1.id).application_outcome == "rejected"
    assert _reget(db, u2.id).application_outcome == "rejected"
    # The file-less + has-CV candidates are untouched by the unreadable card.
    assert _reget(db, file_less.id).application_outcome == "open"
    assert _reget(db, has_cv.id).application_outcome == "open"
    # The unreadable card auto-resolves, but the missing_cv card stays open
    # because the file-less candidate is still there.
    assert db.query(AgentNeedsInput).filter(
        AgentNeedsInput.id == row.id
    ).first().resolved_at is not None
    assert data_readiness.missing_cv_count(db, role=role) == 1


def test_reject_cv_gap_rejects_only_cv_gap_kinds(client, db):
    """A non-CV-gap card (e.g. missing_job_spec) can't be rejected here."""
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org, name="No Spec Role")
    db.commit()
    # Raise a missing_job_spec card by clearing the spec.
    role.job_spec_text = ""
    db.add(role)
    db.commit()
    data_readiness.raise_missing_job_spec(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_job_spec")

    resp = client.post(
        REJECT_URL.format(id=row.id),
        headers=headers,
        json={
            "application_ids": [1],
            "expected_owner_role_version": int(role.version or 1),
            "expected_role_family": {
                "owner": {"id": int(role.id), "name": role.name},
                "related": [],
            },
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("team_role", ["interviewer", "coordinator"])
def test_reject_cv_gap_denies_non_controlling_job_team_roles(
    client, db, team_role
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    preview = _preview(client, headers, row)

    # Keep the authenticated token but make this user a non-controlling member
    # of the now-configured job team.
    user.role = "member"
    db.add(
        JobHiringTeam(
            organization_id=org.id,
            role_id=role.id,
            user_id=user.id,
            team_role=team_role,
        )
    )
    db.commit()

    resp = client.post(
        REJECT_URL.format(id=row.id),
        headers=headers,
        json=_proof_body(preview),
    )

    assert resp.status_code == 403, resp.text
    db.expire_all()
    assert _reget(db, app.id).application_outcome == "open"
    assert db.query(AgentNeedsInput).filter(AgentNeedsInput.id == row.id).one().is_open


def test_reject_cv_gap_404_for_other_org(client, db):
    headers_a, email_a = auth_headers(client, organization_name="OrgA")
    headers_b, email_b = auth_headers(client, organization_name="OrgB")
    org_a = _org_for_user(db, email_a)
    role = _seed_role(db, org_a)
    _seed_app(db, org_a, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    preview = _preview(client, headers_a, row)

    # Org B can't see or act on Org A's card.
    resp = client.post(
        REJECT_URL.format(id=row.id),
        headers=headers_b,
        json=_proof_body(preview),
    )
    assert resp.status_code == 404


def test_preview_is_deterministic_sorted_and_bounded_to_200(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    apps = [_seed_app(db, org, role) for _ in range(202)]
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")

    first = _preview(client, headers, row)
    second = _preview(client, headers, row)
    expected = sorted(int(app.id) for app in apps)[:200]

    assert first == second
    assert first["application_ids"] == expected
    assert len(first["application_ids"]) == 200
    assert first["eligible_count"] == 200
    assert first["has_more"] is True


def test_mutation_rejects_cohort_growth_without_enqueuing(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    original = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    displayed = _preview(client, headers, row)

    added = _seed_app(db, org, role)
    db.commit()
    with patch("app.agent_runtime.cv_gap_rejection_routes.enqueue_workable_op") as enqueue:
        response = client.post(
            REJECT_URL.format(id=row.id),
            headers=headers,
            json=_proof_body(displayed),
        )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "CV_GAP_COHORT_CHANGED"
    assert detail["current_preview"]["application_ids"] == sorted(
        [int(original.id), int(added.id)]
    )
    enqueue.assert_not_called()
    db.expire_all()
    assert _reget(db, original.id).application_outcome == "open"
    assert _reget(db, added.id).application_outcome == "open"


def test_mutation_creates_durable_receipt_before_broker_publish(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    displayed = _preview(client, headers, row)

    with patch(
        "app.tasks.workable_tasks.run_workable_op_task.apply_async"
    ) as publish, patch.object(
        cv_gap_batch, "perform_cv_gap_provider_reject"
    ) as provider_effect:
        response = client.post(
            REJECT_URL.format(id=row.id),
            headers=headers,
            json=_proof_body(displayed),
        )

    assert response.status_code == 202, response.text
    run_id = int(response.json()["job_run_id"])
    provider_effect.assert_not_called()
    publish.assert_called_once()
    published = publish.call_args.kwargs["kwargs"]
    assert published["job_run_id"] == run_id
    assert published["op_type"] == "reject_cv_gap"
    assert published["payload"]["application_ids"] == [int(app.id)]
    db.expire_all()
    run = db.query(BackgroundJobRun).filter(BackgroundJobRun.id == run_id).one()
    assert run.status == "queued"
    assert run.counters["op_type"] == "reject_cv_gap"
    assert run.counters["progress"]["application_ids"] == [int(app.id)]
    assert run.counters["progress"]["processed_count"] == 0
    assert run.counters.get("recovery_payload")


@pytest.mark.parametrize("change", ["cv_upload", "outcome"])
def test_mutation_rejects_candidate_eligibility_drift(client, db, change):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    displayed = _preview(client, headers, row)

    if change == "cv_upload":
        app.cv_file_url = "s3://bucket/new.pdf"
        app.cv_text = "now readable"
    else:
        app.application_outcome = "withdrawn"
    db.commit()

    with patch("app.agent_runtime.cv_gap_rejection_routes.enqueue_workable_op") as enqueue:
        response = client.post(
            REJECT_URL.format(id=row.id),
            headers=headers,
            json=_proof_body(displayed),
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CV_GAP_COHORT_CHANGED"
    assert response.json()["detail"]["current_preview"]["application_ids"] == []
    enqueue.assert_not_called()


def test_mutation_rejects_family_and_owner_version_drift(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    displayed = _preview(client, headers, row)

    related = Role(
        organization_id=int(org.id),
        name="Platform Variant",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=int(role.id),
    )
    db.add(related)
    db.commit()
    family_response = client.post(
        REJECT_URL.format(id=row.id),
        headers=headers,
        json=_proof_body(displayed),
    )
    assert family_response.status_code == 409, family_response.text
    assert family_response.json()["detail"]["code"] == "ROLE_FAMILY_CHANGED"

    refreshed = _preview(client, headers, row)
    role.version = int(role.version or 1) + 1
    db.commit()
    version_response = client.post(
        REJECT_URL.format(id=row.id),
        headers=headers,
        json=_proof_body(refreshed),
    )
    assert version_response.status_code == 409, version_response.text
    assert version_response.json()["detail"]["code"] == "ROLE_VERSION_CONFLICT"


def test_worker_never_expands_beyond_the_confirmed_ids(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    approved = [_seed_app(db, org, role), _seed_app(db, org, role)]
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    displayed = _preview(client, headers, row)
    payload = _accept_without_worker(client, headers, row, displayed)

    later = _seed_app(db, org, role)
    db.commit()
    result = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    assert result["progress"]["rejected_application_ids"] == sorted(
        int(app.id) for app in approved
    )
    assert int(later.id) not in result["progress"]["processed_application_ids"]
    db.expire_all()
    assert all(_reget(db, app.id).application_outcome == "rejected" for app in approved)
    assert _reget(db, later.id).application_outcome == "open"


def test_worker_skips_cv_and_outcome_drift_without_provider_effect(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    cv_changed = _seed_app(db, org, role)
    outcome_changed = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    displayed = _preview(client, headers, row)
    payload = _accept_without_worker(client, headers, row, displayed)

    cv_changed.cv_text = "uploaded after confirmation"
    cv_changed.cv_file_url = "s3://bucket/cv.pdf"
    outcome_changed.application_outcome = "hired"
    db.commit()
    with patch.object(
        cv_gap_batch, "perform_cv_gap_provider_reject"
    ) as provider_effect:
        result = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    provider_effect.assert_not_called()
    assert result["progress"]["skipped_count"] == 2
    assert result["progress"]["failure_count"] == 0
    assert {
        item["application_id"] for item in result["progress"]["skipped"]
    } == {int(cv_changed.id), int(outcome_changed.id)}


def test_worker_stops_on_family_drift_and_preserves_partial_success(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    first = _seed_app(db, org, role)
    second = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    displayed = _preview(client, headers, row)
    payload = _accept_without_worker(client, headers, row, displayed)
    calls = 0
    original_finalize = cv_gap_batch._finalize_provider_success

    def finalize_then_change_family(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original_finalize(*args, **kwargs)
        if calls == 1:
            worker_db = args[0] if args else kwargs["db"]
            worker_db.add(
                Role(
                    organization_id=int(org.id),
                    name="New Linked View",
                    source="sister",
                    role_kind="sister",
                    ats_owner_role_id=int(role.id),
                )
            )
            worker_db.commit()
        return result

    with patch.object(
        cv_gap_batch,
        "_finalize_provider_success",
        side_effect=finalize_then_change_family,
    ):
        result = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    assert calls == 1
    assert result["progress"]["rejected_application_ids"] == [int(first.id)]
    assert result["progress"]["remaining_count"] == 1
    assert result["progress"]["authority_failure"]["code"] == "ROLE_FAMILY_CHANGED"
    db.expire_all()
    assert _reget(db, first.id).application_outcome == "rejected"
    assert _reget(db, second.id).application_outcome == "open"


def test_worker_stops_before_provider_effect_on_owner_version_drift(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    displayed = _preview(client, headers, row)
    payload = _accept_without_worker(client, headers, row, displayed)
    role.version = int(role.version or 1) + 1
    db.commit()

    with patch.object(
        cv_gap_batch, "perform_cv_gap_provider_reject"
    ) as provider_effect:
        result = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    provider_effect.assert_not_called()
    assert result["progress"]["processed_count"] == 0
    assert result["progress"]["authority_failure"]["code"] == "ROLE_VERSION_CONFLICT"
    db.expire_all()
    assert _reget(db, app.id).application_outcome == "open"


def test_worker_persists_partial_progress_and_provider_failures(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    apps = [_seed_app(db, org, role) for _ in range(3)]
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    displayed = _preview(client, headers, row)
    payload = _accept_without_worker(client, headers, row, displayed)
    progress = cv_gap_batch.initial_cv_gap_rejection_progress(
        displayed["application_ids"]
    )
    run = BackgroundJobRun(
        kind="workable_op",
        scope_kind="org",
        scope_id=int(org.id),
        organization_id=int(org.id),
        status="running",
        counters={"op_type": "reject_cv_gap", "progress": progress},
    )
    db.add(run)
    db.commit()
    payload["_job_run_id"] = int(run.id)
    failed_id = int(apps[1].id)

    def deterministic_provider(_db=None, **kwargs):
        app = kwargs["app"]
        if int(app.id) == failed_id:
            return {
                "provider": "workable",
                "provider_target_id": "wk-failed",
                "write_required": True,
                "success": False,
                "message": "provider rejected the write",
            }
        return {
            "provider": "local",
            "provider_target_id": "",
            "write_required": False,
            "success": True,
            "code": "local_only",
        }

    with patch.object(
        cv_gap_batch,
        "perform_cv_gap_provider_reject",
        side_effect=deterministic_provider,
    ):
        result = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    assert result["failed"] is True
    assert result["progress"]["processed_count"] == 3
    assert result["progress"]["rejected_count"] == 2
    assert result["progress"]["failure_count"] == 1
    assert result["progress"]["failures"] == [
        {"application_id": failed_id, "reason": "provider rejected the write"}
    ]
    db.expire_all()
    stored = db.query(BackgroundJobRun).filter(BackgroundJobRun.id == run.id).one()
    assert stored.counters["progress"]["processed_count"] == 3
    assert stored.counters["progress"]["failure_count"] == 1
    assert _reget(db, failed_id).application_outcome == "open"


def test_worker_stops_when_card_closes_mid_batch(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    first = _seed_app(db, org, role)
    second = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    preview = _preview(client, headers, row)
    payload = _accept_without_worker(client, headers, row, preview)
    original_finalize = cv_gap_batch._finalize_provider_success
    calls = 0

    def finalize_then_close_card(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original_finalize(*args, **kwargs)
        if calls == 1:
            worker_db = args[0] if args else kwargs["db"]
            card = worker_db.get(AgentNeedsInput, int(row.id))
            card.dismissed_at = datetime.now(timezone.utc)
            worker_db.commit()
        return result

    with patch.object(
        cv_gap_batch,
        "_finalize_provider_success",
        side_effect=finalize_then_close_card,
    ):
        result = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    assert calls == 1
    assert result["progress"]["rejected_application_ids"] == [int(first.id)]
    assert result["progress"]["remaining_count"] == 1
    assert result["progress"]["authority_failure"]["code"] == "CV_GAP_CARD_CHANGED"
    db.expire_all()
    assert _reget(db, first.id).application_outcome == "rejected"
    assert _reget(db, second.id).application_outcome == "open"


def test_worker_syncs_readiness_once_per_batch(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    [_seed_app(db, org, role) for _ in range(4)]
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    preview = _preview(client, headers, row)
    payload = _accept_without_worker(client, headers, row, preview)
    original_sync = data_readiness.sync_cv_readiness

    with patch.object(
        data_readiness,
        "sync_cv_readiness",
        wraps=original_sync,
    ) as sync:
        result = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    assert result["progress"]["rejected_count"] == 4
    assert sync.call_count == 1


def test_worker_recovers_exact_outcome_when_progress_write_is_lost(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    preview = _preview(client, headers, row)
    payload, run = _attach_running_job(
        db,
        org,
        _accept_without_worker(client, headers, row, preview),
    )

    with patch.object(
        cv_gap_support.background_job_runs,
        "merge_progress",
        return_value=False,
    ):
        first = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    assert first["progress"]["rejected_count"] == 1
    db.expire_all()
    stored = db.get(BackgroundJobRun, int(run.id))
    assert stored.counters["progress"]["processed_count"] == 0
    # A later recruiter override must not make this old exact operation replay.
    stored_app = _reget(db, app.id)
    stored_app.application_outcome = "open"
    db.commit()
    with patch.object(
        cv_gap_batch,
        "perform_cv_gap_provider_reject",
    ) as provider_effect:
        recovered = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    provider_effect.assert_not_called()
    assert recovered["progress"]["rejected_application_ids"] == [int(app.id)]
    db.expire_all()
    assert _reget(db, app.id).application_outcome == "open"
    key = f"cv-gap:{int(run.id)}:missing_cv:{int(app.id)}:outcome"
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == int(app.id),
            CandidateApplicationEvent.idempotency_key == key,
        )
        .count()
        == 1
    )


def test_provider_success_receipt_avoids_duplicate_after_local_failure(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    preview = _preview(client, headers, row)
    payload, run = _attach_running_job(
        db,
        org,
        _accept_without_worker(client, headers, row, preview),
    )

    with patch.object(
        cv_gap_batch,
        "perform_cv_gap_provider_reject",
        wraps=cv_gap_svc.perform_cv_gap_provider_reject,
    ) as provider_effect, patch.object(
        cv_gap_support,
        "finalize_cv_gap_provider_reject",
        side_effect=RuntimeError("local write failed"),
    ):
        with pytest.raises(WorkableWritebackError) as raised:
            cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    assert raised.value.code == "local_reconciliation_failed"
    provider_effect.assert_called_once()
    db.expire_all()
    stored_app = _reget(db, app.id)
    assert stored_app.application_outcome == "open"
    assert cv_gap_rejection_receipt(stored_app)["status"] == "provider_succeeded"
    stored_run = db.get(BackgroundJobRun, int(run.id))
    assert stored_run.counters["progress"]["in_flight"]["status"] == "provider_succeeded"

    with patch.object(
        cv_gap_batch,
        "perform_cv_gap_provider_reject",
    ) as duplicate_provider:
        recovered = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    duplicate_provider.assert_not_called()
    assert recovered["progress"]["rejected_application_ids"] == [int(app.id)]
    db.expire_all()
    assert _reget(db, app.id).application_outcome == "rejected"


def test_worker_retries_only_readiness_after_sync_failure(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    preview = _preview(client, headers, row)
    payload, _run = _attach_running_job(
        db,
        org,
        _accept_without_worker(client, headers, row, preview),
    )

    with patch.object(
        data_readiness,
        "sync_cv_readiness",
        side_effect=RuntimeError("sync failed"),
    ):
        with pytest.raises(WorkableWritebackError) as raised:
            cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    assert raised.value.code == "readiness_sync_failed"
    db.expire_all()
    assert _reget(db, app.id).application_outcome == "rejected"
    with patch.object(
        cv_gap_batch,
        "perform_cv_gap_provider_reject",
    ) as duplicate_provider:
        recovered = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    duplicate_provider.assert_not_called()
    assert recovered["progress"]["rejected_count"] == 1
    db.expire_all()
    assert not db.get(AgentNeedsInput, int(row.id)).is_open


def test_worker_stops_when_recruiter_loses_control_permission(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    preview = _preview(client, headers, row)
    payload = _accept_without_worker(client, headers, row, preview)
    user.role = "member"
    db.add(
        JobHiringTeam(
            organization_id=int(org.id),
            role_id=int(role.id),
            user_id=int(user.id),
            team_role="interviewer",
        )
    )
    db.commit()

    with patch.object(
        cv_gap_batch,
        "perform_cv_gap_provider_reject",
    ) as provider_effect:
        result = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    provider_effect.assert_not_called()
    assert result["progress"]["processed_count"] == 0
    assert result["progress"]["authority_failure"]["code"] == "JOB_PERMISSION_CHANGED"
    db.expire_all()
    assert _reget(db, app.id).application_outcome == "open"


def test_local_only_started_receipt_resumes_without_manual_reconciliation(client, db):
    class WorkerStopped(BaseException):
        pass

    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    preview = _preview(client, headers, row)
    payload, _run = _attach_running_job(
        db,
        org,
        _accept_without_worker(client, headers, row, preview),
    )

    with patch.object(
        cv_gap_batch,
        "perform_cv_gap_provider_reject",
        side_effect=WorkerStopped(),
    ):
        with pytest.raises(WorkerStopped):
            cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)
    db.rollback()
    db.expire_all()
    started = cv_gap_rejection_receipt(_reget(db, app.id))
    assert started["status"] == "provider_call_started"
    assert started["provider_write_required"] is False
    first_operation_id = started["operation_id"]

    duplicate_payload, _duplicate_run = _attach_running_job(
        db,
        org,
        {key: value for key, value in payload.items() if key != "_job_run_id"},
    )
    with patch.object(
        cv_gap_batch,
        "perform_cv_gap_provider_reject",
    ) as duplicate_provider:
        duplicate = cv_gap_batch.run_cv_gap_rejection_batch(
            db,
            int(org.id),
            duplicate_payload,
        )
    duplicate_provider.assert_not_called()
    assert duplicate["progress"]["skipped_count"] == 1
    db.expire_all()
    assert cv_gap_rejection_receipt(_reget(db, app.id))["operation_id"] == first_operation_id

    with patch.object(
        cv_gap_batch,
        "perform_cv_gap_provider_reject",
        wraps=cv_gap_svc.perform_cv_gap_provider_reject,
    ) as local_plan:
        recovered = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    local_plan.assert_called_once()
    assert recovered["progress"]["rejected_application_ids"] == [int(app.id)]
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == int(app.id),
            CandidateApplicationEvent.event_type
            == "cv_gap_rejection_manual_reconciliation_required",
        )
        .count()
        == 0
    )


def test_provider_route_change_is_recorded_as_known_not_called(client, db):
    headers, email = auth_headers(client)
    org = _org_for_user(db, email)
    role = _seed_role(db, org)
    app = _seed_app(db, org, role)
    db.commit()
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()
    row = _open_card(db, role, "missing_cv")
    preview = _preview(client, headers, row)
    payload = _accept_without_worker(client, headers, row, preview)

    with patch.object(
        cv_gap_batch,
        "perform_cv_gap_provider_reject",
        side_effect=cv_gap_svc.CvGapProviderChanged("provider changed"),
    ):
        result = cv_gap_batch.run_cv_gap_rejection_batch(db, int(org.id), payload)

    assert result["progress"]["failure_count"] == 1
    db.expire_all()
    stored_app = _reget(db, app.id)
    receipt = cv_gap_rejection_receipt(stored_app)
    assert receipt["status"] == "failed"
    assert receipt["provider_called"] is False
    assert stored_app.application_outcome == "open"
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == int(app.id),
            CandidateApplicationEvent.event_type
            == "cv_gap_rejection_manual_reconciliation_required",
        )
        .count()
        == 0
    )
