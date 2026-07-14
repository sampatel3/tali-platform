"""GET /analytics/cost-per-outcome — BILLED unit economics next to the funnel.

Direct unit cost (pre-screen, score) = feature billed spend ÷ candidates it
billed for. Fully-loaded (advanced, hire) = total window billed spend ÷
timestamped transitions. Per-unit is null (never a crash) when count == 0.
Org-scoped + role/window scoped. BILLED spend only (credits_charged).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.models.user import User
from tests.conftest import auth_headers


def _app(db, org_id, role_id, email):
    cand = Candidate(organization_id=org_id, email=email, full_name=email.split("@")[0])
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id, candidate_id=cand.id, role_id=role_id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual",
    )
    db.add(app)
    db.flush()
    return app


def _usage(db, org_id, role_id, *, feature, entity_id, charged_micro, created_at=None):
    db.add(UsageEvent(
        organization_id=org_id, role_id=role_id, feature=feature, entity_id=str(entity_id),
        model="claude-haiku-4-5", input_tokens=10, output_tokens=5,
        cost_usd_micro=charged_micro, markup_multiplier=3, credits_charged=charged_micro,
        cache_hit=0, created_at=created_at or datetime.now(timezone.utc),
    ))


def _transition(db, org_id, app_id, *, event_type, to_stage=None, to_outcome=None, created_at=None):
    db.add(CandidateApplicationEvent(
        organization_id=org_id, application_id=app_id, event_type=event_type,
        to_stage=to_stage, to_outcome=to_outcome, actor_type="system",
        idempotency_key=f"t:{app_id}:{event_type}:{to_stage}:{to_outcome}:{created_at}",
        created_at=created_at or datetime.now(timezone.utc),
    ))


def test_cost_per_outcome_direct_and_loaded(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Backend", source="manual")
    db.add(role)
    db.flush()

    a1 = _app(db, org_id, role.id, "c1@x.test")
    a2 = _app(db, org_id, role.id, "c2@x.test")
    # 2 pre-screens (distinct candidates) billed 10_000 micro (1c) each = 2c.
    _usage(db, org_id, role.id, feature="prescreen", entity_id=a1.id, charged_micro=10_000)
    _usage(db, org_id, role.id, feature="prescreen", entity_id=a2.id, charged_micro=10_000)
    # 1 score billed 30_000 micro (3c) on one candidate.
    _usage(db, org_id, role.id, feature="score", entity_id=a1.id, charged_micro=30_000)
    # One advance + one hire transition.
    _transition(db, org_id, a1.id, event_type="pipeline_stage_changed", to_stage="advanced")
    _transition(db, org_id, a1.id, event_type="application_outcome_changed", to_outcome="hired")
    db.commit()

    body = client.get("/api/v1/analytics/cost-per-outcome", headers=headers).json()

    # Total billed = 2c + 3c = 5c.
    assert body["billed_spend_cents"] == 5
    assert body["counts"] == {"pre_screened": 2, "scored": 1, "advanced": 1, "hired": 1}
    # DIRECT: pre-screen 2c / 2 candidates = 1.0c each; score 3c / 1 = 3.0c.
    assert body["per_outcome"]["pre_screen"] == {"cost_cents": 1.0, "count": 2}
    assert body["per_outcome"]["score"] == {"cost_cents": 3.0, "count": 1}
    # FULLY-LOADED: total 5c / 1 advanced = 5.0c; / 1 hired = 5.0c.
    assert body["per_outcome"]["advanced"] == {"cost_cents": 5.0, "count": 1}
    assert body["per_outcome"]["hired"] == {"cost_cents": 5.0, "count": 1}


def test_cost_per_outcome_zero_counts_yield_null_not_crash(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    # Spend but NO transitions and NO score → loaded/score denominators are 0.
    a1 = _app(db, org_id, role.id, "c1@x.test")
    _usage(db, org_id, role.id, feature="prescreen", entity_id=a1.id, charged_micro=10_000)
    db.commit()

    body = client.get("/api/v1/analytics/cost-per-outcome", headers=headers).json()
    assert body["per_outcome"]["score"]["cost_cents"] is None
    assert body["per_outcome"]["advanced"]["cost_cents"] is None
    assert body["per_outcome"]["hired"]["cost_cents"] is None
    assert body["per_outcome"]["pre_screen"]["cost_cents"] == 1.0


def test_cost_per_outcome_role_scoped(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role_a = Role(organization_id=org_id, name="A", source="manual")
    role_b = Role(organization_id=org_id, name="B", source="manual")
    db.add_all([role_a, role_b])
    db.flush()
    aa = _app(db, org_id, role_a.id, "a@x.test")
    bb = _app(db, org_id, role_b.id, "b@x.test")
    _usage(db, org_id, role_a.id, feature="prescreen", entity_id=aa.id, charged_micro=10_000)
    _usage(db, org_id, role_b.id, feature="prescreen", entity_id=bb.id, charged_micro=50_000)
    _transition(db, org_id, aa.id, event_type="pipeline_stage_changed", to_stage="advanced")
    _transition(db, org_id, bb.id, event_type="pipeline_stage_changed", to_stage="advanced")
    db.commit()

    scoped = client.get(f"/api/v1/analytics/cost-per-outcome?role_id={role_a.id}", headers=headers).json()
    # Only role A's spend + its single advance.
    assert scoped["billed_spend_cents"] == 1
    assert scoped["counts"]["advanced"] == 1
    assert scoped["counts"]["pre_screened"] == 1


def test_cost_per_outcome_window_scoped(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    now = datetime.now(timezone.utc)
    a1 = _app(db, org_id, role.id, "recent@x.test")
    a2 = _app(db, org_id, role.id, "old@x.test")
    _usage(db, org_id, role.id, feature="prescreen", entity_id=a1.id, charged_micro=10_000, created_at=now)
    _usage(db, org_id, role.id, feature="prescreen", entity_id=a2.id, charged_micro=10_000,
           created_at=now - timedelta(days=40))
    db.commit()

    since = (now - timedelta(days=10)).date().isoformat()
    body = client.get(f"/api/v1/analytics/cost-per-outcome?date_from={since}", headers=headers).json()
    # Only the recent pre-screen falls in the 10-day window.
    assert body["counts"]["pre_screened"] == 1
    assert body["billed_spend_cents"] == 1
    assert body["window"]["label"].startswith("Last ")


def test_cost_per_outcome_requires_auth(client):
    resp = client.get("/api/v1/analytics/cost-per-outcome")
    assert resp.status_code == 401, resp.text
