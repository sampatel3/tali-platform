"""Tests for the stage scoping on /roles/{role_id}/process.

The cascade endpoint accepts an optional ``stage`` body field. When set,
the dry-run counts and the worker only touch candidates in that stage
(or with that outcome, for ``rejected``). Default = run the whole role.

Why this matters: recruiters on a busy role have already moved a subset
of candidates to ``advanced``. They want to re-score *those* without
burning agent budget on the 300+ Applied rows.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Candidate,
    CandidateApplication,
    Organization,
    Role,
)
from app.platform.database import Base


@pytest.fixture()
def session_factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("app.platform.database.SessionLocal", Session, raising=False)
    return Session


def _seed_role_with_mixed_stages(Session) -> tuple[int, int]:
    """Seed a role with 3 applied + 2 advanced + 1 rejected candidates.

    Returns (org_id, role_id).
    """
    db = Session()
    try:
        org = Organization(
            name="Acme",
            slug="acme",
            workable_subdomain="acme",
            workable_access_token="t",
            workable_connected=True,
        )
        db.add(org)
        db.flush()

        role = Role(
            organization_id=org.id,
            name="Engineer",
            job_spec_text="Backend engineer\nRequirements:\n- Python\n",
        )
        db.add(role)
        db.flush()

        def make_app(idx: int, stage: str, outcome: str = "open") -> None:
            cand = Candidate(
                organization_id=org.id,
                email=f"c{idx}@x.com",
                full_name=f"C{idx}",
                cv_text="Resume body",
            )
            db.add(cand)
            db.flush()
            db.add(
                CandidateApplication(
                    organization_id=org.id,
                    candidate_id=cand.id,
                    role_id=role.id,
                    status="applied",
                    source="workable",
                    cv_text="Resume body",
                    pipeline_stage=stage,
                    application_outcome=outcome,
                )
            )

        for i in range(3):
            make_app(i, "applied")
        for i in range(3, 5):
            make_app(i, "advanced")
        make_app(5, "advanced", outcome="rejected")
        db.commit()
        return org.id, role.id
    finally:
        db.close()


def test_matches_stage_filter_unit():
    """Pure-Python predicate used by the cascade worker's pre-screen step."""
    from app.domains.assessments_runtime.applications_routes import (
        _matches_stage_filter,
    )

    class A:
        def __init__(self, stage, outcome="open"):
            self.pipeline_stage = stage
            self.application_outcome = outcome

    # No filter / all → everything passes
    assert _matches_stage_filter(A("applied"), None) is True
    assert _matches_stage_filter(A("applied"), "all") is True
    assert _matches_stage_filter(A("applied"), "") is True

    # Stage filter — must match stage AND be open
    assert _matches_stage_filter(A("advanced"), "advanced") is True
    assert _matches_stage_filter(A("applied"), "advanced") is False
    assert _matches_stage_filter(A("advanced", "rejected"), "advanced") is False

    # Rejected — outcome-based, ignores stage
    assert _matches_stage_filter(A("advanced", "rejected"), "rejected") is True
    assert _matches_stage_filter(A("applied", "rejected"), "rejected") is True
    assert _matches_stage_filter(A("advanced", "open"), "rejected") is False


def test_process_dry_run_scopes_to_advanced_stage(session_factory):
    """Dry-run with stage='advanced' must count only the 2 advanced
    candidates, not the 3 applied or the rejected one.
    """
    from app.domains.assessments_runtime.applications_routes import _process_dry_run

    org_id, role_id = _seed_role_with_mixed_stages(session_factory)
    db = session_factory()
    try:
        counts_all = _process_dry_run(
            db,
            role_id=role_id,
            organization_id=org_id,
            fetch_cvs=False,
            refresh_cvs=False,
            pre_screen=False,
            refresh_pre_screen=False,
            score_mode="all",
        )
        counts_advanced = _process_dry_run(
            db,
            role_id=role_id,
            organization_id=org_id,
            fetch_cvs=False,
            refresh_cvs=False,
            pre_screen=False,
            refresh_pre_screen=False,
            score_mode="all",
            stage_filter="advanced",
        )
        counts_rejected = _process_dry_run(
            db,
            role_id=role_id,
            organization_id=org_id,
            fetch_cvs=False,
            refresh_cvs=False,
            pre_screen=False,
            refresh_pre_screen=False,
            score_mode="all",
            stage_filter="rejected",
        )
    finally:
        db.close()

    # All: 3 applied + 2 advanced + 1 advanced-but-rejected = 6
    assert counts_all["total_candidates"] == 6
    assert counts_all["score"]["will_run"] == 6

    # Advanced: only the 2 open+advanced (rejected one excluded)
    assert counts_advanced["total_candidates"] == 2
    assert counts_advanced["score"]["will_run"] == 2

    # Rejected: the 1 we marked rejected
    assert counts_rejected["total_candidates"] == 1
    assert counts_rejected["score"]["will_run"] == 1


