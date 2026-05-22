"""``GET /api/v1/billing/admin/metering-gap`` summarises ``claude_call_log``
for the current org over a window. This is the user-facing dashboard for
"where is my Anthropic money going?" — backed by the unconditional
call_log so the numbers are ground truth, not best-effort accounting.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.user import User
from tests.conftest import TestingSessionLocal, auth_headers


# SQLite BigInteger PK workaround (ClaudeCallLog.id is BigInteger).
_BIG_PK = {"claude_call_log": 0}

def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK:
        _BIG_PK[table] += 1
        target.id = _BIG_PK[table]

event.listen(ClaudeCallLog, "before_insert", _assign_big_pk)


def _promote_superuser(email: str) -> None:
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None
        user.is_superuser = True
        db.commit()
    finally:
        db.close()


def _seed_call_log(org_id: int, *, model: str, feature_hint: str, cost_micro: int, attribute: bool = False, status: str = "ok", when: datetime | None = None) -> None:
    """Insert a ClaudeCallLog row. When ``attribute=True``, also creates a
    matching UsageEvent and links the call_log via FK (the wrapper's
    happy path). When False, leaves usage_event_id NULL — that's the
    attribution-gap signal the endpoint counts."""
    db = TestingSessionLocal()
    try:
        usage_event_id = None
        if attribute:
            from app.models.usage_event import UsageEvent
            event_row = UsageEvent(
                organization_id=org_id,
                feature=feature_hint,
                model=model,
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost_usd_micro=cost_micro,
                markup_multiplier=1,
                credits_charged=cost_micro,
                cache_hit=0,
            )
            db.add(event_row)
            db.flush()
            usage_event_id = int(event_row.id)
        db.add(ClaudeCallLog(
            organization_id=org_id,
            model=model,
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd_micro=cost_micro,
            feature_hint=feature_hint,
            status=status,
            usage_event_id=usage_event_id,
            created_at=when or datetime.now(timezone.utc),
        ))
        db.commit()
    finally:
        db.close()


def test_endpoint_requires_superuser(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/billing/admin/metering-gap", headers=headers)
    assert resp.status_code == 403


def test_endpoint_validates_days_range(client):
    headers, email = auth_headers(client)
    _promote_superuser(email)
    assert client.get("/api/v1/billing/admin/metering-gap?days=0", headers=headers).status_code == 422
    assert client.get("/api/v1/billing/admin/metering-gap?days=91", headers=headers).status_code == 422


def test_endpoint_returns_totals_and_breakdowns(client):
    headers, email = auth_headers(client)
    _promote_superuser(email)
    # Resolve the user's org so seeded rows attribute correctly.
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        org_id = int(user.organization_id)
    finally:
        db.close()

    # 3 attributed + 2 unattributed across two features
    _seed_call_log(org_id, model="claude-haiku-4-5-20251001", feature_hint="score", cost_micro=5_000_000, attribute=False)
    _seed_call_log(org_id, model="claude-haiku-4-5-20251001", feature_hint="score", cost_micro=5_000_000, attribute=False)
    _seed_call_log(org_id, model="claude-haiku-4-5-20251001", feature_hint="prescreen", cost_micro=2_000_000, attribute=True)
    _seed_call_log(org_id, model="claude-sonnet-4-5", feature_hint="agent_autonomous", cost_micro=10_000_000, attribute=True)
    _seed_call_log(org_id, model="claude-haiku-4-5-20251001", feature_hint="score", cost_micro=3_000_000, attribute=True)

    resp = client.get("/api/v1/billing/admin/metering-gap?days=1", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Totals
    assert body["totals"]["calls"] == 5
    assert body["totals"]["cost_usd"] == pytest.approx(25.0)

    # Per-feature: score is biggest ($13)
    features = {row["feature"]: row for row in body["by_feature"]}
    assert features["score"]["calls"] == 3
    assert features["score"]["cost_usd"] == pytest.approx(13.0)
    assert features["agent_autonomous"]["cost_usd"] == pytest.approx(10.0)
    assert features["prescreen"]["cost_usd"] == pytest.approx(2.0)

    # Attribution gap: 2 score calls without usage_event FK = $10
    gap = body["attribution_gap"]
    assert gap["calls"] == 2
    assert gap["cost_usd"] == pytest.approx(10.0)


def test_endpoint_window_filter_excludes_old_rows(client):
    """Rows older than ``days`` are excluded from all four sections."""
    headers, email = auth_headers(client)
    _promote_superuser(email)
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        org_id = int(user.organization_id)
    finally:
        db.close()

    now = datetime.now(timezone.utc)
    _seed_call_log(org_id, model="haiku", feature_hint="score", cost_micro=1_000_000, when=now)
    _seed_call_log(org_id, model="haiku", feature_hint="score", cost_micro=99_000_000, when=now - timedelta(days=10))

    resp = client.get("/api/v1/billing/admin/metering-gap?days=1", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["totals"]["cost_usd"] == pytest.approx(1.0)


def test_endpoint_scopes_to_caller_org(client):
    """A superuser of org A only sees org A's call_log rows."""
    headers_a, email_a = auth_headers(client, organization_name="OrgA")
    _promote_superuser(email_a)
    # Second user → different org
    headers_b, email_b = auth_headers(client, organization_name="OrgB")

    db = TestingSessionLocal()
    try:
        u_a = db.query(User).filter(User.email == email_a).first()
        u_b = db.query(User).filter(User.email == email_b).first()
        org_a_id = int(u_a.organization_id)
        org_b_id = int(u_b.organization_id)
    finally:
        db.close()

    _seed_call_log(org_a_id, model="haiku", feature_hint="score", cost_micro=1_000_000)
    _seed_call_log(org_b_id, model="haiku", feature_hint="score", cost_micro=99_000_000)

    resp = client.get("/api/v1/billing/admin/metering-gap?days=1", headers=headers_a)
    assert resp.status_code == 200
    assert resp.json()["totals"]["cost_usd"] == pytest.approx(1.0)
