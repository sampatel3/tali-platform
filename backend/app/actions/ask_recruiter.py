"""Open / answer / dismiss an ``agent_needs_input`` row.

Three pure functions, all going through the unified action layer so
the audit trail (Actor, timestamps, organization scoping) stays
identical whether the agent or a recruiter invokes them.

Idempotency: ``open`` upserts on ``(role_id, kind, subject_id)``.
If an open row already exists for the same kind + subject on the
same role, it returns that row instead of inserting a new one — the
orchestrator can call this freely without spamming the recruiter.

NeedsInput today is only for role-level clarifying questions
(``intent_slot_missing``, ``monthly_budget_missing``,
``threshold_ambiguous``, ``candidate_tie_break``, ``other``).
Per-candidate HITL gates that used to live here as
``send_assessment_approval`` / ``resend_assessment_invite_approval``
now flow through ``agent_decisions`` (see PR #176) — the
``consume_resolved`` helper that bridged the old flow is gone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_needs_input import NEEDS_INPUT_KINDS, AgentNeedsInput
from ..models.role import Role
from .types import ACTOR_AGENT, ACTOR_RECRUITER, ACTOR_SYSTEM, Actor


def open(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    role_id: int,
    kind: str,
    prompt: str,
    options: Optional[list[dict[str, Any]]] = None,
    response_schema: Optional[dict[str, Any]] = None,
    rationale: Optional[str] = None,
    subject_id: Optional[int] = None,
) -> AgentNeedsInput:
    """Agent-only: create (or return existing) open question.

    Idempotent on ``(organization_id, role_id, kind, subject_id)``.
    ``subject_id`` is the per-candidate (or per-anything) discriminator
    so multi-candidate kinds like ``send_assessment_approval`` get one
    row per subject instead of all collapsing onto one card. NULL
    preserves the legacy role-wide semantic for kinds like
    ``monthly_budget_missing`` that don't have a subject.
    """
    # Agent (inside a cycle) and system (e.g. activation checklist run
    # from the PATCH /roles/{id} handler) can both open questions.
    # Recruiters open questions through the UI, which goes through a
    # different route — never this code path.
    if actor.type not in (ACTOR_AGENT, ACTOR_SYSTEM):
        raise HTTPException(
            status_code=403,
            detail="ask_recruiter.open is agent/system-only",
        )
    if kind not in NEEDS_INPUT_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown agent_needs_input kind: {kind!r}",
        )
    if not (prompt or "").strip():
        raise HTTPException(status_code=422, detail="prompt is required")

    role = (
        db.query(Role)
        .filter(Role.id == role_id, Role.organization_id == organization_id)
        .one_or_none()
    )
    if role is None:
        raise HTTPException(
            status_code=404,
            detail=f"role {role_id} not found in org {organization_id}",
        )

    # Override the agent's free-text prompt with a canonical plain-English
    # version for the standard config-gap kinds. The agent's wording tends
    # to leak schema terms ("score_threshold", "CV-match", "triage"); the
    # recruiter sees a consistent, concrete question with a suggested value
    # baked in so they don't have to invent one from scratch.
    canonical = _canonical_for_kind(db, role=role, kind=kind)
    if canonical is not None:
        prompt = canonical["prompt"]
        if canonical.get("rationale") and not (rationale or "").strip():
            rationale = canonical["rationale"]
        if canonical.get("options") and not options:
            options = canonical["options"]
        # Settings-tab link (when present) rides on response_schema so we
        # don't need a schema migration. The needs-input GET view surfaces
        # it back as link_url / link_label on the wire.
        if canonical.get("link_url"):
            response_schema = dict(response_schema or {})
            response_schema["link_url"] = canonical["link_url"]
            response_schema["link_label"] = canonical.get(
                "link_label", "Open settings"
            )

    subject_filter = (
        AgentNeedsInput.subject_id == subject_id
        if subject_id is not None
        else AgentNeedsInput.subject_id.is_(None)
    )
    existing = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.organization_id == organization_id,
            AgentNeedsInput.role_id == role_id,
            AgentNeedsInput.kind == kind,
            subject_filter,
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .order_by(AgentNeedsInput.created_at.desc())
        .first()
    )
    if existing is not None:
        # Update the prompt + rationale in case the agent has refined
        # its question — the recruiter sees the latest framing.
        existing.prompt = prompt.strip()
        if options is not None:
            existing.options = options
        if response_schema is not None:
            existing.response_schema = response_schema
        if rationale is not None:
            existing.rationale = rationale
        if actor.agent_run_id is not None:
            existing.agent_run_id = actor.agent_run_id
        db.flush()
        return existing

    row = AgentNeedsInput(
        organization_id=organization_id,
        role_id=role_id,
        kind=kind,
        subject_id=subject_id,
        prompt=prompt.strip(),
        options=options,
        response_schema=response_schema,
        agent_run_id=actor.agent_run_id,
        rationale=rationale,
    )
    db.add(row)
    db.flush()
    return row


def _canonical_for_kind(
    db: Session, *, role: Role, kind: str
) -> Optional[dict[str, Any]]:
    """Plain-English overrides for the standard role-config-gap kinds.

    The agent's free-text prompts tend to surface schema vocabulary
    ("score_threshold", "CV-match", "triage") that's noise to a
    recruiter. For known kinds we render a fixed prompt with the role
    name + concrete numbers so the question is consistent and
    actionable. Returns None for kinds we don't templatise (the agent's
    own wording wins).
    """
    if kind == "threshold_ambiguous":
        # Run the existing cohort-based recommender so the question
        # leads with a concrete proposal instead of asking the recruiter
        # to pick a number from nothing.
        from ..services.auto_threshold_service import compute_recommended_threshold

        rec = compute_recommended_threshold(db, role=role)
        prompt = (
            f"For '{role.name}', I'd like to use **{rec.value}** as the score "
            f"bar for advancing candidates. Tap Use {rec.value} to accept, or "
            f"send a different number to override."
        )
        rationale_bits = [rec.rationale]
        if rec.sample_size > 0:
            rationale_bits.append(f"(based on {rec.sample_size} candidate signal{'s' if rec.sample_size != 1 else ''})")
        return {
            "prompt": prompt,
            "rationale": " ".join(rationale_bits),
            "options": [{"value": str(rec.value), "label": f"Use {rec.value}"}],
        }
    if kind == "monthly_budget_missing":
        return {
            "prompt": (
                f"What's the monthly spending cap for '{role.name}'? This "
                f"covers scoring, pre-screening, and assessment invites for "
                f"the role. Most technical roles run $50–$100 per month."
            ),
            "rationale": "I can't run cycles on this role until there's a cap.",
        }
    if kind == "intent_slot_missing":
        return {
            "prompt": (
                f"What are the must-have requirements for '{role.name}'? "
                f"Write them in plain language (e.g. \"5+ years Python, AWS, "
                f"remote-friendly, US time zones\") and I'll structure them. "
                f"Or open the role's agent settings to enter must-have / "
                f"preferred / constraints directly."
            ),
            "rationale": (
                "Without must-haves I can only triage on score. Capturing "
                "them lets me reject candidates who clearly don't fit."
            ),
            "link_url": f"/jobs/{int(role.id)}?tab=agent-settings",
            "link_label": "Open agent settings",
        }
    if kind == "task_assignment_missing":
        return {
            "prompt": (
                f"'{role.name}' has no assessment task linked. Without one "
                f"I can't send assessment invites to candidates who clear "
                f"the score bar. Pick a task on the role page (or create a "
                f"new one), then I'll resume."
            ),
            "rationale": (
                "A task defines what the candidate is actually asked to do. "
                "It's the deliverable the invite points at."
            ),
            "link_url": f"/jobs/{int(role.id)}?tab=agent-settings",
            "link_label": "Pick a task",
        }
    return None


def answer(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    needs_input_id: int,
    response: dict[str, Any],
) -> AgentNeedsInput:
    """Recruiter-only: record the recruiter's response.

    Sets ``resolved_at`` + ``response`` + ``resolved_by_user_id``.
    The next agent cycle reads this through
    ``read_pending_recruiter_inputs``.
    """
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(
            status_code=403,
            detail="ask_recruiter.answer is recruiter-only",
        )
    row = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.id == needs_input_id,
            AgentNeedsInput.organization_id == organization_id,
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="needs_input row not found")
    if row.resolved_at is not None:
        raise HTTPException(status_code=409, detail="already answered")
    if row.dismissed_at is not None:
        raise HTTPException(status_code=409, detail="already dismissed")

    row.resolved_at = datetime.now(timezone.utc)
    row.response = response
    row.resolved_by_user_id = actor.user_id
    db.flush()
    return row


def dismiss(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    needs_input_id: int,
) -> AgentNeedsInput:
    """Either party: close the row without an answer.

    Recruiter dismisses to say "skip / not now"; the agent dismisses
    its own stale rows when it gives up after N cycles.
    """
    row = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.id == needs_input_id,
            AgentNeedsInput.organization_id == organization_id,
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="needs_input row not found")
    if row.resolved_at is not None or row.dismissed_at is not None:
        return row  # idempotent — already closed
    row.dismissed_at = datetime.now(timezone.utc)
    db.flush()
    return row
