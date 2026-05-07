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

from datetime import datetime, timedelta, timezone

from ..actions import queue_decision, score_cv, send_assessment
from ..actions.types import Actor
from ..mcp import handlers as mcp_handlers
from ..models.agent_run import AgentRun
from ..models.role import Role
from ..services import cohort_signals_service


# Cohort signals are recomputed when older than this. The full pool query
# touches every scored applicant for the role, so we don't want to do it
# more than once per cycle in normal operation.
COHORT_SIGNALS_TTL = timedelta(hours=1)


@dataclass
class _AgentReadCtx:
    """Synthetic User-shape for read handlers' org-scoping check.

    Read handlers in ``app.mcp.handlers`` only touch ``user.organization_id``
    on the queries; supplying this minimal duck-type lets the agent invoke
    them without a real recruiter session.
    """

    organization_id: int
    id: Optional[int] = None


# ---------------------------------------------------------------------------
# Tool schemas exposed to Anthropic
# ---------------------------------------------------------------------------

_QUEUE_REASONING_DESC = "1-3 sentences explaining why. Cite concrete fields from CV/scores."
_QUEUE_EVIDENCE_DESC = (
    "Cited evidence: e.g. {cv_match_score: 87, taali_score: 78, "
    "criteria_hits: ['python', '5y SaaS'], cv_excerpt: '...'}."
)


