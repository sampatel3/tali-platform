"""Anthropic tool catalogue + dispatcher for the autonomous agent.

Reuses the read-side ``app.mcp.handlers`` and the mutation-side
``app.actions`` package so the agent shares the same surface as the
recruiter UI / Taali Chat. The dispatcher knows which tool calls map to
read handlers (which want a User-shaped context) and which call mutation
actions (which want an ``Actor``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from ..actions import queue_decision, score_cv
from ..actions.types import Actor
from ..mcp import handlers as mcp_handlers
from ..models.agent_run import AgentRun
from ..models.role import Role


@dataclass
class _AgentReadCtx:
    """Synthetic User-shape for read handlers' org-scoping check.

    Read handlers in ``app.mcp.handlers`` only touch ``user.organization_id``
    on the queries; supplying this minimal duck-type lets the agent invoke
    them without a real recruiter session.
    """

    organization_id: int
    id: Optional[int] = None


AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_application",
        "description": (
            "Read full detail for one application: candidate, scores, CV summary, "
            "interview pack, recent events. Always call this before queueing a "
            "decision so your reasoning cites concrete fields."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"application_id": {"type": "integer"}},
            "required": ["application_id"],
        },
    },
    {
        "name": "get_candidate_cv",
        "description": (
            "Parsed CV sections (work history, education, skills) plus raw text. "
            "Use to verify specific experience claims before queueing advance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"candidate_id": {"type": "integer"}},
            "required": ["candidate_id"],
        },
    },
    {
        "name": "score_cv",
        "description": (
            "Enqueue a CV-match score for an application. Idempotent: if a recent "
            "score already exists this returns the existing job. Returns "
            "{job_id, status}. Call this before queueing advance if the "
            "application has no fresh CV-match score."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
                "force": {"type": "boolean", "default": False},
            },
            "required": ["application_id"],
        },
    },
    {
        "name": "queue_advance_decision",
        "description": (
            "Queue a recommendation that the recruiter advance this candidate to "
            "the technical interview stage. The recruiter sees the recommendation "
            "in their pending-decisions panel and approves with one click. Reasoning "
            "must cite concrete evidence from the CV / scores."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
                "reasoning": {
                    "type": "string",
                    "description": "1-3 sentences explaining why this candidate should advance.",
                },
                "evidence": {
                    "type": "object",
                    "description": (
                        "Cited evidence: e.g. {cv_match_score: 87, taali_score: 78, "
                        "criteria_hits: ['python', '5y SaaS'], cv_excerpt: '...'}."
                    ),
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
            },
            "required": ["application_id", "reasoning", "confidence"],
        },
    },
    {
        "name": "agent_run_complete",
        "description": (
            "Signal end of cycle. Always call this last. Provide a 1-2 sentence "
            "summary of what you did and why you're done. The orchestrator uses "
            "this to update calibration and finalise the audit row."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "calibration_observations": {
                    "type": "object",
                    "description": (
                        "Optional: structured observations to persist on role.agent_calibration. "
                        "Supported keys: score_observations (list of numbers), recent_decisions "
                        "(list of {type, status, reasoning_summary}), override_patterns (list of strings)."
                    ),
                },
            },
            "required": ["summary"],
        },
    },
]


# Sentinel returned by agent_run_complete so the orchestrator knows to stop.
_RUN_COMPLETE_SENTINEL = "__AGENT_RUN_COMPLETE__"


def _tool_get_application(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    ctx = _AgentReadCtx(organization_id=role.organization_id)
    return mcp_handlers.get_application(db, ctx, application_id=int(args["application_id"]))


def _tool_get_candidate_cv(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    ctx = _AgentReadCtx(organization_id=role.organization_id)
    return mcp_handlers.get_candidate_cv(db, ctx, candidate_id=int(args["candidate_id"]))


def _tool_score_cv(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    actor = Actor.agent(int(agent_run.id))
    job = score_cv.run(
        db,
        actor,
        organization_id=int(role.organization_id),
        application_id=int(args["application_id"]),
        force=bool(args.get("force", False)),
    )
    if job is None:
        return {"job_id": None, "status": "skipped", "reason": "missing CV/spec/api-key or insufficient credits"}
    return {"job_id": int(job.id), "status": str(job.status)}


def _tool_queue_advance_decision(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    actor = Actor.agent(int(agent_run.id))
    decision = queue_decision.run(
        db,
        actor,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        application_id=int(args["application_id"]),
        decision_type="advance_to_interview",
        reasoning=str(args["reasoning"]),
        evidence=args.get("evidence"),
        confidence=float(args["confidence"]),
        model_version=str(agent_run.model_version or ""),
        prompt_version=str(agent_run.prompt_version or ""),
    )
    return {"decision_id": int(decision.id), "status": str(decision.status)}


def _tool_agent_run_complete(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    summary = str(args.get("summary") or "").strip()
    observations = args.get("calibration_observations") or {}
    return {
        "_sentinel": _RUN_COMPLETE_SENTINEL,
        "summary": summary,
        "observations": observations if isinstance(observations, dict) else {},
    }


_HANDLER_BY_NAME: dict[str, Callable[..., Any]] = {
    "get_application": _tool_get_application,
    "get_candidate_cv": _tool_get_candidate_cv,
    "score_cv": _tool_score_cv,
    "queue_advance_decision": _tool_queue_advance_decision,
    "agent_run_complete": _tool_agent_run_complete,
}


def dispatch(
    name: str,
    arguments: dict[str, Any] | None,
    *,
    db: Session,
    agent_run: AgentRun,
    role: Role,
) -> Any:
    handler = _HANDLER_BY_NAME.get(name)
    if handler is None:
        raise KeyError(f"unknown agent tool: {name}")
    return handler(db, agent_run=agent_run, role=role, args=arguments or {})


def is_run_complete(result: Any) -> bool:
    return isinstance(result, dict) and result.get("_sentinel") == _RUN_COMPLETE_SENTINEL


__all__ = ["AGENT_TOOLS", "dispatch", "is_run_complete"]
