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
