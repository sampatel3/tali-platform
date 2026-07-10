"""Seed the announced-two-stage A/B experiment for one role.

The deep dive (docs/ASSESSMENT_E2E_DEEP_DIVE.md §2) parks the two-stage
question on data: catalog tasks score practice OBSERVED inside the flat
rubric; this script creates the ANNOUNCED variant — same task, practice
re-tagged as Part 1 with a visible stage stepper and the 30/70 blend — and
wires a 50/50 experiment so the two designs compete on completion rate,
score discrimination, and time use. Decide at ≥20 completions per arm
(the comparison analytics already refuse to declare below that).

The variant is DERIVED from the base catalog task at seed time — no
duplicate 800-line JSON in git. Idempotent: re-running updates the variant
spec in place and preserves the experiment salt (stable assignments).

Usage (from backend/):
    python -m scripts.seed_two_stage_ab --org-id 2 --role-id 26 \
        --base-task-key data_eng_bronze_ingestion            # dry run
    ... --apply                                              # create/update
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sql_text  # noqa: E402

from app.models.task import Task  # noqa: E402
from app.platform.database import SessionLocal  # noqa: E402

TWO_STAGE_CONFIG = {
    "parts": [
        {
            "title": "Practice & Setup",
            "minutes": 8,
            "blurb": (
                "Set up the workspace the way you'd genuinely work — a short "
                "CLAUDE.md/AGENTS.md context file, a PLAN.md sketch, a first "
                "test run. This part is scored on load-bearing practice, not "
                "ritual files."
            ),
        },
        {
            "title": "Applied task",
            "minutes": 22,
            "blurb": (
                "Own the decisions Claude raises and direct it to build to "
                "your calls, then verify and submit."
            ),
        },
    ],
    "note": "Both parts count: 30% practice & setup, 70% applied task.",
}


def build_two_stage_variant(base: Task, *, organization_id: int) -> dict:
    """Derive the announced-two-stage variant payload from a base task."""
    extra = copy.deepcopy(base.extra_data) if isinstance(base.extra_data, dict) else {}
    rubric = copy.deepcopy(base.evaluation_rubric) if isinstance(base.evaluation_rubric, dict) else {}

    practice_dims = 0
    for details in rubric.values():
        if not isinstance(details, dict):
            continue
        if (
            str(details.get("grader") or "").strip() == "practice_outcome"
            or str(details.get("lens") or "").strip() == "practice"
        ):
            details["part"] = "practice"  # announced: practice IS Part 1
            practice_dims += 1
    if practice_dims == 0:
        raise ValueError(
            f"base task {base.task_key!r} has no practice dim to re-tag — "
            "adopt ai_native_practice on the base first"
        )

    extra["two_stage"] = copy.deepcopy(TWO_STAGE_CONFIG)
    extra["part_weights"] = {"practice": 0.3, "applied": 0.7}
    extra["two_stage_variant_of"] = base.task_key

    return {
        "organization_id": organization_id,
        "name": f"{base.name} — two-stage",
        "description": base.description,
        "task_type": base.task_type,
        "difficulty": base.difficulty,
        "duration_minutes": base.duration_minutes,
        "is_template": False,
        "is_active": True,
        "calibration_prompt": base.calibration_prompt,
        "task_key": f"{base.task_key}_two_stage",
        "role": base.role,
        "scenario": base.scenario,
        "repo_structure": copy.deepcopy(base.repo_structure),
        "evaluation_rubric": rubric,
        "extra_data": extra,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", type=int, required=True)
    parser.add_argument("--role-id", type=int, required=True)
    parser.add_argument("--base-task-key", default="data_eng_bronze_ingestion")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    from app.models.assessment_experiment import (
        AssessmentExperiment,
        AssessmentExperimentArm,
    )

    db = SessionLocal()
    try:
        base = (
            db.query(Task)
            .filter(Task.task_key == args.base_task_key, Task.organization_id.is_(None))
            .first()
        )
        if base is None:
            print(f"Base template task {args.base_task_key!r} not found.")
            return 1

        payload = build_two_stage_variant(base, organization_id=args.org_id)
        exp_key = f"two_stage_ab_{args.base_task_key}_{args.role_id}"
        print(f"Variant: {payload['task_key']} (from {base.task_key}, task #{base.id})")
        print(f"Experiment: {exp_key} — arm A = base task, arm B = announced two-stage, 50/50")
        if not args.apply:
            print("Dry run — pass --apply to create/update.")
            return 0

        variant = (
            db.query(Task)
            .filter(Task.task_key == payload["task_key"], Task.organization_id == args.org_id)
            .first()
        )
        if variant is None:
            variant = Task(**payload)
            db.add(variant)
        else:
            for field, value in payload.items():
                setattr(variant, field, value)
        db.flush()

        db.execute(
            sql_text(
                "INSERT INTO role_tasks (role_id, task_id) VALUES (:r, :t) ON CONFLICT DO NOTHING"
            ),
            {"r": args.role_id, "t": int(variant.id)},
        )

        exp = (
            db.query(AssessmentExperiment)
            .filter(
                AssessmentExperiment.organization_id == args.org_id,
                AssessmentExperiment.key == exp_key,
            )
            .first()
        )
        if exp is None:
            exp = AssessmentExperiment(
                organization_id=args.org_id,
                role_id=args.role_id,
                key=exp_key,
                name="Observed vs announced two-stage",
                description=(
                    "A/B: flat task with observed practice dim (arm A) vs the same "
                    "task announced as Practice & Setup → Applied with the 30/70 "
                    "blend (arm B). Decision gate: >=20 completions per arm."
                ),
                status="active",
                experiment_type="task",
                salt=exp_key,  # set once; preserved on re-run for stable draws
            )
            db.add(exp)
            db.flush()
        for arm_key, task_id in (("a", int(base.id)), ("b", int(variant.id))):
            arm = (
                db.query(AssessmentExperimentArm)
                .filter(
                    AssessmentExperimentArm.experiment_id == int(exp.id),
                    AssessmentExperimentArm.arm_key == arm_key,
                )
                .first()
            )
            if arm is None:
                db.add(
                    AssessmentExperimentArm(
                        experiment_id=int(exp.id),
                        arm_key=arm_key,
                        task_id=task_id,
                        weight=1,
                    )
                )
            else:
                arm.task_id = task_id
        db.commit()
        print(f"Done: variant task #{variant.id}, experiment #{exp.id} active.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
