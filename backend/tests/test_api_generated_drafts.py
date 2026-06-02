"""Route tests for the generated-task-draft review surface."""

from __future__ import annotations

from tests.conftest import auth_headers, create_task_via_api, TestingSessionLocal


def _make_draft(task_id: int):
    """Flip an API-created task into a generated draft in the DB."""
    from app.models.task import Task
    db = TestingSessionLocal()
    try:
        t = db.query(Task).filter(Task.id == task_id).first()
        t.is_active = False
        t.extra_data = {"generated": True, "needs_review": True,
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


def test_cannot_reject_active(client):
    headers, _ = auth_headers(client)
    tid = create_task_via_api(client, headers, name="Active").json()["id"]
    # Active, non-generated → reject 400.
    resp = client.delete(f"/api/v1/tasks/{tid}/reject", headers=headers)
    assert resp.status_code == 400
