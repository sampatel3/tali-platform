"""Battle-test every generated draft task that's awaiting review.

Runs each un-reviewed generated draft through the E2B battle-test
(materialize → bootstrap → baseline test run + structural checks) and
stamps the report card at ``task.extra_data.battle_test``, where the
agent-chat draft review card surfaces it.

Sandbox-only: no Anthropic calls, no score changes, no task activation.
Re-runnable — reports are overwritten in place.

Usage (from backend/, prod via railway ssh or locally against DATABASE_URL):
    python -m scripts.battle_test_drafts            # list drafts (dry run)
    python -m scripts.battle_test_drafts --apply    # run + persist reports
    python -m scripts.battle_test_drafts --apply --task-id 47
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.task import Task  # noqa: E402
from app.platform.database import SessionLocal  # noqa: E402
from app.services.task_battle_test import persist_battle_test, run_battle_test  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="run the sandboxes and persist reports")
    parser.add_argument("--task-id", type=int, default=None, help="limit to one draft")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(Task).filter(Task.is_active.is_(False), Task.organization_id.isnot(None))
        if args.task_id:
            query = query.filter(Task.id == args.task_id)
        drafts = [
            t for t in query.order_by(Task.id).all()
            if isinstance(t.extra_data, dict) and t.extra_data.get("generated")
            and t.extra_data.get("needs_review")
        ]
        if not drafts:
            print("No generated drafts awaiting review.")
            return 0
        print(f"{len(drafts)} draft(s) awaiting review:")
        for task in drafts:
            existing = (task.extra_data or {}).get("battle_test") or {}
            print(f"  #{task.id} {task.task_key or task.name} (last verdict: {existing.get('verdict') or 'never run'})")
        if not args.apply:
            print("\nDry run — pass --apply to battle-test them.")
            return 0

        failures = 0
        for task in drafts:
            print(f"\n=== battle-testing #{task.id} {task.task_key or task.name} ...")
            report = run_battle_test(task)
            persist_battle_test(db, task, report)
            baseline = report.get("baseline") or {}
            print(
                f"    verdict={report.get('verdict')} bootstrap_ok={report.get('bootstrap_ok')} "
                f"baseline={baseline.get('passed')}/{baseline.get('total')} error={report.get('error')}"
            )
            for check in report.get("checks") or []:
                mark = "ok" if check.get("ok") else "FAIL"
                print(f"      [{mark}] {check.get('id')}: {check.get('detail')}")
            if report.get("verdict") != "pass":
                failures += 1
        print(f"\nDone: {len(drafts) - failures} pass / {failures} fail.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
