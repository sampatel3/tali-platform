"""Reconciliation window widening (cost-recon timing fix).

The binding lateness in reconciliation is our OWN internal rows — the Message
Batches retrieve path lands claude_call_log / usage_events rows hours-to-days
after Anthropic billed the batch. A 2-day window reconciled a day at 03:00 and
never revisited it, leaving a stale negative drift. The window is now 4 days
(daily) plus a weekly 14-day settle sweep so late rows converge drift -> 0.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.platform.database import Base
from app.services import anthropic_reconciliation_service as svc


def test_lookback_is_widened():
    assert svc._RECONCILE_LOOKBACK_DAYS >= 4


def test_drift_alert_threshold():
    """Alert on material-spend rows with large drift (either direction); stay
    quiet on small drift and on sub-dollar noise days."""
    big = 5_000_000  # $5 — material
    # Large negative drift on a material day -> alert (the dangerous direction).
    assert svc._is_alertable_drift(Decimal("-21.7"), big) is True
    # Large positive drift on a material day -> alert too (over-count is a bug).
    assert svc._is_alertable_drift(Decimal("15.0"), big) is True
    # Small drift -> no alert.
    assert svc._is_alertable_drift(Decimal("-3.0"), big) is False
    # Sub-dollar noise day (the real $0.08 / -25% row) -> no alert.
    assert svc._is_alertable_drift(Decimal("-25.4"), 80_000) is False
    # Undefined drift (zero external spend) -> no alert.
    assert svc._is_alertable_drift(None, big) is False
    # Exactly at the threshold on the minimum material spend -> alert.
    assert svc._is_alertable_drift(
        Decimal(str(svc._ALERT_DRIFT_PCT)), svc._ALERT_MIN_COST_USD_MICRO
    ) is True


def test_reconcile_recent_pulls_the_widened_window(monkeypatch):
    """reconcile_recent must request the full widened day-range from Anthropic
    so late-settling internal rows get re-reconciled."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    captured: dict = {}
    monkeypatch.setattr(svc, "admin_is_configured", lambda: True)

    def _fake_usage(*, starting_at, ending_at, bucket_width):
        captured["starting_at"] = starting_at
        captured["ending_at"] = ending_at
        captured["bucket_width"] = bucket_width
        return []

    monkeypatch.setattr(svc, "fetch_usage_buckets", _fake_usage)
    monkeypatch.setattr(svc, "fetch_cost_buckets", lambda **_k: [])

    try:
        summary = svc.reconcile_recent(db, end_date=date(2026, 5, 28))
    finally:
        db.close()

    # Default window = _RECONCILE_LOOKBACK_DAYS (4): starting_at = end - 4 days,
    # ending_at exclusive = end + 1 day. Whole UTC days.
    assert captured["starting_at"].date() == date(2026, 5, 24)
    assert captured["ending_at"].date() == date(2026, 5, 29)
    assert captured["bucket_width"] == "1d"
    # No Anthropic rows -> nothing to upsert, but the run completes cleanly.
    assert "error" not in summary
