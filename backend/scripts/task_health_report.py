"""Per-task operational health report — the difficulty-calibration loop.

For every task with assessment history: funnel (sent → started → completed,
timeout share), score distribution (mean / stddev / min / max), average
active minutes, and flags for the failure modes generated tasks are prone
to (docs/ASSESSMENT_E2E_DEEP_DIVE.md §4):

- ``too_easy``       — mean score high AND baseline discrimination gone;
                       the lazy-model floor rises with every model release,
                       so this drifts upward over time.
- ``no_discrimination`` — scores clustered (stddev < 5 on 0-100).
- ``low_completion`` — < 40% of started assessments finish.
- ``insufficient_n`` — fewer than 5 completions; treat all stats as noise.

Read-only (no writes, no model calls). Complements the weekly
``recompute_task_calibrations`` beat, which tracks predictive quality
against realised outcomes in ``task_calibrations``.

Usage (from backend/): python -m scripts.task_health_report [--org-id 2]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.assessment import Assessment  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.platform.database import SessionLocal  # noqa: E402


def _score(a: Assessment) -> float | None:
    for field in ("taali_score", "assessment_score", "final_score"):
        value = getattr(a, field, None)
        if value is not None:
            return float(value)
    return float(a.score) * 10 if a.score is not None else None


def analyse_task(rows: list[Assessment]) -> dict:
    live = [a for a in rows if not a.is_voided and not a.is_demo]
    started = [a for a in live if a.started_at is not None]
    completed = [a for a in live if str(getattr(a.status, "value", a.status)).lower().startswith("completed")]
    timeouts = [a for a in completed if str(getattr(a.status, "value", a.status)).lower() == "completed_due_to_timeout"]
    scores = [s for s in (_score(a) for a in completed) if s is not None]
    minutes = [
        (a.completed_at - a.started_at).total_seconds() / 60
        for a in completed
        if a.started_at and a.completed_at
    ]

    stats = {
        "sent": len(live),
        "started": len(started),
        "completed": len(completed),
        "timeout_completed": len(timeouts),
        "completion_rate": round(len(completed) / len(started) * 100, 1) if started else None,
        "score_mean": round(statistics.mean(scores), 1) if scores else None,
        "score_stddev": round(statistics.stdev(scores), 1) if len(scores) >= 2 else None,
        "score_min": round(min(scores), 1) if scores else None,
        "score_max": round(max(scores), 1) if scores else None,
        "avg_active_minutes": round(statistics.mean(minutes), 1) if minutes else None,
    }

    flags = []
    if len(scores) < 5:
        flags.append("insufficient_n")
    else:
        if stats["score_mean"] is not None and stats["score_mean"] >= 85:
            flags.append("too_easy")
        if stats["score_stddev"] is not None and stats["score_stddev"] < 5:
            flags.append("no_discrimination")
    if started and stats["completion_rate"] is not None and stats["completion_rate"] < 40:
        flags.append("low_completion")
    stats["flags"] = flags
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", type=int, default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(Assessment)
        if args.org_id:
            query = query.filter(Assessment.organization_id == args.org_id)
        by_task: dict[int, list[Assessment]] = {}
        for a in query.all():
            if a.task_id:
                by_task.setdefault(int(a.task_id), []).append(a)
        if not by_task:
            print("No assessments found.")
            return 0
        tasks = {t.id: t for t in db.query(Task).filter(Task.id.in_(by_task.keys())).all()}
        print(f"{'task':<44} {'sent':>5} {'start':>5} {'done':>5} {'rate%':>6} {'mean':>6} {'sd':>5} {'min-max':>10}  flags")
        for task_id in sorted(by_task, key=lambda t: -len(by_task[t])):
            stats = analyse_task(by_task[task_id])
            task = tasks.get(task_id)
            name = (task.task_key or task.name) if task else f"#{task_id}"
            rng = (
                f"{stats['score_min']}-{stats['score_max']}"
                if stats["score_min"] is not None else "-"
            )
            print(
                f"{str(name)[:44]:<44} {stats['sent']:>5} {stats['started']:>5} "
                f"{stats['completed']:>5} {str(stats['completion_rate'] or '-'):>6} "
                f"{str(stats['score_mean'] or '-'):>6} {str(stats['score_stddev'] or '-'):>5} "
                f"{rng:>10}  {','.join(stats['flags']) or '-'}"
            )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
