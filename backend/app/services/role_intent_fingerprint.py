"""Stable fingerprints for role inputs that drive scoring and automation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from ..components.scoring.role_intent_inputs import (
    active_role_intent_material_payload,
)
from ..models.role import Role
from ..models.role_criterion import RoleCriterion


_ROLE_INPUT_FIELDS = (
    "name",
    "description",
    "job_spec_text",
    "employment_type",
    "workplace_type",
    "location_city",
    "location_country",
    "department",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
)


def _normalized(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


def role_intent_payload(
    role: Role,
    *,
    db: Session | None = None,
    criteria_rows: Iterable[RoleCriterion] | None = None,
) -> dict[str, Any]:
    """Canonical materialized hiring inputs for ``role``.

    Querying criteria directly avoids a stale relationship collection when a
    re-publish reconciles rows inside the same transaction.
    """

    if criteria_rows is not None:
        criteria = list(criteria_rows)
    elif db is not None and getattr(role, "id", None) is not None:
        criteria = (
            db.query(RoleCriterion)
            .filter(
                RoleCriterion.role_id == int(role.id),
                RoleCriterion.deleted_at.is_(None),
            )
            .populate_existing()
            .all()
        )
    else:
        criteria = [
            row
            for row in list(getattr(role, "criteria", None) or [])
            if getattr(row, "deleted_at", None) is None
        ]
    criteria.sort(
        key=lambda row: (
            str(getattr(row, "source", "") or ""),
            int(getattr(row, "ordering", 0) or 0),
            int(getattr(row, "id", 0) or 0),
        )
    )
    return {
        "role": {
            field: _normalized(getattr(role, field, None))
            for field in _ROLE_INPUT_FIELDS
        },
        "criteria": [
            {
                "source": str(getattr(row, "source", "") or ""),
                "ordering": int(getattr(row, "ordering", 0) or 0),
                "text": str(getattr(row, "text", "") or "").strip(),
                "bucket": str(getattr(row, "bucket", "") or ""),
                "must_have": bool(getattr(row, "must_have", False)),
                "weight": float(getattr(row, "weight", 1.0) or 0.0),
            }
            for row in criteria
        ],
        # RoleIntent.free_text is a first-class scoring input even when its
        # optional parser produced no RoleCriterion chips. Keep the full active
        # generation in the in-flight fence; paid prompts use the separate
        # bounded projection.
        "role_intent": (
            active_role_intent_material_payload(db, role_id=int(role.id))
            if db is not None and getattr(role, "id", None) is not None
            else None
        ),
    }


def role_intent_fingerprint(
    role: Role,
    *,
    db: Session | None = None,
    criteria_rows: Iterable[RoleCriterion] | None = None,
) -> str:
    payload = role_intent_payload(role, db=db, criteria_rows=criteria_rows)
    serialized = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def role_reconfiguration_is_active(role: Role) -> bool:
    state = (
        role.assessment_task_provisioning
        if isinstance(getattr(role, "assessment_task_provisioning", None), dict)
        else {}
    )
    reconfiguration = state.get("reconfiguration")
    if not isinstance(reconfiguration, dict):
        return False
    return str(reconfiguration.get("status") or "") in {
        "pending",
        "running",
        "blocked",
    }


__all__ = [
    "role_intent_fingerprint",
    "role_intent_payload",
    "role_reconfiguration_is_active",
]
