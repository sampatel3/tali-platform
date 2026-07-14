"""Atomic recruiter-facing role job-spec editor contract."""
from __future__ import annotations

from app.domains.assessments_runtime import roles_management_routes
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.role import Role
from app.models.task import Task
from app.models.user import User
from tests.conftest import auth_headers, create_task_via_api


SPEC_A = """# Backend Engineer

Description
Build reliable APIs for a high-volume hiring platform.

Requirements
- Python and FastAPI in production
- PostgreSQL schema and query design
- Must own production incidents end to end
"""

SPEC_B = """# Staff Platform Engineer

Description
Lead reliability and platform engineering across the product.

Requirements
- Kubernetes operations at scale
- Must design distributed systems
- Based in Dubai with hybrid working
"""


def _disable_focus_dispatch(monkeypatch) -> None:
    monkeypatch.setattr(roles_management_routes, "on_role_jd_attached", lambda _role: None)


def test_job_spec_editor_persists_spec_name_tasks_and_diff(client, db, monkeypatch):
    _disable_focus_dispatch(monkeypatch)
    headers, _ = auth_headers(client)
    task_a = create_task_via_api(client, headers, name="API incident task").json()
    task_b = create_task_via_api(client, headers, name="Data model task").json()
    role = client.post("/api/v1/roles", json={"name": "Old title"}, headers=headers).json()

    response = client.put(
        f"/api/v1/roles/{role['id']}/job-spec",
        json={
            "name": "Backend Engineer",
            "job_spec_text": SPEC_A,
            "task_ids": [task_b["id"], task_a["id"], task_b["id"]],
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["applied"] is True
    assert payload["role"]["name"] == "Backend Engineer"
    assert payload["role"]["description"] == SPEC_A.strip()
    assert payload["role"]["job_spec_text"] == SPEC_A.strip()
    assert payload["role"]["job_spec_manually_edited_at"] is not None
    assert payload["role"]["tasks_count"] == 2
    assert payload["diff"]["criteria_count"] >= 1
    assert payload["diff"]["added"]
    assert payload["would_rescreen"] == {"count": 0, "est_cost_usd": 0.0}

    db.expire_all()
    saved = db.query(Role).filter(Role.id == role["id"]).first()
    assert saved.name == "Backend Engineer"
    assert saved.job_spec_text == saved.description == SPEC_A.strip()
    assert {task.id for task in saved.tasks} == {task_a["id"], task_b["id"]}
    assert saved.job_spec_manually_edited_at is not None
    assert saved.interview_focus is None
    assert saved.interview_focus_generated_at is None


def test_job_spec_editor_rejects_assessment_task_removal_before_mutation(
    client, db, monkeypatch
):
    _disable_focus_dispatch(monkeypatch)
    headers, email = auth_headers(client)
    task = create_task_via_api(client, headers, name="In-use assessment task").json()
    role = client.post("/api/v1/roles", json={"name": "Original title"}, headers=headers).json()
    first = client.put(
        f"/api/v1/roles/{role['id']}/job-spec",
        json={"job_spec_text": SPEC_A, "task_ids": [task["id"]]},
        headers=headers,
    )
    assert first.status_code == 200, first.text

    user = db.query(User).filter(User.email == email).first()
    candidate = Candidate(
        organization_id=user.organization_id,
        email="job-spec-atomic@example.com",
        full_name="Atomic Candidate",
    )
    db.add(candidate)
    db.flush()
    db.add(
        Assessment(
            organization_id=user.organization_id,
            candidate_id=candidate.id,
            role_id=role["id"],
            task_id=task["id"],
            token="job-spec-atomic-assessment",
        )
    )
    db.commit()

    conflict = client.put(
        f"/api/v1/roles/{role['id']}/job-spec",
        json={
            "name": "Should not persist",
            "job_spec_text": SPEC_B,
            "task_ids": [],
        },
        headers=headers,
    )
    assert conflict.status_code == 409, conflict.text

    db.expire_all()
    saved = db.query(Role).filter(Role.id == role["id"]).first()
    assert saved.name == "Original title"
    assert saved.job_spec_text == SPEC_A.strip()
    assert {linked.id for linked in saved.tasks} == {task["id"]}


def test_job_spec_editor_omitted_task_ids_preserves_in_use_tasks(
    client, db, monkeypatch
):
    _disable_focus_dispatch(monkeypatch)
    headers, email = auth_headers(client)
    task = create_task_via_api(client, headers, name="Preserved assessment task").json()
    role = client.post(
        "/api/v1/roles", json={"name": "Editor-only update"}, headers=headers
    ).json()
    linked = client.put(
        f"/api/v1/roles/{role['id']}/job-spec",
        json={"job_spec_text": SPEC_A, "task_ids": [task["id"]]},
        headers=headers,
    )
    assert linked.status_code == 200, linked.text

    user = db.query(User).filter(User.email == email).first()
    candidate = Candidate(
        organization_id=user.organization_id,
        email="job-spec-preserve-task@example.com",
        full_name="Preserved Task Candidate",
    )
    db.add(candidate)
    db.flush()
    db.add(
        Assessment(
            organization_id=user.organization_id,
            candidate_id=candidate.id,
            role_id=role["id"],
            task_id=task["id"],
            token="job-spec-preserve-task-assessment",
        )
    )
    db.commit()

    # An editor that only owns name/spec fields omits task_ids. The linked,
    # already-used task must remain untouched and must not trip removal checks.
    response = client.put(
        f"/api/v1/roles/{role['id']}/job-spec",
        json={"name": "Updated without task payload", "job_spec_text": SPEC_B},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["role"]["tasks_count"] == 1
    db.expire_all()
    saved = db.query(Role).filter(Role.id == role["id"]).first()
    assert saved.name == "Updated without task payload"
    assert saved.job_spec_text == SPEC_B.strip()
    assert {linked_task.id for linked_task in saved.tasks} == {task["id"]}


def test_job_spec_editor_is_org_scoped_for_roles_and_tasks(client, db, monkeypatch):
    _disable_focus_dispatch(monkeypatch)
    headers_a, _ = auth_headers(client, organization_name="Spec editor org A")
    foreign_task = create_task_via_api(client, headers_a, name="Private org A task").json()
    foreign_role = client.post(
        "/api/v1/roles", json={"name": "Private org A role"}, headers=headers_a
    ).json()

    headers_b, _ = auth_headers(client, organization_name="Spec editor org B")
    local_role = client.post(
        "/api/v1/roles", json={"name": "Org B role"}, headers=headers_b
    ).json()

    role_scope = client.put(
        f"/api/v1/roles/{foreign_role['id']}/job-spec",
        json={"job_spec_text": SPEC_A, "task_ids": []},
        headers=headers_b,
    )
    assert role_scope.status_code == 404

    task_scope = client.put(
        f"/api/v1/roles/{local_role['id']}/job-spec",
        json={"job_spec_text": SPEC_A, "task_ids": [foreign_task["id"]]},
        headers=headers_b,
    )
    assert task_scope.status_code == 422

    db.expire_all()
    saved = db.query(Role).filter(Role.id == local_role["id"]).first()
    assert saved.job_spec_text is None
    assert saved.description is None
    assert saved.tasks == []

    global_task = Task(
        organization_id=None,
        name="Global assessment template",
        description="Visible to every organization",
        task_type="python",
    )
    db.add(global_task)
    db.commit()
    global_link = client.put(
        f"/api/v1/roles/{local_role['id']}/job-spec",
        json={"job_spec_text": SPEC_A, "task_ids": [global_task.id]},
        headers=headers_b,
    )
    assert global_link.status_code == 200, global_link.text
    assert global_link.json()["role"]["tasks_count"] == 1


def test_job_spec_editor_rejects_short_specs_without_mutation(client, db, monkeypatch):
    _disable_focus_dispatch(monkeypatch)
    headers, _ = auth_headers(client)
    role = client.post("/api/v1/roles", json={"name": "Length guard"}, headers=headers).json()

    response = client.put(
        f"/api/v1/roles/{role['id']}/job-spec",
        json={"job_spec_text": "x" * 59, "task_ids": []},
        headers=headers,
    )
    assert response.status_code == 422

    db.expire_all()
    saved = db.query(Role).filter(Role.id == role["id"]).first()
    assert saved.job_spec_text is None
    assert saved.job_spec_manually_edited_at is None


def test_job_spec_editor_rejects_sister_roles(client, db, monkeypatch):
    _disable_focus_dispatch(monkeypatch)
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    owner = Role(organization_id=user.organization_id, name="ATS owner")
    db.add(owner)
    db.flush()
    sister = Role(
        organization_id=user.organization_id,
        name="Alternate scoring view",
        role_kind="sister",
        ats_owner_role_id=owner.id,
    )
    db.add(sister)
    db.commit()

    response = client.put(
        f"/api/v1/roles/{sister.id}/job-spec",
        json={"job_spec_text": SPEC_A, "task_ids": []},
        headers=headers,
    )
    assert response.status_code == 409

    db.expire_all()
    saved = db.query(Role).filter(Role.id == sister.id).first()
    assert saved.job_spec_text is None
