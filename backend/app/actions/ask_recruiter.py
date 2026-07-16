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

from ..domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ..models.agent_needs_input import NEEDS_INPUT_KINDS, AgentNeedsInput
from ..models.org_criterion import BUCKET_MUST
from ..models.role import Role
from ..models.role_criterion import CRITERION_SOURCE_RECRUITER, RoleCriterion
from ..models.user import User
from ..platform.request_context import get_request_id
from ..services.role_change_audit import (
    add_role_change_event,
    capture_role_change_snapshot,
)
from ..services.role_concurrency import assert_role_version, bump_role_version
from .types import ACTOR_AGENT, ACTOR_RECRUITER, ACTOR_SYSTEM, Actor


logger = logging.getLogger("taali.actions.ask_recruiter")


# Answering intent/material-change questions is a direct edit of the job's
# criteria/spec-derived configuration. Every other answer changes or unblocks
# agent behaviour (including candidate tie-breaks), so it requires the
# stronger, explicit CONTROL_AGENT capability.
_EDIT_ROLE_ANSWER_KINDS = frozenset(
    {"intent_slot_missing", "intent_clarification", "confirm_material_change"}
)

_WRITEBACK_AUDIT_FIELDS: dict[str, tuple[str, ...]] = {
    "threshold_ambiguous": ("score_threshold",),
    "monthly_budget_missing": (
        "monthly_usd_budget_cents",
        "agent_paused_at",
        "agent_paused_reason",
    ),
    # RoleIntent/RoleCriterion data lives outside the roles table. An empty
    # generic role diff is intentional for these actions; the typed event and
    # version transition still make the shared configuration change visible.
    "intent_slot_missing": (),
    "intent_clarification": (),
    "confirm_material_change": (),
}

_WRITEBACK_AUDIT_ACTIONS = {
    "threshold_ambiguous": "needs_input_score_threshold_updated",
    "monthly_budget_missing": "needs_input_monthly_budget_updated",
    "intent_slot_missing": "needs_input_intent_criteria_updated",
    "intent_clarification": "needs_input_intent_criteria_updated",
    "confirm_material_change": "needs_input_criteria_rederived",
}


# These questions describe missing external artifacts. A chat/API payload that
# merely says "done" cannot prove the artifact exists, so they may only close
# when the owning readiness reconciliation observes that the gap is actually
# filled (or when the recruiter explicitly dismisses the question).
EXTERNAL_RESOLUTION_KINDS = frozenset(
    {
        "missing_job_spec",
        "missing_cv",
        "cv_unreadable",
        "task_assignment_missing",
    }
)
EXTERNAL_RESOLUTION_DETAIL = (
    "this question is resolved by completing the required setup; "
    "open its linked page or dismiss it instead"
)


def requires_external_resolution(kind: str) -> bool:
    """Whether ``kind`` must be closed by observing its real-world state."""

    return kind in EXTERNAL_RESOLUTION_KINDS


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
                "criteria — the candidate is skipped rather than guessed at. "
                "There's no file to fetch, so the choices are upload one or "
                "reject."
            ),
            "link_url": f"/jobs/{int(role.id)}",
            "link_label": "Review candidates",
        }
    if kind == "cv_unreadable":
        # No prompt override: data_readiness authors a prompt with the live
        # count. We only inject the link + rationale. The card also carries a
        # reject action (reason "CV could not be read") — chasing an OCR
        # re-upload vs. rejecting is the recruiter's call.
        return {
            "rationale": (
                "A scanned or image-only CV has no text layer to evaluate. "
                "Re-fetching from Workable won't help — the file is already on "
                "record — and re-uploading the same scan won't either. It needs "
                "OCR or a text-based CV (PDF/DOCX)."
            ),
            "link_url": f"/jobs/{int(role.id)}",
            "link_label": "Review candidates",
        }
    # confirm_material_change deliberately has no canonical override: the
    # prompt + options + proposed-criteria context are authored by
    # material_change.handle_spec_change (the LLM summary is the question).
    return None


def _answer_permission(kind: str) -> JobPermission:
    if kind in _EDIT_ROLE_ANSWER_KINDS:
        return JobPermission.EDIT_ROLE
    return JobPermission.CONTROL_AGENT


