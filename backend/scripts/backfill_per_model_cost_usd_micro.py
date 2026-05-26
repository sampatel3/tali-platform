"""Backfill ``cost_usd_micro`` (and dependent ``credits_charged``) under
the correct per-model Anthropic rates.

Until 2026-05-26, ``raw_cost_usd_micro`` used a single global env-var
rate ($1 input / $5 output — Haiku's) for every model. Sonnet 4.5+ calls
were therefore booked at roughly ⅓ of their real cost. Going forward
the wrapper writes the correct number; this script fixes the history so
the Anthropic reconciliation report (and per-org spend display) match
actual spend.

Two tables are touched:
- ``claude_call_log`` — unconditional ground-truth log (since #237).
- ``usage_events`` — the customer-facing meter. ``credits_charged`` is
  re-derived from the new ``cost_usd_micro`` so the org's billed credit
  totals stay consistent with the markup table.

The ledger is intentionally NOT rewritten. Past credit debits already
landed in customers' balances at the old (under-counted) rate — that's
the bill they received and the only sensible thing is to leave it
alone. Going forward, debits will reflect the correct cost.

Run from backend/:
  DATABASE_URL=... .venv/bin/python scripts/backfill_per_model_cost_usd_micro.py --dry-run
  DATABASE_URL=... .venv/bin/python scripts/backfill_per_model_cost_usd_micro.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Iterable

# Ensure backend app package imports resolve when running from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.claude_call_log import ClaudeCallLog
from app.models.usage_event import UsageEvent
from app.platform.database import SessionLocal
from app.services.pricing_service import (
    Feature,
    _resolve_model_rates,
    credits_charged as _credits_charged,
    raw_cost_usd_micro,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch-size", type=int, default=2000)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute deltas but do not persist updates",
    )
    p.add_argument(
        "--table",
        choices=["claude_call_log", "usage_events", "both"],
        default="both",
    )
    return p.parse_args()


def _iter_batches(db: Session, model_cls, batch_size: int) -> Iterable[list]:
    """Yield batches of rows whose stored model is in the per-model rate
    table, ordered by id so re-runs are deterministic."""
    last_id = 0
    while True:
        q = (
            select(model_cls)
            .where(model_cls.id > last_id)
            .order_by(model_cls.id)
            .limit(batch_size)
        )
        rows = db.execute(q).scalars().all()
        if not rows:
            return
        yield rows
        last_id = int(rows[-1].id)


def _recompute_cost(row) -> int:
    return raw_cost_usd_micro(
        input_tokens=int(row.input_tokens or 0),
        output_tokens=int(row.output_tokens or 0),
        cache_read_tokens=int(row.cache_read_tokens or 0),
        cache_creation_tokens=int(row.cache_creation_tokens or 0),
        model=str(row.model or ""),
    )


def _is_repricable(model: str) -> bool:
    """Skip rows whose model already routes to the env-var defaults
    (Haiku rates). Re-pricing them would be a no-op; saves writes."""
    if not model:
        return False
    in_rate, _ = _resolve_model_rates(model)
    # The default env-var rate is 1.0 USD/MTok (Haiku) — anything OTHER
    # than that is a model that needs repricing.
    return str(in_rate) != "1"


def backfill_claude_call_log(db: Session, *, batch_size: int, dry_run: bool) -> dict:
    """Walk every claude_call_log row and re-price under per-model rates.

    Cost-only — call_log has no credits_charged or markup column.
    """
    examined = 0
    updated = 0
    total_old_micro = 0
    total_new_micro = 0
    for batch in _iter_batches(db, ClaudeCallLog, batch_size):
        for row in batch:
            examined += 1
            if not _is_repricable(str(row.model or "")):
                continue
            old = int(row.cost_usd_micro or 0)
            new = _recompute_cost(row)
            if new == old:
                continue
            total_old_micro += old
            total_new_micro += new
            updated += 1
            if not dry_run:
                row.cost_usd_micro = new
        if not dry_run:
            db.commit()
        else:
            db.expire_all()  # discard pending changes
    return {
        "examined": examined,
        "updated": updated,
        "old_usd": total_old_micro / 1_000_000,
        "new_usd": total_new_micro / 1_000_000,
    }


def backfill_usage_events(db: Session, *, batch_size: int, dry_run: bool) -> dict:
    """Walk every usage_event row and re-price under per-model rates.
    Re-derive ``credits_charged`` from the new ``cost_usd_micro`` using
    the same markup table the wrapper uses today, so the billing display
    stays consistent. The ledger is NOT updated — past debits are
    immutable (see module docstring).
    """
    examined = 0
    updated = 0
    total_old_micro = 0
    total_new_micro = 0
    total_old_credits = 0
    total_new_credits = 0
    for batch in _iter_batches(db, UsageEvent, batch_size):
        for row in batch:
            examined += 1
            if not _is_repricable(str(row.model or "")):
                continue
            old_cost = int(row.cost_usd_micro or 0)
            new_cost = _recompute_cost(row)
            if new_cost == old_cost:
                continue
            try:
                feature = Feature(row.feature)
            except ValueError:
                feature = Feature.OTHER
            new_credits = _credits_charged(
                feature=feature,
                cost_usd_micro=new_cost,
                cache_hit=bool(int(row.cache_hit or 0)),
            )
            old_credits = int(row.credits_charged or 0)
            total_old_micro += old_cost
            total_new_micro += new_cost
            total_old_credits += old_credits
            total_new_credits += new_credits
            updated += 1
            if not dry_run:
                row.cost_usd_micro = new_cost
                row.credits_charged = new_credits
        if not dry_run:
            db.commit()
        else:
            db.expire_all()
    return {
        "examined": examined,
        "updated": updated,
        "old_usd": total_old_micro / 1_000_000,
        "new_usd": total_new_micro / 1_000_000,
        "old_credits": total_old_credits,
        "new_credits": total_new_credits,
    }


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    started = time.time()
    try:
        results: dict[str, dict] = {}
        if args.table in ("claude_call_log", "both"):
            results["claude_call_log"] = backfill_claude_call_log(
                db, batch_size=args.batch_size, dry_run=args.dry_run
            )
        if args.table in ("usage_events", "both"):
            results["usage_events"] = backfill_usage_events(
                db, batch_size=args.batch_size, dry_run=args.dry_run
            )
    finally:
        db.close()
    elapsed = time.time() - started
    mode = "DRY-RUN" if args.dry_run else "WROTE"
    print(f"[{mode}] backfill complete in {elapsed:.1f}s")
    for table, summary in results.items():
        print(f"  {table}:")
        for k, v in summary.items():
            if isinstance(v, float):
                print(f"    {k}: ${v:,.2f}")
            else:
                print(f"    {k}: {v:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
