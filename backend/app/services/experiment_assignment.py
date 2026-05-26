"""Deterministic A/B arm assignment for assessment sends.

This is the task-selection chokepoint shared by the agent action
(``app.actions.send_assessment``) and the recruiter UI. It replaces the old
``_resolve_task`` with experiment-aware selection while preserving the exact
legacy behavior when no active experiment covers the role.

Design notes:
- Assignment is **stable** per ``(experiment, candidate, role)`` — a resend
  reproduces the same arm, and a void→re-invite reuses the original arm (we look
  up the most recent prior assignment for the key, incl. voided rows, before
  drawing). This keeps each candidate in one arm for the life of the experiment.
- ``pick_arm`` / ``stable_bucket`` are pure functions (no DB, no RNG state) so
  they are trivially unit-testable and reproducible.
- An explicit recruiter ``task_id`` is recorded as ``forced`` and excluded from
  the randomized analysis cohort.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from ..models.assessment import Assessment
from ..models.assessment_experiment import (
    ASSIGNMENT_METHOD_FORCED,
    ASSIGNMENT_METHOD_RANDOM,
    ASSIGNMENT_METHOD_SINGLE_TASK_DEFAULT,
    EXPERIMENT_STATUS_ACTIVE,
    AssessmentExperiment,
    AssessmentExperimentArm,
)
from ..models.role import Role
from ..models.task import Task


class RoleTaskMisconfigured(Exception):
    """Role/experiment config gap the recruiter must resolve — a soft error.

    Distinct from a bad *explicit* ``task_id`` (a hard 422 input error): callers
    degrade this to a soft ``misconfigured`` status instead of raising, so
    approving an agent send recommendation doesn't 422-loop with no signal.
    """

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class ArmChoice:
    task: Task
    method: str
    arm: Optional[AssessmentExperimentArm] = None
    experiment: Optional[AssessmentExperiment] = None
    assignment_key: Optional[str] = None
    knob_overrides: Optional[dict] = None


def stable_bucket(assignment_key: str, salt: str) -> float:
    """Deterministic value in ``[0, 1)`` from ``sha256(salt:assignment_key)``."""
    digest = hashlib.sha256(f"{salt}:{assignment_key}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0x100000000


def pick_arm(
    arms: list[AssessmentExperimentArm], salt: str, assignment_key: str
) -> AssessmentExperimentArm:
    """Weighted, stable choice. Same (arms, salt, key) → same arm."""
    ordered = sorted(arms, key=lambda a: int(a.id))
    total = sum(max(int(a.weight or 1), 0) for a in ordered)
    if total <= 0:
        # Defensive: all weights zero/negative — fall back to equal weighting.
        ordered = sorted(arms, key=lambda a: int(a.id))
        idx = int(stable_bucket(assignment_key, salt) * len(ordered))
        return ordered[min(idx, len(ordered) - 1)]
    target = stable_bucket(assignment_key, salt) * total
    cumulative = 0
    for arm in ordered:
        cumulative += max(int(arm.weight or 1), 0)
        if target < cumulative:
            return arm
    return ordered[-1]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _active_experiments(
    db: Session, *, role_id: int, organization_id: int
) -> list[AssessmentExperiment]:
    rows = (
        db.query(AssessmentExperiment)
        .options(selectinload(AssessmentExperiment.arms))
        .filter(
            AssessmentExperiment.role_id == role_id,
            AssessmentExperiment.organization_id == organization_id,
            AssessmentExperiment.status == EXPERIMENT_STATUS_ACTIVE,
        )
        .all()
    )
    now = _now_utc()
    in_window = []
    for exp in rows:
        starts = _as_aware(exp.starts_at)
        ends = _as_aware(exp.ends_at)
        if starts is not None and now < starts:
            continue
        if ends is not None and now > ends:
            continue
        in_window.append(exp)
    return in_window


def _prior_arm_for_key(
    db: Session,
    *,
    candidate_id: int,
    role_id: int,
    experiment_id: int,
    active_arms: list[AssessmentExperimentArm],
) -> Optional[AssessmentExperimentArm]:
    """Reuse the arm from this candidate's most recent assignment in this
    experiment (incl. voided), so a re-invite keeps them in their original arm."""
    prior = (
        db.query(Assessment.experiment_arm_id)
        .filter(
            Assessment.candidate_id == candidate_id,
            Assessment.role_id == role_id,
            Assessment.experiment_id == experiment_id,
            Assessment.experiment_arm_id.isnot(None),
        )
        .order_by(Assessment.created_at.desc(), Assessment.id.desc())
        .first()
    )
    if prior is None or prior[0] is None:
        return None
    return next((a for a in active_arms if int(a.id) == int(prior[0])), None)


def resolve_task_and_variant(
    db: Session,
    role: Role,
    *,
    candidate_id: int,
    organization_id: int,
    task_id: Optional[int],
) -> ArmChoice:
    """Pick the task (and any knob variant) for an assessment send.

    Order: no-tasks (soft misconfigured) → explicit ``task_id`` (forced) →
    active experiment (random, stable, arm-reuse) → legacy single/ambiguous.
    """
    tasks = list(role.tasks or [])
    if not tasks:
        raise RoleTaskMisconfigured(
            f"role {role.id} has no tasks linked — cannot send assessment"
        )

    # 1. Explicit recruiter override — forced (excluded from the random cohort).
    if task_id is not None:
        task = next((t for t in tasks if int(t.id) == int(task_id)), None)
        if task is None:
            raise HTTPException(
                status_code=422,
                detail=f"task_id={task_id} is not linked to role {role.id}",
            )
        experiments = _active_experiments(
            db, role_id=int(role.id), organization_id=organization_id
        )
        exp = experiments[0] if len(experiments) == 1 else None
        arm = None
        if exp is not None:
            arm = next(
                (
                    a
                    for a in exp.arms
                    if a.is_active and int(a.task_id) == int(task.id)
                ),
                None,
            )
        return ArmChoice(
            task=task,
            method=ASSIGNMENT_METHOD_FORCED,
            arm=arm,
            experiment=exp,
        )

    # 2. Active experiment — randomized, stable assignment.
    experiments = _active_experiments(
        db, role_id=int(role.id), organization_id=organization_id
    )
    if len(experiments) > 1:
        raise RoleTaskMisconfigured(
            f"role {role.id} has {len(experiments)} active experiments; expected at most one"
        )
    if len(experiments) == 1:
        exp = experiments[0]
        active_arms = [a for a in exp.arms if a.is_active]
        if not active_arms:
            raise RoleTaskMisconfigured(
                f"experiment {exp.id} has no active arms — cannot assign"
            )
        assignment_key = f"{exp.id}:{candidate_id}:{int(role.id)}"
        arm = _prior_arm_for_key(
            db,
            candidate_id=candidate_id,
            role_id=int(role.id),
            experiment_id=int(exp.id),
            active_arms=active_arms,
        ) or pick_arm(active_arms, exp.salt, assignment_key)
        task = next((t for t in tasks if int(t.id) == int(arm.task_id)), None)
        if task is None:
            raise RoleTaskMisconfigured(
                f"experiment {exp.id} arm {arm.arm_key} task {arm.task_id} "
                f"is not linked to role {role.id}"
            )
        return ArmChoice(
            task=task,
            method=ASSIGNMENT_METHOD_RANDOM,
            arm=arm,
            experiment=exp,
            assignment_key=assignment_key,
            knob_overrides=dict(arm.knob_overrides) if arm.knob_overrides else None,
        )

    # 3. No experiment — legacy behavior.
    if len(tasks) == 1:
        return ArmChoice(task=tasks[0], method=ASSIGNMENT_METHOD_SINGLE_TASK_DEFAULT)
    raise RoleTaskMisconfigured(
        f"role {role.id} has {len(tasks)} linked tasks; pass task_id explicitly "
        "to disambiguate (recruiter must pick when there are multiple)."
    )
