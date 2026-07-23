"""One-shot: add the ``submission_comprehension`` rubric dimension to stored tasks.

The post-submit understanding check runs on EVERY assessment — it lives in the
submit path, not the rubric. The rubric decides only whether those answers are
scored. So a task whose rubric predates the check still asks the candidate five
questions and still shows the answers on the report; they just never reach the
Discernment axis.

The canonical template tasks fix themselves: ``sync_canonical_task_specs_on_startup``
re-reads ``backend/tasks/*.json`` on every boot and updates the template rows.
Org-scoped generated tasks have no such sync — their rubric was frozen by the
generator at creation time — so they need this.

Weight comes from scaling every existing dimension by ``1 - WEIGHT``, which
preserves each dimension's weight RELATIVE to the others. Deliberately not the
split used in the canonical specs (0.15 output_scrutiny -> 0.08 + 0.07): those
carry the 5-Ds spine, and generated tasks predating it have no output_scrutiny
to split. Taking it proportionally avoids this script deciding, per task, which
dimension matters less.

Does NOT touch a task whose rubric already has a ``comprehension_outcome``
dimension, and does NOT touch template tasks (the boot sync owns those).

Mutations go through ``lock_task_mutation_boundary`` so a task cannot be
rewritten underneath a live assessment.

Run (dry-run prints the plan; ``--execute`` writes):

    railway run --service resourceful-adaptation \
        python scripts/backfill_comprehension_dimension.py            # dry-run
    railway run --service resourceful-adaptation \
        python scripts/backfill_comprehension_dimension.py --execute  # apply
"""
from __future__ import annotations

import argparse
from typing import Any, Dict

from app.models.assessment import Assessment
from app.models.task import Task
from app.platform.database import SessionLocal
from app.services.task_mutation_guard import lock_task_mutation_boundary

DIMENSION_ID = "submission_comprehension"
GRADER = "comprehension_outcome"
WEIGHT = 0.07


def _has_comprehension_dim(rubric: Any) -> bool:
    if not isinstance(rubric, dict):
        return False
    return any(
        isinstance(details, dict)
        and str(details.get("grader") or "").strip() == GRADER
        for details in rubric.values()
    )


def _rebalanced(rubric: Dict[str, Any]) -> Dict[str, Any]:
    """Scale existing weights to make room, then append the new dimension.

    Rounds to 4dp and puts the residue on the heaviest dimension so the total
    lands on exactly 1.0 — ``validate_rubric_weights`` checks the sum, and a
    float-drift failure here would look like a data bug months from now.
    """
    updated: Dict[str, Any] = {}
    scale = 1.0 - WEIGHT
    for dim_id, details in rubric.items():
        if not isinstance(details, dict):
            updated[dim_id] = details
            continue
        weight = float(details.get("weight") or 0.0)
        updated[dim_id] = {**details, "weight": round(weight * scale, 4)}

    weighted = {
        dim_id: details
        for dim_id, details in updated.items()
        if isinstance(details, dict) and "weight" in details
    }
    if weighted:
        residue = round(1.0 - WEIGHT - sum(d["weight"] for d in weighted.values()), 4)
        if residue:
            heaviest = max(weighted, key=lambda k: weighted[k]["weight"])
            updated[heaviest] = {
                **updated[heaviest],
                "weight": round(updated[heaviest]["weight"] + residue, 4),
            }

    updated[DIMENSION_ID] = {"grader": GRADER, "part": "applied", "weight": WEIGHT}
    return updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="write changes")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        tasks = (
            db.query(Task)
            .filter(
                Task.is_active.is_(True),
                Task.organization_id.isnot(None),  # templates are owned by the boot sync
            )
            .order_by(Task.id)
            .all()
        )
        planned: list[tuple[Task, Dict[str, Any]]] = []
        for task in tasks:
            rubric = task.evaluation_rubric
            if not isinstance(rubric, dict) or not rubric:
                print(f"skip  {task.id:>4} {task.task_key} — no rubric")
                continue
            if _has_comprehension_dim(rubric):
                print(f"skip  {task.id:>4} {task.task_key} — already has the dimension")
                continue
            planned.append((task, _rebalanced(rubric)))

        for task, updated in planned:
            assessments = (
                db.query(Assessment).filter(Assessment.task_id == task.id).count()
            )
            total = sum(
                float(d["weight"])
                for d in updated.values()
                if isinstance(d, dict) and "weight" in d
            )
            print(
                f"plan  {task.id:>4} {task.task_key} — "
                f"{len(updated)} dims, sum={round(total, 4)}, "
                f"{assessments} existing assessment(s)"
            )

        if not planned:
            print("\nNothing to do.")
            return
        if not args.execute:
            print(f"\nDry run — {len(planned)} task(s) would change. Re-run with --execute.")
            return

        lock_task_mutation_boundary(db, task_ids=[task.id for task, _ in planned])
        for task, updated in planned:
            task.evaluation_rubric = updated
        db.commit()
        print(f"\nUpdated {len(planned)} task(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
