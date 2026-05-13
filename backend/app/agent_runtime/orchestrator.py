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
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models.agent_run import AgentRun
from ..models.organization import Organization
from ..models.role import Role
from ..platform.config import settings
from ..services.claude_client_resolver import get_client_for_org
from ..services.pricing_service import Feature
from ..services.usage_metering_service import record_event
from . import budget_guard, calibration
from .system_prompt import PROMPT_VERSION, build_system_prompt
from .tool_registry import AGENT_TOOLS, QUEUE_DECISION_TOOL_NAMES, dispatch, is_run_complete


logger = logging.getLogger("taali.agent_runtime")


# Tool surface in v3 has 13 tools. Bumping rounds up gives the agent enough
# headroom to chain a cohort search → compare → decision sequence. Each round
# is still capped to MAX_TOKENS_PER_ROUND, and the per-cycle token + decision
# budgets in budget_guard.py provide hard ceilings independent of round count.
MAX_TOOL_ROUNDS = 10
MAX_TOKENS_PER_ROUND = 2048


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
            "  sitting. Mapping:\n"
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
            "  reject_threshold or clear must-have failure) do not — those\n"
            "  judgements stand without a sending bar.\n\n"
            "Step 2 — dispatch backlog (fire-and-forget):\n"
            "  If survey.needs_score > 0: find_apps_in_state(state='needs_score',\n"
            "    limit=25) → batch_score_cv with those ids. Scoring runs async\n"
            "    on a separate queue; it doesn't block this cycle.\n\n"
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
            "    - clearly below role.reject_threshold (e.g. 30+ points below)\n"
            "      OR missing must-haves → queue_reject_decision or\n"
            "      queue_skip_assessment_reject_decision. LOWER RISK: queue up\n"
            "      to 5 per cycle when the signal is clear. These do NOT need\n"
            "      effective_score_threshold to fire — reject_threshold and\n"
            "      must-haves are independent.\n"
            "    - borderline → skip this cycle.\n"
            "  Always run evaluate_policy before each queue_* call. If the\n"
            "  policy returns abstain / skip_due_to_missing_config for a\n"
            "  candidate, skip them silently and move to the next.\n\n"
            "Rules:\n"
            "  - ≤ 1 send_assessment or queue_advance_decision per cycle.\n"
            "  - ≤ 5 reject decisions per cycle (queue_reject_decision +\n"
            "    queue_skip_assessment_reject_decision combined). Recruiter\n"
            "    reviews them in batch.\n"
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
            "3. For each candidate worth acting on: if score is fresh and clear, "
            "queue/auto-execute; if borderline, use compare_applications or "
            "get_cohort_signals before deciding; if no score yet, score_cv "
            "and end the cycle (next cycle can act once it lands).\n"
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
            "4. If clearly above-threshold and at the right stage, call send_assessment "
            "(if still in CV review) or queue_advance_decision (if assessment is done).\n"
            "5. If clearly below-threshold and missing requirements, queue_reject_decision "
            "or queue_skip_assessment_reject_decision.\n"
            "6. Always end with agent_run_complete."
        )
    return (
        "Cycle tick — no specific application focus. Use search_applications "
        "to find ready candidates (e.g. min_score=70 in stage='review'), then "
        "act on at most one of them. Always end with agent_run_complete."
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
    # Role has no `organization` backref defined on the model — fetch directly.
    org = db.query(Organization).filter(Organization.id == role.organization_id).first()
    if org is None:
        raise ValueError(f"role {role.id} has no organization")

    client = get_client_for_org(org)
    # Per-role override (Sonnet for borderline-judgment roles, etc.). Falls
    # back to the global setting when unset.
    role_model = (role.agent_model or "").strip() if isinstance(role.agent_model, str) else ""
    model = role_model or settings.resolved_claude_model

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
    db.flush()  # populate run.id so tools can stamp it

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

    for round_idx in range(MAX_TOOL_ROUNDS):
        check = budget_guard.check_pre_round(
            role=role,
            tokens_used=run.input_tokens + run.output_tokens,
            decisions_emitted=run.decisions_emitted,
        )
        if not check.ok:
            run.status = "budget_paused"
            run.error = check.reason
            budget_guard.pause_role(db, role=role, reason=check.reason or "budget exhausted")
            break

        system = build_system_prompt(
            role=role,
            trigger_context=trigger_context,
            budget_remaining_tokens=max(
                0, budget_guard.role_token_budget(role) - (run.input_tokens + run.output_tokens)
            ),
            decision_budget_remaining=max(
                0, budget_guard.role_decision_budget(role) - run.decisions_emitted
            ),
        )

        try:
            response = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS_PER_ROUND,
                system=system,
                tools=AGENT_TOOLS,
                messages=messages,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("agent_runtime: anthropic call failed role=%s", role.id)
            run.status = "failed"
            run.error = f"anthropic call failed: {exc}"
            break

        usage = getattr(response, "usage", None)
        round_input = int(getattr(usage, "input_tokens", 0) or 0)
        round_output = int(getattr(usage, "output_tokens", 0) or 0)
        round_cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        round_cache_creation = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)

        run.input_tokens += round_input
        run.output_tokens += round_output
        run.cache_read_tokens += round_cache_read
        run.cache_creation_tokens += round_cache_creation

        try:
            event = record_event(
                db,
                organization_id=role.organization_id,
                role_id=int(role.id),
                feature=Feature.AGENT_AUTONOMOUS,
                model=model,
                input_tokens=round_input,
                output_tokens=round_output,
                cache_read_tokens=round_cache_read,
                cache_creation_tokens=round_cache_creation,
                user_id=None,
                entity_id=str(role.id),
                metadata={"agent_run_id": int(run.id), "round": int(round_idx)},
            )
            run.total_cost_micro_usd += int(getattr(event, "cost_usd_micro", 0) or 0)
        except Exception:  # pragma: no cover — never let metering kill the cycle
            logger.exception("agent_runtime: usage_metering record_event failed")

        assistant_blocks = [_block_to_dict(b) for b in (response.content or [])]
        messages.append({"role": "assistant", "content": assistant_blocks})

        if getattr(response, "stop_reason", None) != "tool_use":
            break

        tool_results: list[dict[str, Any]] = []
        run_complete_payload: Optional[dict[str, Any]] = None

        for block in assistant_blocks:
            if block.get("type") != "tool_use":
                continue
            tool_use_id = str(block.get("id", ""))
            name = str(block.get("name", ""))
            args = block.get("input") or {}
            tools_called_summary[name] = tools_called_summary.get(name, 0) + 1

            try:
                result = dispatch(name, args, db=db, agent_run=run, role=role)
                if name in QUEUE_DECISION_TOOL_NAMES:
                    run.decisions_emitted += 1
                if is_run_complete(result):
                    run_complete_payload = result
                is_error = False
            except Exception as exc:
                logger.exception("agent_runtime: tool %s failed", name)
                result = {"error": str(exc), "tool": name}
                is_error = True

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(result, default=str),
                    "is_error": is_error,
                }
            )

        messages.append({"role": "user", "content": tool_results})

        if run_complete_payload is not None:
            finished_via_complete_tool = True
            observations = run_complete_payload.get("observations") or {}
            calibration.save(
                db,
                role=role,
                updates={
                    "decisions_total": run.decisions_emitted,
                    **(observations if isinstance(observations, dict) else {}),
                },
            )
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

    # Persist the per-cycle decisions_total even when the cycle didn't
    # call agent_run_complete — otherwise aborted / failed cycles lose
    # their feedback signal and calibration drifts over time. Only the
    # complete-tool branch carries observations; on other paths we save
    # the bare decisions count.
    if not finished_via_complete_tool:
        try:
            calibration.save(
                db,
                role=role,
                updates={"decisions_total": run.decisions_emitted},
            )
        except Exception:  # pragma: no cover — calibration save must never break the cycle
            logger.exception(
                "calibration.save on non-complete terminal failed role=%s run=%s",
                role.id,
                getattr(run, "id", None),
            )

    run.tools_called = [{"name": n, "count": c} for n, c in tools_called_summary.items()]
    run.finished_at = datetime.now(timezone.utc)
    role.agent_last_run_at = run.finished_at
    db.add(role)
    db.flush()
    return run
