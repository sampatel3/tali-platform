"""P0: flag-gated configurable-stage transitions in pipeline_service.

Flag OFF (default) must be byte-for-byte the legacy strict behaviour; flag ON
makes transitions org-aware with ATS-standard free recruiter movement.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime import pipeline_service
from app.domains.assessments_runtime.pipeline_service import (
    ensure_pipeline_fields,
    role_pipeline_counts,
    transition_outcome,
    transition_stage,
)
from app.domains.assessments_runtime.pipeline_stages_service import (
    ensure_org_stages_seeded,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.pipeline_stage import PipelineStage
from app.models.role import Role


def _seed_app(db, *, stage="applied"):
    org = Organization(name="Acme")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Eng", source="manual")
    db.add(role)
    db.flush()
    cand = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage=stage,
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    return org, app


def _enable_flag(monkeypatch):
    monkeypatch.setattr(
        pipeline_service.settings, "ATS_CONFIGURABLE_STAGES_ENABLED", True
    )


# --- Flag OFF: legacy strict graph unchanged -------------------------------

def test_flag_off_blocks_non_edge_recruiter_move(db):
    _org, app = _seed_app(db, stage="applied")
    # applied->review is NOT an allowed recruiter edge in the legacy graph.
    with pytest.raises(HTTPException) as exc:
        transition_stage(
            db, app=app, to_stage="review", source="recruiter", actor_type="recruiter"
        )
    assert exc.value.status_code == 409
    assert app.pipeline_stage == "applied"


def test_flag_off_allows_legacy_edge(db):
    _org, app = _seed_app(db, stage="applied")
    transition_stage(
        db, app=app, to_stage="invited", source="recruiter", actor_type="recruiter"
    )
    assert app.pipeline_stage == "invited"


def test_flag_off_unknown_stage_still_remapped_from_status(db):
    # Legacy repair path, byte-identical with the flag off: a stage outside
    # PIPELINE_STAGES is treated as invalid and remapped from `status` by
    # ensure_pipeline_fields — both directly and via transition_outcome.
    _org, app = _seed_app(db, stage="mystery_stage")
    ensure_pipeline_fields(app)
    assert app.pipeline_stage == "applied"  # remapped from status='applied'

    _org2, app2 = _seed_app(db, stage="another_mystery")
    transition_outcome(
        db, app=app2, to_outcome="rejected", actor_type="recruiter"
    )
    assert app2.pipeline_stage == "applied"  # remapped, legacy behaviour
    assert app2.application_outcome == "rejected"


# --- Flag ON: org-aware, free recruiter movement ---------------------------

def test_flag_on_allows_free_recruiter_move(db, monkeypatch):
    _enable_flag(monkeypatch)
    org, app = _seed_app(db, stage="applied")
    ensure_org_stages_seeded(db, org.id)
    # applied->review is blocked when OFF, allowed when ON (free movement).
    transition_stage(
        db, app=app, to_stage="review", source="recruiter", actor_type="recruiter"
    )
    assert app.pipeline_stage == "review"


def test_flag_on_rejects_stage_not_in_org(db, monkeypatch):
    _enable_flag(monkeypatch)
    org, app = _seed_app(db, stage="applied")
    ensure_org_stages_seeded(db, org.id)
    with pytest.raises(HTTPException) as exc:
        transition_stage(
            db, app=app, to_stage="nonexistent", source="recruiter", actor_type="recruiter"
        )
    assert exc.value.status_code == 422


def test_flag_on_accepts_custom_stage(db, monkeypatch):
    _enable_flag(monkeypatch)
    org, app = _seed_app(db, stage="applied")
    ensure_org_stages_seeded(db, org.id)
    db.add(
        PipelineStage(
            organization_id=org.id,
            slug="sourced",
            name="Sourced",
            kind="sourced",
            position=10,
            is_default=False,
            is_active=True,
        )
    )
    db.flush()
    transition_stage(
        db, app=app, to_stage="sourced", source="recruiter", actor_type="recruiter"
    )
    assert app.pipeline_stage == "sourced"


def test_flag_on_custom_stage_survives_non_transition_paths(db, monkeypatch):
    # Codex P2: callers that don't thread allowed_slugs (transition_outcome,
    # direct ensure_pipeline_fields) must not clobber a custom stage under the
    # flag — normalization resolves the org's slugs via the app's session.
    _enable_flag(monkeypatch)
    org, app = _seed_app(db, stage="applied")
    ensure_org_stages_seeded(db, org.id)
    db.add(
        PipelineStage(
            organization_id=org.id,
            slug="onsite",
            name="Onsite",
            kind="interview",
            position=10,
            is_default=False,
            is_active=True,
        )
    )
    db.flush()
    transition_stage(
        db, app=app, to_stage="onsite", source="recruiter", actor_type="recruiter"
    )
    db.flush()
    assert app.pipeline_stage == "onsite"

    # Direct ensure_pipeline_fields (no allowed_slugs threaded) keeps the stage.
    ensure_pipeline_fields(app)
    assert app.pipeline_stage == "onsite"

    # Closing the application via transition_outcome keeps the stage too —
    # previously it was remapped from status before the outcome was recorded.
    transition_outcome(db, app=app, to_outcome="rejected", actor_type="recruiter")
    assert app.pipeline_stage == "onsite"
    assert app.application_outcome == "rejected"


def test_flag_on_funnel_buckets_custom_stage_by_kind(db, monkeypatch):
    _enable_flag(monkeypatch)
    org, app = _seed_app(db, stage="applied")
    ensure_org_stages_seeded(db, org.id)
    db.add(
        PipelineStage(
            organization_id=org.id,
            slug="onsite",
            name="Onsite",
            kind="interview",
            position=10,
            is_default=False,
            is_active=True,
        )
    )
    db.flush()
    transition_stage(
        db, app=app, to_stage="onsite", source="recruiter", actor_type="recruiter"
    )
    db.flush()  # autoflush is off in the test session; persist before counting
    counts = role_pipeline_counts(db, organization_id=org.id, role_id=app.role_id)
    # 'interview'-kind custom stage buckets into the 'advanced' display bucket.
    assert counts["advanced"] == 1
    assert counts["applied"] == 0
