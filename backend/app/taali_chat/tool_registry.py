"""Anthropic-tool-format catalogue + dispatcher for Taali Chat.

Reuses the pure-function handlers in ``app.mcp.handlers`` so the in-product
chat exposes the same surface as the public MCP server. Each tool
definition includes a JSON schema so Claude can structure its arguments
correctly.

Adding a tool: implement it in ``app/mcp/handlers.py``, then register it
here. The schema deliberately stays hand-written rather than auto-derived
from the function signature — Claude's tool-calling accuracy is much
better with curated descriptions and tight schemas than with introspected
ones.
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from ..mcp import handlers
from ..models.user import User


# Anthropic tool schema. Keep descriptions terse but pointed — these are
# what Claude actually reads to decide which tool to call.
TAALI_CHAT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_roles",
        "description": (
            "List every active role in the user's org. Use first to discover "
            "role_id values. Set include_stage_counts=true to also return "
            "per-stage open application counts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "include_stage_counts": {
                    "type": "boolean",
                    "description": "Include open-application counts per pipeline stage.",
                    "default": False,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_role",
        "description": (
            "Fetch one role with its job spec, criteria, and per-stage open-"
            "application counts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"role_id": {"type": "integer"}},
            "required": ["role_id"],
        },
    },
    {
        "name": "search_applications",
        "description": (
            "Filter applications by score / stage / outcome / simple text. "
            "Default scope: open applications, sorted by taali_score desc. "
            "For semantic queries (skills, years of experience, narrative "
            "fit), use nl_search_candidates instead — this tool's q only "
            "matches name/email/position."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role_id": {"type": ["integer", "null"]},
                "min_score": {
                    "type": ["number", "null"],
                    "description": "Threshold 0-100 (or 0-10, auto-scaled).",
                },
                "score_type": {
                    "type": "string",
                    "enum": ["taali", "pre_screen", "rank", "cv_match"],
                    "default": "taali",
                },
                "pipeline_stage": {
                    "type": ["string", "null"],
                    "enum": [None, "applied", "invited", "in_assessment", "review"],
                },
                "application_outcome": {
                    "type": ["string", "null"],
                    "enum": [None, "open", "rejected", "withdrawn", "hired"],
                    "default": "open",
                },
                "q": {"type": ["string", "null"]},
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
                "sort_order": {
                    "type": "string",
                    "enum": ["desc", "asc"],
                    "default": "desc",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "Page through the complete ordered match set.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_application",
        "description": (
            "Fetch one application with all four scores, evidence, auto-reject "
            "reason, and notes. include_cv_text=true embeds the full CV."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
                "include_cv_text": {"type": "boolean", "default": False},
            },
            "required": ["application_id"],
        },
    },
    {
        "name": "get_candidate",
        "description": (
            "Fetch a candidate's profile and the full list of applications they "
            "have across every role. Use for cross-role questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"candidate_id": {"type": "integer"}},
            "required": ["candidate_id"],
        },
    },
    {
        "name": "compare_applications",
        "description": (
            "Side-by-side scorecard for 2-5 applications. Use when the user "
            "asks 'which candidate should advance' — surfaces every score on a "
            "common scale so you can reason over them."
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
        "name": "find_top_candidates",
        "description": (
            "GROUNDED top-N ranking. Use this whenever the recruiter asks for "
            "the 'best' or 'top N' candidates with one or more qualities "
            "(e.g. 'top 5 data engineers with banking domain experience'). It "
            "(1) ranks the structured-match set by score, then (2) attaches "
            "to each shortlisted candidate a per-criterion verdict backed by "
            "VERBATIM CV evidence (a real quote with char offsets, via "
            "citations or a stored requirement assessment). Returns a `spec` "
            "echo of how the query was read, `total_matched`, and grounded "
            "`candidates` (each with `criteria[].status` + `evidence[].quote`). "
            "Prefer this over nl_search_candidates for ranked 'top/best with "
            "<quality>' asks — it does the ranking and grounding for you. When "
            "you answer, cite the quotes; never add a fact that isn't in the "
            "evidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
                "rank_by": {
                    "type": "string",
                    "enum": ["taali", "pre_screen", "rank", "cv_match"],
                    "default": "taali",
                    "description": "Score to rank by. Default 'taali' (merged fit).",
                },
                "role_id": {"type": ["integer", "null"]},
            },
            "required": ["query"],
        },
    },
    {
        "name": "screen_pool_against_requirement",
        "description": (
            "REDISCOVERY across the WHOLE scored history. The default is a "
            "zero-per-candidate-model-call, person-deduplicated Postgres search. "
            "Use when the recruiter "
            "has a NEW requirement or role and wants to find who — among ALL "
            "candidates ever scored, INCLUDING ones scored for OTHER roles whose "
            "existing score says nothing about this requirement — fits it. "
            "Unlike find_top_candidates (a top-N shortlist of the current "
            "pipeline ranked by existing score), this (1) reuses each "
            "candidate's stored fields and CV full-text index, and when "
            "deep_verify=true (2) grounds a bounded promising set with verbatim "
            "CV citations. Returns database coverage fields and candidates; "
            "when deep verification is requested those candidates also carry "
            "`criteria[].status` + `evidence[].quote`, plus how many were "
            "`screened` (and whether the "
            "pool was `capped`), and `rescore_candidate_ids` (those a full "
            "re-score would clarify). Choose THIS over find_top_candidates / "
            "nl_search_candidates whenever the ask is 'who in my existing/"
            "already-scored candidates fits this NEW requirement'. Only cite "
            "quotes or claim verified fit when deep_verify=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "requirement_text": {
                    "type": "string",
                    "description": "The new requirement / mini job-spec to screen against.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "Page through exhaustive database matches when deep_verify is false.",
                },
                "role_id": {
                    "type": ["integer", "null"],
                    "description": "Restrict the scored history to one role; omit for org-wide.",
                },
                "deep_verify": {
                    "type": "boolean",
                    "default": False,
                    "description": "Opt in to bounded per-candidate CV evidence checks.",
                },
            },
            "required": ["requirement_text"],
        },
    },
    {
        "name": "nl_search_candidates",
        "description": (
            "Exhaustive, person-deduplicated natural-language candidate search "
            "over normalized skills/taxonomy aliases, current and historical "
            "titles, locations, years and indexed CV text. Common structured "
            "queries require no model call. deep_verify optionally checks a "
            "bounded relevance-ranked subset; include_graph is opt-in. "
            "Use this for questions like 'AWS Glue engineer with 5+ years' or "
            "'senior backend devs in EMEA who've worked at fintechs'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "role_id": {"type": ["integer", "null"]},
                "deep_verify": {"type": "boolean", "default": False},
                "include_graph": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            },
            "required": ["query"],
        },
    },
    {
        "name": "graph_search_candidates",
        "description": (
            "Knowledge-graph search across the org's temporal subgraph. "
            "Returns candidates whose stored facts mention the query, the "
            "actual fact strings so you can cite specifics, AND the matching "
            "subgraph (nodes + edges) which the chat UI renders as an inline "
            "force-directed graph. Use for graph-shaped questions: "
            "'colleagues of X', 'people who worked at startups before joining "
            "Big Tech', 'engineers whose CVs mention tool Y'. Returns "
            "warnings: [{code: 'neo4j_unavailable'}] when graph is not "
            "configured — fall back to nl_search_candidates."
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
        "name": "get_candidate_cv",
        "description": (
            "Parsed CV sections (work history, education, skills) plus the raw "
            "extracted CV text for one candidate. Use when you need to quote a "
            "candidate's CV verbatim or check specific experience details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"candidate_id": {"type": "integer"}},
            "required": ["candidate_id"],
        },
    },
    # ----- Agent-aware tools -----
    # When the conversation is role-scoped (TaaliChatConversation.role_id
    # set), the chat service injects "default role_id = X" into the
    # system prompt so these tools fall back to that role without the
    # recruiter spelling it out. Outside a role-scoped conversation the
    # recruiter passes role_id explicitly or omits it for org-wide.
    {
        "name": "list_recent_agent_decisions",
        "description": (
            "Recent decisions queued by the autonomous agent (advance / reject "
            "/ skip-assessment-reject) — what was queued, the reasoning the "
            "agent gave, the recruiter's resolution (pending / approved / "
            "overridden / discarded). Use to answer 'why did the agent queue "
            "Lucas?' or 'what did the agent decide today?'. Filter by status "
            "to surface just pending decisions awaiting recruiter review."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role_id": {
                    "type": "integer",
                    "description": "Restrict to one role. Defaults to the conversation's role when set.",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "approved", "overridden", "discarded", "expired"],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
    },
    {
        "name": "list_recent_agent_runs",
        "description": (
            "Recent autonomous-cycle log entries — one row per agent cycle. "
            "Each row has trigger (event/cron/manual), status (succeeded/"
            "failed/aborted/budget_paused), tools called, decisions emitted, "
            "errors if any, model + prompt versions. Use to answer 'what "
            "did the agent do today?' or 'why did the cycle fail this morning?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role_id": {
                    "type": "integer",
                    "description": "Restrict to one role. Defaults to the conversation's role when set.",
                },
                "trigger": {
                    "type": "string",
                    "enum": ["event", "cron", "manual"],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
    },
    {
        "name": "explain_agent_decision",
        "description": (
            "Drilldown for one queued agent decision. Returns the decision "
            "(reasoning + cited evidence + confidence + status) plus the "
            "linked AgentRun (which cycle produced it, what tools the agent "
            "called, model + prompt versions). Use when the recruiter asks "
            "'why did you queue this one' on a specific decision."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"decision_id": {"type": "integer"}},
            "required": ["decision_id"],
        },
    },
]


_HANDLER_BY_NAME: dict[str, Callable[..., Any]] = {
    "list_roles": handlers.list_roles,
    "get_role": handlers.get_role,
    "search_applications": handlers.search_applications,
    "get_application": handlers.get_application,
    "get_candidate": handlers.get_candidate,
    "compare_applications": handlers.compare_applications,
    "find_top_candidates": handlers.find_top_candidates,
    "screen_pool_against_requirement": handlers.screen_pool_against_requirement,
    "nl_search_candidates": handlers.nl_search_candidates,
    "graph_search_candidates": handlers.graph_search_candidates,
    "get_candidate_cv": handlers.get_candidate_cv,
    "list_recent_agent_decisions": handlers.list_recent_agent_decisions,
    "list_recent_agent_runs": handlers.list_recent_agent_runs,
    "explain_agent_decision": handlers.explain_agent_decision,
}


def dispatch_tool(
    name: str, arguments: dict[str, Any], *, db: Session, user: User
) -> Any:
    """Run one tool call, returning its raw payload.

    Raises ``KeyError`` on unknown tool, ``ValueError`` on bad arguments.
    Caller is responsible for catching + converting errors into a
    ``tool_result`` content block with ``is_error=True``.
    """
    handler = _HANDLER_BY_NAME.get(name)
    if handler is None:
        raise KeyError(f"unknown tool: {name}")
    safe_args = arguments or {}
    return handler(db, user, **safe_args)


__all__ = ["TAALI_CHAT_TOOLS", "dispatch_tool"]
