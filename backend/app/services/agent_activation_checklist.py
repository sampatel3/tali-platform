"""Surface all role-config gaps as recruiter questions on agent activation.

Fired from the PATCH /roles/{id} handler whenever ``agentic_mode_enabled``
flips from False to True (regardless of whether the role was ever active
before). Idempotent: each gap maps to one open ``agent_needs_input`` row
via ``ask_recruiter.open``'s upsert-on-(role_id, kind) semantics, so
running this multiple times never duplicates.

The agent's own cycle prompt also surfaces gaps, but it can only do one
``ask_recruiter`` per cycle to keep the loop tight. Activation is the
right moment to dump the full checklist into the Home hub so the
recruiter can answer everything at once rather than discovering gaps
one cycle at a time.
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy.orm import Session

from ..actions import ask_recruiter
from ..actions.types import Actor
from ..models.role import Role


logger = logging.getLogger("taali.services.agent_activation_checklist")


_ACTIVATION_QUESTION_KINDS = frozenset(
    {
        "threshold_ambiguous",
        "intent_slot_missing",
        "task_assignment_missing",
        "monthly_budget_missing",
    }
)


def resolve_satisfied_activation_questions(db: Session, *, role: Role) -> int:
    """Close activation questions whose underlying role gap is now filled.

    Settings can be completed outside the question card (for example, by
    assigning an assessment task on the role page). Those external mutations
    must reconcile the durable prompt instead of requiring a fictitious chat
    answer. The response marker matches the existing data-readiness
    auto-resolution contract.
    """

    from ..agent_runtime.data_readiness import resolve_open

    live_gaps = set(_gaps_for(role))
    resolved = 0
    for kind in _ACTIVATION_QUESTION_KINDS - live_gaps:
        resolved += resolve_open(db, role=role, kind=kind)
    return resolved


def surface_activation_questions(db: Session, *, role: Role) -> list[int]:
    """Open one needs-input row per gap. Returns the ids of opened rows.

    Idempotent — ``ask_recruiter.open`` upserts on (role_id, kind), so
    re-running this when a question is already open is a no-op apart
    from refreshing the prompt text.
    """
    # Re-activation also reconciles any older prompt whose setting was filled
    # while the agent was off.
    resolve_satisfied_activation_questions(db, role=role)

    actor = Actor.system()
    opened: list[int] = []
    for kind in _gaps_for(role):
        try:
            row = ask_recruiter.open(
                db,
                actor,
                organization_id=int(role.organization_id),
                role_id=int(role.id),
                kind=kind,
                # ``open`` overrides this with the canonical templated
                # prompt for known kinds — pass a placeholder so the
                # non-empty check passes.
                prompt="(canonical prompt populated by ask_recruiter)",
            )
            opened.append(int(row.id))
        except Exception:  # pragma: no cover — never block activation on this
            logger.exception(
                "surface_activation_questions: failed to open %s for role=%s",
                kind,
                role.id,
            )
    return opened


def _gaps_for(role: Role) -> Iterable[str]:
    """Yield the canonical needs-input kind for each missing-config slot.

    Order matters — the recruiter sees them in the Home hub in this
    order. Lead with the things that block triage (threshold, must-haves,
    task) so they're visible above secondary items.
    """
    from ..models.role_criterion import CRITERION_SOURCE_DERIVED

    # 1. Score threshold for advancing candidates. In ``auto`` mode the
    # threshold is dynamic / agent-managed (recalibrated live), so there's
    # nothing for the recruiter to set — don't surface the gap. Only ask
    # when they've chosen manual mode but left the value blank.
    mode = getattr(role, "auto_reject_threshold_mode", None) or "manual"
    if mode != "auto" and role.score_threshold is None:
        yield "threshold_ambiguous"

    # 2. Recruiter-set must-have requirements (excluding derived-from-spec).
    must_chips = [
        c
        for c in (role.criteria or [])
        if c.deleted_at is None
        and c.source != CRITERION_SOURCE_DERIVED
        and getattr(c, "bucket", None) == "must"
        and (c.text or "").strip()
    ]
    if not must_chips:
        yield "intent_slot_missing"

    # 3. Only an active assessment task can be assigned. A generated draft is
    # intentionally not executable until its content has been approved.
    active_tasks = [t for t in (role.tasks or []) if bool(getattr(t, "is_active", False))]
    if not active_tasks and not bool(getattr(role, "auto_skip_assessment", False)):
        yield "task_assignment_missing"

    # 4. Monthly USD budget cap. Activation already checks this is set in
    # the PATCH handler (422 if missing), but keep the question kind for
    # roles that lose their cap later — defensive.
    if role.monthly_usd_budget_cents is None or role.monthly_usd_budget_cents <= 0:
        yield "monthly_budget_missing"
