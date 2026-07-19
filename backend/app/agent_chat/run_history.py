"""Recruiter-safe autonomous run history for role Agent Chat."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.agent_run import AGENT_RUN_STATUSES, AGENT_RUN_TRIGGERS, AgentRun
from ..models.role import Role


RUN_HISTORY_TOOL_DEFINITION: dict[str, Any] = {
    "name": "list_recent_agent_runs",
    "description": (
        "List recent autonomous cycles for THIS role with status, trigger, timing, "
        "rounds, decisions, cost and a recruiter-safe failure explanation. Use to "
        "answer an event-card follow-up such as 'why did the latest run fail?' or "
        "'what has the agent done today?'. Raw provider errors and secrets are never "
        "returned."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {
                "type": ["string", "null"],
                "enum": [*AGENT_RUN_STATUSES, None],
            },
            "trigger": {
                "type": ["string", "null"],
                "enum": [*AGENT_RUN_TRIGGERS, None],
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
        },
        "required": [],
    },
}


def _failure_type(error: str | None) -> str | None:
    reason = str(error or "").strip().lower()
    if not reason:
        return None
    if reason in {"model_client_unavailable", "model_provider_failure"} or reason.startswith("anthropic call failed"):
        return "model_provider"
    if reason.startswith("watchdog"):
        return "worker_timeout"
    if "no-progress circuit breaker" in reason:
        return "no_progress"
    if "token budget" in reason:
        return "cycle_token_budget"
    if "max_tool_rounds" in reason:
        return "round_limit"
    if reason == "missing_job_spec":
        return "missing_job_spec"
    if reason == "skipped_overlap":
        return "overlapping_cycle"
    if reason.startswith("insufficient organization credits"):
        return "organization_credits"
    if "monthly" in reason and "cap" in reason:
        return "monthly_role_budget"
    return "operational_error"


_FAILURE_SUMMARIES = {
    "model_provider": "The model provider call did not complete.",
    "worker_timeout": "The worker stopped responding and the watchdog closed the run.",
    "no_progress": "The agent repeated work without progress, so the safety circuit stopped it.",
    "cycle_token_budget": "The run reached this role's per-cycle token guard.",
    "round_limit": "The run reached its maximum reasoning-round guard.",
    "missing_job_spec": "The role needs a job description before the agent can work.",
    "overlapping_cycle": "A second cycle was skipped because one was already running.",
    "organization_credits": "The organization did not have enough credits for another model call.",
    "monthly_role_budget": "The role reached its monthly agent budget.",
    "operational_error": "The run ended on an operational error; diagnostics remain in server logs.",
}


def public_failure_summary(error: str | None) -> str | None:
    """Map stored run diagnostics to a stable recruiter-safe explanation."""

    return _FAILURE_SUMMARIES.get(_failure_type(error))


def _tools(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:100]
        if name:
            try:
                count = max(0, int(item.get("count") or 0))
            except (TypeError, ValueError):
                count = 0
            out.append({"name": name, "count": count})
    return out[:50]


def list_recent_agent_runs(
    db: Session,
    role: Role,
    *,
    status: str | None = None,
    trigger: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    normalized_status = str(status).strip() if status is not None else None
    normalized_trigger = str(trigger).strip() if trigger is not None else None
    if normalized_status and normalized_status not in AGENT_RUN_STATUSES:
        raise ValueError(f"unknown agent run status: {normalized_status}")
    if normalized_trigger and normalized_trigger not in AGENT_RUN_TRIGGERS:
        raise ValueError(f"unknown agent run trigger: {normalized_trigger}")
    capped = max(1, min(int(limit or 5), 20))
    query = db.query(AgentRun).filter(
        AgentRun.organization_id == int(role.organization_id),
        AgentRun.role_id == int(role.id),
    )
    if normalized_status:
        query = query.filter(AgentRun.status == normalized_status)
    if normalized_trigger:
        query = query.filter(AgentRun.trigger == normalized_trigger)
    rows = (
        query.order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
        .limit(capped)
        .all()
    )
    runs: list[dict[str, Any]] = []
    for row in rows:
        failure_type = _failure_type(row.error)
        runs.append(
            {
                "run_id": int(row.id),
                "trigger": str(row.trigger),
                "status": str(row.status),
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                "rounds_executed": (
                    int(row.rounds_executed) if row.rounds_executed is not None else None
                ),
                "decisions_emitted": int(row.decisions_emitted or 0),
                "cost_usd": round(int(row.total_cost_micro_usd or 0) / 1_000_000, 6),
                "tools_called": _tools(row.tools_called),
                "failure_type": failure_type,
                "failure_summary": public_failure_summary(row.error),
            }
        )
    return {"role_id": int(role.id), "count": len(runs), "runs": runs}


__all__ = [
    "RUN_HISTORY_TOOL_DEFINITION",
    "list_recent_agent_runs",
    "public_failure_summary",
]
