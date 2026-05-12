"""Open / answer / dismiss / consume an ``agent_needs_input`` row.

Four pure functions, all going through the unified action layer so
the audit trail (Actor, timestamps, organization scoping) stays
identical whether the agent or a recruiter invokes them.

Idempotency: ``open`` upserts on ``(role_id, kind, subject_id)``.
If an open row already exists for the same kind + subject on the
same role, it returns that row instead of inserting a new one — the
orchestrator can call this freely without spamming the recruiter.

Consumption: ``consume_resolved`` reads the most recent
resolved-but-not-yet-consumed approval card for a given
``(role, kind, subject_id)`` and marks it consumed, returning the
recruiter's choice. This is what HITL tool handlers use to honour a
prior approval instead of opening a fresh card forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_needs_input import NEEDS_INPUT_KINDS, AgentNeedsInput
from ..models.role import Role
from .types import ACTOR_AGENT, ACTOR_RECRUITER, Actor


# Resolved approvals older than this are treated as stale and not
# consumed — the agent must re-ask. 24h is wide enough to absorb a
# weekend gap; tighter would force re-approval after a normal break.
_APPROVAL_TTL = timedelta(hours=24)


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
    if actor.type != ACTOR_AGENT:
        raise HTTPException(
            status_code=403,
            detail="ask_recruiter.open is agent-only",
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
                f"List the things a candidate has to have for you to consider "
                f"them — e.g. specific skills, years of experience, "
                f"certifications, location constraints."
            ),
            "rationale": (
                "Without must-haves I can only triage on score. Capturing "
                "them lets me reject candidates who clearly don't fit."
            ),
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
    # Apply the answer to the role's config for the standard config-gap
    # kinds. Without this the answer just sits as a record and the next
    # agent cycle has to read it via read_pending_recruiter_inputs and
    # decide what to do with it — meanwhile the recruiter sees their
    # answer "vanish" because role.score_threshold etc. are still null
    # and the UI keeps falling back to the auto-suggested value.
    _apply_resolved_answer_to_role_config(db, row=row, response=response)
    db.flush()
    return row


def _apply_resolved_answer_to_role_config(
    db: Session, *, row: AgentNeedsInput, response: dict[str, Any]
) -> None:
    """Persist the recruiter's answer to the role row for known kinds.

    Only the config-gap kinds map cleanly to a single role column. Other
    kinds (candidate_tie_break, other) stay as advisory records — the
    agent reads them next cycle and acts.
    """
    raw_value = response.get("value") if isinstance(response, dict) else None
    if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
        return
    role = (
        db.query(Role)
        .filter(
            Role.id == row.role_id,
            Role.organization_id == row.organization_id,
        )
        .one_or_none()
    )
    if role is None:
        return
    if row.kind == "threshold_ambiguous":
        try:
            n = int(float(str(raw_value)))
        except (TypeError, ValueError):
            return
        # Clamp to a sane band — same floor/ceiling auto_threshold_service uses.
        role.score_threshold = max(0, min(100, n))
        db.add(role)
    elif row.kind == "monthly_budget_missing":
        # Recruiter can answer in dollars ("$50", "50") or cents ("5000").
        # Treat ≤ 1000 as dollars (typical monthly caps are $10–$1000), else cents.
        try:
            n = float(str(raw_value).strip().lstrip("$").replace(",", ""))
        except (TypeError, ValueError):
            return
        cents = int(n * 100) if n <= 1000 else int(n)
        if cents > 0:
            role.monthly_usd_budget_cents = cents
            db.add(role)
    # intent_slot_missing arrives as free text. Persisting it would require
    # parsing structured must-haves out of prose — defer to the agent's
    # next-cycle read via read_pending_recruiter_inputs. The answer is
    # still stored on the row for that lookup.


@dataclass(frozen=True)
class ConsumedApproval:
    """Outcome of looking up the agent's latest resolved approval card.

    ``choice`` is the recruiter's response value (typically ``"approve"``
    or ``"skip"``) extracted from ``AgentNeedsInput.response``. ``row``
    is the underlying record so callers can audit which approval was
    consumed. ``None`` is returned (not this dataclass) when there is
    no matching resolved row.
    """

    choice: str
    row: AgentNeedsInput


def consume_resolved(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    role_id: int,
    kind: str,
    subject_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Optional[ConsumedApproval]:
    """Look up the most recent resolved-and-uncon­sumed approval card
    for ``(org, role, kind, subject_id)`` and dismiss it so it can't
    be consumed twice.

    Use case: HITL-gated tool handlers (``send_assessment``,
    ``resend_assessment_invite``) need to know whether the recruiter
    already approved this exact subject before opening a fresh card.
    Without this lookup the handler always opens a new card and the
    send/resend never actually fires after approval — Codex flagged
    this on #141 as "approval loop where resend never executes for
    HITL roles."

    Matching:

    - Scoped to ``(organization_id, role_id, kind, subject_id)``. The
      subject_id discriminator is required so an approval for
      application 42 cannot be consumed when the agent is processing
      application 99. Pass ``None`` only for legacy role-wide kinds
      (e.g. ``monthly_budget_missing``) that don't have a subject.
    - Must be resolved (``resolved_at`` not null).
    - Must not already be consumed (``dismissed_at`` is null).
    - Must be within the 24h approval TTL — a stale approval (e.g.
      from days ago, recruiter context long gone) is ignored so the
      agent re-asks.

    Side effect: on a successful match, sets ``dismissed_at`` on the
    row so the next call sees no approval and opens a fresh card.
    """
    if actor.type != ACTOR_AGENT:
        raise HTTPException(
            status_code=403,
            detail="ask_recruiter.consume_resolved is agent-only",
        )
    if kind not in NEEDS_INPUT_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown agent_needs_input kind: {kind!r}",
        )

    current = now or datetime.now(timezone.utc)
    cutoff = current - _APPROVAL_TTL

    # Filter by subject_id at the SQL layer (NOT in Python) so the
    # ``order_by(resolved_at desc).first()`` query picks the latest
    # approval *for this subject*, not the latest overall. Otherwise
    # a later approval for subject B masks an earlier-but-still-valid
    # approval for subject A and the caller opens a fresh card —
    # Codex flagged this exact bug on the previous iteration.
    subject_filter = (
        AgentNeedsInput.subject_id == subject_id
        if subject_id is not None
        else AgentNeedsInput.subject_id.is_(None)
    )
    row = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.organization_id == organization_id,
            AgentNeedsInput.role_id == role_id,
            AgentNeedsInput.kind == kind,
            subject_filter,
            AgentNeedsInput.resolved_at.isnot(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .order_by(AgentNeedsInput.resolved_at.desc())
        .first()
    )
    if row is None:
        return None
    resolved_at = row.resolved_at
    if resolved_at is not None and resolved_at.tzinfo is None:
        # SQLite strips tzinfo from DateTime(timezone=True); normalise
        # for cutoff comparison so the test fixtures and prod behave
        # identically.
        resolved_at = resolved_at.replace(tzinfo=timezone.utc)
    if resolved_at is None or resolved_at < cutoff:
        return None
    response = row.response if isinstance(row.response, dict) else {}
    choice = str(response.get("value") or "").strip().lower()
    if not choice:
        return None
    row.dismissed_at = current
    db.flush()
    return ConsumedApproval(choice=choice, row=row)


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
