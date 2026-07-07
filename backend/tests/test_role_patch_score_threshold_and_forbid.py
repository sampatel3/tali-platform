"""PATCH /roles/{id} actually applies score_threshold; RoleUpdate rejects unknown fields.

Codex #84 + #107.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.role import Role
from app.models.user import User

from .conftest import auth_headers


def _seed_role(db, *, org_id: int, score_threshold: int | None = None) -> Role:
    role = Role(
        organization_id=org_id,
        name=f"Backend {id(db)}",
        source="manual",
        score_threshold=score_threshold,
    )
    db.add(role)
    db.flush()
    db.commit()
    return role


def _current_user(db) -> User:
    return db.query(User).order_by(User.id.desc()).first()


def test_patch_role_applies_score_threshold(db, client):
    headers, _ = auth_headers(client, organization_name="ScoreOrg")
    me = _current_user(db)
    role = _seed_role(db, org_id=me.organization_id, score_threshold=60)

    resp = client.patch(
        f"/api/v1/roles/{role.id}",
        json={"score_threshold": 75},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["score_threshold"] == 75

    db.expire(role)
    assert role.score_threshold == 75


def test_patch_role_can_clear_score_threshold(db, client):
    headers, _ = auth_headers(client, organization_name="ScoreOrg2")
    me = _current_user(db)
    role = _seed_role(db, org_id=me.organization_id, score_threshold=70)

    resp = client.patch(
        f"/api/v1/roles/{role.id}",
        json={"score_threshold": None},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    db.expire(role)
    assert role.score_threshold is None


def test_patch_role_threshold_change_reconciles_reject_queue(db, client):
    """Lowering the threshold through the PATCH endpoint must retire reject
    cards the new cutoff no longer justifies — the end-to-end wiring of
    ``reconcile_pre_screen_reject_decisions`` into ``update_role``.
    """
    from app.models.agent_decision import AgentDecision
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication
    from app.services.pre_screen_decision_emitter import queue_pre_screen_reject

    headers, _ = auth_headers(client, organization_name="ReconcileOrg")
    me = _current_user(db)
    role = Role(
        organization_id=me.organization_id,
        name=f"Agent role {id(db)}",
        source="manual",
        score_threshold=50,
        # Manual mode: this test reconciles against a recruiter-pinned threshold
        # change. The product default is now ``auto`` (the pinned value is
        # ignored), so opt into manual to exercise the reconciliation path.
        auto_reject_threshold_mode="manual",
        agentic_mode_enabled=True,
        auto_reject=False,
    )
    db.add(role); db.flush()
    cand = Candidate(organization_id=me.organization_id, email="r@x.test", full_name="R")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=me.organization_id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        pre_screen_score_100=40.0,
        pre_screen_run_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(app); db.flush()
    card = queue_pre_screen_reject(
        db, organization_id=me.organization_id, role=role, application=app,
        pre_screen_score=40.0, threshold=50.0,
    )
    db.commit()
    assert card.status == "pending"

    # 40 was below 50, but is at/above the new cutoff of 30 → card retired.
    resp = client.patch(
        f"/api/v1/roles/{role.id}", json={"score_threshold": 30}, headers=headers,
    )
    assert resp.status_code == 200, resp.text

    db.expire(card)
    assert db.query(AgentDecision).filter(AgentDecision.id == card.id).one().status == "discarded"


def test_patch_role_threshold_resolution_failure_skips_reconcile(db, client, monkeypatch):
    """A threshold-resolution error after the PATCH must NOT crash the edit
    or run reconcile with a (None) threshold — it's skipped, the role edit
    still applies. Guards the data-loss path Codex flagged.
    """
    from app.domains.assessments_runtime import roles_management_routes as rmr

    headers, _ = auth_headers(client, organization_name="ResolveFailOrg")
    me = _current_user(db)
    role = Role(
        organization_id=me.organization_id,
        name=f"Agent {id(db)}",
        source="manual",
        score_threshold=50,
        agentic_mode_enabled=True,
        auto_reject=False,
    )
    db.add(role); db.flush(); db.commit()

    real = rmr._effective_pre_screen_threshold
    calls = {"n": 0}

    def flaky(dbsess, r):
        calls["n"] += 1
        if calls["n"] >= 2:  # pre-update read ok; post-update read fails
            raise RuntimeError("threshold boom")
        return real(dbsess, r)

    monkeypatch.setattr(rmr, "_effective_pre_screen_threshold", flaky)

    resp = client.patch(
        f"/api/v1/roles/{role.id}", json={"score_threshold": 30}, headers=headers,
    )
    assert resp.status_code == 200, resp.text
    db.expire(role)
    assert role.score_threshold == 30  # edit applied despite resolution failure


def test_patch_role_rejects_unknown_field(db, client):
    headers, _ = auth_headers(client, organization_name="ForbidOrg")
    me = _current_user(db)
    role = _seed_role(db, org_id=me.organization_id)

    resp = client.patch(
        f"/api/v1/roles/{role.id}",
        json={"additional_requirements": "this key was retired in alembic 068"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


def test_create_role_rejects_unknown_field(db, client):
    headers, _ = auth_headers(client, organization_name="ForbidOrg2")

    resp = client.post(
        "/api/v1/roles",
        json={
            "name": "Test Role",
            "additional_requirements": "retired key",
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


def test_patch_role_updates_job_spec_text(db, client):
    """The Job Specification tab edits job_spec_text directly (the field the
    read-view renders) — PATCH must accept and apply it."""
    headers, _ = auth_headers(client, organization_name="SpecOrg")
    me = _current_user(db)
    role = _seed_role(db, org_id=me.organization_id)

    resp = client.patch(
        f"/api/v1/roles/{role.id}",
        json={"name": "Senior AI Engineer", "job_spec_text": "The full, edited spec."},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    db.expire(role)
    assert role.job_spec_text == "The full, edited spec."
    assert role.name == "Senior AI Engineer"

    # Empty clears it (read-view falls back to description).
    resp = client.patch(
        f"/api/v1/roles/{role.id}",
        json={"job_spec_text": ""},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    db.expire(role)
    assert role.job_spec_text is None