AGENT_TOOLS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # READ — single application / candidate detail
    # ------------------------------------------------------------------
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
        "name": "get_candidate",
        "description": (
            "Read full candidate detail across all of their applications in this "
            "org. Useful when triaging duplicates or evaluating someone who's "
            "applied to multiple roles."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"candidate_id": {"type": "integer"}},
            "required": ["candidate_id"],
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
    # ------------------------------------------------------------------
    # READ — cross-candidate (cohort reasoning)
    # ------------------------------------------------------------------
    {
        "name": "search_applications",
        "description": (
            "Filter and rank applications for a role by score thresholds, "
            "pipeline stage, and outcome. Returns up to 100 application "
            "summaries sorted by the chosen score. Use this BEFORE queueing "
            "individual decisions to understand the cohort: e.g. find all "
            "open applications in 'review' with taali_score >= 70 to identify "
            "the top of the funnel. Default sort is taali_score desc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role_id": {"type": "integer", "description": "Restrict to one role. Usually your current role."},
                "min_score": {
                    "type": "number",
                    "description": "Threshold on the score named in score_type. Accepts 0-10 or 0-100.",
                },
                "score_type": {
                    "type": "string",
                    "enum": [
                        "taali",
                        "pre_screen",
                        "rank",
                        "cv_match",
                        "workable",
                        "assessment",
                        "role_fit",
                    ],
                    "default": "taali",
                },
                "pipeline_stage": {
                    "type": "string",
                    "enum": ["applied", "invited", "in_assessment", "review", "technical_interview"],
                },
                "application_outcome": {
                    "type": "string",
                    "enum": ["open", "rejected", "withdrawn", "hired"],
                    "default": "open",
                },
                "q": {"type": "string", "description": "Optional name/email/position substring."},
                "sort_by": {
                    "type": "string",
                    "enum": [
                        "taali_score",
                        "pre_screen_score",
                        "rank_score",
                        "cv_match_score",
                        "created_at",
                    ],
                    "default": "taali_score",
                },
                "sort_order": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            },
        },
    },
    {
        "name": "compare_applications",
        "description": (
            "Side-by-side comparison of up to 5 applications: scores, stage, "
            "outcome, basic candidate info. Cheaper than calling get_application "
            "repeatedly. Use when deciding which of several candidates to "
            "advance, or to spot-check ranking before queueing a reject."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "maxItems": 5,
                },
            },
            "required": ["application_ids"],
        },
    },
    {
        "name": "nl_search_candidates",
        "description": (
            "Natural-language candidate search across CV text, skills, "
            "experience entries, AND the org's knowledge graph (when "
            "configured). Wraps the same parser the recruiter UI uses. "
            "Examples: 'senior python engineers who worked at fintechs', "
            "'candidates with kubernetes experience'. Returns matched "
            "applications plus the parsed filter so you can verify the "
            "search interpreted your intent correctly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language description of the candidates you want."},
                "role_id": {"type": "integer", "description": "Optional: restrict matches to one role."},
                "rerank": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            },
            "required": ["query"],
        },
    },
    {
        "name": "graph_search_candidates",
        "description": (
            "Knowledge-graph-only search across the org's Graphiti subgraph. "
            "Returns candidates whose graph facts mention the query plus the "
            "actual fact strings so you can cite specifics (e.g. 'Senior "
            "Engineer at Stripe, 2020-2024'). Falls back gracefully when the "
            "graph isn't configured. Prefer nl_search_candidates unless you "
            "specifically want graph-only matches."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_cohort_signals",
        "description": (
            "Compute (or return cached) 'do high scorers cluster?' signals "
            "for this role: which skills, companies, job titles, and schools "
            "are over-represented in the top decile of TAALI scores vs the "
            "full applicant pool. Each signal carries a `lift` value — "
            "lift > 1 means the feature is more common among top scorers "
            "than applicants generally. Cached on the role for 1 hour so "
            "calling repeatedly within a cycle is cheap. Returns "
            "{insufficient_data: true} when there are fewer than 5 scored "
            "applicants. Use this BEFORE deciding to advance a borderline "
            "candidate ('do they fit the top-scorer pattern?') or BEFORE "
            "queueing a reject ('is the candidate missing widely-shared "
            "top-scorer features?')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "force_recompute": {
                    "type": "boolean",
                    "default": False,
                    "description": "Skip the 1-hour cache and recompute now. Use sparingly.",
                },
            },
        },
    },
    # ------------------------------------------------------------------
    # EXECUTE — auto-runs side effect
    # ------------------------------------------------------------------
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
        "name": "send_assessment",
        "description": (
            "Create an assessment for the application and dispatch the invite "
            "email to the candidate. Auto-execute (no recruiter approval). "
            "Use when the candidate has cleared CV/pre-screen review and is "
            "ready to take the technical assessment. Idempotent: returns "
            "status='already_exists' if a valid assessment is already open. "
            "Refuses with status='misconfigured' if the role has 0 or >1 "
            "tasks linked unless task_id is passed explicitly. Returns "
            "{assessment_id, status, detail} where status is 'sent', "
            "'already_exists', 'insufficient_credits', 'misconfigured', or 'blocked'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
                "task_id": {
                    "type": "integer",
                    "description": "Optional: pick a specific task. Required when role has multiple tasks.",
                },
                "duration_minutes": {
                    "type": "integer",
                    "minimum": 15,
                    "maximum": 180,
                    "default": 90,
                },
            },
            "required": ["application_id"],
        },
    },
    # ------------------------------------------------------------------
    # QUEUE — recruiter must approve before side effect
    # ------------------------------------------------------------------
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
                "reasoning": {"type": "string", "description": _QUEUE_REASONING_DESC},
                "evidence": {"type": "object", "description": _QUEUE_EVIDENCE_DESC},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["application_id", "reasoning", "confidence"],
        },
    },
    {
        "name": "queue_reject_decision",
        "description": (
            "Queue a recommendation to reject this candidate. The recruiter "
            "sees the recommendation and approves or overrides with one click. "
            "Use when the candidate has completed assessment / review and the "
            "evidence clearly indicates they are not a fit. Always cite "
            "concrete weaknesses (low TAALI, missing requirements, "
            "assessment failures). Be conservative — when in doubt prefer "
            "queue_advance_decision or simply do nothing this cycle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
                "reasoning": {"type": "string", "description": _QUEUE_REASONING_DESC},
                "evidence": {"type": "object", "description": _QUEUE_EVIDENCE_DESC},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["application_id", "reasoning", "confidence"],
        },
    },
    {
        "name": "queue_skip_assessment_reject_decision",
        "description": (
            "Queue a recommendation to reject WITHOUT sending the assessment, "
            "i.e. cut the candidate at the pre-screen / CV stage. Recruiter "
            "approves or overrides. Use only when CV-match and pre-screen "
            "scores are clearly below threshold AND requirements are not met. "
            "This decision is more impactful than queue_reject_decision because "
            "the candidate never gets the chance to demonstrate skill in the "
            "assessment — be cautious and require strong evidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
                "reasoning": {"type": "string", "description": _QUEUE_REASONING_DESC},
                "evidence": {"type": "object", "description": _QUEUE_EVIDENCE_DESC},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["application_id", "reasoning", "confidence"],
        },
    },
    # ------------------------------------------------------------------
    # TERMINAL
    # ------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Read tool handlers
# ---------------------------------------------------------------------------


def _read_ctx(role: Role) -> _AgentReadCtx:
    return _AgentReadCtx(organization_id=role.organization_id)


