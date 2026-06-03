"""Role-constraint edits driven by the conversational agent.

When the recruiter says "cap salary at 25k on this role" the agent edits a
``RoleCriterion`` (a constraint chip) and re-screens — exactly what the
manual criteria CRUD does, plus an immediate stale-score sweep so the
re-evaluation starts now instead of waiting for the 30-minute safety net.

Unlike a score-threshold change (instant re-filter, no LLM — see
``impact.apply_threshold``), a constraint edit changes the pre-screen prompt
and therefore requires re-scoring. So these helpers mark scores stale and
kick the sweep; the impact lands as scores re-settle and the agent's
reconcile/cohort tick re-cards from there.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.org_criterion import (
    BUCKET_CONSTRAINT,
    BUCKET_MUST,
    BUCKET_PREFERRED,
    CRITERION_BUCKETS,
)
from ..models.role import Role
from ..models.role_criterion import CRITERION_SOURCE_DERIVED, CRITERION_SOURCE_RECRUITER, RoleCriterion


# Buckets that feed the pre-screen prompt — editing either side of this set
# invalidates scores (mirrors roles_management_routes._INVALIDATING_BUCKETS).
_INVALIDATING_BUCKETS = {BUCKET_MUST, BUCKET_CONSTRAINT}


def _editable_criteria(role: Role) -> list[RoleCriterion]:
    """Recruiter-editable constraint chips on the role (not spec-derived,
    not soft-deleted), in display order."""
    rows = [
        c
        for c in (role.criteria or [])
        if c.deleted_at is None and c.source != CRITERION_SOURCE_DERIVED
    ]
    return sorted(rows, key=lambda c: (c.ordering or 0, c.id or 0))


def list_constraints(role: Role) -> list[dict[str, Any]]:
    return [
        {
            "id": int(c.id),
            "text": c.text,
            "bucket": c.bucket,
            "weight": float(c.weight) if c.weight is not None else 1.0,
        }
        for c in _editable_criteria(role)
    ]


def _next_ordering(role: Role) -> int:
    existing = _editable_criteria(role)
    return (max((c.ordering or 0) for c in existing) + 1) if existing else 0


def _trigger_rescreen(db: Session, role: Role, *, reason: str) -> int:
    """Mark the role's scores stale and kick an immediate stale-score sweep.

    Returns the number of applications invalidated. The sweep is dispatched
    with a short countdown so the caller's outer commit lands before a worker
    reads the stale jobs (same guard ``mark_role_scores_stale`` uses for the
    tech-questions regen).
    """
    from ..services.cv_score_orchestrator import mark_role_scores_stale

    invalidated = mark_role_scores_stale(db, int(role.id), reason=reason)
    try:
        from ..tasks.scoring_tasks import sweep_stale_scores

        sweep_stale_scores.apply_async(kwargs={"limit": 500}, countdown=10)
    except Exception:  # pragma: no cover — never fail the edit on dispatch
        import logging

        logging.getLogger("taali.agent_chat").exception(
            "constraint edit: failed to dispatch stale-score sweep for role_id=%s",
            role.id,
        )
    return int(invalidated)


# Rough per-candidate cost of a re-screen (prescreen + full score), from
# observed prod usage (~$0.045/candidate). For the opt-in heads-up only.
_RESCREEN_COST_PER_CANDIDATE_USD = 0.05


def estimate_rescreen(db: Session, role: Role) -> dict[str, Any]:
    """How many candidates a re-screen would touch + a rough $ estimate, for the
    opt-in heads-up — WITHOUT marking anything stale or spending anything."""
    from .impact import load_open_candidates

    try:
        count = len(load_open_candidates(db, role))
    except Exception:  # pragma: no cover — never block the edit on the estimate
        count = 0
    return {
        "count": int(count),
        "est_cost_usd": round(count * _RESCREEN_COST_PER_CANDIDATE_USD, 2),
    }


def rescreen_role(
    db: Session, role: Role, *, reason: str = "agent_chat:opt_in_rescreen"
) -> dict[str, Any]:
    """Run the re-screen the recruiter explicitly opted into (after a constraint
    change). Separated from the edit so the spend is never automatic."""
    count = _trigger_rescreen(db, role, reason=reason)
    return {"type": "rescreen_started", "rescreening_count": int(count)}


def add_or_update_constraint(
    db: Session,
    role: Role,
    *,
    text: str,
    bucket: str = BUCKET_CONSTRAINT,
    criterion_id: int | None = None,
    trigger_rescreen: bool = True,
) -> dict[str, Any]:
    """Add a new constraint chip or edit an existing one, then re-screen.

    ``bucket`` defaults to ``constraint`` (a hard filter the pre-screen
    enforces). Pass ``criterion_id`` to edit an existing recruiter chip.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("constraint text is required")
    if bucket not in CRITERION_BUCKETS:
        raise ValueError(
            f"invalid bucket {bucket!r}; one of {sorted(CRITERION_BUCKETS)}"
        )

    if criterion_id is not None:
        chip = next(
            (c for c in _editable_criteria(role) if int(c.id) == int(criterion_id)),
            None,
        )
        if chip is None:
            raise ValueError(f"constraint {criterion_id} not found on this role")
        old_bucket = chip.bucket
        chip.text = text
        chip.bucket = bucket
        chip.must_have = bucket == BUCKET_MUST
        if chip.org_criterion_id is not None:
            chip.customized_at = datetime.now(timezone.utc)
        action = "updated"
        invalidating = old_bucket in _INVALIDATING_BUCKETS or bucket in _INVALIDATING_BUCKETS
    else:
        chip = RoleCriterion(
            role_id=int(role.id),
            source=CRITERION_SOURCE_RECRUITER,
            ordering=_next_ordering(role),
            weight=1.0,
            must_have=bucket == BUCKET_MUST,
            bucket=bucket,
            org_criterion_id=None,
            text=text,
        )
        db.add(chip)
        action = "added"
        invalidating = bucket in _INVALIDATING_BUCKETS

    db.flush()
    rescreening_count = 0
    if trigger_rescreen and invalidating:
        rescreening_count = _trigger_rescreen(
            db, role, reason=f"agent_chat:constraint_{action}"
        )

    return {
        "type": "constraint_change",
        "action": action,
        "criterion": {
            "id": int(chip.id),
            "text": chip.text,
            "bucket": chip.bucket,
        },
        "invalidates_scores": bool(invalidating),
        "rescreening_count": rescreening_count,
    }


