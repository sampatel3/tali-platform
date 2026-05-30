"""One-off: re-reconcile a window against Anthropic and print drift per day/model.

Run with prod env injected (DB + ANTHROPIC_ADMIN_API_KEY), from backend/:

    railway run --service resourceful-adaptation \
        python scripts/reconcile_and_report.py --days 14

Recomputes ``anthropic_usage_reconciliations`` for the last N days (idempotent
upsert) using the CURRENT internal rows — so after the capture + window fixes
deploy, you can watch the stored drift converge. Read-mostly: it only upserts
reconciliation rows and reads Anthropic's reports; no billing side effects.
"""
from __future__ import annotations

import argparse

from app.models.anthropic_usage_reconciliation import AnthropicUsageReconciliation
from app.platform.database import SessionLocal
from app.services.anthropic_reconciliation_service import reconcile_recent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=14, help="window to re-reconcile")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        summary = reconcile_recent(db, days=args.days)
        print("reconcile summary:", summary)
        if summary.get("drift_alerts"):
            print(f"\n⚠️  {summary['drift_alerts']} drift alert(s):")
            for a in summary.get("drift_alert_details", []):
                print(f"    {a}")

        rows = (
            db.query(AnthropicUsageReconciliation)
            .order_by(
                AnthropicUsageReconciliation.usage_date.desc(),
                AnthropicUsageReconciliation.model,
            )
            .limit(80)
            .all()
        )
        print(
            f"\n{'date':12}{'model':30}{'anthropic$':>11}{'internal$':>11}"
            f"{'cost_drift%':>12}{'tok_drift%':>11}"
        )
        for r in rows:
            a = (r.anthropic_cost_usd_micro or 0) / 1e6
            i = (r.internal_cost_usd_micro or 0) / 1e6
            cd = "" if r.cost_drift_pct is None else f"{float(r.cost_drift_pct):.1f}"
            td = "" if r.tokens_drift_pct is None else f"{float(r.tokens_drift_pct):.1f}"
            print(
                f"{str(r.usage_date):12}{(r.model or ''):30}"
                f"{a:>10.2f}{i:>11.2f}{cd:>11}{td:>11}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
