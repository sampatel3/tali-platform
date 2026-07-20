"""End-to-end smoke tests for the new chip-based criteria flow:

- workspace criteria CRUD via ``/organizations/me/criteria``
- role criteria CRUD + sync + reset via ``/roles/{id}/criteria/...``
- bucket inference + provenance + suppression behavior

These exercise the API surface the new chip composer UI will call. The
underlying service helpers are also covered indirectly here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
from app.models.role import Role
from app.models.role_criterion import RoleCriterion
from app.platform.config import settings
from app.tasks.automation_tasks import (
    generate_role_interview_focus,
    regenerate_role_tech_questions,
)
from tests.conftest import auth_headers


# ---------------------------------------------------------------------------
# Workspace criteria CRUD
# ---------------------------------------------------------------------------


def _list_org_criteria(client, headers):
    return client.get("/api/v1/organizations/me/criteria", headers=headers)


def _post_org_criterion(client, headers, **kwargs):
    return client.post("/api/v1/organizations/me/criteria", json=kwargs, headers=headers)


def _role_version(client, headers, role_id):
    return client.get(f"/api/v1/roles/{role_id}", headers=headers).json()["version"]


def test_workspace_criteria_crud_round_trip(client):
    headers, _ = auth_headers(client)
    resp = _list_org_criteria(client, headers)
    assert resp.status_code == 200
    assert resp.json() == []

    create = _post_org_criterion(
        client, headers, text="Senior backend (5+ yrs)", bucket="must"
    )
    assert create.status_code == 201, create.text
    chip_id = create.json()["id"]
    assert create.json()["bucket"] == "must"
    assert create.json()["text"] == "Senior backend (5+ yrs)"

    _post_org_criterion(client, headers, text="Worked with LLMs in prod", bucket="preferred")
    _post_org_criterion(client, headers, text="EU timezone (±2h CET)", bucket="constraint")

    listed = _list_org_criteria(client, headers).json()
    assert len(listed) == 3
    assert {c["bucket"] for c in listed} == {"must", "preferred", "constraint"}

    # Edit the must
    patch = client.patch(
        f"/api/v1/organizations/me/criteria/{chip_id}",
        json={"text": "Senior backend (7+ yrs)"},
        headers=headers,
    )
    assert patch.status_code == 200
    assert patch.json()["text"] == "Senior backend (7+ yrs)"
    assert patch.json()["bucket"] == "must"

    # Delete
    delete = client.delete(
        f"/api/v1/organizations/me/criteria/{chip_id}",
        headers=headers,
    )
    assert delete.status_code == 204
    listed_after = _list_org_criteria(client, headers).json()
    assert len(listed_after) == 2
    assert chip_id not in {c["id"] for c in listed_after}


# ---------------------------------------------------------------------------
# Role-level chip flow + workspace sync + reset
# ---------------------------------------------------------------------------


def test_role_inherits_workspace_chips_with_provenance(client):
    headers, _ = auth_headers(client)
    _post_org_criterion(client, headers, text="Python", bucket="must")
    _post_org_criterion(client, headers, text="LLMs", bucket="preferred")

    role_resp = client.post("/api/v1/roles", json={"name": "Backend"}, headers=headers)
    assert role_resp.status_code == 201
    role = role_resp.json()
    chips = role.get("criteria") or []
    # All 2 workspace chips inherited, both carry org_criterion_id provenance.
    inherited = [c for c in chips if c["source"] == "recruiter" and c.get("org_criterion_id") is not None]
    assert len(inherited) == 2
    assert {c["text"] for c in inherited} == {"Python", "LLMs"}


def test_role_sync_pulls_in_new_workspace_chips_and_keeps_role_only(client):
    headers, _ = auth_headers(client)
    _post_org_criterion(client, headers, text="Python", bucket="must")
    role_resp = client.post("/api/v1/roles", json={"name": "Backend"}, headers=headers)
    role_id = role_resp.json()["id"]

    # Recruiter adds a role-only chip.
    add = client.post(
        f"/api/v1/roles/{role_id}/criteria",
        json={
            "text": "Built async messaging at scale",
            "bucket": "must",
            "expected_version": _role_version(client, headers, role_id),
        },
        headers=headers,
    )
    assert add.status_code == 201
    assert add.json()["org_criterion_id"] is None

    # Workspace adds a brand new criterion AFTER the role was created.
    _post_org_criterion(client, headers, text="Postgres", bucket="must")

    # Sync pulls in the new workspace chip; role-only chip is preserved.
    sync = client.post(
        f"/api/v1/roles/{role_id}/criteria/sync",
        json={"expected_version": _role_version(client, headers, role_id)},
        headers=headers,
    )
    assert sync.status_code == 200
    chips = sync.json()["criteria"]
    texts = {c["text"] for c in chips}
    assert {"Python", "Postgres", "Built async messaging at scale"}.issubset(texts)

    # Provenance is intact: role-only chip stays ``role`` source, workspace
    # chips have org_criterion_id populated.
    role_only = [c for c in chips if c["text"] == "Built async messaging at scale"]
    assert len(role_only) == 1 and role_only[0]["org_criterion_id"] is None
    workspace_chips = [c for c in chips if c["text"] in {"Python", "Postgres"}]
    assert all(c["org_criterion_id"] is not None for c in workspace_chips)


def test_deleting_workspace_inherited_chip_on_role_records_suppression(client):
    headers, _ = auth_headers(client)
    org_resp = _post_org_criterion(client, headers, text="Python", bucket="must")
    org_chip_id = org_resp.json()["id"]
    role_id = client.post("/api/v1/roles", json={"name": "Backend"}, headers=headers).json()["id"]

    # Find the role chip linked to the workspace chip.
    role_chips = client.get(f"/api/v1/roles/{role_id}", headers=headers).json()["criteria"]
    role_chip = next(c for c in role_chips if c.get("org_criterion_id") == org_chip_id)

    # Delete it on the role.
    deleted = client.delete(
        f"/api/v1/roles/{role_id}/criteria/{role_chip['id']}",
        params={"expected_version": _role_version(client, headers, role_id)},
        headers=headers,
    )
    assert deleted.status_code == 204

    # Sync workspace must NOT re-add it because it's suppressed.
    sync = client.post(
        f"/api/v1/roles/{role_id}/criteria/sync",
        json={"expected_version": _role_version(client, headers, role_id)},
        headers=headers,
    )
    assert sync.status_code == 200
    after_sync = sync.json()["criteria"]
    assert org_chip_id not in {c.get("org_criterion_id") for c in after_sync}


def test_reset_role_to_workspace_drops_role_only_and_clears_suppression(client):
    headers, _ = auth_headers(client)
    org_chip = _post_org_criterion(client, headers, text="Python", bucket="must").json()
    role_id = client.post("/api/v1/roles", json={"name": "Backend"}, headers=headers).json()["id"]

    # Add a role-only chip.
    client.post(
        f"/api/v1/roles/{role_id}/criteria",
        json={
            "text": "role-only",
            "bucket": "preferred",
            "expected_version": _role_version(client, headers, role_id),
        },
        headers=headers,
    )
    # Suppress the workspace chip.
    role_chip = next(
        c for c in client.get(f"/api/v1/roles/{role_id}", headers=headers).json()["criteria"]
        if c.get("org_criterion_id") == org_chip["id"]
    )
    client.delete(
        f"/api/v1/roles/{role_id}/criteria/{role_chip['id']}",
        params={"expected_version": _role_version(client, headers, role_id)},
        headers=headers,
    )

    # Reset → the role-only chip is gone, suppression cleared so workspace chip is back.
    reset = client.post(
        f"/api/v1/roles/{role_id}/criteria/reset",
        json={"expected_version": _role_version(client, headers, role_id)},
        headers=headers,
    )
    assert reset.status_code == 200
    chips = reset.json()["criteria"]
    texts = {c["text"] for c in chips}
    assert "role-only" not in texts
    assert "Python" in texts


def test_editing_workspace_chip_on_role_marks_customized_and_blocks_sync_overwrite(client):
    headers, _ = auth_headers(client)
    org_chip = _post_org_criterion(client, headers, text="Python", bucket="must").json()
    role_id = client.post("/api/v1/roles", json={"name": "Backend"}, headers=headers).json()["id"]

    role_chip = next(
        c for c in client.get(f"/api/v1/roles/{role_id}", headers=headers).json()["criteria"]
        if c.get("org_criterion_id") == org_chip["id"]
    )
    # Recruiter customizes the chip on the role.
    edit = client.patch(
        f"/api/v1/roles/{role_id}/criteria/{role_chip['id']}",
        json={
            "text": "Python 3.11+",
            "expected_version": _role_version(client, headers, role_id),
        },
        headers=headers,
    )
    assert edit.status_code == 200
    assert edit.json()["customized_at"] is not None

    # Workspace edits the same chip.
    client.patch(
        f"/api/v1/organizations/me/criteria/{org_chip['id']}",
        json={"text": "Python (any version)"},
        headers=headers,
    )

    # Sync must NOT overwrite the recruiter customization.
    sync = client.post(
        f"/api/v1/roles/{role_id}/criteria/sync",
        json={"expected_version": _role_version(client, headers, role_id)},
        headers=headers,
    ).json()
    same = next(c for c in sync["criteria"] if c.get("org_criterion_id") == org_chip["id"])
    assert same["text"] == "Python 3.11+"


def _seed_preferred_role_artifacts(db, *, role_id: int) -> tuple[Role, RoleCriterion]:
    role = db.get(Role, int(role_id))
    role.job_spec_text = "Build reliable distributed backend services."
    role.interview_focus = {"questions": [{"question": "Old focus"}]}
    role.interview_focus_generated_at = datetime.now(timezone.utc)
    role.screening_pack_template = {
        "stage": "screening",
        "questions": [{"question": "Old screening"}],
    }
    role.tech_interview_pack_template = {
        "stage": "tech_stage_2",
        "questions": [{"question": "Old tech"}],
    }
    role.tech_questions_cached = [{"question": "Old role question"}]
    role.tech_questions_signature = "old-generation"
    criterion = RoleCriterion(
        role_id=int(role.id),
        source="recruiter",
        ordering=0,
        weight=1.0,
        must_have=False,
        bucket="preferred",
        text="Python experience",
    )
    db.add(criterion)
    db.commit()
    return role, criterion


def test_preferred_edit_refreshes_provider_artifacts_without_staling_scores(
    client, db
):
    headers, _ = auth_headers(client)
    role_id = client.post(
        "/api/v1/roles", json={"name": "Artifact refresh"}, headers=headers
    ).json()["id"]
    role, criterion = _seed_preferred_role_artifacts(db, role_id=role_id)
    candidate = Candidate(
        organization_id=int(role.organization_id),
        full_name="Current Score",
        email="current-score@example.test",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        candidate_id=int(candidate.id),
        source="manual",
        cv_match_score=84.0,
        pre_screen_score_100=79.0,
    )
    db.add(application)
    db.flush()
    score_job = CvScoreJob(
        application_id=int(application.id),
        role_id=int(role.id),
        status="done",
    )
    db.add(score_job)
    db.commit()

    with (
        patch(
            "app.tasks.automation_tasks.generate_role_interview_focus.delay"
        ) as focus_dispatch,
        patch(
            "app.tasks.automation_tasks.regenerate_role_tech_questions.delay"
        ) as tech_dispatch,
        patch(
            "app.domains.assessments_runtime.role_criteria_runtime.mark_role_scores_stale"
        ) as mark_scores_stale,
    ):
        response = client.patch(
            f"/api/v1/roles/{role_id}/criteria/{criterion.id}",
            json={
                "text": "Python and async systems experience",
                "expected_version": _role_version(client, headers, role_id),
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    focus_dispatch.assert_called_once_with(role_id, requires_running_agent=False)
    tech_dispatch.assert_called_once_with(role_id)
    mark_scores_stale.assert_not_called()
    db.expire_all()
    saved_role = db.get(Role, role_id)
    saved_application = db.get(CandidateApplication, int(application.id))
    saved_jobs = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == int(application.id))
        .all()
    )
    assert saved_role.interview_focus is None
    assert saved_role.screening_pack_template is None
    assert saved_role.tech_interview_pack_template is None
    assert saved_role.tech_questions_cached == [{"question": "Old role question"}]
    assert saved_role.tech_questions_signature is None
    assert saved_application.cv_match_score == 84.0
    assert saved_application.pre_screen_score_100 == 79.0
    assert [(job.id, job.status) for job in saved_jobs] == [(score_job.id, "done")]


def test_noop_preferred_edit_dispatches_nothing_and_keeps_artifacts(client, db):
    headers, _ = auth_headers(client)
    role_id = client.post(
        "/api/v1/roles", json={"name": "Artifact no-op"}, headers=headers
    ).json()["id"]
    _role, criterion = _seed_preferred_role_artifacts(db, role_id=role_id)

    with (
        patch(
            "app.tasks.automation_tasks.generate_role_interview_focus.delay"
        ) as focus_dispatch,
        patch(
            "app.tasks.automation_tasks.regenerate_role_tech_questions.delay"
        ) as tech_dispatch,
    ):
        response = client.patch(
            f"/api/v1/roles/{role_id}/criteria/{criterion.id}",
            json={
                "text": "Python experience",
                "bucket": "preferred",
                "expected_version": _role_version(client, headers, role_id),
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    focus_dispatch.assert_not_called()
    tech_dispatch.assert_not_called()
    db.expire_all()
    saved = db.get(Role, role_id)
    assert saved.interview_focus == {"questions": [{"question": "Old focus"}]}
    assert saved.tech_questions_signature == "old-generation"


def test_rapid_preferred_edits_converge_with_one_paid_generation(
    client, db, monkeypatch
):
    headers, _ = auth_headers(client)
    role_id = client.post(
        "/api/v1/roles", json={"name": "Artifact convergence"}, headers=headers
    ).json()["id"]
    role, criterion = _seed_preferred_role_artifacts(db, role_id=role_id)
    role.agentic_mode_enabled = True
    db.commit()

    with (
        patch(
            "app.tasks.automation_tasks.generate_role_interview_focus.delay"
        ) as focus_dispatch,
        patch(
            "app.tasks.automation_tasks.regenerate_role_tech_questions.delay"
        ) as tech_dispatch,
    ):
        for text in ("Rust services", "Go services"):
            response = client.patch(
                f"/api/v1/roles/{role_id}/criteria/{criterion.id}",
                json={
                    "text": text,
                    "expected_version": _role_version(client, headers, role_id),
                },
                headers=headers,
            )
            assert response.status_code == 200, response.text

    assert focus_dispatch.call_count == 2
    assert tech_dispatch.call_count == 2
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)
    focus_output = {"questions": [{"question": "Current focus"}]}
    tech_output = [{"question": "Current technical question"}]
    with (
        patch(
            "app.services.interview_focus_service.generate_interview_focus_sync",
            return_value=focus_output,
        ) as focus_provider,
        patch(
            "app.services.role_tech_questions_service.generate_tech_questions",
            return_value=tech_output,
        ) as tech_provider,
    ):
        first_focus = generate_role_interview_focus.run(
            role_id, requires_running_agent=True
        )
        second_focus = generate_role_interview_focus.run(
            role_id, requires_running_agent=True
        )
        first_tech = regenerate_role_tech_questions.run(role_id)
        second_tech = regenerate_role_tech_questions.run(role_id)

    assert first_focus["status"] == "ok"
    assert second_focus == {
        "status": "skipped",
        "reason": "already_generated",
        "role_id": role_id,
    }
    assert first_tech["status"] == "ok"
    assert second_tech["status"] == "ok"
    focus_provider.assert_called_once()
    tech_provider.assert_called_once()
    assert "Go services" in focus_provider.call_args.kwargs["additional_requirements"]
    assert "Rust services" not in focus_provider.call_args.kwargs["additional_requirements"]
    assert "Go services" in tech_provider.call_args.kwargs["recruiter_requirements"]
    assert "Rust services" not in tech_provider.call_args.kwargs["recruiter_requirements"]