def remove_constraint(
    db: Session, role: Role, criterion_id: int, *, trigger_rescreen: bool = True
) -> dict[str, Any]:
    """Soft-delete a recruiter constraint chip, then re-screen if it fed the
    pre-screen prompt."""
    chip = next(
        (c for c in _editable_criteria(role) if int(c.id) == int(criterion_id)),
        None,
    )
    if chip is None:
        raise ValueError(f"constraint {criterion_id} not found on this role")
    old_bucket = chip.bucket
    removed_text = chip.text
    chip.deleted_at = datetime.now(timezone.utc)
    db.flush()

    invalidating = old_bucket in _INVALIDATING_BUCKETS
    rescreening_count = 0
    if trigger_rescreen and invalidating:
        rescreening_count = _trigger_rescreen(db, role, reason="agent_chat:constraint_removed")

    return {
        "type": "constraint_change",
        "action": "removed",
        "criterion": {"id": int(criterion_id), "text": removed_text, "bucket": old_bucket},
        "invalidates_scores": bool(invalidating),
        "rescreening_count": rescreening_count,
    }


__all__ = [
    "BUCKET_CONSTRAINT",
    "BUCKET_MUST",
    "BUCKET_PREFERRED",
    "add_or_update_constraint",
    "estimate_rescreen",
    "list_constraints",
    "remove_constraint",
    "rescreen_role",
]