def test_process_dry_run_unknown_stage_no_filter(session_factory):
    """Defensive: an unknown stage string falls through to no-filter rather
    than silently returning zero — the endpoint validates first, but the
    helper shouldn't claim "0 candidates" for an unrecognised filter."""
    from app.domains.assessments_runtime.applications_routes import _process_dry_run

    org_id, role_id = _seed_role_with_mixed_stages(session_factory)
    db = session_factory()
    try:
        counts = _process_dry_run(
            db,
            role_id=role_id,
            organization_id=org_id,
            fetch_cvs=False,
            refresh_cvs=False,
            pre_screen=False,
            refresh_pre_screen=False,
            score_mode="all",
            stage_filter="not_a_real_stage",
        )
    finally:
        db.close()

    assert counts["total_candidates"] == 6


def test_process_dry_run_explicit_application_ids(session_factory):
    """Ticked checkboxes → only those IDs are processed, regardless of stage.

    application_ids overrides stage_filter when both arrive (the explicit
    selection wins).
    """
    from app.domains.assessments_runtime.applications_routes import _process_dry_run
    from app.models import CandidateApplication

    org_id, role_id = _seed_role_with_mixed_stages(session_factory)
    db = session_factory()
    try:
        applied_id = (
            db.query(CandidateApplication.id)
            .filter(CandidateApplication.role_id == role_id)
            .filter(CandidateApplication.pipeline_stage == "applied")
            .first()
        )[0]
        advanced_id = (
            db.query(CandidateApplication.id)
            .filter(CandidateApplication.role_id == role_id)
            .filter(CandidateApplication.pipeline_stage == "advanced")
            .filter(CandidateApplication.application_outcome == "open")
            .first()
        )[0]

        counts = _process_dry_run(
            db,
            role_id=role_id,
            organization_id=org_id,
            fetch_cvs=False,
            refresh_cvs=False,
            pre_screen=False,
            refresh_pre_screen=False,
            score_mode="all",
            application_ids=[applied_id, advanced_id],
        )
        counts_override = _process_dry_run(
            db,
            role_id=role_id,
            organization_id=org_id,
            fetch_cvs=False,
            refresh_cvs=False,
            pre_screen=False,
            refresh_pre_screen=False,
            score_mode="all",
            stage_filter="rejected",
            application_ids=[applied_id, advanced_id],
        )
    finally:
        db.close()

    assert counts["total_candidates"] == 2
    assert counts["score"]["will_run"] == 2
    # application_ids takes precedence over stage_filter
    assert counts_override["total_candidates"] == 2


def test_search_applications_advanced_first(session_factory):
    """The MCP search_applications helper must surface candidates in
    pipeline_stage='advanced' before applied/etc., regardless of score.

    Recruiter has already moved Advanced candidates forward — those
    carry hard signal and the agent should re-evaluate them before
    fresh-applied rows that haven't been triaged yet.
    """
    from app.mcp.handlers import search_applications
    from app.models import CandidateApplication, User

    org_id, role_id = _seed_role_with_mixed_stages(session_factory)
    db = session_factory()
    try:
        apps = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.role_id == role_id)
            .all()
        )
        # Bias scores so applied has the HIGHEST score — without
        # Advanced-first ordering, score-desc would put applied first.
        for a in apps:
            if a.pipeline_stage == "applied":
                a.taali_score_cache_100 = 90
            elif a.pipeline_stage == "advanced":
                a.taali_score_cache_100 = 40
        db.commit()

        user = User(
            email="r@x.com",
            hashed_password="x",
            full_name="R",
            organization_id=org_id,
        )
        db.add(user)
        db.commit()

        results = search_applications(
            db,
            user,
            role_id=role_id,
            limit=10,
        )
    finally:
        db.close()

    # All 5 open rows (3 applied + 2 advanced) come back
    assert len(results) == 5
    # First entries must be the advanced ones — recruiter signal beats raw score
    first_two_stages = [r.get("pipeline_stage") for r in results[:2]]
    assert all(s == "advanced" for s in first_two_stages), (
        f"Expected first 2 to be 'advanced', got {first_two_stages}"
    )
