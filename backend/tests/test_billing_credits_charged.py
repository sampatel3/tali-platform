"""Customer-facing dollars use credits_charged (marked-up), not raw cost.

Goal: keep the three displays (Settings → Usage tab, Jobs page budget card,
per-role $X/$50 indicator) in the same unit as ``Role.monthly_usd_budget_cents``
so they reconcile. Also covers the admin reconcile endpoint guard.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.conftest import (
    TestingSessionLocal,
    auth_headers,
)


def _promote_superuser(email: str) -> None:
    from app.models.user import User

    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None
        user.is_superuser = True
        db.commit()
    finally:
        db.close()


def _seed_usage(*, org_id: int, role_id: int | None, cost_micro: int, credits: int, feature: str = "score") -> None:
    """Insert one UsageEvent with explicit raw + charged values."""
    from app.models.usage_event import UsageEvent

    db = TestingSessionLocal()
    try:
        db.add(
            UsageEvent(
                organization_id=org_id,
                role_id=role_id,
                feature=feature,
                model="claude-haiku-4-5-20251001",
                input_tokens=1000,
                output_tokens=500,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost_usd_micro=cost_micro,
                markup_multiplier=3,
                credits_charged=credits,
                cache_hit=0,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()


def _resolve_ids(email: str) -> tuple[int, int]:
    """Return (org_id, role_id) for the test user — creating a role if needed."""
    from app.models.role import Role
    from app.models.user import User

    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None
        org_id = int(user.organization_id)
        role = (
            db.query(Role)
            .filter(Role.organization_id == org_id, Role.deleted_at.is_(None))
            .first()
        )
        if role is None:
            role = Role(
                organization_id=org_id,
                name="Test Role",
                monthly_usd_budget_cents=5000,  # $50 customer-facing cap
            )
            db.add(role)
            db.commit()
            db.refresh(role)
        return org_id, int(role.id)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Phase 1: backend readers return marked-up dollars
# ---------------------------------------------------------------------------

# Note: a /agent/org-status integration test for the same SQL switch is
# intentionally omitted — the OrgKpiPayload schema in _hub_shared.py requires
# pending_decisions/pending_questions which _compute_kpis doesn't currently
# populate (pre-existing latent bug, tracked separately). The per-role
# breakdown test below exercises the same credits_charged aggregation pattern
# in the same module.

def test_role_breakdown_per_role_spend_uses_credits_charged(client):
    """Per-role 'AGENT ON · $X/$50' chip on the Jobs page."""
    headers, email = auth_headers(client)
    org_id, role_id = _resolve_ids(email)
    _seed_usage(org_id=org_id, role_id=role_id, cost_micro=1_500_000, credits=4_500_000)

    resp = client.get("/api/v1/agent/roles/breakdown", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    matched = [r for r in rows if r["role_id"] == role_id]
    assert len(matched) == 1
    # $4.50 charged → 450 cents.
    assert matched[0]["budget_cents"] == 450
    assert matched[0]["cap_cents"] == 5000


def test_role_usage_breakdown_feature_costs_use_credits_charged(client):
    """Role detail → usage breakdown table."""
    headers, email = auth_headers(client)
    org_id, role_id = _resolve_ids(email)
    _seed_usage(org_id=org_id, role_id=role_id, cost_micro=1_000_000, credits=3_000_000, feature="score")

    resp = client.get(f"/api/v1/roles/{role_id}/usage/breakdown", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    score_line = next(l for l in payload["by_feature"] if l["feature"] == "score")
    assert score_line["cost_cents"] == 300  # $3 charged, not $1 raw
    assert payload["monthly_spent_cents"] == 300


def test_budget_guard_check_monthly_usd_sums_credits_charged(db):
    """Per-cycle enforcement: a 3× markup feature should hit a $1.50 cap
    after $0.50 of raw Anthropic spend, not after $1.50."""
    from app.agent_runtime.budget_guard import check_monthly_usd, month_to_date_spend_cents
    from app.models.organization import Organization
    from app.models.role import Role
    from app.models.usage_event import UsageEvent

    org = Organization(name="t", slug="t")
    db.add(org)
    db.commit()
    role = Role(organization_id=org.id, name="r", monthly_usd_budget_cents=150)  # $1.50 cap
    db.add(role)
    db.commit()
    db.add(
        UsageEvent(
            organization_id=org.id,
            role_id=role.id,
            feature="score",
            model="claude-haiku-4-5-20251001",
            input_tokens=0,
            output_tokens=0,
            cost_usd_micro=500_000,       # $0.50 raw
            markup_multiplier=3,
            credits_charged=1_500_000,    # $1.50 charged → at cap
            cache_hit=0,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    assert month_to_date_spend_cents(db, role=role) == 150
    check = check_monthly_usd(db, role=role)
    assert check.ok is False
    assert "cap reached" in (check.reason or "")


# ---------------------------------------------------------------------------
# Phase 4: admin reconcile endpoint
# ---------------------------------------------------------------------------

def test_admin_reconcile_rejects_non_superuser(client):
    headers, _email = auth_headers(client)
    resp = client.post("/api/v1/billing/admin/reconcile?days=7", headers=headers)
    assert resp.status_code == 403


def test_admin_reconcile_rejects_out_of_range_days(client):
    headers, email = auth_headers(client)
    _promote_superuser(email)
    too_large = client.post("/api/v1/billing/admin/reconcile?days=999", headers=headers)
    assert too_large.status_code == 422
    too_small = client.post("/api/v1/billing/admin/reconcile?days=0", headers=headers)
    assert too_small.status_code == 422


def test_admin_reconcile_returns_skip_when_admin_key_missing(client, monkeypatch):
    """No ANTHROPIC_ADMIN_API_KEY → ``reconcile_recent`` short-circuits with
    a ``skipped`` summary, which the endpoint forwards as a normal 200."""
    from app.platform import config as config_module

    monkeypatch.setattr(config_module.settings, "ANTHROPIC_ADMIN_API_KEY", "", raising=False)

    headers, email = auth_headers(client)
    _promote_superuser(email)
    resp = client.post("/api/v1/billing/admin/reconcile?days=7", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True  # nothing failed; the run just skipped
    assert body["days"] == 7
    assert body.get("skipped") is True
