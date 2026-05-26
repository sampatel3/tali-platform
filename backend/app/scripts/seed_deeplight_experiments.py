"""Seed the deeplight A/B assessment-task experiments (Phase 2 trial).

Creates one active *task* experiment per role family, each with two arms, and
ensures both arm tasks are linked to the role. Parametrized by role NAME (role
ids are environment-specific), so it can run against prod without hardcoded ids.

Resolution
----------
* Org: the row whose name/slug contains "deeplight" (override with --org-id).
* Roles: matched by name substring (override with --genai-role-id /
  --data-role-id when the names don't match or are ambiguous).
* Tasks: canonical templates resolved by ``task_key`` (run seed_tasks_db first).

Idempotent
----------
Experiments are keyed by (organization_id, key); arms by (experiment_id,
arm_key). Re-running updates in place and never re-rolls existing assignments
(the per-experiment ``salt`` is set once on create and preserved).

Usage::

    python -m app.scripts.seed_deeplight_experiments              # dry run
    python -m app.scripts.seed_deeplight_experiments --apply      # commit
    python -m app.scripts.seed_deeplight_experiments --apply --genai-role-id 12 --data-role-id 15
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.assessment_experiment import (
    EXPERIMENT_STATUS_ACTIVE,
    EXPERIMENT_TYPE_TASK,
    AssessmentExperiment,
    AssessmentExperimentArm,
)
from ..models.organization import Organization
from ..models.role import Role
from ..models.task import Task


# Each experiment: a role matched by name substrings, plus the two task arms
# (arm_key -> canonical task_key). Arm "A" is the existing anchor task.
EXPERIMENTS = [
    {
        "key": "deeplight_genai_task_ab",
        "name": "GenAI / AI Engineer — task A/B",
        "role_id_flag": "genai_role_id",
        "role_match": ["genai", "ai engineer", "ai eng", "solution architect"],
        "arms": [
            ("A", "ai_eng_genai_production_readiness"),
            ("B", "ai_eng_rag_eval_harness"),
        ],
    },
    {
        "key": "deeplight_data_task_ab",
        "name": "Data Engineer — task A/B",
        "role_id_flag": "data_role_id",
        "role_match": ["data eng", "data engineer"],
        "arms": [
            ("A", "data_eng_aws_glue_pipeline_recovery"),
            ("B", "data_eng_data_quality_contract_framework"),
        ],
    },
]


def _resolve_org(db: Session, org_id: Optional[int]) -> Optional[Organization]:
    if org_id is not None:
        return db.query(Organization).filter(Organization.id == org_id).first()
    matches = (
        db.query(Organization)
        .filter(
            func.lower(Organization.name).contains("deeplight")
            | func.lower(Organization.slug).contains("deeplight")
        )
        .all()
    )
    if len(matches) == 1:
        return matches[0]
    if not matches:
        print("  ! no organization matching 'deeplight' — pass --org-id", flush=True)
    else:
        ids = ", ".join(f"{o.id}:{o.name}" for o in matches)
        print(f"  ! ambiguous deeplight orgs ({ids}) — pass --org-id", flush=True)
    return None


def _resolve_role(
    db: Session, *, org_id: int, explicit_id: Optional[int], matchers: List[str]
) -> Optional[Role]:
    if explicit_id is not None:
        role = (
            db.query(Role)
            .filter(Role.id == explicit_id, Role.organization_id == org_id)
            .first()
        )
        if role is None:
            print(f"  ! role id={explicit_id} not found in org {org_id}", flush=True)
        return role
    candidates = (
        db.query(Role)
        .filter(Role.organization_id == org_id, Role.deleted_at.is_(None))
        .all()
    )
    hits = [r for r in candidates if any(m in (r.name or "").lower() for m in matchers)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        print(f"  ! no role matching {matchers} — pass the explicit role id flag", flush=True)
    else:
        names = ", ".join(f"{r.id}:{r.name}" for r in hits)
        print(f"  ! ambiguous role match {matchers} ({names}) — pass the explicit role id flag", flush=True)
    return None


def _resolve_template_task(db: Session, task_key: str) -> Optional[Task]:
    return (
        db.query(Task)
        .filter(Task.task_key == task_key, Task.is_template.is_(True))
        .order_by(Task.id.desc())
        .first()
    )


def seed_deeplight_experiments(
    db: Session,
    *,
    apply: bool = False,
    org_id: Optional[int] = None,
    genai_role_id: Optional[int] = None,
    data_role_id: Optional[int] = None,
) -> dict:
    summary = {"experiments_created": 0, "experiments_updated": 0, "arms_upserted": 0, "links_added": 0, "skipped": 0}

    org = _resolve_org(db, org_id)
    if org is None:
        summary["skipped"] += len(EXPERIMENTS)
        return summary
    print(f"[seed] org={org.id} ({org.name!r})", flush=True)

    role_id_by_flag = {"genai_role_id": genai_role_id, "data_role_id": data_role_id}

    for cfg in EXPERIMENTS:
        role = _resolve_role(
            db,
            org_id=int(org.id),
            explicit_id=role_id_by_flag.get(cfg["role_id_flag"]),
            matchers=cfg["role_match"],
        )
        if role is None:
            summary["skipped"] += 1
            continue

        tasks = []
        missing = False
        for arm_key, task_key in cfg["arms"]:
            task = _resolve_template_task(db, task_key)
            if task is None:
                print(f"  ! task template {task_key!r} not found — run seed_tasks_db first", flush=True)
                missing = True
            tasks.append((arm_key, task))
        if missing:
            summary["skipped"] += 1
            continue

        print(f"  role={role.id} ({role.name!r}) → experiment {cfg['key']!r}", flush=True)

        # Ensure both arm tasks are linked to the role.
        linked_ids = {int(t.id) for t in (role.tasks or [])}
        for _arm_key, task in tasks:
            if int(task.id) not in linked_ids:
                print(f"    + link task {task.task_key!r} (id={task.id}) to role", flush=True)
                summary["links_added"] += 1
                if apply:
                    role.tasks.append(task)

        # Upsert experiment by (org, key).
        exp = (
            db.query(AssessmentExperiment)
            .filter(
                AssessmentExperiment.organization_id == int(org.id),
                AssessmentExperiment.key == cfg["key"],
            )
            .first()
        )
        if exp is None:
            print(f"    + create experiment {cfg['key']!r} (active)", flush=True)
            summary["experiments_created"] += 1
            exp = AssessmentExperiment(
                organization_id=int(org.id),
                role_id=int(role.id),
                key=cfg["key"],
                name=cfg["name"],
                status=EXPERIMENT_STATUS_ACTIVE,
                experiment_type=EXPERIMENT_TYPE_TASK,
                salt=cfg["key"],  # set once; preserved on re-run to keep assignments stable
            )
            if apply:
                db.add(exp)
                db.flush()
        else:
            print(f"    ~ update experiment {cfg['key']!r} (id={exp.id})", flush=True)
            summary["experiments_updated"] += 1
            exp.role_id = int(role.id)
            exp.name = cfg["name"]
            exp.status = EXPERIMENT_STATUS_ACTIVE
            exp.experiment_type = EXPERIMENT_TYPE_TASK

        # Upsert arms by (experiment_id, arm_key). Only when we have a flushed exp id.
        if apply and exp.id is not None:
            for arm_key, task in tasks:
                arm = (
                    db.query(AssessmentExperimentArm)
                    .filter(
                        AssessmentExperimentArm.experiment_id == int(exp.id),
                        AssessmentExperimentArm.arm_key == arm_key,
                    )
                    .first()
                )
                if arm is None:
                    arm = AssessmentExperimentArm(
                        experiment_id=int(exp.id),
                        arm_key=arm_key,
                        task_id=int(task.id),
                        weight=1,
                        is_active=True,
                    )
                    db.add(arm)
                else:
                    arm.task_id = int(task.id)
                    arm.weight = 1
                    arm.is_active = True
                summary["arms_upserted"] += 1
                print(f"    arm {arm_key} → task {task.task_key!r}", flush=True)
        else:
            for arm_key, task in tasks:
                summary["arms_upserted"] += 1
                print(f"    arm {arm_key} → task {task.task_key!r} (dry-run)", flush=True)

    if apply:
        db.commit()
    return summary


def main() -> int:
    from ..platform.database import SessionLocal

    parser = argparse.ArgumentParser(description="Seed deeplight A/B assessment experiments")
    parser.add_argument("--apply", action="store_true", help="commit changes (default: dry run)")
    parser.add_argument("--org-id", type=int, default=None)
    parser.add_argument("--genai-role-id", type=int, default=None)
    parser.add_argument("--data-role-id", type=int, default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        print(f"[seed_deeplight_experiments] mode={'APPLY' if args.apply else 'DRY-RUN'}", flush=True)
        summary = seed_deeplight_experiments(
            db,
            apply=args.apply,
            org_id=args.org_id,
            genai_role_id=args.genai_role_id,
            data_role_id=args.data_role_id,
        )
        print(f"[seed_deeplight_experiments] {summary}", flush=True)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
