"""Role-constraint edits driven by the conversational agent.

When the recruiter says "cap salary at 25k on this role" the agent edits a
``RoleCriterion`` (a constraint chip) and re-screens — exactly what the
manual criteria CRUD does, plus an immediate stale-score sweep so the
re-evaluation starts now instead of waiting for the hourly safety net.

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


def _trigger_rescreen(
    db: Session, role: Role, *, reason: str, application_ids: list[int] | None = None
) -> int:
    """Mark the role's scores stale and kick an immediate stale-score sweep.

    ``application_ids`` scopes the invalidation to the agent's reasoned subset
    (re-screen only the genuinely-affected); ``None`` invalidates the whole pool.
    Returns the number of applications invalidated. The sweep is dispatched
    with a short countdown so the caller's outer commit lands before a worker
    reads the stale jobs (same guard ``mark_role_scores_stale`` uses for the
    tech-questions regen).
    """
    from ..services.cv_score_orchestrator import mark_role_scores_stale

    invalidated = mark_role_scores_stale(
        db,
        int(role.id),
        reason=reason,
        application_ids=application_ids,
    )
    # An explicit empty scope means the recruiter-approved filter matched no
    # candidates. Keep that distinct from ``None`` (the whole role): provider
    # artifacts were already handled by the transaction lifecycle, and there
    # is no paid candidate score sweep to dispatch.
    if application_ids is not None and not application_ids:
        return int(invalidated)
    try:
        from ..tasks.scoring_tasks import sweep_stale_scores

        sweep_stale_scores.apply_async(
            kwargs={
                "limit": 500,
                "role_id": int(role.id),
                "application_ids": (
                    [int(value) for value in application_ids]
                    if application_ids is not None
                    else None
                ),
                "explicit": True,
            },
            countdown=10,
        )
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
    db: Session, role: Role, *, reason: str = "agent_chat:opt_in_rescreen",
    application_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Run the re-screen the recruiter explicitly opted into (after a constraint
    change). Separated from the edit so the spend is never automatic.
    ``application_ids`` scopes it to the agent's reasoned subset."""
    count = _trigger_rescreen(db, role, reason=reason, application_ids=application_ids)
    return {
        "type": "rescreen_started",
        "rescreening_count": int(count),
        "scoped": application_ids is not None,
    }


def _criteria_text_map(db: Session, role: Role) -> dict[str, str]:
    """Every live criterion on the role keyed by lower-cased text — for diffing the
    chip set before vs after a job-spec re-derive. Queried fresh so the soft-delete
    + re-add `sync_derived_criteria` performs is reflected."""
    rows = (
        db.query(RoleCriterion)
        .filter(RoleCriterion.role_id == int(role.id), RoleCriterion.deleted_at.is_(None))
        .all()
    )
    return {
        (c.text or "").strip().lower(): (c.text or "").strip()
        for c in rows
        if (c.text or "").strip()
    }


