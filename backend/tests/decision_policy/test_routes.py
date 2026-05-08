"""HTTP routes for the DecisionPolicy Hub."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.deps import get_current_user
from app.main import app as fastapi_app
from app.decision_policy.bootstrap import bootstrap_org
from app.models.decision_policy import DecisionPolicy
from app.models.organization import Organization
from app.models.rubric_revision import RubricRevision
from app.models.user import User
from app.platform.database import get_db


def _admin_user(db, *, organization_id: int) -> User:
    user = User(
        organization_id=organization_id,
        email=f"admin-{organization_id}@x.test",
        full_name="Admin",
        hashed_password="x",
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _override_user(user: User):
    fastapi_app.dependency_overrides[get_current_user] = lambda: user


def _override_db(db):
    def _yield():
        try:
            yield db
        finally:
            pass

    fastapi_app.dependency_overrides[get_db] = _yield


@pytest.fixture
def admin_client(db):
    org = Organization(name="Hub Org", slug=f"hub-org-{id(db)}")
    db.add(org)
    db.flush()
    bootstrap_org(db, organization_id=int(org.id))
    db.commit()
    user = _admin_user(db, organization_id=int(org.id))
    _override_user(user)
    _override_db(db)
    with TestClient(fastapi_app) as client:
        yield client, user, org
    fastapi_app.dependency_overrides.clear()


def test_get_active_policy_returns_bootstrap(admin_client):
    client, _user, _org = admin_client
    resp = client.get("/api/v1/admin/decision-policy")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["policy_id"]
    assert body["revision_id"]
    assert body["policy_json"]["schema_version"] == "v1"


def test_pending_retunes_empty_when_none_proposed(admin_client):
    client, _, _ = admin_client
    resp = client.get("/api/v1/admin/decision-policy/pending")
    assert resp.status_code == 200
    assert resp.json() == []


def test_activate_pending_policy(admin_client, db):
    client, user, org = admin_client
    # Manually craft a pending policy + revision.
    rev = RubricRevision(
        organization_id=org.id,
        cause="feedback_retune",
        feedback_ids=[],
        notes="proposed",
    )
    db.add(rev)
    db.flush()
    pending = DecisionPolicy(
        organization_id=org.id,
        role_id=None,
        revision_id=int(rev.id),
        policy_json={
            "schema_version": "v1",
            "decision_points": {
                "send_assessment": {
                    "thresholds": {"role_fit_min": 60.0},
                    "weights": {"role_fit_score": 1.0},
                    "rules": [
                        {
                            "if": "role_fit_score >= role_fit_min",
                            "then": "queue_send_assessment",
                            "priority": 50,
                        }
                    ],
                    "confidence_floor": 0.5,
                }
            },
        },
        activated_at=None,
    )
    db.add(pending)
    db.commit()

    pending_list = client.get("/api/v1/admin/decision-policy/pending").json()
    assert any(p["policy_id"] == pending.id for p in pending_list)

    resp = client.post(f"/api/v1/admin/decision-policy/{pending.id}/activate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["policy_id"] == pending.id
    assert body["deactivated_previous"] is not None


def test_signals_returns_zero_when_no_data(admin_client):
    client, _, _ = admin_client
    resp = client.get("/api/v1/admin/decision-policy/signals?days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_days"] == 30
    assert body["manual_action_volume"] == 0
    assert body["agent_decision_volume"] == 0
