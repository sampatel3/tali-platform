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
from .tool_registry import AGENT_TOOLS, dispatch, is_run_complete


logger = logging.getLogger("taali.agent_runtime")


MAX_TOOL_ROUNDS = 6
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


def _initial_user_message(*, application_id: Optional[int]) -> str:
    if application_id is not None:
        return (
            f"Focus on application_id={application_id}. "
            f"Read it, optionally read its CV, decide whether to recommend "
            f"advancing to a technical interview, and call agent_run_complete when done. "
            f"If the candidate isn't ready (e.g. no CV-match score yet), call score_cv "
            f"and then agent_run_complete — the next cycle can act on the result."
        )
    return (
        "Cycle tick — there's no specific application focus. "
        "Use get_application or score_cv on candidates that look ready, then call agent_run_complete."
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
    org = (
        role.organization
        or db.query(Organization).filter(Organization.id == role.organization_id).first()
    )
    if org is None:
        raise ValueError(f"role {role.id} has no organization")

    client = get_client_for_org(org)
    model = settings.resolved_claude_model

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
        {"role": "user", "content": _initial_user_message(application_id=application_id)}
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
                if name == "queue_advance_decision":
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

    if run.status == "running":
        run.status = "succeeded" if finished_via_complete_tool else "succeeded"

    run.tools_called = [{"name": n, "count": c} for n, c in tools_called_summary.items()]
    run.finished_at = datetime.now(timezone.utc)
    role.agent_last_run_at = run.finished_at
    db.add(role)
    db.flush()
    return run
