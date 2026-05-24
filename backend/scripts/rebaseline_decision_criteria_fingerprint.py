"""Rebaseline pending decisions' criteria fingerprint onto the content-only hash.

One-time data fix for the staleness-churn incident. Before PR #334 the criteria
fingerprint hashed the volatile RoleCriterion row ``id``. Workable sync
hard-deletes + re-inserts derived criteria with fresh ids every tick, so an
UNCHANGED job spec churned the stored fingerprint and the read-time recompute
(now content-only) no longer matches it -> every pending decision shows
"criteria_changed" and 409s on approve/advance.

This recomputes ``criteria_content_fingerprint`` for each pending decision's role
and writes it to BOTH ``decision.criteria_fingerprint`` and
``input_fingerprint['criteria_fingerprint']`` so stored == current and the
spurious criteria dimension goes quiet. It deliberately touches ONLY the criteria
dimension: cv/score/note/cutoff drift stay as captured, so a decision with a
genuine input change remains correctly stale.

MUST run AFTER PR #334 is deployed — running it against the old (id-based) code
would just re-introduce the churn. No agent re-run, no Claude spend.

Run from backend/ (dry-run first):
  .venv/bin/python scripts/rebaseline_decision_criteria_fingerprint.py --dry-run
  .venv/bin/python scripts/rebaseline_decision_criteria_fingerprint.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure backend app package imports resolve when running from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.agent_decision import AgentDecision
from app.platform.database import SessionLocal
from app.services import decision_staleness


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebaseline pending decisions' criteria fingerprint (content-only)"
    )
    parser.add_argument("--batch-size", type=int, default=500, help="Row batch size (default: 500)")
    parser.add_argument("--org-id", type=int, default=None, help="Optional organization id filter")
    parser.add_argument("--role-id", type=int, default=None, help="Optional role id filter")
    parser.add_argument("--dry-run", action="store_true", help="Compute + report only; persist nothing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    started_at = time.time()
    processed = 0
    rebaselined = 0
    skipped_pre_a1 = 0
    stale_before = 0
    stale_after = 0
    failed = 0
    last_id = 0

    # Shared per-request memo so the per-role content fingerprint is computed
    # once per role across the whole run, not once per decision.
    cache = decision_staleness.StalenessCache()

    try:
        base = db.query(AgentDecision).filter(AgentDecision.status == "pending")
        if args.org_id is not None:
            base = base.filter(AgentDecision.organization_id == args.org_id)
        if args.role_id is not None:
            base = base.filter(AgentDecision.role_id == args.role_id)
        total_target = int(base.count())
        print(
            f"Starting criteria-fingerprint rebaseline: target={total_target} "
            f"batch_size={args.batch_size} org_id={args.org_id or 'all'} "
            f"role_id={args.role_id or 'all'} dry_run={args.dry_run}"
        )

        while True:
            batch_query = db.query(AgentDecision).filter(
                AgentDecision.status == "pending",
                AgentDecision.id > last_id,
            )
            if args.org_id is not None:
                batch_query = batch_query.filter(AgentDecision.organization_id == args.org_id)
            if args.role_id is not None:
                batch_query = batch_query.filter(AgentDecision.role_id == args.role_id)
            rows = batch_query.order_by(AgentDecision.id.asc()).limit(args.batch_size).all()
            if not rows:
                break

            for decision in rows:
                last_id = max(last_id, int(decision.id))
                processed += 1
                try:
                    report_before = decision_staleness.evaluate(db, decision, cache=cache)
                    if report_before.is_stale:
                        stale_before += 1

                    fp = decision.input_fingerprint or {}
                    if not fp:
                        # Pre-A1 decision: no baseline, never flagged. Leave alone.
                        skipped_pre_a1 += 1
                        continue

                    new_fp = decision_staleness.criteria_content_fingerprint(
                        db, int(decision.role_id), cache=cache
                    )
                    stored = decision.criteria_fingerprint
                    if new_fp != stored or fp.get("criteria_fingerprint") != new_fp:
                        decision.criteria_fingerprint = new_fp
                        # Reassign a fresh dict so SQLAlchemy flags the JSON column dirty.
                        decision.input_fingerprint = {**fp, "criteria_fingerprint": new_fp}
                        db.add(decision)
                        rebaselined += 1

                    # Re-evaluate against the rebaselined values (uncommitted in
                    # dry-run; reflects the session state either way) so the
                    # "still stale" count reflects only GENUINE drift.
                    report_after = decision_staleness.evaluate(db, decision, cache=cache)
                    if report_after.is_stale:
                        stale_after += 1
                except Exception as exc:
                    failed += 1
                    print(f"[warn] decision_id={getattr(decision, 'id', '?')} failed: {exc}")

                if processed % 200 == 0:
                    elapsed = max(0.1, time.time() - started_at)
                    print(
                        f"progress processed={processed} rebaselined={rebaselined} "
                        f"failed={failed} rate={processed / elapsed:.1f}/s"
                    )

            if args.dry_run:
                db.rollback()
            else:
                db.commit()
    finally:
        db.close()

    elapsed = max(0.1, time.time() - started_at)
    print(
        f"Done: processed={processed} rebaselined={rebaselined} "
        f"skipped_pre_a1={skipped_pre_a1} stale_before={stale_before} "
        f"stale_after={stale_after} failed={failed} elapsed_sec={elapsed:.1f}"
    )
    print(
        "stale_after counts decisions with GENUINE non-criteria drift "
        "(cv/score/note/cutoff) — those SHOULD stay stale."
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
