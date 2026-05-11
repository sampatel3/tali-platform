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

from ..actions import (
    advance_stage,
    ask_recruiter,
    create_application,
    post_workable_note,
    queue_decision,
    reject_application,
    resend_assessment_invite,
    score_cv,
    send_assessment,
)
from ..actions.types import Actor
from ..mcp import handlers as mcp_handlers
from ..models.agent_needs_input import NEEDS_INPUT_KINDS
from ..models.agent_run import AgentRun
from ..models.role import Role
from ..services import cohort_signals_service
from . import cohort_tools, policy_evaluator


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
    # COHORT SURVEY — call FIRST every cycle.
    # Cheap, summary-shaped tools so the agent can see the whole role's
    # state in one shot before drilling into individuals.
    # ------------------------------------------------------------------
    {
        "name": "survey_role_state",
        "description": (
            "ALWAYS call this first. Returns the role's cohort state in a "
            "single dict: counts of applications in each pipeline state "
            "(needs_cv_fetch, needs_pre_screen, needs_score, "
            "ready_for_assessment_decision, in_assessment, "
            "ready_for_advance_decision, rejected, hired), plus role-config "
            "intent_gaps (missing budget, threshold, must-haves), plus open "
            "recruiter questions (so you don't re-ask them). Use the counts "
            "to decide where the leverage is THIS cycle — do NOT iterate "
            "individual applications when a batch action will do."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "find_apps_in_state",
        "description": (
            "Returns up to N application_ids in a single cohort state. Pair "
            "with survey_role_state: 'survey says 47 apps need_pre_screen → "
            "give me the first 25 ids' → batch_pre_screen on those ids."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": list(cohort_tools.COHORT_STATES),
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            },
            "required": ["state"],
        },
    },
    {
        "name": "read_pending_recruiter_inputs",
        "description": (
            "Returns the open + recently-resolved recruiter questions for "
            "this role. ALWAYS call this after survey_role_state — if a "
            "question you'd ask is already open, do NOT re-ask. If a "
            "previously-asked question now has a response, use it (e.g. "
            "the recruiter has approved a send-assessment batch — proceed "
            "with send)."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    # ------------------------------------------------------------------
    # BATCH ACTIONS — auto-execute deterministic work across N apps in
    # one tool call. Cheap risk: pre-screen + scoring only mutate
    # cv_score_cache; sends/queues stay one-at-a-time.
    # ------------------------------------------------------------------
    {
        "name": "batch_score_cv",
        "description": (
            "Enqueue CV-match scoring for up to 25 applications in one "
            "call. Idempotent — applications that already have a fresh "
            "score are skipped. Returns per-id status. Cheap auto-execute "
            "tool; no recruiter approval needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "maxItems": 25,
                },
                "force": {"type": "boolean", "default": False},
            },
            "required": ["application_ids"],
        },
    },
    # ------------------------------------------------------------------
    # ASK RECRUITER — third lane: agent surfaces a question and waits.
    # Idempotent on (role_id, kind) so re-asking re-uses the open card.
    # ------------------------------------------------------------------
    {
        "name": "ask_recruiter",
        "description": (
            "Open (or update) a recruiter-facing question on the role page. "
            "Use sparingly — only when survey_role_state shows a real gap "
            "you can't fill on your own (empty must_have intent, no monthly "
            "budget, ambiguous threshold, two equally-strong candidates "
            "needing a tie-break, or send-assessment HITL approval needed). "
            "Idempotent on (role_id, kind): re-calling refreshes the existing "
            "open card, so you can call freely without spamming. Always pair "
            "with read_pending_recruiter_inputs first to skip already-open "
            "asks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(NEEDS_INPUT_KINDS),
                    "description": (
                        "Which class of question. intent_slot_missing for "
                        "empty must_have/preferred slots; monthly_budget_missing "
                        "for null budget; threshold_ambiguous when score_threshold "
                        "is unset and the cohort spread is high; "
                        "send_assessment_approval for HITL gate; "
                        "candidate_tie_break for advance/reject ambiguity; "
                        "other as a last resort."
                    ),
                },
                "prompt": {"type": "string", "description": "1-3 sentence question, recruiter-facing."},
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                            "label": {"type": "string"},
                        },
                        "required": ["value", "label"],
                    },
                    "description": (
                        "Optional list of click-to-answer options. Mutually "
                        "exclusive with response_schema."
                    ),
                },
                "response_schema": {
                    "type": "object",
                    "description": (
                        "Optional shape descriptor for free-text/numeric "
                        "responses. Mutually exclusive with options."
                    ),
                },
                "rationale": {
                    "type": "string",
                    "description": "1-2 sentence note on why you're asking — shown under the prompt to the recruiter.",
                },
            },
            "required": ["kind", "prompt"],
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
        "name": "refresh_candidate_graph",
        "description": (
            "Re-project a candidate into the knowledge graph (Graphiti). "
            "The graph normally auto-updates via SQLAlchemy listeners on "
            "candidate / interview / event writes; use this tool when those "
            "listeners failed or the candidate's graph_synced_at is stale "
            "relative to cv_uploaded_at. Returns the number of episodes "
            "ingested. No-op when Graphiti is not configured. Cost is "
            "billed to the role's monthly budget under the graph_sync "
            "feature, so use sparingly — usually only when a downstream "
            "graph_search / graph_priors call returned empty for a "
            "candidate that should have signal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
            },
            "required": ["application_id"],
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
    {
        "name": "resend_assessment_invite",
        "description": (
            "Re-dispatch the invite email for an existing assessment without "
            "creating a new Assessment row. Use when the candidate didn't "
            "receive the original invite, asked to be re-invited, or the link "
            "expired. Honors the role's auto_promote toggle just like "
            "send_assessment — when auto_promote is False, opens an "
            "ask_recruiter card instead of resending. Returns "
            "{assessment_id, status, detail} where status is 'resent', "
            "'voided', 'no_candidate', or 'awaiting_recruiter_approval'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "assessment_id": {"type": "integer"},
            },
            "required": ["assessment_id"],
        },
    },
    {
        "name": "create_application",
        "description": (
            "Create a candidate application against a role. Reuses an existing "
            "Candidate row when one matches the email; otherwise creates a new "
            "candidate. Refuses with 400 if the candidate already has an "
            "application for this role. Use when processing inbound Workable "
            "webhooks or email-driven candidate ingest that aren't covered by "
            "the automatic sync path. Returns {application_id, candidate_id, status}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role_id": {"type": "integer"},
                "candidate_email": {"type": "string"},
                "candidate_name": {"type": "string"},
                "candidate_position": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["role_id", "candidate_email"],
        },
    },
    {
        "name": "post_workable_note",
        "description": (
            "Post a free-form note to a candidate's Workable activity feed. "
            "Use to leave context that doesn't correspond to a stage change "
            "— e.g. flagging why you queued a rejection, recording a side-"
            "channel observation, or adding a heads-up the recruiter should "
            "see in their Workable view. Skipped if the application has no "
            "linked Workable candidate or the org isn't Workable-connected. "
            "Body is capped at 8000 chars. Returns {application_id, status, "
            "detail} where status is 'posted', 'skipped', or 'failed'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
                "body": {"type": "string", "description": "Note text to post."},
            },
            "required": ["application_id", "body"],
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
    # POLICY — call BEFORE any queue_* tool.
    # ------------------------------------------------------------------
    {
        "name": "evaluate_policy",
        "description": (
            "Run the deterministic decision policy against this application. "
            "Pulls pre-screen, CV-scoring, assessment-scoring, and recent "
            "manual recruiter actions, builds the policy inputs, and returns "
            "the verdict + reasoning trace + policy_revision_id. ALWAYS call "
            "this before queue_advance_decision / queue_reject_decision / "
            "queue_skip_assessment_reject_decision so the human-readable "
            "reasoning + audit trail are anchored to the policy. If the "
            "verdict says skipped_due_to_manual=true, do NOT queue — the "
            "recruiter has already acted. If the verdict's decision_type is "
            "queue_*, you may pass the same reasoning into the matching "
            "queue tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
                "skip_cache": {
                    "type": "boolean",
                    "default": False,
                    "description": "Force sub-agents to recompute rather than use cached scores. Use sparingly.",
                },
            },
            "required": ["application_id"],
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


def _tool_refresh_candidate_graph(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    """Re-project the candidate associated with ``application_id`` into Graphiti.

    Mirrors the per-role Process-candidates "Sync graph" step but for a
    single application. Billed to the role budget so the spend flows
    through the same monthly cap.
    """
    from ..candidate_graph import client as graph_client
    from ..candidate_graph import sync as graph_sync_module
    from ..models.candidate import Candidate
    from ..models.candidate_application import CandidateApplication

    application_id = int(args["application_id"])
    if not graph_client.is_configured():
        return {
            "status": "unconfigured",
            "application_id": application_id,
            "episodes_sent": 0,
        }
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == int(role.organization_id),
        )
        .first()
    )
    if app is None or app.candidate_id is None:
        return {
            "status": "not_found",
            "application_id": application_id,
            "episodes_sent": 0,
        }
    candidate = (
        db.query(Candidate)
        .filter(Candidate.id == int(app.candidate_id))
        .first()
    )
    if candidate is None:
        return {
            "status": "not_found",
            "application_id": application_id,
            "episodes_sent": 0,
        }
    sent = graph_sync_module.sync_candidate(
        candidate,
        db=db,
        include_cv_text=True,
        bill_organization_id=int(role.organization_id),
        bill_role_id=int(role.id),
    )
    return {
        "status": "ok" if sent > 0 else "no_episodes",
        "application_id": application_id,
        "candidate_id": int(candidate.id),
        "episodes_sent": int(sent),
    }


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
    application_id = int(args["application_id"])

    # HITL gate per Role.auto_promote. When False (the default), instead
    # of auto-sending, the agent opens an approval-style
    # ``agent_needs_input`` row keyed on (role_id, send_assessment_approval).
    # The recruiter approves on the role page; the next cycle reads the
    # response and sends. This is the OpenAI-guide "high-risk action →
    # human oversight" pattern.
    if not bool(getattr(role, "auto_promote", False)):
        existing = ask_recruiter.open(
            db,
            actor,
            organization_id=int(role.organization_id),
            role_id=int(role.id),
            kind="send_assessment_approval",
            subject_id=application_id,
            prompt=(
                f"Approve sending the assessment to application {application_id}? "
                "I'll dispatch the invite as soon as you confirm."
            ),
            options=[
                {"value": "approve", "label": "Approve & send"},
                {"value": "skip", "label": "Skip this candidate"},
            ],
            rationale=(
                "auto_promote is off for this role; every send goes through "
                "the recruiter."
            ),
        )
        return {
            "status": "awaiting_recruiter_approval",
            "needs_input_id": int(existing.id),
            "application_id": application_id,
        }

    task_id = args.get("task_id")
    duration = args.get("duration_minutes")
    result = send_assessment.run(
        db,
        actor,
        organization_id=int(role.organization_id),
        application_id=application_id,
        task_id=int(task_id) if task_id is not None else None,
        duration_minutes=int(duration) if duration is not None else 90,
    )
    return result.as_dict()


def _tool_resend_assessment_invite(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    actor = Actor.agent(int(agent_run.id))
    assessment_id = int(args["assessment_id"])

    # HITL gate — same auto_promote toggle that gates send_assessment.
    # Resending an invite is a candidate-facing email, so it must
    # respect the same recruiter approval policy.
    if not bool(getattr(role, "auto_promote", False)):
        existing = ask_recruiter.open(
            db,
            actor,
            organization_id=int(role.organization_id),
            role_id=int(role.id),
            kind="resend_assessment_invite_approval",
            prompt=(
                f"Approve resending the assessment invite for assessment "
                f"{assessment_id}? I'll re-dispatch the invite as soon as "
                "you confirm."
            ),
            options=[
                {"value": "approve", "label": "Approve & resend"},
                {"value": "skip", "label": "Skip — don't resend"},
            ],
            rationale=(
                "auto_promote is off for this role; every candidate-facing "
                "send goes through the recruiter."
            ),
        )
        return {
            "status": "awaiting_recruiter_approval",
            "needs_input_id": int(existing.id),
            "assessment_id": assessment_id,
        }

    result = resend_assessment_invite.run(
        db,
        actor,
        organization_id=int(role.organization_id),
        assessment_id=assessment_id,
    )
    return result.as_dict()


def _tool_create_application(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    actor = Actor.agent(int(agent_run.id))
    target_role_id = int(args["role_id"])
    # Org boundary: agent can only create applications under its own org's
    # roles. Mismatched role_id results in a 404 from get_role inside the
    # action, so no additional check here.
    result = create_application.run(
        db,
        actor,
        organization_id=int(role.organization_id),
        role_id=target_role_id,
        candidate_email=str(args["candidate_email"]),
        candidate_name=args.get("candidate_name"),
        candidate_position=args.get("candidate_position"),
        notes=args.get("notes"),
    )
    return result.as_dict()


def _tool_post_workable_note(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    actor = Actor.agent(int(agent_run.id))
    result = post_workable_note.run(
        db,
        actor,
        organization_id=int(role.organization_id),
        application_id=int(args["application_id"]),
        body=str(args["body"]),
    )
    return result.as_dict()


def _stamp_policy_revision_in_evidence(
    db: Session, *, role: Role, evidence: dict[str, Any] | None
) -> dict[str, Any]:
    """Augment the agent's ``evidence`` dict with the active policy
    revision id so every queued decision is traceable to the policy
    that produced it. Idempotent: if the agent already supplied a
    ``policy_revision_id``, it wins.
    """
    base: dict[str, Any] = dict(evidence or {})
    if base.get("policy_revision_id"):
        return base
    try:
        from ..decision_policy.engine import load_active_policy

        row = load_active_policy(
            db,
            organization_id=int(role.organization_id),
            role_id=int(role.id),
        )
        base["policy_revision_id"] = int(row.revision_id)
    except Exception:  # pragma: no cover — never block queueing on this
        pass
    return base


# Maps each queueable decision_type to the role attribute that controls
# auto-execution. When the toggle is True, the agent's queue tool calls
# the same action ``approve_decision.run`` triggers on recruiter approval
# rather than creating a pending Decision Hub card.
_AUTO_TOGGLE_FOR_DECISION_TYPE: dict[str, str] = {
    "advance_to_interview": "auto_promote",
    "reject": "auto_reject",
    "skip_assessment_reject": "auto_reject",
}


def _auto_execute_decision(
    db: Session,
    *,
    role: Role,
    decision: Any,
    decision_type: str,
) -> None:
    """Resolve and execute an AgentDecision immediately as a system action.

    Mirrors the side effects of ``approve_decision.run`` — same
    underlying action call, same idempotency key shape — but with
    ``actor=system`` and a ``human_disposition`` that records the
    auto-toggle that drove the call.
    """
    actor = Actor.system()
    metadata = {
        "agent_decision_id": int(decision.id),
        "agent_run_id": int(decision.agent_run_id) if decision.agent_run_id else None,
        "agent_reasoning": decision.reasoning,
        "model_version": decision.model_version,
        "prompt_version": decision.prompt_version,
        "auto_toggle": _AUTO_TOGGLE_FOR_DECISION_TYPE.get(decision_type),
    }
    reason = f"Auto-approved per role.{metadata['auto_toggle']} (decision #{decision.id})"

    if decision_type == "advance_to_interview":
        advance_stage.run(
            db,
            actor,
            organization_id=int(role.organization_id),
            application_id=int(decision.application_id),
            to_stage="technical_interview",
            reason=reason,
            idempotency_key=f"approve_decision:{decision.id}",
            metadata=metadata,
        )
    elif decision_type in ("reject", "skip_assessment_reject"):
        reject_application.run(
            db,
            actor,
            organization_id=int(role.organization_id),
            application_id=int(decision.application_id),
            reason=reason,
            idempotency_key=f"approve_decision:{decision.id}",
            metadata={**metadata, "decision_type": decision_type},
        )
    decision.status = "approved"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = None
    decision.resolution_note = reason
    decision.human_disposition = "auto_approved"


def _queue(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any], decision_type: str
) -> Any:
    actor = Actor.agent(int(agent_run.id))
    evidence = _stamp_policy_revision_in_evidence(
        db, role=role, evidence=args.get("evidence")
    )
    decision = queue_decision.run(
        db,
        actor,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        application_id=int(args["application_id"]),
        decision_type=decision_type,
        reasoning=str(args["reasoning"]),
        evidence=evidence,
        confidence=float(args["confidence"]),
        model_version=str(agent_run.model_version or ""),
        prompt_version=str(agent_run.prompt_version or ""),
    )
    auto_attr = _AUTO_TOGGLE_FOR_DECISION_TYPE.get(decision_type)
    if auto_attr and bool(getattr(role, auto_attr, False)):
        _auto_execute_decision(
            db, role=role, decision=decision, decision_type=decision_type
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


def _tool_evaluate_policy(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    """Bridge: gather sub-agent outputs and run the deterministic engine."""
    import logging

    application_id = int(args["application_id"])
    skip_cache = bool(args.get("skip_cache", False))
    metering_context = {
        "agent_run_id": int(agent_run.id),
        "feature": "evaluate_policy",
    }
    verdict, sub_outputs = policy_evaluator.evaluate_for_application(
        db,
        role=role,
        application_id=application_id,
        metering_context=metering_context,
        skip_cache=skip_cache,
    )
    # Telemetry: structured log so the Hub's signals dashboard can
    # bucket evaluations per (org, role, decision_type, revision).
    logging.getLogger("taali.policy.evaluation").info(
        "policy_evaluation",
        extra={
            "event": "policy_evaluation",
            "organization_id": int(role.organization_id),
            "role_id": int(role.id),
            "application_id": application_id,
            "agent_run_id": int(agent_run.id),
            "policy_revision_id": verdict.policy_revision_id,
            "decision_type": verdict.decision_type,
            "decision_point": verdict.decision_point,
            "confidence": float(verdict.confidence),
            "intent_overrode": bool(verdict.intent_overrode),
            "skipped_due_to_manual": bool(verdict.skipped_due_to_manual),
        },
    )
    return {
        "decision_type": verdict.decision_type,
        "decision_point": verdict.decision_point,
        "confidence": float(verdict.confidence),
        "reasoning": verdict.reasoning,
        "rule_path": list(verdict.rule_path),
        "policy_revision_id": (
            int(verdict.policy_revision_id)
            if verdict.policy_revision_id is not None
            else None
        ),
        "intent_overrode": bool(verdict.intent_overrode),
        "skipped_due_to_manual": bool(verdict.skipped_due_to_manual),
        "sub_agent_outputs": {
            name: (
                {
                    "ok": sa.ok,
                    "output": sa.output,
                    "confidence": sa.confidence,
                    "cache_hit": sa.cache_hit,
                    "error": sa.error,
                }
            )
            for name, sa in sub_outputs.items()
        },
    }


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


# ---------------------------------------------------------------------------
# Cohort tools
# ---------------------------------------------------------------------------


def _tool_survey_role_state(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    return cohort_tools.survey_role_state(
        db,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
    )


def _tool_find_apps_in_state(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    state = str(args.get("state") or "")
    limit = int(args.get("limit") or 50)
    ids = cohort_tools.find_apps_in_state(
        db,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        state=state,
        limit=limit,
    )
    return {"state": state, "application_ids": ids, "count": len(ids)}


def _tool_read_pending_recruiter_inputs(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    rows = cohort_tools.read_pending_recruiter_inputs(
        db,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
    )
    return {"rows": rows}


def _tool_batch_score_cv(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    """Run score_cv across N applications in one tool call."""
    actor = Actor.agent(int(agent_run.id))
    application_ids = [int(i) for i in (args.get("application_ids") or [])][:25]
    force = bool(args.get("force", False))
    out: list[dict[str, Any]] = []
    for app_id in application_ids:
        try:
            job = score_cv.run(
                db,
                actor,
                organization_id=int(role.organization_id),
                application_id=app_id,
                force=force,
            )
            if job is None:
                out.append({"application_id": app_id, "status": "skipped"})
            else:
                out.append(
                    {
                        "application_id": app_id,
                        "job_id": int(job.id),
                        "status": str(job.status),
                    }
                )
        except Exception as exc:  # pragma: no cover — defensive
            out.append({"application_id": app_id, "status": "error", "error": str(exc)})
    return {"results": out, "total": len(out)}


def _tool_ask_recruiter(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    actor = Actor.agent(int(agent_run.id))
    row = ask_recruiter.open(
        db,
        actor,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        kind=str(args.get("kind") or ""),
        prompt=str(args.get("prompt") or ""),
        options=args.get("options"),
        response_schema=args.get("response_schema"),
        rationale=args.get("rationale"),
    )
    return {
        "needs_input_id": int(row.id),
        "kind": row.kind,
        "is_open": row.is_open,
        "created_at": row.created_at.isoformat() if row.created_at else None,
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
    "refresh_candidate_graph": _tool_refresh_candidate_graph,
    "get_cohort_signals": _tool_get_cohort_signals,
    "score_cv": _tool_score_cv,
    "send_assessment": _tool_send_assessment,
    "resend_assessment_invite": _tool_resend_assessment_invite,
    "create_application": _tool_create_application,
    "post_workable_note": _tool_post_workable_note,
    "queue_advance_decision": _tool_queue_advance_decision,
    "queue_reject_decision": _tool_queue_reject_decision,
    "queue_skip_assessment_reject_decision": _tool_queue_skip_assessment_reject_decision,
    "evaluate_policy": _tool_evaluate_policy,
    # Cohort survey + batch + ask-recruiter tools (Phase 7).
    "survey_role_state": _tool_survey_role_state,
    "find_apps_in_state": _tool_find_apps_in_state,
    "read_pending_recruiter_inputs": _tool_read_pending_recruiter_inputs,
    "batch_score_cv": _tool_batch_score_cv,
    "ask_recruiter": _tool_ask_recruiter,
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
