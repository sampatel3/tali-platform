"""A failed Anthropic cost-report fetch must not wipe good cost history.

``reconcile_recent`` re-reconciles the trailing ``_RECONCILE_LOOKBACK_DAYS``
window on every run (upsert by date/workspace/model). The cost report is
fetched separately and its failure is swallowed to an empty list. Before the
fix, a swallowed failure defaulted every ``anthropic_cost`` to 0 and the upsert
overwrote the previously-captured non-zero cost with that phantom 0 — which is
exactly how the 2026-07-23 03:00 run zeroed the cost on every 07-19..07-22 row
that earlier runs had priced correctly.

The fix distinguishes "Anthropic reports $0" from "cost report unavailable":
on a failed fetch we preserve the existing cost and leave cost_drift untouched.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.components.integrations.anthropic_admin.usage_reports import (
    AnthropicUsageError,
    CostBucket,
    UsageBucket,
)
from app.models.anthropic_usage_reconciliation import AnthropicUsageReconciliation
from app.models.organization import Organization
from app.services import anthropic_reconciliation_service as svc

_DAY = date(2026, 7, 21)
_MODEL = "claude-sonnet-4-6"


def _usage_bucket() -> UsageBucket:
    start = datetime(2026, 7, 21, tzinfo=timezone.utc)
    end = datetime(2026, 7, 22, tzinfo=timezone.utc)
    return UsageBucket(
        starting_at=start,
        ending_at=end,
        workspace_id=None,
        api_key_id="apikey_test",
        model=_MODEL,
        service_tier="standard",
        context_window="0-200k",
        uncached_input_tokens=900_000,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        output_tokens=50_000,
        web_search_requests=0,
    )


def _cost_bucket() -> CostBucket:
    start = datetime(2026, 7, 21, tzinfo=timezone.utc)
    end = datetime(2026, 7, 22, tzinfo=timezone.utc)
    return CostBucket(
        starting_at=start,
        ending_at=end,
        workspace_id=None,
        cost_type="tokens",
        description="Claude Sonnet 4.6 Usage - Input Tokens",
        model=_MODEL,
        token_type="uncached_input_tokens",
        context_window="0-200k",
        service_tier="standard",
        amount_cents=363,  # $3.63
        currency="USD",
    )


def _patch_fetches(monkeypatch, *, cost_ok: bool) -> None:
    monkeypatch.setattr(svc, "admin_is_configured", lambda: True)
    monkeypatch.setattr(
        svc, "fetch_usage_buckets", lambda **_kw: iter([_usage_bucket()])
    )
    if cost_ok:
        monkeypatch.setattr(
            svc, "fetch_cost_buckets", lambda **_kw: iter([_cost_bucket()])
        )
    else:
        def _boom(**_kw):
            raise AnthropicUsageError("cost report 503")

        monkeypatch.setattr(svc, "fetch_cost_buckets", _boom)


def _row(db) -> AnthropicUsageReconciliation:
    return (
        db.query(AnthropicUsageReconciliation)
        .filter(
            AnthropicUsageReconciliation.usage_date == _DAY,
            AnthropicUsageReconciliation.model == _MODEL,
        )
        .one()
    )


def test_failed_cost_fetch_preserves_prior_cost(db, monkeypatch):
    # A shared-key org so the Default-workspace bucket resolves an internal
    # aggregate (irrelevant to cost, but keeps the row realistic).
    db.add(Organization(name="Shared", slug=f"shared-{id(db)}"))
    db.commit()

    # Run 1: cost report available → row priced at $3.63.
    _patch_fetches(monkeypatch, cost_ok=True)
    svc.reconcile_recent(db, days=1, end_date=_DAY)
    assert _row(db).anthropic_cost_usd_micro == 3_630_000

    # Run 2: cost report fails. The token side still updates, but the cost
    # must be PRESERVED, not overwritten with a phantom 0.
    _patch_fetches(monkeypatch, cost_ok=False)
    result = svc.reconcile_recent(db, days=1, end_date=_DAY)

    row = _row(db)
    assert row.anthropic_cost_usd_micro == 3_630_000  # preserved, not zeroed
    assert result.get("cost_report_unavailable") is True
    # Tokens still reconcile on the same run.
    assert row.anthropic_input_tokens == 900_000


def test_successful_cost_fetch_still_updates_cost(db, monkeypatch):
    """Guard against over-correction: when cost IS available it must write."""
    db.add(Organization(name="Shared2", slug=f"shared2-{id(db)}"))
    db.commit()

    _patch_fetches(monkeypatch, cost_ok=True)
    svc.reconcile_recent(db, days=1, end_date=_DAY)
    result = svc.reconcile_recent(db, days=1, end_date=_DAY)

    assert _row(db).anthropic_cost_usd_micro == 3_630_000
    assert result.get("cost_report_unavailable") is False