def update_job_spec(
    db: Session,
    role: Role,
    *,
    job_spec_text: str,
    provision_assessment_task: bool = True,
) -> dict[str, Any]:
    """Replace THIS role's job description + re-derive its spec criteria.

    A new JD re-derives the must / preferred / constraint chips from the spec's
    Requirements section (``sync_derived_criteria``) — the biggest criteria change
    there is — so it invalidates the whole pool's scores. We apply the spec +
    re-derive IMMEDIATELY (cheap, no LLM) but do NOT re-screen: return the criteria
    diff + a cost estimate and leave the spend to an explicit ``rescreen_role``
    (same opt-in guard as a constraint edit). Recruiter-added chips (salary caps
    etc.) are untouched — only the spec-derived ones change. Related-role callers
    disable assessment-task provisioning because their score lifecycle is separate.
    """
    text = (job_spec_text or "").strip()
    if len(text) < 60:
        return {"ok": False, "error": "That doesn't look like a full job spec — paste the whole description and I'll apply it."}

    is_sister = str(getattr(role, "role_kind", "") or "") == "sister"
    previous_provider_generation = None
    if not is_sister:
        from ..services.role_provider_generation import (
            capture_role_provider_generation,
        )

        previous_provider_generation = capture_role_provider_generation(
            db,
            role_id=int(role.id),
            organization_id=int(role.organization_id),
        )

    before = _criteria_text_map(db, role)
    now = datetime.now(timezone.utc)
    role.job_spec_text = text
    # Text edits are first-class recruiter overrides, not an ephemeral agent
    # note. Keep the legacy description reader truthful and protect the edit
    # from the next ATS sync.
    role.description = text
    if hasattr(role, "job_spec_uploaded_at"):
        role.job_spec_uploaded_at = now
    if hasattr(role, "job_spec_manually_edited_at"):
        role.job_spec_manually_edited_at = now
    try:
        from ..services.role_criteria_service import sync_derived_criteria

        sync_derived_criteria(db, role)
        from ..platform.config import settings

        if provision_assessment_task and getattr(
            settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False
        ):
            from ..services.task_provisioning_service import (
                request_assessment_task_provisioning,
            )

            # The chat turn commits this mutation. Persist provisioning intent
            # with it; Beat supplies the post-commit dispatch/recovery.
            request_assessment_task_provisioning(
                role,
                reason="agent_job_spec_update",
                supersede_generated_drafts=True,
            )
        db.flush()
        if not is_sister:
            from ..services.role_provider_artifact_lifecycle import (
                invalidate_role_provider_artifacts_if_changed,
            )

            invalidate_role_provider_artifacts_if_changed(
                db,
                role=role,
                previous=previous_provider_generation,
            )
    except Exception as exc:  # noqa: BLE001 — surface, don't crash the turn
        db.rollback()
        return {"ok": False, "error": f"I couldn't parse that spec into criteria ({type(exc).__name__}); the role is unchanged."}

    after = _criteria_text_map(db, role)
    added = [after[k] for k in after if k not in before]
    removed = [before[k] for k in before if k not in after]
    return {
        "type": "job_spec_change",
        "applied": True,
        "added": added[:12],
        "removed": removed[:12],
        "criteria_count": len(after),
        # A new JD re-derives every criterion → the whole pool needs re-scoring.
        # Opt-in: show the cost, run rescreen_role only on the recruiter's yes.
        "would_rescreen": estimate_rescreen(db, role),
    }


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

    from ..services.role_provider_generation import (
        capture_role_provider_generation,
    )

    previous_provider_generation = capture_role_provider_generation(
        db,
        role_id=int(role.id),
        organization_id=int(role.organization_id),
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
    from ..services.role_provider_artifact_lifecycle import (
        invalidate_role_provider_artifacts_if_changed,
    )

    provider_inputs_changed = invalidate_role_provider_artifacts_if_changed(
        db,
        role=role,
        previous=previous_provider_generation,
    )
    rescreening_count = 0
    if trigger_rescreen and invalidating and provider_inputs_changed:
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
        "invalidates_scores": bool(invalidating and provider_inputs_changed),
        "rescreening_count": rescreening_count,
    }


def remove_constraint(
    db: Session, role: Role, criterion_id: int, *, trigger_rescreen: bool = True
) -> dict[str, Any]:
    """Soft-delete a recruiter constraint chip, then re-screen if it fed the
    pre-screen prompt."""
    from ..services.role_provider_generation import (
        capture_role_provider_generation,
    )

    previous_provider_generation = capture_role_provider_generation(
        db,
        role_id=int(role.id),
        organization_id=int(role.organization_id),
    )
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
    from ..services.role_provider_artifact_lifecycle import (
        invalidate_role_provider_artifacts_if_changed,
    )

    provider_inputs_changed = invalidate_role_provider_artifacts_if_changed(
        db,
        role=role,
        previous=previous_provider_generation,
    )

    invalidating = old_bucket in _INVALIDATING_BUCKETS
    rescreening_count = 0
    if trigger_rescreen and invalidating and provider_inputs_changed:
        rescreening_count = _trigger_rescreen(db, role, reason="agent_chat:constraint_removed")

    return {
        "type": "constraint_change",
        "action": "removed",
        "criterion": {"id": int(criterion_id), "text": removed_text, "bucket": old_bucket},
        "invalidates_scores": bool(invalidating and provider_inputs_changed),
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
