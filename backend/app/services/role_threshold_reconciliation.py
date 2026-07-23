"""Canonical threshold reconciliation for every logical role.

Ordinary roles keep their verdict state on ``CandidateApplication``. Related
roles keep membership, scores and funnel state on ``SisterRoleEvaluation``.
Threshold writers must come through this seam so a related role can never be
reconciled from the optional ATS transport application's score or stage.

The service is deterministic. It never scores candidates or invokes a model;
the related-role branch re-runs only the role-local decision materialiser over
already-persisted evaluations.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.role import ROLE_KIND_SISTER, Role


def role_uses_related_membership(role: Role) -> bool:
    """Whether candidate truth for ``role`` lives on related evaluations."""

    return str(getattr(role, "role_kind", "") or "") == ROLE_KIND_SISTER


def effective_role_threshold(db: Session, *, role: Role) -> float | None:
    """Return the exact threshold used by this role's decision runtime."""

    if role_uses_related_membership(role):
        from .decision_policy_generation import capture_decision_policy_generation

        resolved = capture_decision_policy_generation(
            db,
            role=role,
        ).effective_threshold
        if resolved is not None:
            return float(resolved)
        # Keep this fallback in lockstep with ``run_related_role_cycle``. The
        # related runtime has always used 50 when no learned/manual boundary
        # can be resolved; returning the same value makes preview, change
        # detection and committed decisions agree.
        if role.score_threshold is not None:
            return float(role.score_threshold)
        return 50.0

    from .pre_screening_service import resolved_auto_reject_config

    value = resolved_auto_reject_config(None, role, db=db)["threshold_100"]
    return float(value) if value is not None else None


def reconcile_role_threshold_decisions(
    db: Session,
    *,
    role: Role,
    organization_id: int,
    threshold: float | None = None,
) -> dict[str, Any]:
    """Reconcile one logical role after its effective threshold changes.

    ``threshold`` is accepted for callers that already captured the effective
    value. Related roles intentionally resolve the live role-owned generation
    again: using a caller-supplied owner/default value there could recreate a
    verdict whose frozen policy evidence disagrees with the related role.
    """

    if int(role.organization_id) != int(organization_id):
        raise ValueError("Role does not belong to the requested organization")

    if role_uses_related_membership(role):
        from .related_role_runtime import run_related_role_cycle

        return run_related_role_cycle(db, role=role)

    from .pre_screen_decision_emitter import (
        reconcile_pre_screen_reject_decisions,
        retract_advances_below_threshold,
    )

    effective = (
        effective_role_threshold(db, role=role)
        if threshold is None
        else float(threshold)
    )
    retracted = retract_advances_below_threshold(
        db,
        role=role,
        organization_id=int(organization_id),
        threshold=effective,
    )
    reconciled = reconcile_pre_screen_reject_decisions(
        db,
        role=role,
        organization_id=int(organization_id),
        threshold=effective,
    )
    return {
        "status": "ok",
        "role_id": int(role.id),
        "discarded_advances": int(retracted.get("discarded", 0)),
        "created_rejects": int(reconciled.get("created", 0)),
        "reconcile_discarded": int(reconciled.get("discarded", 0)),
        "skipped_existing": int(reconciled.get("skipped_existing", 0)),
    }


__all__ = [
    "effective_role_threshold",
    "reconcile_role_threshold_decisions",
    "role_uses_related_membership",
]
