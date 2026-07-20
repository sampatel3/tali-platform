"""Persisted generation fence for deterministic decision-policy inputs."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import case, or_
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.decision_policy import DecisionPolicy
from ..models.role import Role, role_tasks
from ..models.task import Task
from .agent_policy_settings import (
    GRANULAR_AUTOMATION_FIELDS,
    role_automation_enabled,
)
from .auto_threshold_service import resolve_role_fit_threshold


POLICY_GENERATION_FINGERPRINT_KEY = "decision_policy_generation"
_ACTIONABLE_STATUSES = frozenset(
    {"pending", "processing", "reverted_for_feedback"}
)


def _threshold(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


@dataclass(frozen=True)
class DecisionPolicyGeneration:
    """Exact role inputs that can change a policy verdict or its execution."""

    role_id: int
    effective_threshold: float | None
    active_assessment_task_ids: tuple[int, ...]
    auto_skip_assessment: bool
    automation: tuple[tuple[str, bool], ...]
    policy_revision_id: int | None

    @property
    def has_assessment_task(self) -> bool:
        return bool(self.active_assessment_task_ids) and not self.auto_skip_assessment

    def as_fingerprint(self) -> dict[str, Any]:
        payload = {
            "version": 1,
            "role_id": int(self.role_id),
            "effective_threshold": self.effective_threshold,
            "active_assessment_task_ids": list(self.active_assessment_task_ids),
            "auto_skip_assessment": bool(self.auto_skip_assessment),
            "automation": {key: value for key, value in self.automation},
            # ``None`` is deliberate compatibility for organizations predating
            # an active DecisionPolicy row; absence is not conflated with it.
            "policy_revision_id": self.policy_revision_id,
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return {
            **payload,
            "digest": hashlib.sha256(canonical).hexdigest(),
        }


def _active_policy_revision(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    lock: bool,
) -> int | None:
    query = (
        db.query(DecisionPolicy.revision_id)
        .filter(
            DecisionPolicy.organization_id == int(organization_id),
            or_(
                DecisionPolicy.role_id == int(role_id),
                DecisionPolicy.role_id.is_(None),
            ),
            DecisionPolicy.activated_at.isnot(None),
            DecisionPolicy.deactivated_at.is_(None),
        )
        .order_by(
            case((DecisionPolicy.role_id == int(role_id), 0), else_=1),
            DecisionPolicy.activated_at.desc(),
        )
    )
    if lock:
        query = query.with_for_update(of=DecisionPolicy)
    row = query.first()
    return int(row[0]) if row is not None else None


def capture_decision_policy_generation(
    db: Session,
    *,
    role: Role,
    cache: dict[int, DecisionPolicyGeneration] | None = None,
    lock_policy: bool = False,
) -> DecisionPolicyGeneration:
    """Capture one canonical JSON-ready generation for a live role."""
    role_id = int(role.id)
    if cache is not None and role_id in cache:
        return cache[role_id]
    organization_id = int(role.organization_id)
    active_task_ids = tuple(
        int(task_id)
        for task_id, in (
            db.query(Task.id)
            .select_from(role_tasks.join(Task, role_tasks.c.task_id == Task.id))
            .filter(
                role_tasks.c.role_id == role_id,
                Task.is_active.is_(True),
            )
            .order_by(Task.id)
            .all()
        )
    )
    generation = DecisionPolicyGeneration(
        role_id=role_id,
        effective_threshold=_threshold(resolve_role_fit_threshold(db, role=role)),
        active_assessment_task_ids=active_task_ids,
        auto_skip_assessment=bool(getattr(role, "auto_skip_assessment", False)),
        automation=tuple(
            (field, role_automation_enabled(role, field))
            for field in GRANULAR_AUTOMATION_FIELDS
        ),
        policy_revision_id=_active_policy_revision(
            db,
            organization_id=organization_id,
            role_id=role_id,
            lock=lock_policy,
        ),
    )
    if cache is not None:
        cache[role_id] = generation
    return generation


def _evidence_mismatches(
    evidence: dict[str, Any], generation: DecisionPolicyGeneration
) -> list[str]:
    mismatches: list[str] = []
    raw_threshold = evidence.get("effective_threshold")
    threshold_valid = raw_threshold is None or (
        not isinstance(raw_threshold, bool)
        and _threshold(raw_threshold) is not None
    )
    if (
        "effective_threshold" not in evidence
        or not threshold_valid
        or _threshold(raw_threshold) != generation.effective_threshold
    ):
        mismatches.append("effective_threshold")
    if (
        "has_assessment_task" not in evidence
        or not isinstance(evidence.get("has_assessment_task"), bool)
        or evidence.get("has_assessment_task") is not generation.has_assessment_task
    ):
        mismatches.append("has_assessment_task")
    revision = evidence.get("policy_revision_id")
    revision_valid = revision is None or (
        isinstance(revision, int) and not isinstance(revision, bool)
    )
    revision_missing_legacy_none = (
        "policy_revision_id" not in evidence
        and generation.policy_revision_id is None
    )
    if not revision_missing_legacy_none and (
        "policy_revision_id" not in evidence
        or not revision_valid
        or revision != generation.policy_revision_id
    ):
        mismatches.append("policy_revision_id")
    return mismatches


def validate_queue_policy_generation(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    evidence: dict[str, Any] | None,
    locked_role: Role | None,
) -> DecisionPolicyGeneration | None:
    """Validate server policy evidence at the locked queue boundary."""
    if not isinstance(evidence, dict) or evidence.get("decision_source") != "policy":
        return None
    if (
        locked_role is None
        or int(locked_role.id) != int(role_id)
        or int(locked_role.organization_id) != int(organization_id)
    ):
        from .role_execution_guard import lock_live_role

        locked_role = lock_live_role(
            db,
            role_id=int(role_id),
            organization_id=int(organization_id),
        )
    if locked_role is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "decision_policy_generation_changed",
                "message": "Current decision policy could not be secured.",
            },
        )
    generation = capture_decision_policy_generation(
        db,
        role=locked_role,
        lock_policy=True,
    )
    mismatches = _evidence_mismatches(evidence, generation)
    if mismatches:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "decision_policy_generation_changed",
                "message": (
                    "Role decision policy changed after this verdict was computed."
                ),
                "mismatches": mismatches,
                "current": generation.as_fingerprint(),
            },
        )
    return generation


def policy_generation_drift(
    db: Session,
    decision: AgentDecision,
    role: Role,
    cache: dict[int, DecisionPolicyGeneration] | None = None,
) -> dict[str, Any] | None:
    """Return emitted/current generations when an actionable card drifted."""
    if str(getattr(decision, "status", "")) not in _ACTIONABLE_STATUSES:
        return None
    fingerprint = (
        decision.input_fingerprint
        if isinstance(decision.input_fingerprint, dict)
        else {}
    )
    if POLICY_GENERATION_FINGERPRINT_KEY not in fingerprint:
        return None
    emitted = fingerprint.get(POLICY_GENERATION_FINGERPRINT_KEY)
    current = capture_decision_policy_generation(
        db,
        role=role,
        cache=cache,
    ).as_fingerprint()
    if isinstance(emitted, dict) and emitted == current:
        return None
    return {"at_emit": emitted, "current": current}


__all__ = [
    "DecisionPolicyGeneration",
    "POLICY_GENERATION_FINGERPRINT_KEY",
    "capture_decision_policy_generation",
    "policy_generation_drift",
    "validate_queue_policy_generation",
]
