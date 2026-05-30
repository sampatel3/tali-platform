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

Answer write-back
-----------------
When the recruiter answers a config-gap card, ``answer`` promotes the
response into canonical role state so the agent settings tab reflects
what the recruiter just told the agent:

- ``threshold_ambiguous``    → ``role.score_threshold``
- ``monthly_budget_missing`` → ``role.monthly_usd_budget_cents``
- ``intent_slot_missing`` /
  ``intent_clarification``   → ``RoleIntent.free_text`` (new version)
                               + LLM-parsed chips into ``role_criteria``
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_needs_input import NEEDS_INPUT_KINDS, AgentNeedsInput
from ..models.org_criterion import BUCKET_MUST
from ..models.role import Role
from ..models.role_criterion import CRITERION_SOURCE_RECRUITER, RoleCriterion
from .types import ACTOR_AGENT, ACTOR_RECRUITER, ACTOR_SYSTEM, Actor


logger = logging.getLogger("taali.actions.ask_recruiter")


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
        # Prompt override is opt-in per kind. Some kinds (like
        # intent_clarification) want the agent's free-text question to
        # win and only inherit the settings-tab link.
        if canonical.get("prompt"):
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
    if kind == "intent_clarification":
        # Agentic kind — the agent passes a specific question about a thin
        # intent dimension ("you have 3 preferreds but no must-haves",
        # "missing seniority signal", etc.). We don't override the prompt
        # so the agent's framing wins; we only inject the settings link
        # so the recruiter has the same one-click escape hatch.
        return {
            "link_url": f"/jobs/{int(role.id)}?tab=agent-settings",
            "link_label": "Open agent settings",
        }
    if kind == "missing_job_spec":
        return {
            "prompt": (
                f"'{role.name}' has agent mode on but no job description. "
                f"I won't score or decide on candidates without one — I'd just "
                f"be guessing the bar. Add a job spec (or sync it from Workable) "
                f"and I'll resume automatically on the next cycle."
            ),
            "rationale": (
                "Running the agent with no job spec wastes money and produces "
                "untrustworthy verdicts, so cycles are held until one exists."
            ),
            "link_url": f"/jobs/{int(role.id)}",
            "link_label": "Add a job spec",
        }
    if kind == "missing_cv":
        # No prompt override: data_readiness authors a prompt with the live
        # count of CV-less candidates. We only inject the link + rationale.
        return {
            "rationale": (
                "Without a CV there's nothing to evaluate against the role's "
                "criteria — the candidate is skipped rather than guessed at."
            ),
            "link_url": f"/jobs/{int(role.id)}",
            "link_label": "Review candidates",
        }
    # confirm_material_change deliberately has no canonical override: the
    # prompt + options + proposed-criteria context are authored by
    # material_change.handle_spec_change (the LLM summary is the question).
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

    # Promote the answer into canonical role state. Failures are logged
    # but don't undo the answer — the recruiter sees their reply resolved
    # either way, and a follow-up cycle can re-derive structure from the
    # raw response if needed.
    try:
        _apply_recruiter_answer(
            db,
            row=row,
            response=response,
            user_id=actor.user_id,
        )
    except Exception:
        logger.exception(
            "recruiter answer write-back failed (kind=%s, row_id=%s)",
            row.kind,
            row.id,
        )

    return row


