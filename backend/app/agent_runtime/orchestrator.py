"""One-cycle autonomous orchestrator.

Mirrors the shape of ``app.taali_chat.service.run_chat_turn`` but:
- non-streaming (``client.messages.create``, not ``stream``)
- no persistent conversation — each cycle rebuilds messages from scratch
- bounded by ``MAX_TOOL_ROUNDS`` per cycle and per-job budgets
- writes one ``AgentRun`` row instead of ``TaaliChatMessage`` rows
- records ``UsageEvent`` with ``Feature.AGENT_AUTONOMOUS``
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..agent_chat.events import try_post_agent_run_event
from ..candidate_search.tool_failure_contract import (
    CANDIDATE_SEARCH_UNAVAILABLE_CODE,
    candidate_search_result_failed,
    candidate_search_tools_first,
    is_candidate_search_tool,
    new_candidate_search_incident_id,
    unexpected_tool_failure_result,
)
from ..models.agent_run import AgentRun
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.organization import Organization
from ..models.role import Role
from ..platform.config import settings
from ..llm import CallUsage, MeteringContext, one_call
from ..services.claude_client_resolver import get_client_for_org
from ..services.pricing_service import Feature, raw_cost_usd_micro
from ..services.provider_usage_admission import (
    release_provider_usage,
    reserve_provider_usage,
)
from ..services.usage_credit_reservations import InsufficientRoleBudgetError
from ..services.usage_metering_service import InsufficientCreditsError
from . import budget_guard, calibration, data_readiness
from .system_prompt import PROMPT_VERSION, build_system_prompt
from .tool_registry import (
    QUEUE_DECISION_TOOL_NAMES as QUEUE_DECISION_TOOL_NAMES,
    dispatch,
    is_run_complete,
    tools_for_role,
)


logger = logging.getLogger("taali.agent_runtime")


# The governed tool surface has 26 tools. Bumping rounds up gives the agent enough
# headroom to chain a cohort search → compare → decision sequence. Each round
# is still capped to MAX_TOKENS_PER_ROUND, and the per-cycle token + decision
# budgets in budget_guard.py provide hard ceilings independent of round count.
# 10 was producing aborts mid-deliberation on roles with rich cohorts — see
# the agent_runs table on roles 31/112. 18 leaves headroom for survey →
# read → batch_score → 2-3 evaluate_policy → queue → complete.
MAX_TOOL_ROUNDS = 18
MAX_TOKENS_PER_ROUND = 2048
MAX_IDENTICAL_TOOL_ROUNDS = 2
MAX_CONSECUTIVE_ERROR_ROUNDS = 2


def _cycle_tokens(run: AgentRun) -> int:
    return int(
        (run.input_tokens or 0)
        + (run.output_tokens or 0)
        + (run.cache_read_tokens or 0)
        + (run.cache_creation_tokens or 0)
    )


def _tool_round_signature(blocks: list[dict[str, Any]]) -> str:
    calls = [
        {"name": block.get("name"), "input": block.get("input") or {}}
        for block in blocks
        if block.get("type") == "tool_use"
    ]
    return json.dumps(calls, sort_keys=True, separators=(",", ":"), default=str)


def _emit_cycle_abort_event(
    db: Session,
    *,
    run: AgentRun,
    application_id: Optional[int],
    reason: str,
) -> None:
    """C6: emit a CandidateApplicationEvent so the recruiter sees the
    abort in the candidate timeline. Without this signal, aborted /
    budget_paused / failed cycles are invisible until the recruiter
    digs into the AgentRun table — silent failure is the worst trust
    killer.

    Only emits when there's a focus application — cron triggers without
    a specific candidate are surfaced via the role-level banner on the
    Decision Hub (driven by AgentRun.status), not per-candidate events.
    Failure here is logged and swallowed; never breaks the run path.
    """
    if application_id is None:
        return
    try:
        idempotency_key = f"agent_cycle_aborted:run:{int(run.id) if run.id else 0}"
        db.add(
            CandidateApplicationEvent(
                application_id=int(application_id),
                organization_id=int(run.organization_id),
                event_type="agent_cycle_aborted",
                actor_type="agent",
                actor_id=int(run.id) if run.id else None,
                reason=reason,
                idempotency_key=idempotency_key,
                event_metadata={
                    "agent_run_id": int(run.id) if run.id else None,
                    "status": str(run.status),
                    "trigger": str(run.trigger),
                },
            )
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "agent_cycle_aborted event emit failed run_id=%s app_id=%s",
            getattr(run, "id", None), application_id,
        )


def _block_to_dict(block: Any) -> dict[str, Any]:
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", {}) or {},
        }
    return {"type": btype or "unknown"}


def _initial_user_message(*, trigger: str, application_id: Optional[int]) -> str:
    if trigger == "cron" and application_id is None:
        return (
            "Proactive sweep across the role. Tight loop — survey, act, complete.\n"
            "No exploration spirals.\n\n"
            "Step 0 — survey:\n"
            "  survey_role_state + read_pending_recruiter_inputs (one round).\n\n"
            "Step 1 — SURFACE MISSING CONFIG (don't block on it):\n"
            "  Look at survey.intent_gaps. For EVERY entry not already covered\n"
            "  by an open recruiter question, call ask_recruiter — one call\n"
            "  per gap, all in the same cycle. The recruiter filters the Home\n"
            "  hub by role, so don't worry about flooding; ask everything you\n"
            "  need to ask now so the recruiter can answer the full set in one\n"
            "  sitting. The single role-fit boundary the engine rejects /\n"
            "  advances on is survey.effective_role_fit_threshold — reason\n"
            "  against THAT, never role.reject_threshold (the engine ignores\n"
            "  it). Mapping:\n"
            "    - 'score_threshold is unset'          → threshold_ambiguous\n"
            "    - 'monthly_usd_budget_cents is unset' → monthly_budget_missing\n"
            "    - 'no must-have requirements captured' / 'no job spec attached'\n"
            "                                          → intent_slot_missing\n"
            "  AND if survey.role_intent_shape looks thin in a specific way\n"
            "  the deterministic gaps don't catch — e.g. must_count == 0 with\n"
            "  some preferreds, must-haves listed but no seniority/location\n"
            "  signal, or constraints_count == 0 for a role that obviously\n"
            "  needs them — also call ask_recruiter with kind='intent_clarification'\n"
            "  and YOUR OWN specific question. Quote the existing chips and\n"
            "  the dimension you think is missing so the recruiter can fill\n"
            "  the gap without re-typing what's already captured. All\n"
            "  ask_recruiter calls are idempotent on (role_id, kind) so\n"
            "  re-asking the same question refines the existing card rather\n"
            "  than spawning new ones.\n"
            "  Then KEEP GOING. Asking questions never halts the cycle. Sends\n"
            "  and advances need a score_threshold, but rejects (against\n"
            "  effective_role_fit_threshold or clear must-have failure) do not\n"
            "  — those judgements stand without a sending bar.\n\n"
            "Step 2 — dispatch backlog (fire-and-forget):\n"
            "  If survey.needs_score > 0: find_apps_in_state(state='needs_score',\n"
            "    limit=25) → batch_score_cv with those ids. Scoring runs async\n"
            "    on a separate queue; it doesn't block this cycle.\n"
            "  If survey.needs_pre_screen > 0 (and needs_score == 0): the\n"
            "    cv_score_orchestrator runs pre-screen automatically as part of\n"
            "    scoring, so find_apps_in_state(state='needs_pre_screen',\n"
            "    limit=25) → batch_score_cv on those ids dispatches both.\n\n"
            "Step 3 — TRIAGE A BATCH then END:\n"
            "  find_apps_in_state(state='ready_for_assessment_decision', limit=20).\n"
            "  The list is sorted by cv_match_score desc and excludes candidates\n"
            "  who already have a pending decision — so you see fresh, high-signal\n"
            "  applications each cycle. Use survey.effective_score_threshold as\n"
            "  your advance bar (this folds in role.score_threshold OR the\n"
            "  recruiter's most recent answer to threshold_ambiguous). For each\n"
            "  id, in order, decide quickly:\n"
            "    - clearly above effective_score_threshold (skip this branch\n"
            "      only when effective_score_threshold is null — wait for the\n"
            "      recruiter to answer) → send_assessment or\n"
            "      queue_advance_decision. HIGH RISK: only queue ONE send/advance\n"
            "      per cycle.\n"
            "    - clearly below effective_role_fit_threshold (e.g. 20+ points\n"
            "      below) OR missing must-haves → queue_reject_decision or\n"
            "      queue_skip_assessment_reject_decision. LOWER RISK: queue up\n"
            "      to 5 per cycle when the signal is clear. These fire off\n"
            "      effective_role_fit_threshold (the engine's boundary) and\n"
            "      must-haves — independent of any send bar.\n"
            "    - borderline → skip this cycle.\n"
            "  Always run evaluate_policy before each queue_* call. If the\n"
            "  policy returns escalate_low_confidence, call\n"
            "  queue_escalate_decision with its reasoning and confidence, using\n"
            "  rule_path / conflicting sub-agent signals as evidence, so the\n"
            "  recruiter adjudicates. If it returns\n"
            "  skip / no_action, skip the candidate and move to the next.\n\n"
            "Rules:\n"
            "  - ≤ 1 send_assessment or queue_advance_decision per cycle.\n"
            "  - ≤ 5 reject decisions per cycle (queue_reject_decision +\n"
            "    queue_skip_assessment_reject_decision combined). Recruiter\n"
            "    reviews them in batch.\n"
            "  - queue_escalate_decision counts against the overall decision\n"
            "    budget but neither risk cap; it never acts on the candidate.\n"
            "  - ask_recruiter is unbounded per cycle — surface every gap at\n"
            "    once. Idempotency on (role_id, kind) prevents duplicates.\n"
            "  - Don't call compare_applications / get_cohort_signals /\n"
            "    get_application unless evaluate_policy returns 'borderline'.\n"
            "    Default to the score signal.\n"
            "  - End with agent_run_complete summarising what you queued.\n"
            "    Aborting (MAX_TOOL_ROUNDS) is a failure — never leave the\n"
            "    cycle hanging."
        )
    if application_id is not None and trigger == "event":
        return (
            f"Event-triggered cycle. The most recent applicant is "
            f"application_id={application_id}, but events are debounced — other "
            f"applications for this role may have arrived in the same window. "
            f"Suggested flow:\n"
            "1. get_application on the focus id.\n"
            "2. search_applications (stage='applied' or 'review', sort_by=created_at desc) "
            "to surface any other recent arrivals worth a look.\n"
            "3. For each candidate worth acting on: if the score is fresh, call "
            "evaluate_policy once and queue its matching verdict. If it returns "
            "escalate_low_confidence, call queue_escalate_decision so the recruiter "
            "adjudicates. If the score is borderline, use compare_applications or "
            "get_cohort_signals before deciding; if no score exists yet, score_cv "
            "and end the cycle (the next cycle can act once it lands).\n"
            "4. Stay within the per-cycle decision budget — at most one queued "
            "decision per cycle.\n"
            "5. End with agent_run_complete."
        )
    if application_id is not None:
        return (
            f"Focus on application_id={application_id}.\n\n"
            "Suggested flow:\n"
            "1. get_application — read its scores, stage, evidence.\n"
            "2. If no fresh CV-match score, call score_cv and then agent_run_complete "
            "(the next cycle can act on the result).\n"
            "3. If the score is borderline, use compare_applications or "
            "get_cohort_signals to see how this candidate ranks against the cohort "
            "before deciding to advance or reject.\n"
            "4. Call evaluate_policy once before any queue tool. If it returns "
            "escalate_low_confidence, call queue_escalate_decision with its reasoning.\n"
            "5. If clearly above-threshold and the policy agrees, call send_assessment "
            "(if still in CV review) or queue_advance_decision (if assessment is done).\n"
            "6. If clearly below-threshold and the policy agrees, queue_reject_decision "
            "or queue_skip_assessment_reject_decision.\n"
            "7. Always end with agent_run_complete."
        )
    return (
        "Cycle tick — no specific application focus. Use search_applications "
        "to find ready candidates (e.g. min_score=70 in stage='review'), then "
        "call evaluate_policy and act on at most one. If it returns "
        "escalate_low_confidence, queue_escalate_decision rather than dropping "
        "the candidate. Always end with agent_run_complete."
    )


def run_cycle(
    db: Session,
    *,
    role: Role,
    trigger: str,
    application_id: Optional[int] = None,
    trigger_event_id: Optional[int] = None,
) -> AgentRun:
    """Run one autonomous cycle for ``role``. Returns the persisted ``AgentRun``.

    Side effects: creates one ``AgentRun`` row, may insert ``AgentDecision``
    rows (via ``queue_*`` tools), may enqueue ``CvScoreJob``s, records
    ``UsageEvent``s for each Anthropic call. The caller commits the
    session — we ``flush`` at boundaries so ids populate, but never
    ``commit`` ourselves.
    """
    # C1: prevent overlapping cycles for the same role across ALL trigger
    # paths (cron tick, event-driven, manual). Two layers working together:
    #   1. A Postgres TRANSACTION-scoped advisory lock serialises the
    #      check-and-create critical section so two cycles racing in at the
    #      same instant can't both pass the in-flight check (TOCTOU).
    #      SQLite (tests) is single-threaded, so the lock is a no-op there.
    #   2. An in-flight check: if a running AgentRun for this role started
    #      in the last 15 min, abort. ``run_cycle`` commits the running row
    #      early (so the watchdog can observe a crashed worker), which
    #      RELEASES the xact lock — so it's this committed row, not the
    #      lock, that guards subsequent cycles once the lock is gone. The
    #      lock only has to hold long enough to make the check-and-create
    #      atomic against a simultaneous racer.
    # The 10-min watchdog (agent_expire_stuck_runs) reaps genuinely-stuck
    # running rows so a crashed worker can't block the role forever.
    is_postgres = db.bind is not None and db.bind.dialect.name == "postgresql"
    lock_acquired = True
    if is_postgres:
        from sqlalchemy import text as _sql_text
        # Lock key derived from a stable hash of ('agent_run', role_id)
        # so concurrent cycles for the same role compete for the same
        # lock without conflicting with other unrelated lock keys.
        lock_acquired = bool(
            db.execute(
                _sql_text("SELECT pg_try_advisory_xact_lock(hashtext('agent_run'), :role_id)"),
                {"role_id": int(role.id)},
            ).scalar()
        )

    in_flight = None
    if lock_acquired:
        in_flight = (
            db.query(AgentRun.id)
            .filter(
                AgentRun.role_id == role.id,
                AgentRun.status == "running",
                AgentRun.started_at > datetime.now(timezone.utc) - timedelta(minutes=15),
            )
            .first()
        )

    if not lock_acquired or in_flight is not None:
        import logging
        logging.getLogger("taali.agent_runtime.orchestrator").info(
            "cycle_skipped_overlap role_id=%s trigger=%s lock_acquired=%s in_flight=%s",
            role.id, trigger, lock_acquired,
            int(in_flight[0]) if in_flight is not None else None,
        )
        run = AgentRun(
            organization_id=role.organization_id,
            role_id=role.id,
            trigger=trigger,
            trigger_event_id=trigger_event_id,
            status="aborted",
            error="skipped_overlap",
            model_version=settings.resolved_claude_model,
            prompt_version=PROMPT_VERSION,
            finished_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        _emit_cycle_abort_event(
            db, run=run, application_id=application_id,
            reason="Agent cycle skipped — another cycle was already running for this role.",
        )
        try_post_agent_run_event(db, role=role, run=run)
        return run

    # Role has no `organization` backref defined on the model — fetch directly.
    org = db.query(Organization).filter(Organization.id == role.organization_id).first()
    if org is None:
        raise ValueError(f"role {role.id} has no organization")

    from ..services.workspace_agent_control import (
        workspace_agent_control_snapshot,
    )

    workspace_paused, cycle_workspace_version = workspace_agent_control_snapshot(
        db,
        organization_id=int(role.organization_id),
    )
    if workspace_paused:
        run = AgentRun(
            organization_id=role.organization_id,
            role_id=role.id,
            trigger=trigger,
            trigger_event_id=trigger_event_id,
            status="aborted",
            error="workspace_paused_before_cycle",
            model_version=settings.resolved_agent_autonomous_model,
            prompt_version=PROMPT_VERSION,
            finished_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        _emit_cycle_abort_event(
            db,
            run=run,
            application_id=application_id,
            reason="Agent cycle skipped — the workspace agent is paused.",
        )
        try_post_agent_run_event(db, role=role, run=run)
        return run

    # Per-role override (Sonnet for borderline-judgment roles, etc.) wins; else
    # the autonomous-loop model (cheaper than the interactive agent by default —
    # this loop is ~92% no-op/fail and the clear decisions are deterministic).
    role_model = (role.agent_model or "").strip() if isinstance(role.agent_model, str) else ""
    model = role_model or settings.resolved_agent_autonomous_model

    monthly = budget_guard.check_monthly_usd(db, role=role)
    if not monthly.ok:
        budget_guard.pause_role(db, role=role, reason=monthly.reason or "monthly cap reached")
        run = AgentRun(
            organization_id=role.organization_id,
            role_id=role.id,
            trigger=trigger,
            trigger_event_id=trigger_event_id,
            status="budget_paused",
            error=monthly.reason,
            model_version=model,
            prompt_version=PROMPT_VERSION,
            finished_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        _emit_cycle_abort_event(
            db, run=run, application_id=application_id,
            reason=(
                "Agent paused — monthly budget cap reached for this role. "
                "Raise the monthly cap above current spend to resume."
            ),
        )
        try_post_agent_run_event(db, role=role, run=run)
        return run

    # Data-readiness gate: never spend Claude tokens on a role with no job
    # spec. Aborts BEFORE the first Anthropic call ($0) and raises a HITL
    # item so the recruiter knows to add one; resolves automatically on the
    # next cycle that finds a spec. (See agent_runtime.data_readiness.)
    if not data_readiness.has_job_spec(role):
        data_readiness.raise_missing_job_spec(db, role=role)
        run = AgentRun(
            organization_id=role.organization_id,
            role_id=role.id,
            trigger=trigger,
            trigger_event_id=trigger_event_id,
            status="aborted",
            error="missing_job_spec",
            model_version=model,
            prompt_version=PROMPT_VERSION,
            finished_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        _emit_cycle_abort_event(
            db, run=run, application_id=application_id,
            reason=(
                "Agent held — this role has no job description. Add a job spec "
                "(or sync it from Workable) and the agent resumes automatically."
            ),
        )
        try_post_agent_run_event(db, role=role, run=run)
        return run

    # Job spec present: clear any stale missing-spec item and surface (or
    # clear) the count of candidates the agent can't act on for lack of a CV.
    data_readiness.resolve_open(db, role=role, kind="missing_job_spec")
    data_readiness.sync_cv_readiness(db, role=role)

    # Resolve provider access only after the free budget/readiness gates. A
    # configuration failure must still become durable role state (and a safe
    # chat event), but the underlying exception may contain credentials or
    # provider internals and therefore belongs only in server logs.
    try:
        client = get_client_for_org(org)
    except Exception:
        logger.exception(
            "agent model client resolution failed role_id=%s org_id=%s",
            role.id,
            role.organization_id,
        )
        run = AgentRun(
            organization_id=role.organization_id,
            role_id=role.id,
            trigger=trigger,
            trigger_event_id=trigger_event_id,
            status="failed",
            error="model_client_unavailable",
            model_version=model,
            prompt_version=PROMPT_VERSION,
            finished_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        _emit_cycle_abort_event(
            db,
            run=run,
            application_id=application_id,
            reason="Agent cycle failed before model access was available.",
        )
        try_post_agent_run_event(db, role=role, run=run)
        return run

    snapshot = calibration.load(role)
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger=trigger,
        trigger_event_id=trigger_event_id,
        status="running",
        model_version=model,
        prompt_version=PROMPT_VERSION,
        agent_state_snapshot=snapshot,
    )
    db.add(run)
    # Commit the "running" row immediately so the watchdog
    # (agent_expire_stuck_runs) can observe it if the worker crashes
    # mid-cycle. Otherwise the row sits in the worker's transaction
    # un-committed and the watchdog's status='running' scan finds
    # nothing (Codex #188). Tool-side flushes after this still need
    # explicit commits at the task wrapper boundary — that's where
    # the terminal status update lands.
    db.flush()  # populate run.id so tools can stamp it
    db.commit()
    # commit() expires loaded objects by default; refresh both so
    # subsequent mutations stay attached and tracked.
    db.refresh(run)
    db.refresh(role)
    cycle_role_version = int(getattr(role, "version", 1) or 1)

    def _control_state_abort_reason(*, lock: bool = False) -> str | None:
        """Re-read, and optionally lock, the shared control-state boundary."""

        # Workspace control is the outer execution authority and therefore the
        # first lock.  A Pause that commits before this fence is observed; one
        # that starts after it waits for this one tool transaction and wins
        # before the next tool/provider round.
        current_workspace_paused, current_workspace_version = (
            workspace_agent_control_snapshot(
                db,
                organization_id=int(role.organization_id),
                lock=lock,
            )
        )
        if current_workspace_paused:
            return "workspace_paused_during_cycle"
        if int(current_workspace_version) != int(cycle_workspace_version):
            return "workspace_control_changed_during_cycle"

        control_query = db.query(Role).filter(
            Role.id == int(role.id),
            Role.organization_id == int(role.organization_id),
            Role.deleted_at.is_(None),
        )
        if lock:
            # Linearize each tool with UI/chat power changes. A turn-off that
            # commits first is observed here; one that starts after this lock
            # waits for this single tool transaction and wins before the next.
            control_query = control_query.with_for_update(of=Role)
        current_role = control_query.populate_existing().first()
        if current_role is None:
            return "role_unavailable_during_cycle"
        if not bool(current_role.agentic_mode_enabled):
            return "agent_disabled_during_cycle"
        if current_role.agent_paused_at is not None:
            return "agent_paused_during_cycle"
        if int(getattr(current_role, "version", 1) or 1) != cycle_role_version:
            return "role_configuration_changed_during_cycle"
        return None

    trigger_context = (
        f"{trigger} → application_id={application_id}"
        if application_id is not None
        else f"{trigger} → no specific focus"
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": _initial_user_message(trigger=trigger, application_id=application_id),
        }
    ]

    tools_called_summary: dict[str, int] = {}
    finished_via_complete_tool = False
    run_complete_observations: dict[str, Any] = {}
    rounds_used = 0
    previous_tool_round_signature: str | None = None
    identical_tool_rounds = 0
    consecutive_error_rounds = 0

    # Build the system prompt ONCE per cycle, not per round. Its content
    # (role spec, criteria, intent, recruiter notes, calibration) is fixed
    # for the duration of a cycle — the agent's mid-cycle observations land
    # in the message history, not the system blocks. Rebuilding it every
    # round re-ran ~4s of slow queries (_render_role_intent ~2s +
    # _render_recruiter_feedback_notes ~2s on role 31's data) up to 18×,
    # i.e. ~70s+ of pure DB work per cycle that, under connection
    # contention, ballooned into the 600s+ pre-LLM "0-token" hangs that the
    # Anthropic timeout couldn't catch (the stall isn't in the LLM call).
    # Building once also makes the prompt-cache blocks (B2) genuinely
    # static across rounds, so rounds 2-18 hit cache cleanly.
    system = build_system_prompt(
        role=role,
        trigger_context=trigger_context,
    )

    role_tools = tools_for_role(role)

    for round_idx in range(MAX_TOOL_ROUNDS):
        rounds_used = round_idx + 1

        control_abort = _control_state_abort_reason()
        if control_abort is not None:
            run.status = "aborted"
            run.error = control_abort
            break

        # Re-check every paid round.  A long cycle must not spend past a cap
        # that another worker reached after this cycle's initial preflight.
        monthly = budget_guard.check_monthly_usd(db, role=role)
        if not monthly.ok:
            budget_guard.pause_role(db, role=role, reason=monthly.reason or "monthly cap reached")
            run.status = "budget_paused"
            run.error = monthly.reason or "monthly cap reached"
            break

        token_budget = getattr(role, "agent_token_budget_per_cycle", None)
        if token_budget is not None and _cycle_tokens(run) >= int(token_budget):
            run.status = "aborted"
            run.error = f"per-cycle token budget reached ({int(token_budget)})"
            break

        reservation = None
        try:
            reservation = reserve_provider_usage(
                organization_id=int(role.organization_id),
                role_id=int(role.id),
                feature=Feature.AGENT_AUTONOMOUS,
                trace_id=f"agent-run:{int(run.id)}:round:{int(round_idx)}",
                entity_id=str(role.id),
                sub_feature="agent_autonomous_round",
                metadata={
                    "agent_run_id": int(run.id),
                    "round": int(round_idx),
                },
            )
        except InsufficientCreditsError as exc:
            credit_reason = (
                "usage credits exhausted: "
                f"need {exc.required}, have {exc.available}; top up to resume"
            )
            # This is an organization-level spend stop, not a transient LLM
            # failure. Persist the same durable hold used by scoring so future
            # cohort/event tasks cannot repeatedly enter the paid phase while
            # the ledger is empty.
            budget_guard.pause_role(db, role=role, reason=credit_reason)
            run.status = "budget_paused"
            run.error = credit_reason
            break
        except InsufficientRoleBudgetError as exc:
            role_reason = (
                "monthly USD cap admission blocked agent round: "
                f"need {exc.required}, have {exc.available} remaining"
            )
            budget_guard.pause_role(db, role=role, reason=role_reason)
            run.status = "budget_paused"
            run.error = role_reason
            break
        except Exception as exc:
            # A ledger/hold failure is not permission to bypass billing. Leave
            # the role enabled for the scheduled recovery path, but fail this
            # cycle before the provider call.
            logger.exception(
                "agent_runtime: usage reservation failed role=%s round=%s",
                role.id,
                round_idx,
            )
            run.status = "failed"
            run.error = f"usage reservation failed: {exc}"
            break

        # Fresh sink per round for the AgentRun rollup. The metered wrapper
        # independently commits the authoritative UsageEvent + call log.
        round_usage = CallUsage()
        round_metering = MeteringContext(
            feature=Feature.AGENT_AUTONOMOUS,
            organization_id=int(role.organization_id),
            role_id=int(role.id),
            entity_id=str(role.id),
            # Keep one stable trace across every paid round. In particular,
            # the metered wrapper can now correlate SDK-error call logs (which
            # have no UsageEvent) directly back to this AgentRun.
            trace_id=f"agent-run:{int(run.id)}",
            metadata={"agent_run_id": int(run.id), "round": int(round_idx)},
            credit_reservation=reservation.as_metering_payload(),
        )
        try:
            response = one_call(
                client,
                model=model,
                system=system,
                messages=messages,
                max_tokens=MAX_TOKENS_PER_ROUND,
                tools=role_tools,
                metering=round_metering,
                usage_sink=round_usage,
            )
        except Exception as exc:  # pragma: no cover — defensive
            # MeteredAnthropicClient releases SDK-error holds centrally; this
            # idempotent fallback covers injected clients and pre-wrapper
            # failures.
            release_provider_usage(
                reservation,
                reason=f"agent_round_call_failed:{type(exc).__name__}",
            )
            logger.exception("agent_runtime: anthropic call failed role=%s", role.id)
            run.status = "failed"
            run.error = f"anthropic call failed: {exc}"
            break

        run.input_tokens += round_usage.input_tokens
        run.output_tokens += round_usage.output_tokens
        run.cache_read_tokens += round_usage.cache_read_tokens
        run.cache_creation_tokens += round_usage.cache_creation_tokens

        # The metered client already wrote the UsageEvent + ClaudeCallLog in
        # independent committed sessions. Keep the run's raw-cost rollup using
        # the same canonical pricing function without creating a second event.
        run.total_cost_micro_usd += raw_cost_usd_micro(
            model=model,
            input_tokens=round_usage.input_tokens,
            output_tokens=round_usage.output_tokens,
            cache_read_tokens=round_usage.cache_read_tokens,
            cache_creation_tokens=round_usage.cache_creation_tokens,
        )

        # A recruiter may turn the agent off while the provider request is in
        # flight. The call is already metered, but its response must not queue
        # decisions or actions against a now-disabled/stale job snapshot.
        control_abort = _control_state_abort_reason()
        if control_abort is not None:
            run.status = "aborted"
            run.error = control_abort
            break

        # Do not execute actions produced by a response that already pushed the
        # cycle over its configured token ceiling.  The paid call is still
        # durably metered above; the next cycle starts cleanly.
        if token_budget is not None and _cycle_tokens(run) > int(token_budget):
            run.status = "aborted"
            run.error = f"per-cycle token budget exceeded ({_cycle_tokens(run)} > {int(token_budget)})"
            break

        assistant_blocks = [_block_to_dict(b) for b in (response.content or [])]
        messages.append({"role": "assistant", "content": assistant_blocks})

        if getattr(response, "stop_reason", None) != "tool_use":
            break

        tool_results: list[dict[str, Any]] = []
        run_complete_payload: Optional[dict[str, Any]] = None

        # Generic no-progress breaker.  The existing evaluate_policy guard is
        # candidate-specific; this catches every tool, including repeated reads
        # and repeated cache bypasses.  Two retries are permitted; a third
        # identical tool round aborts before any further side effect or spend.
        current_signature = _tool_round_signature(assistant_blocks)
        if current_signature == previous_tool_round_signature:
            identical_tool_rounds += 1
        else:
            identical_tool_rounds = 0
        previous_tool_round_signature = current_signature
        if identical_tool_rounds >= MAX_IDENTICAL_TOOL_ROUNDS:
            run.status = "aborted"
            run.error = "no-progress circuit breaker: repeated identical tool round"
            break

        round_tool_count = 0
        round_error_count = 0
        control_aborted_during_tools = False
        search_failure_incident: str | None = None

        # Persist the provider usage before isolating each tool in its own
        # transaction. This lets a failed tool roll back only its own partial
        # writes and prevents long tool rounds from holding a Role row lock
        # that would delay a recruiter's Turn off request.
        db.commit()
        db.refresh(run)
        db.refresh(role)

        for block in candidate_search_tools_first(assistant_blocks):
            control_abort = _control_state_abort_reason(lock=True)
            if control_abort is not None:
                run.status = "aborted"
                run.error = control_abort
                control_aborted_during_tools = True
                db.commit()
                break
            tool_use_id = str(block.get("id", ""))
            name = str(block.get("name", ""))
            args = block.get("input") or {}
            round_tool_count += 1
            tools_called_summary[name] = tools_called_summary.get(name, 0) + 1
            provider_capable_search = is_candidate_search_tool(name)
            if provider_capable_search:
                # The authority fence above uses FOR UPDATE. Paid search
                # admission owns the same Organization/Role locks in an
                # independent session, so release our fence before dispatch or
                # the worker deadlocks itself waiting in both directions.
                db.commit()

            try:
                result = dispatch(name, args, db=db, agent_run=run, role=role)
                # decisions_emitted is incremented inside _queue when a
                # real AgentDecision row is created — moved out of here
                # so dedup / auto-execute-direct-dispatch paths don't
                # over-count (Codex #179). QUEUE_DECISION_TOOL_NAMES is
                # still exported for tests and prompt docs.
                if is_run_complete(result):
                    run_complete_payload = result
                is_error = False
                if candidate_search_result_failed(name, result):
                    search_failure_incident = new_candidate_search_incident_id()
                    logger.warning(
                        "agent_runtime: candidate search unavailable tool=%s "
                        "incident_id=%s",
                        name,
                        search_failure_incident,
                    )
                    db.rollback()
                    db.refresh(run)
                    db.refresh(role)
                    run.status = "failed"
                    run.error = (
                        f"{CANDIDATE_SEARCH_UNAVAILABLE_CODE}:"
                        f"{search_failure_incident}"
                    )
                    break
                if provider_capable_search:
                    # Make the verified report durable, then re-acquire the
                    # authority fence. A recruiter may have paused/changed the
                    # role while the paid read was in flight; never continue to
                    # a later mutation from stale authority.
                    db.commit()
                    control_abort = _control_state_abort_reason(lock=True)
                    if control_abort is not None:
                        run.status = "aborted"
                        run.error = control_abort
                        control_aborted_during_tools = True
                        db.commit()
                        break
            except Exception:
                incident_id = new_candidate_search_incident_id()
                logger.exception(
                    "agent_runtime: tool %s failed incident_id=%s",
                    name,
                    incident_id,
                )
                if is_candidate_search_tool(name):
                    db.rollback()
                    db.refresh(run)
                    db.refresh(role)
                    search_failure_incident = incident_id
                    run.status = "failed"
                    run.error = f"{CANDIDATE_SEARCH_UNAVAILABLE_CODE}:{incident_id}"
                    break
                result = unexpected_tool_failure_result(
                    tool=name,
                    incident_id=incident_id,
                )
                is_error = True
            if is_error:
                round_error_count += 1
                db.rollback()
            else:
                # One tool is one durable, lock-bounded action. This makes
                # power/config changes linearizable between tool blocks in a
                # single model response instead of only between LLM rounds.
                db.commit()
            db.refresh(run)
            db.refresh(role)

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(result, default=str),
                    "is_error": is_error,
                }
            )

        if control_aborted_during_tools:
            break
        if search_failure_incident is not None:
            break

        messages.append({"role": "user", "content": tool_results})

        if round_tool_count > 0 and round_error_count == round_tool_count:
            consecutive_error_rounds += 1
        else:
            consecutive_error_rounds = 0
        if consecutive_error_rounds >= MAX_CONSECUTIVE_ERROR_ROUNDS:
            run.status = "aborted"
            run.error = "no-progress circuit breaker: consecutive tool-error rounds"
            break

        if run_complete_payload is not None:
            finished_via_complete_tool = True
            observations = run_complete_payload.get("observations") or {}
            if isinstance(observations, dict):
                run_complete_observations = observations
            break

    else:
        run.status = "aborted"
        run.error = run.error or "exceeded MAX_TOOL_ROUNDS without agent_run_complete"

    # The for-else above sets "aborted"; tool exception path sets "failed";
    # complete-tool break leaves status="running" so we promote to
    # "succeeded" here. Status can still be "budget_paused" from the round
    # gate — leave that alone.
    if run.status == "running":
        run.status = "succeeded" if finished_via_complete_tool else "aborted"

    # C6: emit cycle-abort event for non-success terminal statuses so the
    # candidate timeline shows "agent tried but didn't finish" instead of
    # silent failure. Limited to the focus application; role-level aborts
    # without a focus surface via the Hub banner reading AgentRun.status.
    if run.status in ("aborted", "failed", "budget_paused") and application_id is not None:
        reason_map = {
            "aborted": (
                f"Agent cycle aborted after {rounds_used} round(s) — "
                f"did not reach a decision. Will retry on the next tick."
            ),
            "failed": (
                "Agent cycle failed with an error during deliberation. "
                "See agent_runs.error for details."
            ),
            "budget_paused": (
                f"Agent paused mid-cycle — {run.error or 'a spend guard was reached'}. "
                "Resolve the stated hold, then resume the agent."
            ),
        }
        _emit_cycle_abort_event(
            db, run=run, application_id=application_id,
            reason=reason_map[run.status],
        )

    # Persist calibration on every terminal path, not just on
    # agent_run_complete. An aborted cycle still produced observations
    # (scores enqueued, tools tried, rounds spent); not saving them means
    # the next cycle has no memory of what happened. The notes-via-
    # record_observation tool already commits per-call; this is the
    # cycle-summary writeback. Wrapped because calibration.save must
    # never break the cycle's ability to record a finished_at row.
    try:
        calibration.save(
            db,
            role=role,
            updates={
                "decisions_total": run.decisions_emitted,
                **run_complete_observations,
                "last_cycle": {
                    "status": run.status,
                    "rounds_used": rounds_used,
                    "decisions_emitted": int(run.decisions_emitted),
                    "finished_via_complete": finished_via_complete_tool,
                    "error": run.error,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
            },
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "calibration.save on cycle end failed role=%s run=%s",
            role.id,
            getattr(run, "id", None),
        )

    run.tools_called = [{"name": n, "count": c} for n, c in tools_called_summary.items()]
    # B7 step 1: instrument round count so we can histogram p95
    # post-deploy and tune MAX_TOOL_ROUNDS downward if appropriate.
    run.rounds_executed = int(rounds_used)
    run.finished_at = datetime.now(timezone.utc)
    role.agent_last_run_at = run.finished_at
    db.add(role)
    db.flush()

    # Role-level failures and budget stops belong where recruiters already
    # steer the agent: the shared Agent Chat transcript. Publication is
    # idempotent by AgentRun source key and isolated in a savepoint, so it is
    # committed atomically with this terminal status but can never break it.
    try_post_agent_run_event(db, role=role, run=run)

    # Structured cycle summary — picked up by Railway log aggregation so
    # abort rate, rounds-to-completion and $-per-decision are observable
    # without a new metrics table.
    cost_usd = float(run.total_cost_micro_usd or 0) / 1_000_000.0
    logger.info(
        "agent_runtime.cycle_complete "
        "role_id=%s org_id=%s status=%s rounds=%d/%d decisions=%d "
        "input_tokens=%d output_tokens=%d cost_usd=%.4f "
        "finished_via_complete=%s error=%r",
        role.id,
        role.organization_id,
        run.status,
        rounds_used,
        MAX_TOOL_ROUNDS,
        run.decisions_emitted,
        run.input_tokens,
        run.output_tokens,
        cost_usd,
        finished_via_complete_tool,
        run.error,
    )
    return run
