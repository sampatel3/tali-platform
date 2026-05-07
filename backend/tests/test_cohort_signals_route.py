"""HTTP tests for GET /api/v1/roles/{id}/agent/cohort-signals.

Covers:
- 404 when role doesn't exist
- 404 when role belongs to a different org (org-scoping)
- 200 + insufficient_data payload for tiny pools
- 200 + computed signals when there's a real cohort, persisted to role
- Cache hit on second call (from_cache=true)
- force_recompute=true bypasses cache
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers, TestingSessionLocal


def _seed_pool(*, organization_id: int, role_id: int, top_skill: str = "kubernetes"):
    """Create a cohort large enough to surface signals.

    Top 5 (high TAALI) all carry top_skill; bottom 6 don't. With pool=11
    and TOP_FRACTION=0.10 → top_size clamps to MIN_TOP_SIZE=5, so the
    top group is the 5 highest-TAALI candidates and the rest are 6.
    """
    db = TestingSessionLocal()
    try:
        for i in range(5):
            cand = Candidate(
                organization_id=organization_id,
                email=f"top{i}-{role_id}@x.test",
                full_name=f"Top {i}",
                skills=["python", top_skill],
            )
            db.add(cand)
            db.flush()
            db.add(
                CandidateApplication(
                    organization_id=organization_id,
                    candidate_id=cand.id,
                    role_id=role_id,
                    status="applied",
                    pipeline_stage="review",
                    pipeline_stage_source="recruiter",
                    application_outcome="open",
                    source="manual",
                    taali_score_cache_100=85.0 + i,
                )
            )
        for i in range(6):
            cand = Candidate(
                organization_id=organization_id,
                email=f"bot{i}-{role_id}@x.test",
                full_name=f"Bot {i}",
                skills=["python"],
            )
            db.add(cand)
            db.flush()
            db.add(
                CandidateApplication(
                    organization_id=organization_id,
                    candidate_id=cand.id,
                    role_id=role_id,
                    status="applied",
                    pipeline_stage="review",
                    pipeline_stage_source="recruiter",
                    application_outcome="open",
                    source="manual",
                    taali_score_cache_100=40.0 + i,
                )
            )
        db.commit()
    finally:
        db.close()


def _create_role(*, organization_id: int, name: str = "Backend Engineer") -> int:
    db = TestingSessionLocal()
    try:
        role = Role(organization_id=organization_id, name=name, source="manual")
        db.add(role)
        db.commit()
        return int(role.id)
    finally:
        db.close()


def _user_org_id(email: str) -> int:
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        return int(user.organization_id)
    finally:
        db.close()


def test_cohort_signals_route_404_when_role_missing(client):
    headers, _email = auth_headers(client, organization_name="Org1")
    resp = client.get(
        "/api/v1/roles/999999/agent/cohort-signals", headers=headers
    )
    assert resp.status_code == 404


def test_cohort_signals_route_404_for_role_in_different_org(client):
    """Org-scoping: a role visible to org A must 404 for org B."""
    headers_a, email_a = auth_headers(client, organization_name="OrgA")
    headers_b, _email_b = auth_headers(client, organization_name="OrgB")

    org_a_id = _user_org_id(email_a)
    role_a_id = _create_role(organization_id=org_a_id, name="OrgA Role")

    # Org B user tries to read it
    resp = client.get(
        f"/api/v1/roles/{role_a_id}/agent/cohort-signals", headers=headers_b
    )
    assert resp.status_code == 404


def test_cohort_signals_route_returns_insufficient_data_for_small_pool(client):
    headers, email = auth_headers(client, organization_name="OrgC")
    org_id = _user_org_id(email)
    role_id = _create_role(organization_id=org_id, name="Empty Role")

    resp = client.get(
        f"/api/v1/roles/{role_id}/agent/cohort-signals", headers=headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["insufficient_data"] is True
    assert data["pool_size"] == 0
    assert data["signals"]["skills"] == []
    assert "insufficient" in data["summary"].lower()


def test_cohort_signals_route_computes_and_caches(client):
    headers, email = auth_headers(client, organization_name="OrgD")
    org_id = _user_org_id(email)
    role_id = _create_role(organization_id=org_id, name="Backend")
    _seed_pool(organization_id=org_id, role_id=role_id)

    # First call: compute + cache
    resp1 = client.get(
        f"/api/v1/roles/{role_id}/agent/cohort-signals", headers=headers
    )
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()
    assert data1["from_cache"] is False
    assert data1["pool_size"] == 11
    assert data1["top_size"] == 5
    skills = {s["feature"]: s for s in data1["signals"]["skills"]}
    assert "kubernetes" in skills
    assert skills["kubernetes"]["top_freq"] == pytest.approx(1.0)
    assert skills["kubernetes"]["exclusive_to_top"] is True
    assert "kubernetes" in data1["summary"]

    # Cache was populated on the role row.
    db = TestingSessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_id).one()
        assert role.agent_cohort_signals is not None
        assert role.agent_cohort_signals_at is not None
    finally:
        db.close()

    # Second call: cache hit
    resp2 = client.get(
        f"/api/v1/roles/{role_id}/agent/cohort-signals", headers=headers
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["from_cache"] is True
    assert data2["pool_size"] == 11


def test_cohort_signals_route_force_recompute_bypasses_cache(client):
    headers, email = auth_headers(client, organization_name="OrgE")
    org_id = _user_org_id(email)
    role_id = _create_role(organization_id=org_id, name="Backend")
    _seed_pool(organization_id=org_id, role_id=role_id)

    # Pre-warm cache with stale-looking data.
    db = TestingSessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_id).one()
        role.agent_cohort_signals = {
            "pool_size": 99999,
            "top_size": 99,
            "signals": {"skills": [], "companies": [], "titles": [], "schools": []},
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "insufficient_data": False,
        }
        role.agent_cohort_signals_at = datetime.now(timezone.utc)
        db.add(role)
        db.commit()
    finally:
        db.close()

    # Without force_recompute we'd get the bogus 99999 value back.
    resp = client.get(
        f"/api/v1/roles/{role_id}/agent/cohort-signals?force_recompute=true",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["from_cache"] is False
    assert data["pool_size"] == 11  # real value, not the 99999 we planted


def test_cohort_signals_route_recomputes_when_cache_expired(client):
    headers, email = auth_headers(client, organization_name="OrgF")
    org_id = _user_org_id(email)
    role_id = _create_role(organization_id=org_id, name="Backend")
    _seed_pool(organization_id=org_id, role_id=role_id)

    # Plant a cache that's older than the TTL.
    db = TestingSessionLocal()
    try:
        role = db.query(Role).filter(Role.id == role_id).one()
        role.agent_cohort_signals = {
            "pool_size": 1,
            "signals": {"skills": [], "companies": [], "titles": [], "schools": []},
            "computed_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "insufficient_data": False,
        }
        role.agent_cohort_signals_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.add(role)
        db.commit()
    finally:
        db.close()

    resp = client.get(
        f"/api/v1/roles/{role_id}/agent/cohort-signals", headers=headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["from_cache"] is False
    assert data["pool_size"] == 11


def test_cohort_signals_route_requires_auth(client):
    resp = client.get("/api/v1/roles/1/agent/cohort-signals")
    assert resp.status_code in (401, 403)