def _require_authenticated_recruiter_job_permission(
    db: Session,
    *,
    actor: Actor,
    row: AgentNeedsInput,
    permission: JobPermission,
    lock_for_update: bool = True,
) -> tuple[User, Role]:
    """Resolve the Actor back to the authenticated tenant user and authorize.

    Action helpers are also called outside HTTP routes, so enforcing the job
    policy here prevents an internal/direct caller from bypassing the route's
    permission boundary.
    """

    if actor.type != ACTOR_RECRUITER or actor.user_id is None:
        raise HTTPException(status_code=403, detail="Forbidden")
    user = (
        db.query(User)
        .filter(
            User.id == int(actor.user_id),
            User.organization_id == int(row.organization_id),
            User.is_active.is_(True),
        )
        .one_or_none()
    )
    if user is None:
        raise HTTPException(status_code=403, detail="Forbidden")
    role = require_job_permission(
        db,
        current_user=user,
        role_id=int(row.role_id),
        permission=permission,
        lock_for_update=lock_for_update,
    )
    return user, role


def _prepare_intent_chips(
    db: Session,
    *,
    row: AgentNeedsInput,
    role: Role,
    response: dict[str, Any],
) -> list[Any] | None:
    """Prepare optional intent chips without holding the shared Role lock.

    The parser can make a model call. The caller checks the Role revision
    before and after this work, so prepared output is discarded whenever a
    concurrent editor changes the job.
    """

    if row.kind not in ("intent_slot_missing", "intent_clarification"):
        return None
    value = (response or {}).get("value") if isinstance(response, dict) else None
    answer_text = str(value).strip() if value is not None else ""
    if not answer_text:
        return None

    from ..services.intent_chip_parser import parse_intent_text_to_chips

    existing_texts = [
        (criterion.text or "").strip()
        for criterion in (role.criteria or [])
        if criterion.deleted_at is None and (criterion.text or "").strip()
    ]
    try:
        return parse_intent_text_to_chips(
            db,
            organization_id=int(role.organization_id),
            role=role,
            answer_text=answer_text,
            agent_question=row.prompt,
            existing_chip_texts=existing_texts,
        )
    except Exception:
        logger.exception(
            "recruiter intent chip parsing failed (row_id=%s, role_id=%s)",
            row.id,
            role.id,
        )
        return None


def _apply_versioned_recruiter_answer(
    db: Session,
    *,
    row: AgentNeedsInput,
    role: Role,
    response: dict[str, Any],
    user_id: int,
    prepared_intent_chips: list[Any] | None = None,
) -> None:
    """Apply a role-setting answer with one atomic version/audit transition.

    The surrounding caller already holds the Role row lock. A savepoint keeps
    the established best-effort answer semantics: if parsing or audit
    persistence fails, no partial setting/criteria/version write survives,
    while the recruiter's raw answer remains available for a later cycle.
    """

    fields = _WRITEBACK_AUDIT_FIELDS.get(row.kind)
    if fields is None:
        return

    with db.begin_nested():
        before = capture_role_change_snapshot(role, fields=fields)
        applied = _apply_recruiter_answer(
            db,
            row=row,
            role=role,
            response=response,
            user_id=user_id,
            prepared_intent_chips=prepared_intent_chips,
        )
        if not applied:
            return

        # Threshold/budget answers that normalize to the already-stored value
        # are true no-ops. Criteria/intent live in related tables, so their
        # typed event deliberately carries an empty generic Role-column diff.
        if fields:
            after = capture_role_change_snapshot(role, fields=fields)
            if before == after:
                return

        from_version = int(getattr(role, "version", 1) or 1)
        to_version = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=before,
            action=_WRITEBACK_AUDIT_ACTIONS[row.kind],
            actor_user_id=user_id,
            from_version=from_version,
            to_version=to_version,
            reason=f"Answered agent needs-input #{int(row.id)} ({row.kind})",
            request_id=get_request_id(),
            fields=fields,
            allow_empty_changes=not bool(fields),
        )
        db.flush()


