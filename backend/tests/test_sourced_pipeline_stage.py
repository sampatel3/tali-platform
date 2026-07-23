"""Phase 3a — the ``sourced`` pipeline stage (a pre-applied prospect).

A sourced candidate is a ``CandidateApplication`` at ``pipeline_stage='sourced'``:
no CV, never auto-scored, never in the decision queue. It moves to ``applied``
(and only then gets scored) when the person engages / applies.

These tests lock the two HARD GUARDS (no auto-score, no decision), the stage's
transitions and funnel bucket, the engage->applied->score transition, the
creation endpoint, and — critically — that EXISTING behavior is unchanged.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime.pipeline_service import (
    FUNNEL_BUCKETS,
    PIPELINE_STAGES,
    SYNC_MAPPABLE_STAGES,
    _STAGE_ORDER,
    funnel_bucket_for,
    map_legacy_status_to_pipeline,
    normalize_pipeline_stage,
    role_pipeline_counts,
    should_auto_advance_to_advanced,
    transition_stage,
)
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.services.pre_screen_decision_emitter import (
    queue_knockout_reject,
    queue_pre_screen_reject,
)
from tests.conftest import auth_headers

_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_SEQ = itertools.count(1)


def _seed(db, *, stage="sourced", outcome="open", **cols):
    n = next(_SEQ)
    org = Organization(name="O", slug=f"o-{id(db)}-{stage}-{n}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        auto_reject=False,
        agentic_mode_enabled=True,
        score_threshold=50,
        auto_reject_threshold_mode="manual",
    )
    db.add(role)
    db.flush()
    cand = Candidate(organization_id=org.id, email=f"c-{stage}-{n}@x.test", full_name="C")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status=stage,
        pipeline_stage=stage,
        pipeline_stage_source="recruiter",
        application_outcome=outcome,
        source="sourced" if stage == "sourced" else "manual",
        **cols,
    )
    db.add(app)
    db.flush()
    return org, role, cand, app


# ─────────────────────────── pure unit ────────────────────────────


def test_sourced_is_first_stage_and_accepted():
    assert PIPELINE_STAGES[0] == "sourced"
    assert normalize_pipeline_stage("sourced") == "sourced"
    # Ranked FIRST — below applied — so forward-only auto-advance holds and a
    # sourced lead never counts as "past applied".
    assert _STAGE_ORDER["sourced"] == 0
    assert _STAGE_ORDER["sourced"] < _STAGE_ORDER["applied"]


def test_normalize_still_422s_on_garbage():
    # Regression: the guard still rejects genuinely-invalid stages.
    with pytest.raises(HTTPException) as exc:
        normalize_pipeline_stage("not_a_stage")
    assert exc.value.status_code == 422


def test_funnel_bucket_sourced_is_its_own_bucket():
    assert "sourced" in FUNNEL_BUCKETS
    assert FUNNEL_BUCKETS[0] == "sourced"
    # Never folded into applied/scored — regardless of the scored flag.
    assert funnel_bucket_for("sourced", False) == "sourced"
    assert funnel_bucket_for("sourced", True) == "sourced"
    # Regression: the existing buckets are unchanged.
    assert funnel_bucket_for("applied", False) == "applied"
    assert funnel_bucket_for("applied", True) == "scored"
    assert funnel_bucket_for("review", False) == "completed"
    assert funnel_bucket_for("advanced", False) == "advanced"


def test_legacy_and_sync_mapping_never_yields_sourced():
    # Workable/Bullhorn/legacy statuses must NEVER map to sourced — it's
    # Taali-native only.
    for status in [
        "applied", "invited", "pending", "in_progress", "review", "completed",
        "rejected", "withdrawn", "hired", "offer_accepted", "phone_screen",
        "anything_unknown", None, "",
    ]:
        stage, _outcome = map_legacy_status_to_pipeline(status)
        assert stage != "sourced", status
    # And a synced ATS can't select it as a mapping target.
    assert "sourced" not in SYNC_MAPPABLE_STAGES
    assert set(SYNC_MAPPABLE_STAGES) == set(PIPELINE_STAGES) - {"sourced"}


def test_auto_advance_still_forward_only():
    # Regression: forward-only auto-advance is unchanged for the existing stages.
    assert should_auto_advance_to_advanced("applied") is True
    assert should_auto_advance_to_advanced("review") is True
    assert should_auto_advance_to_advanced("advanced") is False


# ─────────────────────────── transitions ────────────────────────────


def test_sourced_to_applied_allowed(db):
    _org, _role, _cand, app = _seed(db)
    transition_stage(
        db, app=app, to_stage="applied", source="system", actor_type="system",
    )
    assert app.pipeline_stage == "applied"


def test_sourced_cannot_skip_to_invited_or_advanced(db):
    for target in ("invited", "advanced", "review"):
        _org, _role, _cand, app = _seed(db, stage="sourced")
        with pytest.raises(HTTPException) as exc:
            transition_stage(
                db, app=app, to_stage=target, source="recruiter",
                actor_type="recruiter",
            )
        assert exc.value.status_code == 409
        assert app.pipeline_stage == "sourced"


# ─────────────────────── HARD GUARD: no decision ───────────────────────


def test_sourced_app_never_gets_a_pre_screen_reject_card(db):
    _org, role, _cand, app = _seed(
        db, stage="sourced", pre_screen_score_100=5.0, pre_screen_run_at=_AT,
    )
    result = queue_pre_screen_reject(
        db, organization_id=role.organization_id, role=role, application=app,
        pre_screen_score=5.0, threshold=50.0,
    )
    assert result is None
    assert db.query(AgentDecision).filter(
        AgentDecision.application_id == app.id
    ).count() == 0


def test_sourced_app_never_gets_a_knockout_reject_card(db):
    _org, role, _cand, app = _seed(db, stage="sourced")
    result = queue_knockout_reject(
        db, organization_id=role.organization_id, role=role, application=app,
        reason="Missing required skills", failed_question_ids=[1],
    )
    assert result is None
    assert db.query(AgentDecision).filter(
        AgentDecision.application_id == app.id
    ).count() == 0


def test_applied_below_threshold_still_gets_a_card(db):
    """Regression: the guard must NOT break the normal reject path — an
    APPLIED, genuinely-pre-screened, below-threshold candidate still cards."""
    _org, role, _cand, app = _seed(
        db, stage="applied", pre_screen_score_100=5.0, pre_screen_run_at=_AT,
    )
    result = queue_pre_screen_reject(
        db, organization_id=role.organization_id, role=role, application=app,
        pre_screen_score=5.0, threshold=50.0,
    )
    assert result is not None
    assert db.query(AgentDecision).filter(
        AgentDecision.application_id == app.id
    ).count() == 1


def test_auto_reject_task_hard_skips_sourced(db):
    """The auto-reject celery task (fired by on_application_created) hard-skips a
    sourced prospect — no evaluation, no decision."""
    from app.tasks.automation_tasks import run_application_auto_reject

    _org, _role, _cand, app = _seed(
        db, stage="sourced", pre_screen_score_100=1.0, pre_screen_run_at=_AT,
    )
    db.commit()
    out = run_application_auto_reject.apply(args=(app.id,)).get()
    assert out["status"] == "skipped"
    assert out["reason"] == "sourced_prospect"
    assert db.query(AgentDecision).filter(
        AgentDecision.application_id == app.id
    ).count() == 0


# ─────────────────────── HARD GUARD: no auto-score ───────────────────────


def test_auto_enqueue_scoring_skips_sourced(db, monkeypatch):
    """The cohort tick's auto-scorer must never enqueue a sourced prospect —
    even one carrying cv_text (defense-in-depth over the cv_text filter)."""
    from app.tasks import agent_tasks
    import app.services.cv_score_orchestrator as orch

    org, role, _cand, sourced_app = _seed(
        db, stage="sourced", cv_text="some cv text",
    )
    # A normal applied+unscored app on the same role, WITH cv_text — the
    # scorer SHOULD consider it (contrast).
    cand2 = Candidate(organization_id=org.id, email="applied@x.test", full_name="A")
    db.add(cand2)
    db.flush()
    applied_app = CandidateApplication(
        organization_id=org.id, candidate_id=cand2.id, role_id=role.id,
        status="applied", pipeline_stage="applied", pipeline_stage_source="system",
        application_outcome="open", source="manual", cv_text="some cv text",
    )
    db.add(applied_app)
    db.commit()

    seen: list[int] = []

    def _recorder(
        _db,
        app,
        force=False,
        *,
        requires_active_agent=True,
    ):
        assert requires_active_agent is True
        seen.append(int(app.id))
        return None

    monkeypatch.setattr(orch, "enqueue_score", _recorder)

    agent_tasks._auto_enqueue_scoring(db, role=role)

    assert sourced_app.id not in seen
    assert applied_app.id in seen


def test_sourced_app_has_zero_score_jobs(db):
    _org, _role, _cand, app = _seed(db, stage="sourced")
    db.commit()
    assert db.query(CvScoreJob).filter(
        CvScoreJob.application_id == app.id
    ).count() == 0


# ─────────────────── engage -> applied -> score ───────────────────


def test_engagement_moves_sourced_to_applied(db):
    """When a sourced prospect applies, the SAME row moves sourced->applied
    (respecting the unique constraint) rather than duplicating."""
    from app.domains.job_pages.apply_service import submit_application

    org, role, cand, sourced_app = _seed(db, stage="sourced")
    cand.email = "engage@x.test"
    db.flush()

    result = submit_application(
        db, org.id, role, full_name="Engager", email="engage@x.test",
    )
    assert result.created is True
    assert result.application.id == sourced_app.id  # no duplicate row
    assert result.application.pipeline_stage == "applied"
    # Only one application for (candidate, role).
    assert db.query(CandidateApplication).filter(
        CandidateApplication.candidate_id == cand.id,
        CandidateApplication.role_id == role.id,
    ).count() == 1


# ─────────────────────── creation endpoint ───────────────────────


def _make_role(client, headers):
    r = client.post(
        "/api/v1/roles",
        json={"name": "Sourcing Role", "description": "Hiring"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _make_related_role(client, headers, db):
    payload = _make_role(client, headers)
    role = db.get(Role, int(payload["id"]))
    role.source = "sister"
    role.role_kind = ROLE_KIND_SISTER
    role.job_spec_text = (
        "A complete related sourcing specification for production engineering "
        "delivery, operational reliability, and measurable outcomes."
    )
    db.commit()
    return role


def test_create_sourced_candidate_endpoint(client):
    headers, _ = auth_headers(client)
    role = _make_role(client, headers)
    resp = client.post(
        f"/api/v1/roles/{role['id']}/sourced-candidates",
        json={
            "name": "Jane Prospect",
            "email": "jane.prospect@example.com",
            "linkedin": "https://linkedin.com/in/jane",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["pipeline_stage"] == "sourced"
    assert body["application_outcome"] == "open"
    aid = body["id"]
    # No decision, no score job for a sourced prospect.
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        assert sess.query(AgentDecision).filter(
            AgentDecision.application_id == aid
        ).count() == 0
        assert sess.query(CvScoreJob).filter(
            CvScoreJob.application_id == aid
        ).count() == 0
    finally:
        sess.close()


def test_list_applications_filters_by_sourced_stage(client):
    """The Home hub's Sourced tracker fetches
    ``GET /applications?pipeline_stage=sourced`` — the filter must be accepted
    (not 422) and return only the sourced prospect."""
    headers, _ = auth_headers(client)
    role = _make_role(client, headers)
    created = client.post(
        f"/api/v1/roles/{role['id']}/sourced-candidates",
        json={"name": "Sourced Lead", "email": "sourced.lead@example.com"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    aid = created.json()["id"]

    resp = client.get(
        "/api/v1/applications",
        params={"pipeline_stage": "sourced", "include_stage_counts": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    ids = {item["id"] for item in items}
    assert aid in ids
    assert all(item["pipeline_stage"] == "sourced" for item in items)


def test_create_sourced_candidate_is_idempotent(client):
    headers, _ = auth_headers(client)
    role = _make_role(client, headers)
    payload = {"name": "Dup", "email": "dup.prospect@example.com"}
    first = client.post(
        f"/api/v1/roles/{role['id']}/sourced-candidates", json=payload, headers=headers,
    )
    second = client.post(
        f"/api/v1/roles/{role['id']}/sourced-candidates", json=payload, headers=headers,
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_related_sourced_candidate_materializes_only_its_canonical_membership(
    client, db
):
    headers, _ = auth_headers(client)
    related = _make_related_role(client, headers, db)
    sibling = _make_related_role(client, headers, db)
    candidate = Candidate(
        organization_id=int(related.organization_id),
        full_name="Existing Candidate With CV",
        email="related-sourced@example.com",
        cv_text="Existing Python and distributed systems evidence.",
    )
    db.add(candidate)
    db.commit()

    response = client.post(
        f"/api/v1/roles/{related.id}/sourced-candidates",
        json={
            "name": "Related Sourced Prospect",
            "email": "related-sourced@example.com",
        },
        headers=headers,
    )

    assert response.status_code == 201, response.text
    application_id = int(response.json()["id"])
    membership = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == int(related.id))
        .one()
    )
    assert membership.source_application_id == application_id
    assert membership.membership_source == "direct"
    assert membership.pipeline_stage == "sourced"
    assert membership.application_outcome == "open"
    assert membership.status == "unscorable"
    assert membership.cv_fingerprint is None
    assert membership.last_error_code == "sourced_prospect"
    assert (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == application_id)
        .count()
        == 0
    )

    related_rows = client.get(
        f"/api/v1/roles/{related.id}/applications",
        params={"pipeline_stage": "sourced"},
        headers=headers,
    )
    sibling_rows = client.get(
        f"/api/v1/roles/{sibling.id}/applications",
        params={"pipeline_stage": "sourced"},
        headers=headers,
    )
    assert related_rows.status_code == 200, related_rows.text
    assert [row["id"] for row in related_rows.json()] == [application_id]
    assert sibling_rows.status_code == 200, sibling_rows.text
    assert sibling_rows.json() == []
    assert (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == int(sibling.id))
        .count()
        == 0
    )


def test_related_sourced_candidate_reactivates_fresh_canonical_membership(
    client, db
):
    headers, _ = auth_headers(client)
    related = _make_related_role(client, headers, db)
    payload = {
        "name": "Returning Related Prospect",
        "email": "returning-related-sourced@example.com",
    }
    first = client.post(
        f"/api/v1/roles/{related.id}/sourced-candidates",
        json=payload,
        headers=headers,
    )
    assert first.status_code == 201, first.text
    application_id = int(first.json()["id"])
    application = db.get(CandidateApplication, application_id)
    membership = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == int(related.id))
        .one()
    )
    membership_id = int(membership.id)
    membership.version = 4
    membership.status = "done"
    membership.pipeline_stage = "advanced"
    membership.application_outcome = "rejected"
    membership.role_fit_score = 93
    membership.summary = "Prior sourced membership lifecycle"
    membership.deleted_at = datetime.now(timezone.utc)
    application.deleted_at = datetime.now(timezone.utc)
    db.commit()

    restored_response = client.post(
        f"/api/v1/roles/{related.id}/sourced-candidates",
        json=payload,
        headers=headers,
    )

    assert restored_response.status_code == 201, restored_response.text
    assert int(restored_response.json()["id"]) == application_id
    restored = db.get(SisterRoleEvaluation, membership_id)
    assert restored.deleted_at is None
    assert restored.source_application_id == application_id
    assert restored.membership_source == "direct"
    assert restored.pipeline_stage == "sourced"
    assert restored.application_outcome == "open"
    assert restored.status == "unscorable"
    assert restored.role_fit_score is None
    assert restored.summary is None
    assert restored.version == 5
    assert restored.history[-1]["role_fit_score"] == 93

    visible = client.get(
        f"/api/v1/roles/{related.id}/applications",
        params={"pipeline_stage": "sourced"},
        headers=headers,
    )
    assert visible.status_code == 200, visible.text
    assert [row["id"] for row in visible.json()] == [application_id]


@pytest.mark.parametrize(
    ("pipeline_stage", "application_outcome"),
    [("advanced", "open"), ("applied", "rejected")],
)
def test_related_sourced_writer_materializes_terminal_membership_as_excluded(
    client,
    db,
    pipeline_stage,
    application_outcome,
):
    headers, _ = auth_headers(client)
    related = _make_related_role(client, headers, db)
    candidate = Candidate(
        organization_id=int(related.organization_id),
        full_name="Existing Terminal Candidate",
        email=f"terminal-{pipeline_stage}-{application_outcome}@example.com",
        cv_text="Existing production engineering evidence.",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(related.organization_id),
        role_id=int(related.id),
        candidate_id=int(candidate.id),
        status=pipeline_stage,
        pipeline_stage=pipeline_stage,
        pipeline_stage_source="recruiter",
        application_outcome=application_outcome,
        source="manual",
    )
    db.add(application)
    db.commit()

    response = client.post(
        f"/api/v1/roles/{related.id}/sourced-candidates",
        json={"email": candidate.email},
        headers=headers,
    )

    assert response.status_code == 201, response.text
    membership = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == int(related.id))
        .one()
    )
    assert membership.source_application_id == int(application.id)
    assert membership.pipeline_stage == pipeline_stage
    assert membership.application_outcome == application_outcome
    assert membership.status == "excluded"
    assert membership.last_error_code == "direct_application_not_active"


def test_create_sourced_candidate_requires_identity(client):
    headers, _ = auth_headers(client)
    role = _make_role(client, headers)
    resp = client.post(
        f"/api/v1/roles/{role['id']}/sourced-candidates",
        json={"name": "No Contact"},
        headers=headers,
    )
    assert resp.status_code == 422


def test_create_sourced_candidate_requires_auth(client):
    resp = client.post(
        "/api/v1/roles/1/sourced-candidates",
        json={"email": "x@example.com"},
    )
    assert resp.status_code in (401, 403)


# ─────────────────────── funnel regression ───────────────────────


def test_sourced_does_not_inflate_applied_count(db):
    org, role, _cand, _app = _seed(db, stage="sourced")
    # An additional applied candidate on the same role.
    cand2 = Candidate(organization_id=org.id, email="applied2@x.test", full_name="A2")
    db.add(cand2)
    db.flush()
    db.add(CandidateApplication(
        organization_id=org.id, candidate_id=cand2.id, role_id=role.id,
        status="applied", pipeline_stage="applied", pipeline_stage_source="system",
        application_outcome="open", source="manual",
    ))
    db.commit()

    counts = role_pipeline_counts(db, organization_id=org.id, role_id=role.id)
    assert counts["sourced"] == 1
    assert counts["applied"] == 1  # sourced NOT folded in
