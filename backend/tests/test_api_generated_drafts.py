"""Route tests for the generated-task-draft review surface."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import auth_headers, create_task_via_api, TestingSessionLocal


def _make_draft(task_id: int, *, verdict: str = "pass"):
    """Flip an API-created task into a generated draft in the DB."""
    from app.models.task import Task
    db = TestingSessionLocal()
    try:
        t = db.query(Task).filter(Task.id == task_id).first()
        t.is_active = False
        t.extra_data = {"generated": True, "needs_review": True,
                        "battle_test": {"verdict": verdict},
                        "decision_points": [{"id": "x", "headline": "X", "tension": "t"}],
                        "deliverable": {"kind": "code", "primary_artifact": "src/a.py"}}
        t.repo_structure = {"name": "r", "files": {"README.md": "x", "src/a.py": "y"}}
        t.evaluation_rubric = {"design_decisions_articulated": {"weight": 0.6, "grader": "interrogation_outcome"},
                               "deliv": {"weight": 0.4, "lens": "deliverable", "criteria": {}}}
        db.commit()
    finally:
        db.close()


def test_drafts_listed_then_approved(client):
    headers, _ = auth_headers(client)
    tid = create_task_via_api(client, headers, name="Generated Draft").json()["id"]
    _make_draft(tid)

    # Listed as a draft.
    resp = client.get("/api/v1/tasks/drafts", headers=headers)
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert tid in ids

    # NOT in the live catalogue yet (is_active=False).
    live = client.get("/api/v1/tasks/", headers=headers)
    assert tid not in [t["id"] for t in live.json()]

    # Approve → activates, clears needs_review.
    ap = client.post(f"/api/v1/tasks/{tid}/approve", headers=headers)
    assert ap.status_code == 200, ap.text
    assert ap.json()["is_active"] is True
    assert ap.json()["extra_data"]["needs_review"] is False

    # Now in the live catalogue, gone from drafts.
    assert tid in [t["id"] for t in client.get("/api/v1/tasks/", headers=headers).json()]
    assert tid not in [t["id"] for t in client.get("/api/v1/tasks/drafts", headers=headers).json()]


def test_draft_rejected(client):
    headers, _ = auth_headers(client)
    tid = create_task_via_api(client, headers, name="To Reject").json()["id"]
    _make_draft(tid)

    rej = client.delete(f"/api/v1/tasks/{tid}/reject", headers=headers)
    assert rej.status_code == 204
    # Gone entirely.
    assert client.get(f"/api/v1/tasks/{tid}", headers=headers).status_code == 404


def test_cannot_approve_non_generated(client):
    headers, _ = auth_headers(client)
    tid = create_task_via_api(client, headers, name="Normal").json()["id"]
    # Not flipped to generated draft → approve should 400.
    resp = client.post(f"/api/v1/tasks/{tid}/approve", headers=headers)
    assert resp.status_code == 400


def test_repository_failure_does_not_activate_generated_draft(client):
    headers, _ = auth_headers(client)
    tid = create_task_via_api(client, headers, name="Repo Failure Draft").json()["id"]
    _make_draft(tid)

    from app.services.task_approval_service import TaskApprovalError

    with patch(
        "app.domains.tasks_repository.task_generated_approval_command."
        "prepare_task_approval",
        side_effect=TaskApprovalError("template main missing"),
    ):
        response = client.post(f"/api/v1/tasks/{tid}/approve", headers=headers)

    assert response.status_code == 503
    assert "draft remains inactive" in response.text
    assert "task_repository_unavailable" in response.text
    assert "template main missing" not in response.text
    db = TestingSessionLocal()
    try:
        from app.models.task import Task

        task = db.query(Task).filter(Task.id == tid).one()
        assert task.is_active is False
        assert task.extra_data["needs_review"] is True
    finally:
        db.close()


def test_generic_task_patch_cannot_bypass_generated_draft_approval(client):
    headers, _ = auth_headers(client)
    tid = create_task_via_api(
        client, headers, name="Patch Approval Boundary"
    ).json()["id"]
    _make_draft(tid)

    response = client.patch(
        f"/api/v1/tasks/{tid}",
        json={
            "is_active": True,
            # Even an attempted metadata rewrite cannot erase the fact that
            # this persisted row is an unapproved generated draft.
            "extra_data": {"generated": False, "needs_review": False},
        },
        headers=headers,
    )

    assert response.status_code == 409, response.text
    assert "explicit approval endpoint" in response.text
    task = client.get(f"/api/v1/tasks/{tid}", headers=headers).json()
    assert task["is_active"] is False
    assert task["extra_data"]["generated"] is True
    assert task["extra_data"]["needs_review"] is True


def test_generated_draft_remains_editable_while_inactive(client):
    headers, _ = auth_headers(client)
    tid = create_task_via_api(
        client, headers, name="Editable Generated Draft"
    ).json()["id"]
    _make_draft(tid)

    response = client.patch(
        f"/api/v1/tasks/{tid}",
        json={"name": "Edited Generated Draft"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Edited Generated Draft"
    assert response.json()["is_active"] is False


@pytest.mark.parametrize(
    ("field", "value", "requires_battle_test"),
    [
        ("sample_data", {"sample": [1]}, True),
        ("dependencies", ["httpx==0.28.1"], True),
        ("success_criteria", {"required": ["safe retry"]}, True),
        ("test_weights", {"correctness": 1.0}, False),
    ],
)
def test_approved_generated_task_edits_require_exact_recertification(
    client,
    field,
    value,
    requires_battle_test,
):
    headers, _ = auth_headers(client)
    tid = create_task_via_api(
        client,
        headers,
        name=f"Recertify {field}",
    ).json()["id"]
    _make_draft(tid)
    assert client.post(f"/api/v1/tasks/{tid}/approve", headers=headers).status_code == 200

    response = client.patch(
        f"/api/v1/tasks/{tid}",
        json={field: value},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body[field] == value
    assert body["is_active"] is False
    assert body["extra_data"]["needs_review"] is True
    assert "approved_by_user_id" not in body["extra_data"]
    if requires_battle_test:
        assert "battle_test" not in body["extra_data"]
        assert body["extra_data"]["battle_test_provisioning"]["status"] == "pending"
    else:
        assert body["extra_data"]["battle_test"]["verdict"] == "pass"


def test_task_patch_cannot_forge_approval_metadata_across_two_calls(client):
    headers, _ = auth_headers(client)
    tid = create_task_via_api(
        client, headers, name="Two-call approval boundary"
    ).json()["id"]
    _make_draft(tid, verdict="fail")

    metadata = client.patch(
        f"/api/v1/tasks/{tid}",
        json={
            "extra_data": {
                "generated": True,
                "needs_review": False,
                "approved_by_user_id": 999,
                "provenance": {"source": "forged"},
                "battle_test": {"verdict": "pass"},
                "battle_test_provisioning": {"status": "succeeded"},
                "repository_ready": {
                    "verified_at": "2099-01-01T00:00:00Z",
                    "repo_url": "https://example.invalid/forged.git",
                },
                "author_note": "ordinary draft metadata remains editable",
            }
        },
        headers=headers,
    )

    assert metadata.status_code == 200, metadata.text
    extra = metadata.json()["extra_data"]
    assert extra["generated"] is True
    assert extra["needs_review"] is True
    assert "battle_test" not in extra
    assert extra["battle_test_history"][-1]["verdict"] == "fail"
    assert extra["battle_test_provisioning"]["status"] == "pending"
    assert extra["author_note"] == "ordinary draft metadata remains editable"
    assert "approved_by_user_id" not in extra
    assert "provenance" not in extra
    assert "repository_ready" not in extra
    assert extra["last_revision"]["source"] == "recruiter_task_edit"

    activation = client.patch(
        f"/api/v1/tasks/{tid}",
        json={"is_active": True},
        headers=headers,
    )
    assert activation.status_code == 409, activation.text

    approval = client.post(f"/api/v1/tasks/{tid}/approve", headers=headers)
    assert approval.status_code == 503, approval.text
    assert "task_battle_test_pending" in approval.text


def test_task_create_strips_system_owned_approval_metadata(client):
    headers, _ = auth_headers(client)
    response = create_task_via_api(
        client,
        headers,
        name="Create trust boundary",
        extra_data={
            "generated": True,
            "needs_review": False,
            "approved_by_user_id": 999,
            "provenance": {"source": "forged"},
            "battle_test": {"verdict": "pass"},
            "repository_ready": {
                "verified_at": "2099-01-01T00:00:00Z",
                "repo_url": "https://example.invalid/forged.git",
            },
            "author_note": "retained",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["extra_data"] == {"author_note": "retained"}


def test_approved_generated_task_can_be_deactivated_and_reactivated(client):
    headers, _ = auth_headers(client)
    tid = create_task_via_api(
        client, headers, name="Approved reactivation"
    ).json()["id"]
    _make_draft(tid)
    approved = client.post(f"/api/v1/tasks/{tid}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["extra_data"]["repository_ready"]["verified_at"]

    deactivated = client.patch(
        f"/api/v1/tasks/{tid}", json={"is_active": False}, headers=headers
    )
    assert deactivated.status_code == 200, deactivated.text
    reactivated = client.patch(
        f"/api/v1/tasks/{tid}", json={"is_active": True}, headers=headers
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["is_active"] is True


def test_cannot_reject_active(client):
    headers, _ = auth_headers(client)
    tid = create_task_via_api(client, headers, name="Active").json()["id"]
    # Active, non-generated → reject 400.
    resp = client.delete(f"/api/v1/tasks/{tid}/reject", headers=headers)
    assert resp.status_code == 400
