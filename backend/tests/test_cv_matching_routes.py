"""Route tests for the cv_match_v3.0 admin + override endpoints.

Auth is exercised via the shared ``auth_headers`` helper in ``conftest``,
which goes through the real registration → verify → login flow.
Superuser-only routes get an extra DB update to flip ``is_superuser``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.cv_matching import telemetry
from tests.conftest import (
    TestingSessionLocal,
    auth_headers,
    register_user,
    verify_user,
    login_user,
)


@pytest.fixture(autouse=True)
def _reset_traces():
    with telemetry._ring_lock:
        telemetry._ring.clear()
    yield


def _seed_trace(trace_id: str = "trace-test", final_status: str = "ok") -> None:
    with telemetry._ring_lock:
        telemetry._ring.append(
            {
                "trace_id": trace_id,
                "cv_hash": "deadbeef",
                "jd_hash": "feedface",
                "prompt_version": "cv_match_v3.0",
                "model_version": "claude-haiku-4-5-20251001",
                "input_tokens": 100,
                "output_tokens": 200,
                "latency_ms": 350,
                "retry_count": 0,
                "validation_failures": 0,
                "cache_hit": False,
                "final_status": final_status,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )


def _make_superuser(email: str) -> None:
    """Promote a registered user to is_superuser via direct DB update."""
    from app.models.user import User

    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None, f"user {email} not found"
        user.is_superuser = True
        db.commit()
    finally:
        db.close()


def _seed_application(headers: dict) -> tuple[int, int]:
    """Create a candidate + role + application via API. Returns (candidate_id, application_id)."""
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication
    from app.models.role import Role
    from app.models.user import User

    db = TestingSessionLocal()
    try:
        # Resolve org_id from the just-registered recruiter user.
        # Header doesn't carry it, so look up by the most recent user.
        recruiter = (
            db.query(User).order_by(User.id.desc()).first()
        )
        org_id = recruiter.organization_id if recruiter else None
        assert org_id is not None, "recruiter has no organization"

        candidate = Candidate(
            organization_id=org_id,
            email=f"cand-{recruiter.id}@test.com",
            full_name="Pipeline Candidate",
            cv_text="Senior engineer with AWS experience.",
        )
        db.add(candidate)
        db.flush()

        role = Role(
            organization_id=org_id,
            name="Senior Data Engineer",
        )
        db.add(role)
        db.flush()

        application = CandidateApplication(
            organization_id=org_id,
            candidate_id=candidate.id,
            role_id=role.id,
            status="applied",
        )
        db.add(application)
        db.commit()
        return candidate.id, application.id
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# /admin/cv-match/traces                                                       #
# --------------------------------------------------------------------------- #


def test_admin_traces_requires_auth(client):
    resp = client.get("/api/v1/admin/cv-match/traces")
    assert resp.status_code in (401, 403)


def test_admin_traces_rejects_non_superuser(client):
    headers, email = auth_headers(client)
    _seed_trace()
    resp = client.get("/api/v1/admin/cv-match/traces", headers=headers)
    assert resp.status_code == 403


def test_admin_traces_returns_recent(client):
    headers, email = auth_headers(client)
    _make_superuser(email)
    _seed_trace("t1")
    _seed_trace("t2")
    resp = client.get("/api/v1/admin/cv-match/traces?limit=10", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]["trace_id"] == "t2"  # newest first
    assert rows[1]["trace_id"] == "t1"


# --------------------------------------------------------------------------- #
# /candidates/{id}/cv-match-override                                           #
# --------------------------------------------------------------------------- #


def test_override_creates_row(client):
    headers, email = auth_headers(client)
    candidate_id, application_id = _seed_application(headers)

    body = {
        "application_id": application_id,
        "original_trace_id": "trace-xyz",
        "original_recommendation": "lean_no",
        "override_recommendation": "yes",
        "original_score": 62.5,
        "recruiter_notes": "Strong cultural signal in interview.",
    }
    resp = client.post(
        f"/api/v1/candidates/{candidate_id}/cv-match-override",
        json=body,
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    row = resp.json()
    assert row["application_id"] == application_id
    assert row["override_recommendation"] == "yes"
    assert row["original_recommendation"] == "lean_no"
    assert "Strong cultural signal" in row["recruiter_notes"]


def test_override_404_when_application_missing(client):
    headers, _ = auth_headers(client)
    body = {
        "application_id": 999_999,
        "override_recommendation": "no",
    }
    resp = client.post(
        "/api/v1/candidates/1/cv-match-override",
        json=body,
        headers=headers,
    )
    assert resp.status_code == 404


def test_override_400_when_application_not_for_candidate(client):
    headers, email = auth_headers(client)
    candidate_id, application_id = _seed_application(headers)
    wrong_candidate_id = candidate_id + 999

    body = {
        "application_id": application_id,
        "override_recommendation": "no",
    }
    resp = client.post(
        f"/api/v1/candidates/{wrong_candidate_id}/cv-match-override",
        json=body,
        headers=headers,
    )
    assert resp.status_code == 400


def test_override_requires_auth(client):
    body = {
        "application_id": 1,
        "override_recommendation": "no",
    }
    resp = client.post(
        "/api/v1/candidates/1/cv-match-override",
        json=body,
    )
    assert resp.status_code in (401, 403)