def answer(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    needs_input_id: int,
    response: dict[str, Any],
    expected_version: int,
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

    # Intent parsing can make a slow model call. Authorize and reject an
    # already-stale card before doing that work, but do not hold the shared
    # Role lock while waiting on the provider.
    user, role = _require_authenticated_recruiter_job_permission(
        db,
        actor=actor,
        row=row,
        permission=_answer_permission(row.kind),
        lock_for_update=False,
    )
    assert_role_version(role, expected_version=expected_version)
    prepared_intent_chips = _prepare_intent_chips(
        db,
        row=row,
        role=role,
        response=response,
    )

    # Lock ordering is Role first, then needs-input row. Re-authorize after the
    # optional model call so a team removal cannot race the actual write, and
    # re-check the revision so a concurrent editor wins truthfully with 409.
    db.expire(role)
    user, role = _require_authenticated_recruiter_job_permission(
        db,
        actor=actor,
        row=row,
        permission=_answer_permission(row.kind),
    )
    assert_role_version(role, expected_version=expected_version)
    row = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.id == needs_input_id,
            AgentNeedsInput.organization_id == organization_id,
        )
        .with_for_update(of=AgentNeedsInput)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="needs_input row not found")
    if row.resolved_at is not None:
        raise HTTPException(status_code=409, detail="already answered")
    if row.dismissed_at is not None:
        raise HTTPException(status_code=409, detail="already dismissed")
    if requires_external_resolution(row.kind):
        raise HTTPException(
            status_code=422,
            detail=EXTERNAL_RESOLUTION_DETAIL,
        )

    row.resolved_at = datetime.now(timezone.utc)
    row.response = response
    row.resolved_by_user_id = int(user.id)
    db.flush()

    # Promote the answer into canonical role state. Failures are logged
    # but don't undo the answer — the recruiter sees their reply resolved
    # either way, and a follow-up cycle can re-derive structure from the
    # raw response if needed.
    try:
        _apply_versioned_recruiter_answer(
            db,
            row=row,
            role=role,
            response=response,
            user_id=int(user.id),
            prepared_intent_chips=prepared_intent_chips,
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
    role: Role,
    response: dict[str, Any],
    user_id: Optional[int],
    prepared_intent_chips: list[Any] | None = None,
) -> bool:
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
        return False
    text_value = str(value).strip()
    if not text_value:
        return False

    if row.kind == "threshold_ambiguous":
        if not _writeback_threshold(role, text_value):
            return False
        db.flush()
        return True

    if row.kind == "monthly_budget_missing":
        if not _writeback_budget(role, text_value):
            return False
        # A role with no explicit budget still runs against the default cap
        # and can pause on it. Answering this card with a high-enough number
        # is the same "raise the cap" intent as the settings PATCH, so clear
        # a budget-pause that the new budget now covers. Pure mutation on the
        # same role; the existing flush persists it and the next hourly cohort
        # beat picks the role back up. Mirrors the PATCH-route resume.
        from ..agent_runtime import budget_guard

        budget_guard.resume_if_under_budget(db, role=role)
        db.flush()
        return True

    if row.kind in ("intent_slot_missing", "intent_clarification"):
        _writeback_intent(
            db,
            role=role,
            row=row,
            answer_text=text_value,
            user_id=user_id,
            chips=prepared_intent_chips,
        )
        db.flush()
        return True

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
            return True
        return False

    return False


def _writeback_threshold(role: Role, raw: str) -> bool:
    try:
        n = int(float(raw.strip()))
    except (TypeError, ValueError):
        return False
    role.score_threshold = max(0, min(100, n))
    return True


def _writeback_budget(role: Role, raw: str) -> bool:
    cleaned = raw.strip().lstrip("$").replace(",", "")
    try:
        n = float(cleaned)
    except (TypeError, ValueError):
        return False
    # Recruiters answer this question in dollars per month ("$50",
    # "2000"), always. The field is stored in cents, so just scale by
    # 100. The old "small number is dollars, large number is cents"
    # heuristic mangled any genuine budget over $1000 — "2000" ($2000/mo)
    # was stored as 2000 cents = $20.
    role.monthly_usd_budget_cents = max(0, int(round(n * 100)))
    return True


def _writeback_intent(
    db: Session,
    *,
    role: Role,
    row: AgentNeedsInput,
    answer_text: str,
    user_id: Optional[int],
    chips: list[Any] | None,
) -> None:
    from ..agent_runtime.contracts import StructuredIntent
    from ..agent_runtime.role_intent import (
        author_new_version,
        fetch_active_intent,
    )

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

    # 2. Add chips prepared before the Role lock was acquired. A parser
    #    failure deliberately yields no chips; the free-text version above
    #    remains the canonical answer.
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

    if actor.type == ACTOR_RECRUITER:
        _require_authenticated_recruiter_job_permission(
            db,
            actor=actor,
            row=row,
            permission=JobPermission.CONTROL_AGENT,
        )

    row = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.id == needs_input_id,
            AgentNeedsInput.organization_id == organization_id,
        )
        .with_for_update(of=AgentNeedsInput)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="needs_input row not found")
    if row.resolved_at is not None or row.dismissed_at is not None:
        return row  # idempotent — already closed
    row.dismissed_at = datetime.now(timezone.utc)
    db.flush()
    return row
