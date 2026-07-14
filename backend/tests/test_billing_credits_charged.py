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

def test_org_status_sums_credits_charged_and_populates_pending_splits(client):
    """Org-wide KPI poll: ``org_budget_spent_cents`` is now charged credits
    (raw × markup), and the previously-broken ``pending_decisions`` /
    ``pending_questions`` schema fields are populated so OrgKpiPayload
    validation passes (was 500'ing pre-fix)."""
    headers, email = auth_headers(client)
    org_id, role_id = _resolve_ids(email)
    _seed_usage(org_id=org_id, role_id=role_id, cost_micro=2_000_000, credits=6_000_000)

    resp = client.get("/api/v1/agent/org-status", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    # $6 charged → 600 cents. (Raw would have been 200 cents — proves the switch.)
    assert payload["org_budget_spent_cents"] == 600
    # The split fields exist and start at zero for a freshly-registered org.
    assert payload["pending_decisions"] == 0
    assert payload["pending_questions"] == 0
    assert payload["pending"] == payload["pending_decisions"] + payload["pending_questions"]


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
# resume_if_under_budget: raising the cap above spend auto-clears the pause
# (regression — previously only an off/on agent toggle cleared it, so a
# raised cap had no effect until a manual resume).
# ---------------------------------------------------------------------------

def _paused_role_at_cap(db, *, cap_cents: int = 5000, spend_cents: int = 5000):
    """Org + agent-enabled role paused on the cap, with ``spend_cents`` of
    month-to-date usage. Returns the ``Role``."""
    from app.agent_runtime.budget_guard import pause_role
    from app.models.organization import Organization
    from app.models.role import Role
    from app.models.usage_event import UsageEvent

    org = Organization(name="t", slug=f"t-{cap_cents}-{spend_cents}")
    db.add(org)
    db.commit()
    role = Role(
        organization_id=org.id,
        name="r",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=cap_cents,
    )
    db.add(role)
    db.commit()
    db.add(
        UsageEvent(
            organization_id=org.id,
            role_id=role.id,
            feature="agent_autonomous",
            model="claude-haiku-4-5-20251001",
            input_tokens=0,
            output_tokens=0,
            cost_usd_micro=spend_cents * 10_000,
            markup_multiplier=1,
            credits_charged=spend_cents * 10_000,  # micro-USD → cents on read
            cache_hit=0,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    pause_role(db, role=role, reason=f"monthly USD cap reached: {spend_cents}c >= {cap_cents}c")
    db.commit()
    db.refresh(role)
    assert role.agent_paused_at is not None
    return role


def test_resume_clears_pause_when_cap_raised_above_spend(db):
    from app.agent_runtime.budget_guard import resume_if_under_budget

    role = _paused_role_at_cap(db, cap_cents=5000, spend_cents=5000)
    role.monthly_usd_budget_cents = 10_000  # recruiter raises $50 → $100
    db.commit()

    assert resume_if_under_budget(db, role=role) is True
    assert role.agent_paused_at is None
    assert role.agent_paused_reason is None
    assert role.agent_bootstrap_status == "starting"
    assert role.agent_bootstrap_started_at is not None


def test_resume_stays_paused_when_production_runtime_is_unready(db):
    from unittest.mock import patch

    from app.agent_runtime.budget_guard import resume_if_under_budget

    role = _paused_role_at_cap(db, cap_cents=5000, spend_cents=5000)
    role.monthly_usd_budget_cents = 10_000
    db.commit()

    with (
        patch("app.platform.startup_validation.is_production_like", return_value=True),
        patch(
            "app.services.agent_worker_health.worker_beat_status",
            return_value={"ready": False, "reason": "heartbeat_stale"},
        ),
    ):
        assert resume_if_under_budget(db, role=role) is False

    assert role.agent_paused_at is not None
    assert role.agent_bootstrap_status is None


def test_resume_is_noop_while_still_over_cap(db):
    # Raising the cap but not above spend must NOT resume — otherwise the
    # next cycle re-pauses immediately and emits a confusing pause event.
    from app.agent_runtime.budget_guard import resume_if_under_budget

    role = _paused_role_at_cap(db, cap_cents=5000, spend_cents=5000)
    role.monthly_usd_budget_cents = 4000  # still below the 5000c spend
    db.commit()

    assert resume_if_under_budget(db, role=role) is False
    assert role.agent_paused_at is not None


def test_resume_is_noop_when_agent_disabled(db):
    # A manually disabled agent stays off even if nominally under cap.
    from app.agent_runtime.budget_guard import resume_if_under_budget

    role = _paused_role_at_cap(db, cap_cents=5000, spend_cents=5000)
    role.agentic_mode_enabled = False
    role.monthly_usd_budget_cents = 100_000
    db.commit()

    assert resume_if_under_budget(db, role=role) is False
    assert role.agent_paused_at is not None


def test_resume_is_noop_when_not_paused(db):
    # A running (non-paused) role is left untouched.
    from app.agent_runtime.budget_guard import resume_if_under_budget
    from app.models.organization import Organization
    from app.models.role import Role

    org = Organization(name="t", slug="t-running")
    db.add(org)
    db.commit()
    role = Role(
        organization_id=org.id,
        name="r",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=100_000,
    )
    db.add(role)
    db.commit()

    assert resume_if_under_budget(db, role=role) is False
    assert role.agent_paused_at is None


def test_patch_raising_budget_resumes_paused_role_via_route(client):
    """End-to-end of the actual bug: a budget-paused, agent-enabled role
    comes back ON when the recruiter raises the cap above spend through
    PATCH /roles/{id} alone — no agent off/on toggle — and an immediate
    review cycle is kicked instead of waiting for the 30-min beat."""
    from unittest.mock import patch as _patch
    from app.models.role import Role
    from app.models.usage_event import UsageEvent
    from app.models.user import User

    headers, email = auth_headers(client)

    # Seed a paused, over-cap, agent-enabled role directly in the DB — the
    # state the role lands in after the orchestrator hits the cap.
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        org_id = int(user.organization_id)
        role = Role(
            organization_id=org_id,
            name="Paused Agent Role",
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=5000,
            agent_paused_at=datetime.now(timezone.utc),
            agent_paused_reason="monthly USD cap reached: 5000c >= 5000c",
        )
        db.add(role)
        db.commit()
        db.refresh(role)
        role_id = int(role.id)
        db.add(
            UsageEvent(
                organization_id=org_id,
                role_id=role_id,
                feature="agent_autonomous",
                model="claude-haiku-4-5-20251001",
                input_tokens=0,
                output_tokens=0,
                cost_usd_micro=50_000_000,   # 5000c
                markup_multiplier=1,
                credits_charged=50_000_000,  # 5000c → at cap
                cache_hit=0,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    # Raise the cap above spend via the API. Mock the kicked task so we
    # assert dispatch without running a real cycle.
    with _patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as mock_delay:
        resp = client.patch(
            f"/api/v1/roles/{role_id}",
            json={"expected_version": 1, "monthly_usd_budget_cents": 10000},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_paused_at"] is None
    assert body.get("agent_paused_reason") in (None, "")
    mock_delay.assert_called_once_with(
        role_id,
        activation=False,
        dispatch_role_version=body["version"],
    )


def test_patch_budget_edit_does_not_clear_recruiter_pause(client):
    """A field edit is not implicit consent to undo a manual soft pause."""
    from unittest.mock import patch as _patch

    from app.models.role import Role
    from app.models.user import User

    headers, email = auth_headers(client)
    sess = TestingSessionLocal()
    try:
        user = sess.query(User).filter(User.email == email).one()
        role = Role(
            organization_id=int(user.organization_id),
            name="Manually Paused Budget Edit",
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=100,
            agent_paused_at=datetime.now(timezone.utc),
            agent_paused_reason="paused by recruiter",
        )
        sess.add(role)
        sess.commit()
        role_id = int(role.id)
    finally:
        sess.close()

    with _patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as kick:
        response = client.patch(
            f"/api/v1/roles/{role_id}",
            json={"expected_version": 1, "monthly_usd_budget_cents": 10_000},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["agent_paused_at"] is not None
    assert response.json()["agent_paused_reason"] == "paused by recruiter"
    assert not kick.called


def test_patch_explicit_true_resumes_through_guard_and_wakes_role(client):
    """The explicit toggle may clear a manual pause, but only via the guard."""
    from unittest.mock import patch as _patch

    from app.models.role import Role
    from app.models.user import User

    headers, email = auth_headers(client)
    sess = TestingSessionLocal()
    try:
        user = sess.query(User).filter(User.email == email).one()
        role = Role(
            organization_id=int(user.organization_id),
            name="Explicit Guarded Resume",
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=5000,
            agent_paused_at=datetime.now(timezone.utc),
            agent_paused_reason="paused by recruiter",
        )
        sess.add(role)
        sess.commit()
        role_id = int(role.id)
    finally:
        sess.close()

    with _patch("app.tasks.agent_tasks.agent_cohort_tick_role.delay") as kick:
        response = client.patch(
            f"/api/v1/roles/{role_id}",
            json={"expected_version": 1, "agentic_mode_enabled": True},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["agent_paused_at"] is None
    assert response.json()["agent_bootstrap_status"] == "starting"
    kick.assert_called_once_with(
        role_id,
        activation=False,
        dispatch_role_version=response.json()["version"],
    )


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