def _apply_recruiter_answer(
    db: Session,
    *,
    row: AgentNeedsInput,
    response: dict[str, Any],
    user_id: Optional[int],
) -> None:
    """Promote the recruiter's answer to the canonical source of truth.

    Dispatch on ``row.kind``:
      - ``threshold_ambiguous`` writes ``role.score_threshold`` (always
        overwrites — the card explicitly proposes a value, approving it
        is consent to change the column).
      - ``monthly_budget_missing`` writes ``role.monthly_usd_budget_cents``.
      - ``intent_slot_missing`` / ``intent_clarification`` author a new
        ``RoleIntent`` version with the answer appended to ``free_text``,
        then LLM-parse the answer into ``role_criteria`` chips.

    Best-effort: any failure here is swallowed by the caller so the
    recruiter's resolution still sticks.
    """
    value = (response or {}).get("value") if isinstance(response, dict) else None
    if value is None:
        return
    text_value = str(value).strip()
    if not text_value:
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
        _writeback_threshold(role, text_value)
        db.flush()
        return

    if row.kind == "monthly_budget_missing":
        _writeback_budget(role, text_value)
        # A role with no explicit budget still runs against the default cap
        # and can pause on it. Answering this card with a high-enough number
        # is the same "raise the cap" intent as the settings PATCH, so clear
        # a budget-pause that the new budget now covers. Pure mutation on the
        # same role; the existing flush persists it and the next cohort beat
        # (≤30 min) picks the role back up. Mirrors the PATCH-route resume.
        from ..agent_runtime import budget_guard

        budget_guard.resume_if_under_budget(db, role=role)
        db.flush()
        return

    if row.kind in ("intent_slot_missing", "intent_clarification"):
        _writeback_intent(
            db,
            role=role,
            row=row,
            answer_text=text_value,
            user_id=user_id,
        )
        db.flush()
        return

    if row.kind == "confirm_material_change":
        # "apply" => re-derive criteria from the new spec. That changes the
        # content fingerprint, which (by design) marks affected pending
        # decisions stale so the recruiter re-evaluates them against the new
        # bar. "ignore" => keep the current criteria frozen (no churn, no
        # re-eval spend); the new spec text is still saved for display.
        if text_value.strip().lower() == "apply":
            from ..services.role_criteria_service import sync_derived_criteria

            sync_derived_criteria(db, role)
            db.flush()
        return


def _writeback_threshold(role: Role, raw: str) -> None:
    try:
        n = int(float(raw.strip()))
    except (TypeError, ValueError):
        return
    role.score_threshold = max(0, min(100, n))


def _writeback_budget(role: Role, raw: str) -> None:
    cleaned = raw.strip().lstrip("$").replace(",", "")
    try:
        n = float(cleaned)
    except (TypeError, ValueError):
        return
    # Recruiters answer this question in dollars per month ("$50",
    # "2000"), always. The field is stored in cents, so just scale by
    # 100. The old "small number is dollars, large number is cents"
    # heuristic mangled any genuine budget over $1000 — "2000" ($2000/mo)
    # was stored as 2000 cents = $20.
    role.monthly_usd_budget_cents = max(0, int(round(n * 100)))


def _writeback_intent(
    db: Session,
    *,
    role: Role,
    row: AgentNeedsInput,
    answer_text: str,
    user_id: Optional[int],
) -> None:
    from ..agent_runtime.contracts import StructuredIntent
    from ..agent_runtime.role_intent import (
        author_new_version,
        fetch_active_intent,
    )
    from ..services.intent_chip_parser import parse_intent_text_to_chips

    # 1. Append the answer onto RoleIntent.free_text so the agent prompt
    #    picks it up next cycle via `_render_role_intent`.
    active = fetch_active_intent(db, role_id=int(role.id))
    prior_free = (active.free_text if active else None) or ""
    prior_structured = active.structured if active else StructuredIntent()
    next_free = (
        f"{prior_free.strip()}\n\n{answer_text}".strip()
        if prior_free.strip()
        else answer_text
    )
    author_new_version(
        db,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        structured=prior_structured,
        free_text=next_free,
        authored_by_user_id=user_id,
    )

    # 2. LLM-parse the new text into chips and add them. Best-effort —
    #    if the call fails, the free-text version above still shapes the
    #    agent's prompt.
    existing_texts = [
        (c.text or "").strip()
        for c in (role.criteria or [])
        if c.deleted_at is None and (c.text or "").strip()
    ]
    chips = parse_intent_text_to_chips(
        db,
        organization_id=int(role.organization_id),
        role=role,
        answer_text=answer_text,
        agent_question=row.prompt,
        existing_chip_texts=existing_texts,
    )
    if not chips:
        return
    existing_ordering = [
        int(c.ordering)
        for c in (role.criteria or [])
        if c.deleted_at is None
    ]
    next_ordering = (max(existing_ordering) + 1) if existing_ordering else 0
    now = datetime.now(timezone.utc)
    for chip in chips:
        db.add(
            RoleCriterion(
                role_id=int(role.id),
                source=CRITERION_SOURCE_RECRUITER,
                ordering=next_ordering,
                weight=1.0,
                must_have=(chip.bucket == BUCKET_MUST),
                bucket=chip.bucket,
                org_criterion_id=None,
                customized_at=now,
                text=chip.text,
            )
        )
        next_ordering += 1


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