def _tool_get_application(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    return mcp_handlers.get_application(
        db, _read_ctx(role), application_id=int(args["application_id"])
    )


def _tool_get_candidate(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    return mcp_handlers.get_candidate(
        db, _read_ctx(role), candidate_id=int(args["candidate_id"])
    )


def _tool_get_candidate_cv(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    return mcp_handlers.get_candidate_cv(
        db, _read_ctx(role), candidate_id=int(args["candidate_id"])
    )


def _tool_search_applications(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    # Default to the agent's own role unless the caller explicitly passes one.
    role_id = args.get("role_id")
    return mcp_handlers.search_applications(
        db,
        _read_ctx(role),
        role_id=int(role_id) if role_id is not None else int(role.id),
        min_score=args.get("min_score"),
        score_type=str(args.get("score_type") or "taali"),
        pipeline_stage=args.get("pipeline_stage"),
        application_outcome=(
            args["application_outcome"]
            if "application_outcome" in args
            else "open"
        ),
        q=args.get("q"),
        sort_by=str(args.get("sort_by") or "taali_score"),
        sort_order=str(args.get("sort_order") or "desc"),
        limit=int(args.get("limit") or 25),
    )


def _tool_compare_applications(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    ids = args.get("application_ids") or []
    return mcp_handlers.compare_applications(
        db,
        _read_ctx(role),
        application_ids=[int(i) for i in ids],
    )


def _tool_nl_search_candidates(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    role_id = args.get("role_id")
    return mcp_handlers.nl_search_candidates(
        db,
        _read_ctx(role),
        query=str(args.get("query") or ""),
        role_id=int(role_id) if role_id is not None else int(role.id),
        rerank=bool(args.get("rerank", True)),
        limit=int(args.get("limit") or 25),
    )


def _tool_graph_search_candidates(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    return mcp_handlers.graph_search_candidates(
        db,
        _read_ctx(role),
        query=str(args.get("query") or ""),
        limit=int(args.get("limit") or 25),
    )


def _cohort_signals_is_fresh(role: Role, *, now: Optional[datetime] = None) -> bool:
    cached = role.agent_cohort_signals
    cached_at = role.agent_cohort_signals_at
    if not isinstance(cached, dict) or cached_at is None:
        return False
    # SQLite strips tzinfo from DateTime(timezone=True); Postgres preserves it.
    # Normalize so this works in tests + prod identically.
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    moment = now or datetime.now(timezone.utc)
    return (moment - cached_at) < COHORT_SIGNALS_TTL


def _tool_get_cohort_signals(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    force = bool(args.get("force_recompute", False))
    if not force and _cohort_signals_is_fresh(role):
        cached = role.agent_cohort_signals or {}
        return {**cached, "from_cache": True}

    payload = cohort_signals_service.compute_cohort_signals(
        db,
        role_id=int(role.id),
        organization_id=int(role.organization_id),
    )
    role.agent_cohort_signals = payload
    role.agent_cohort_signals_at = datetime.now(timezone.utc)
    db.add(role)
    db.flush()
    return {**payload, "from_cache": False}


# ---------------------------------------------------------------------------
# Mutation tool handlers
# ---------------------------------------------------------------------------


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


def _tool_send_assessment(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    actor = Actor.agent(int(agent_run.id))
    task_id = args.get("task_id")
    duration = args.get("duration_minutes")
    result = send_assessment.run(
        db,
        actor,
        organization_id=int(role.organization_id),
        application_id=int(args["application_id"]),
        task_id=int(task_id) if task_id is not None else None,
        duration_minutes=int(duration) if duration is not None else 90,
    )
    return result.as_dict()


def _queue(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any], decision_type: str
) -> Any:
    actor = Actor.agent(int(agent_run.id))
    decision = queue_decision.run(
        db,
        actor,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        application_id=int(args["application_id"]),
        decision_type=decision_type,
        reasoning=str(args["reasoning"]),
        evidence=args.get("evidence"),
        confidence=float(args["confidence"]),
        model_version=str(agent_run.model_version or ""),
        prompt_version=str(agent_run.prompt_version or ""),
    )
    return {"decision_id": int(decision.id), "status": str(decision.status), "decision_type": decision_type}


def _tool_queue_advance_decision(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    return _queue(db, agent_run=agent_run, role=role, args=args, decision_type="advance_to_interview")


def _tool_queue_reject_decision(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    return _queue(db, agent_run=agent_run, role=role, args=args, decision_type="reject")


def _tool_queue_skip_assessment_reject_decision(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    return _queue(db, agent_run=agent_run, role=role, args=args, decision_type="skip_assessment_reject")


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


# Decision tools that count against role.agent_decision_budget_per_cycle.
QUEUE_DECISION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "queue_advance_decision",
        "queue_reject_decision",
        "queue_skip_assessment_reject_decision",
    }
)


_HANDLER_BY_NAME: dict[str, Callable[..., Any]] = {
    "get_application": _tool_get_application,
    "get_candidate": _tool_get_candidate,
    "get_candidate_cv": _tool_get_candidate_cv,
    "search_applications": _tool_search_applications,
    "compare_applications": _tool_compare_applications,
    "nl_search_candidates": _tool_nl_search_candidates,
    "graph_search_candidates": _tool_graph_search_candidates,
    "get_cohort_signals": _tool_get_cohort_signals,
    "score_cv": _tool_score_cv,
    "send_assessment": _tool_send_assessment,
    "queue_advance_decision": _tool_queue_advance_decision,
    "queue_reject_decision": _tool_queue_reject_decision,
    "queue_skip_assessment_reject_decision": _tool_queue_skip_assessment_reject_decision,
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


__all__ = [
    "AGENT_TOOLS",
    "QUEUE_DECISION_TOOL_NAMES",
    "dispatch",
    "is_run_complete",
]
