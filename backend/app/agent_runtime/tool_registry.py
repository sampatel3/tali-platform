"""Anthropic tool catalogue + dispatcher for the autonomous agent.

Reuses the read-side ``app.mcp.handlers`` and the mutation-side
``app.actions`` package so the agent shares the same surface as the
recruiter UI / Taali Chat. The dispatcher knows which tool calls map to
read handlers (which want a User-shaped context) and which call mutation
actions (which want an ``Actor``).
"""

from __future__ import annotations

import logging
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
from ..actions._decision_side_effects import apply_decision_side_effects
from ..actions.types import Actor
from ..mcp import handlers as mcp_handlers
from ..models.agent_decision import AgentDecision
from ..models.agent_needs_input import NEEDS_INPUT_KINDS
from ..models.agent_run import AgentRun
from ..models.assessment import Assessment
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..services import cohort_signals_service
from ..services.assessment_autosend_guard import check_auto_send
from ..services.agent_policy_settings import automation_enabled_for_decision
from ..services.auto_threshold_service import resolve_role_fit_threshold
from ..services.decision_evidence_service import blocked_must_have_requirements
from ..services.decision_presentation_service import normalize_candidate_summary
from ..sub_agents.base import public_sub_agent_error
from . import calibration, cohort_tools, decision_translation, policy_evaluator


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
            "decision so your reasoning cites concrete fields. The "
            "``recruiter_notes`` field holds standing guidance the recruiter "
            "wrote about THIS candidate (e.g. 'already interviewed — not "
            "suitable', 'lacks the technical depth'); treat it as a strong human "
            "signal — defer to it over your own read and don't re-propose an "
            "action it rules out."
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
                        "candidate_tie_break for advance/reject ambiguity; "
                        "other as a last resort. Per-candidate HITL gates "
                        "(approve send/resend) are NOT NeedsInput — they flow "
                        "through agent_decisions via send_assessment / "
                        "resend_assessment_invite tools."
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
                    "enum": ["sourced", "applied", "invited", "in_assessment", "review", "advanced"],
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
                        "workable_score",
                        "assessment_score",
                        "role_fit_score",
                        "created_at",
                    ],
                    "default": "taali_score",
                },
                "sort_order": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
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
                    "minItems": 2,
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
            "email to the candidate. With auto_send_assessment on, an enabled and "
            "unpaused role sends automatically while policy, budget, credit, "
            "and daily-volume guards pass; otherwise this returns an "
            "awaiting-recruiter-approval decision. "
            "Use when the candidate has cleared CV/pre-screen review and is "
            "ready to take the technical assessment. Idempotent: returns "
            "status='already_exists' if a valid assessment is already open. "
            "Refuses with status='misconfigured' if the role has 0 or >1 "
            "tasks linked unless task_id is passed explicitly. Returns "
            "{assessment_id, status, detail} where status includes 'queued', "
            "'already_exists', 'awaiting_recruiter_approval', "
            "'insufficient_credits', 'misconfigured', or 'blocked'."
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
            "expired. Honors the role's auto_resend_assessment toggle — when "
            "it is False, queues an "
            "AgentDecision for human approval instead of resending. Returns "
            "{assessment_id, status, detail} where status is 'queued', "
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
    # DECISIONS — positive actions may auto-execute under auto_promote;
    # irreversible reject recommendations always require human confirmation.
    # ------------------------------------------------------------------
    {
        "name": "queue_advance_decision",
        "description": (
            "Recommend advancing this candidate to the technical interview stage. "
            "With auto_advance on, an enabled/unpaused role executes an on-policy "
            "advance automatically. Otherwise it remains in the Decision Hub for "
            "human approval. Reasoning must cite concrete CV/score evidence."
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
            "This irreversible rejection ALWAYS requires explicit human "
            "confirmation, including when auto_reject is on. "
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
            "always approves or overrides this LLM/full-score recommendation; "
            "auto_reject only applies to the separate deterministic pre-screen "
            "path. Use only when CV-match and pre-screen "
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
    {
        "name": "queue_escalate_decision",
        "description": (
            "Queue a non-executable escalation for recruiter adjudication when "
            "evaluate_policy returns decision_type='escalate_low_confidence'. "
            "This creates a pending Decision Hub card; it never advances, rejects, "
            "or contacts the candidate. Preserve the policy's low-confidence or "
            "disagreement reasoning and cite the signals that conflicted. Use only "
            "for the matching policy verdict, never as a substitute for your own "
            "uncertainty."
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
            "queue_skip_assessment_reject_decision / queue_escalate_decision "
            "so the human-readable "
            "reasoning + audit trail are anchored to the policy.\n\n"
            "How to react to the returned ``decision_type``:\n"
            "- ``queue_advance_decision`` / ``queue_reject_decision`` / "
            "``queue_skip_assessment_reject_decision`` → call the matching queue tool "
            "with the same reasoning.\n"
            "- ``escalate_low_confidence`` → call ``queue_escalate_decision`` "
            "with the verdict's reasoning and confidence; build its evidence "
            "from ``rule_path`` and the conflicting ``sub_agent_outputs`` so "
            "the recruiter can adjudicate.\n"
            "- ``no_action`` / ``skip`` → this candidate is not actionable "
            "right now. Do NOT re-call evaluate_policy on the same candidate "
            "this cycle. Move on: either pick a different candidate from your "
            "earlier ``find_apps_in_state`` results, or call "
            "``agent_run_complete`` if you've already tried the top candidates "
            "from each state. Repeated ``no_action`` verdicts in one cycle "
            "mean the cohort isn't ready — record an observation and end.\n"
            "- ``skipped_due_to_manual=true`` (any decision_type) → the "
            "recruiter has already acted; do NOT queue.\n\n"
            "Hard rule: never call ``evaluate_policy`` more than ONCE per "
            "(application_id, cycle). Calling it twice on the same candidate "
            "in one cycle indicates you're spinning — end the cycle instead."
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
    # MEMORY — cross-cycle breadcrumbs
    # ------------------------------------------------------------------
    {
        "name": "record_observation",
        "description": (
            "Persist a short note that survives across cycles. Use this when "
            "you notice something worth carrying forward — e.g. 'cohort "
            "clusters around score 60-65 on must-haves; threshold may be too "
            "high', or 'recruiter has 3 open ask_recruiter cards from last "
            "cycle — wait before queueing new decisions'. Notes are rendered "
            "into the system prompt of the NEXT cycle. Capped at 10 (FIFO). "
            "Cheaper than re-deriving the observation next cycle and the only "
            "way to leave durable context for yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "Short observation, <200 chars.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["pattern", "blocker", "todo", "context"],
                    "description": (
                        "pattern = something about the cohort. "
                        "blocker = work stuck waiting on something. "
                        "todo = work to pick up next cycle. "
                        "context = anything else worth remembering."
                    ),
                },
            },
            "required": ["note"],
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
        # B2: cache_control on the final tool entry caches the entire
        # AGENT_TOOLS array. Rounds 2-18 of each cycle (and subsequent
        # ticks within the TTL window) hit cache for the ~3-5K tokens
        # of tool schemas instead of paying full input price each time.
        #
        # MUST be ttl="1h" to match the system-prompt blocks. Anthropic
        # processes cache blocks in order (tools, system, messages) and
        # rejects a 1h block that comes AFTER a 5m block. Tools come
        # first, so a 5m here + 1h on the system prompt 400s every agent
        # call ("a ttl='1h' cache_control block must not come after a
        # ttl='5m' cache_control block"). Keep all agent cache blocks 1h.
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
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
        offset=max(0, int(args.get("offset") or 0)),
    )


def _tool_compare_applications(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    ids = args.get("application_ids") or []
    return mcp_handlers.compare_applications(
        db,
        _read_ctx(role),
        application_ids=[int(i) for i in ids],
    )


def _tool_nl_search_candidates(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    return mcp_handlers.nl_search_candidates(
        db,
        _read_ctx(role),
        query=str(args.get("query") or ""),
        # Autonomous search is always scoped and billed to the running role.
        # Never accept a model-supplied billing identity: otherwise an agent
        # could consume another role's allowance and bypass its own cap.
        role_id=int(role.id),
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
        require_role_admission=True,
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


def _existing_decision_for_subject(
    db: Session,
    *,
    role: Role,
    application_id: int,
    decision_type: str,
) -> Any:
    """Latest non-discarded AgentDecision for this (role, app, type).

    Used by the send_assessment / resend_assessment_invite HITL gates so
    repeated agent calls don't pile up duplicate cards in the recruiter's
    queue. Returns the row or None.
    """
    return (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == int(role.organization_id),
            AgentDecision.role_id == int(role.id),
            AgentDecision.application_id == application_id,
            AgentDecision.decision_type == decision_type,
            AgentDecision.status != "discarded",
        )
        .order_by(AgentDecision.created_at.desc())
        .first()
    )


def _tool_send_assessment(db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]) -> Any:
    actor = Actor.agent(int(agent_run.id))
    application_id = int(args["application_id"])

    # Cross-role guard (mirrors _tool_resend_assessment_invite). The agent runs
    # in the context of one role, but send_assessment.run reloads the
    # application and creates the assessment for the APPLICATION'S OWN role
    # (app.role_id). If we let a different-role application through here, every
    # downstream decision — the auto_promote gate AND the budget/volume
    # auto-send guard below — would be evaluated against the running role, not
    # the role that actually receives the invite, letting role B's caps be
    # bypassed while role A is under its limits. Refuse the send when the
    # application doesn't belong to the running role; the agent should only send
    # for its own role's candidates.
    app_row = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == int(role.organization_id),
        )
        .first()
    )
    if app_row is None:
        return {
            "status": "not_found",
            "application_id": application_id,
            "detail": "application not found in this organization",
        }
    if app_row.role_id is None or int(app_row.role_id) != int(role.id):
        return {
            "status": "wrong_role",
            "application_id": application_id,
            "detail": (
                f"application {application_id} belongs to role "
                f"{app_row.role_id}, not the running role {int(role.id)}; "
                "refusing send to avoid bypassing the other role's HITL "
                "policy and budget/volume caps"
            ),
        }

    # No assessment stage on this role — either no task is configured, or
    # the recruiter flipped auto_skip_assessment — so there is nothing to
    # send. Rather than dead-end (the approve/dispatch path would 422 on a
    # missing task), advance the strong candidate straight to interview.
    # This keeps a positive verdict actionable and mirrors the deterministic
    # bulk-decision pass's send→advance switch. Runs before the HITL gate
    # so it applies in both auto_promote modes (advance needs no task).
    if not decision_translation.role_has_assessment_stage(role):
        skip_toggled = bool(getattr(role, "auto_skip_assessment", False))
        result = _queue(
            db,
            agent_run=agent_run,
            role=role,
            args={
                "application_id": application_id,
                "reasoning": str(
                    args.get("reasoning")
                    or (
                        "Strong candidate; assessments are skipped on this role "
                        "(auto-skip), advancing directly to interview."
                        if skip_toggled
                        else "Strong candidate; role has no assessment task "
                        "configured, advancing directly to interview."
                    )
                ),
                "evidence": {
                    "redirected_from": "send_assessment",
                    "reason": (
                        "auto_skip_assessment" if skip_toggled else "no_assessment_task"
                    ),
                },
                "confidence": float(args.get("confidence") or 0.85),
            },
            decision_type="advance_to_interview",
        )
        return {
            "status": (
                "awaiting_recruiter_approval"
                if result["status"] == "pending"
                else result["status"]
            ),
            "decision_id": result["decision_id"],
            "application_id": application_id,
            "redirected_to": "advance_to_interview",
        }

    # HITL gate per Role.auto_promote — plus the auto-send safety guard.
    #
    # auto_promote=False → queue an AgentDecision(decision_type='send_assessment').
    #                      Recruiter approves/overrides on the Home page; the
    #                      approve path calls send_assessment.run directly.
    #                      The agent never has to ``consume`` an answer — once
    #                      the decision is approved, the action ran; the
    #                      next agent cycle sees the candidate already moved.
    # auto_promote=True  → dispatch the send immediately as a system action,
    #                      UNLESS the auto-send guard trips (role monthly budget
    #                      cap or the per-day volume cap). On a trip we neither
    #                      send silently nor drop the candidate: we fall through
    #                      to the same HITL card the auto_promote=False path
    #                      produces, tagged with the reason, so the recruiter can
    #                      approve the batch ("send anyway") or hold. Autonomous
    #                      within the guard; human-in-the-loop only at the cap.
    #                      See services.assessment_autosend_guard.
    auto_promote = automation_enabled_for_decision(role, "send_assessment")
    guard_hold_reason: Optional[str] = None
    if auto_promote:
        guard = check_auto_send(db, role=role)
        if not guard.ok:
            auto_promote = False
            guard_hold_reason = guard.reason

    if not auto_promote:
        existing = _existing_decision_for_subject(
            db,
            role=role,
            application_id=application_id,
            decision_type="send_assessment",
        )
        if existing is not None:
            return {
                "status": (
                    "awaiting_recruiter_approval"
                    if existing.status == "pending"
                    else existing.status
                ),
                "decision_id": int(existing.id),
                "application_id": application_id,
            }

        # Preserve task_id / duration_minutes in evidence so the approve
        # path can dispatch with the same params the agent chose.
        task_id = args.get("task_id")
        duration = args.get("duration_minutes")
        evidence: dict[str, Any] = {}
        if task_id is not None:
            evidence["task_id"] = int(task_id)
        if duration is not None:
            evidence["duration_minutes"] = int(duration)
        # When the guard downgraded an auto_promote=True send to a card, tag
        # the reason so the Decision Hub can show why it's awaiting approval.
        if guard_hold_reason is not None:
            evidence["auto_send_hold"] = guard_hold_reason

        # No generic placeholder: pass the agent's rationale when it wrote one,
        # else leave it empty so queue_decision derives the canonical cv_match
        # summary — one consistent card shape for every producer. Prefix the
        # guard note when the send was held so the card reads self-explanatory.
        base_reasoning = str(args.get("reasoning") or "")
        if guard_hold_reason is not None:
            hold_note = f"Auto-send held for review: {guard_hold_reason}."
            reasoning = f"{hold_note} {base_reasoning}".strip()
        else:
            reasoning = base_reasoning
        queue_args = {
            "application_id": application_id,
            "reasoning": reasoning,
            "evidence": evidence or None,
            "confidence": float(args.get("confidence") or 0.85),
        }
        result = _queue(
            db,
            agent_run=agent_run,
            role=role,
            args=queue_args,
            decision_type="send_assessment",
        )
        # _queue returns {decision_id, status, decision_type}; the agent
        # reads ``status`` to decide whether to retry or move on.
        out = {
            "status": (
                "awaiting_recruiter_approval"
                if result["status"] == "pending"
                else result["status"]
            ),
            "decision_id": result["decision_id"],
            "application_id": application_id,
        }
        if guard_hold_reason is not None:
            out["auto_send_hold"] = guard_hold_reason
        return out

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

    # Cross-role guard. The agent runs in the context of one role. An
    # assessment can belong to a *different* role in the same org —
    # if we gated by the running role's auto_promote and that role had
    # auto_promote=True, we'd happily resend an invite for an
    # assessment whose own role has auto_promote=False, bypassing that
    # role's HITL policy. Refuse the resend entirely when the
    # assessment doesn't belong to the running role; the agent should
    # only resend invites for its own role's candidates.
    assessment = (
        db.query(Assessment)
        .filter(
            Assessment.id == assessment_id,
            Assessment.organization_id == int(role.organization_id),
        )
        .first()
    )
    if assessment is None:
        return {
            "status": "not_found",
            "assessment_id": assessment_id,
            "detail": "assessment not found in this organization",
        }
    if assessment.role_id is None or int(assessment.role_id) != int(role.id):
        return {
            "status": "wrong_role",
            "assessment_id": assessment_id,
            "detail": (
                f"assessment {assessment_id} belongs to role "
                f"{assessment.role_id}, not the running role {int(role.id)}; "
                "refusing resend to avoid bypassing the other role's HITL policy"
            ),
        }

    # HITL gate — same auto_promote toggle that gates send_assessment.
    # Resending an invite is a candidate-facing email, so it must
    # respect the same recruiter approval policy.
    #
    # auto_promote=True  → resend immediately as a system action.
    # auto_promote=False → queue an AgentDecision(decision_type='resend_assessment_invite')
    #                      anchored to the assessment's application_id (decisions
    #                      are per-application). evidence.assessment_id tells the
    #                      approve path which assessment to resend.
    if not automation_enabled_for_decision(role, "resend_assessment_invite"):
        if assessment.application_id is None:
            return {
                "status": "misconfigured",
                "assessment_id": assessment_id,
                "detail": (
                    f"assessment {assessment_id} has no application_id; cannot "
                    "anchor a decision row to it."
                ),
            }
        application_id = int(assessment.application_id)
        existing = _existing_decision_for_subject(
            db,
            role=role,
            application_id=application_id,
            decision_type="resend_assessment_invite",
        )
        # Resend gates can stack across distinct assessment_ids for the
        # same application, so the existing-row check also filters on
        # evidence.assessment_id to keep them separate.
        if existing is not None and (existing.evidence or {}).get("assessment_id") == assessment_id:
            return {
                "status": (
                    "awaiting_recruiter_approval"
                    if existing.status == "pending"
                    else existing.status
                ),
                "decision_id": int(existing.id),
                "assessment_id": assessment_id,
            }

        queue_args = {
            "application_id": application_id,
            "reasoning": str(
                args.get("reasoning")
                or f"Agent recommends resending the invite for assessment {assessment_id}."
            ),
            "evidence": {"assessment_id": assessment_id},
            "confidence": float(args.get("confidence") or 0.8),
        }
        result = _queue(
            db,
            agent_run=agent_run,
            role=role,
            args=queue_args,
            decision_type="resend_assessment_invite",
            # Two assessments on the same application would otherwise
            # collide on the base idempotency key {run}:{app}:{type}
            # and the second resend would silently reuse the first
            # decision row (Codex #176). Include assessment_id so each
            # invite gets its own queue entry.
            idempotency_key_suffix=f"assess{assessment_id}",
        )
        return {
            "status": (
                "awaiting_recruiter_approval"
                if result["status"] == "pending"
                else result["status"]
            ),
            "decision_id": result["decision_id"],
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

    # Single-role execution boundary. The agent runs in the context of
    # one role; creating an application under a *different* role in the
    # same org would let role A's agent provision candidates into role
    # B's pipeline, mixing intent and bypassing role B's policy. Refuse
    # cross-role creates outright. (Org boundary is enforced inside the
    # action via get_role; this guard is the stricter intra-org check.)
    if target_role_id != int(role.id):
        return {
            "status": "wrong_role",
            "role_id": target_role_id,
            "detail": (
                f"create_application targets role {target_role_id}, but the "
                f"running agent is for role {int(role.id)}; refusing to "
                "create applications outside the running role's scope"
            ),
        }

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
    application_id = int(args["application_id"])

    # Single-role execution boundary — matches the guard on
    # resend_assessment_invite. An agent running for role A posting a
    # note to role B's candidate would leak agent-side actions across
    # role workflows and surface in role B's Workable feed with no
    # corresponding pipeline event under role B's intent. Refuse.
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == int(role.organization_id),
        )
        .first()
    )
    if app is None:
        return {
            "status": "not_found",
            "application_id": application_id,
            "detail": "application not found in this organization",
        }
    if app.role_id is None or int(app.role_id) != int(role.id):
        return {
            "status": "wrong_role",
            "application_id": application_id,
            "detail": (
                f"application {application_id} belongs to role {app.role_id}, "
                f"not the running role {int(role.id)}; refusing to post a "
                "note across roles"
            ),
        }

    result = post_workable_note.run(
        db,
        actor,
        organization_id=int(role.organization_id),
        application_id=application_id,
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


# Evidence fields that assert *why policy acted* are owned by the server.  Tool
# arguments are model-authored, so accepting these keys from ``args.evidence``
# would let an agent label its own judgment as policy or invent decisive
# requirements.  The matching evaluate_policy snapshot below is the only writer.
_POLICY_EVIDENCE_KEYS = frozenset(
    {
        "decision_source",
        "decision_trigger",
        "decision_factors",
        "decision_point",
        "candidate_summary",
        "engine_verdict",
        "policy_basis",
        "policy_confidence",
        "policy_reasoning",
        "policy_revision_id",
        "rule_path",
    }
)


def _fired_policy_rule(rule_path: Any) -> str | None:
    if not isinstance(rule_path, list):
        return None
    for step in reversed(rule_path):
        if isinstance(step, str) and step.startswith("rule:fired:"):
            return step[len("rule:fired:") :] or None
    return None


def _candidate_summary_snapshot(app: CandidateApplication | None) -> str | None:
    details = getattr(app, "cv_match_details", None) if app is not None else None
    if not isinstance(details, dict):
        return None
    summary = normalize_candidate_summary(details.get("summary"))
    if summary:
        return summary
    bullets = details.get("score_rationale_bullets")
    if isinstance(bullets, list):
        for bullet in bullets:
            summary = normalize_candidate_summary(bullet)
            if summary:
                return summary
    return None


def _policy_snapshot_for_evaluation(
    db: Session,
    *,
    role: Role,
    application_id: int,
    verdict: Any,
    sub_outputs: dict[str, Any],
    persisted_decision_type: str | None,
) -> dict[str, Any]:
    """Build the server-owned, cycle-scoped snapshot later attached by queue.

    It stays transient until the model queues the exact persisted decision type.
    That exact-match condition prevents an off-policy model action from borrowing
    the engine's attribution or causal trace.
    """
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(role.organization_id),
        )
        .one_or_none()
    )

    def output_value(agent_name: str, key: str) -> Any:
        result = sub_outputs.get(agent_name)
        if result is None or not bool(getattr(result, "ok", False)):
            return None
        output = getattr(result, "output", None)
        return output.get(key) if isinstance(output, dict) else None

    rule_path = list(getattr(verdict, "rule_path", None) or [])
    fired = _fired_policy_rule(rule_path)
    role_fit = output_value("cv_scoring", "role_fit_score")
    if role_fit is None and app is not None:
        role_fit = getattr(app, "role_fit_score_cache_100", None)
        if role_fit is None:
            role_fit = getattr(app, "cv_match_score", None)
    pre_screen = output_value("pre_screen", "score")
    if pre_screen is None and app is not None:
        pre_screen = getattr(app, "genuine_pre_screen_score_100", None)
    taali_score = output_value("assessment_scoring", "taali_score")
    if taali_score is None and app is not None:
        taali_score = getattr(app, "taali_score_cache_100", None)

    try:
        threshold = resolve_role_fit_threshold(db, role=role)
    except Exception:  # pragma: no cover - presentation context is best effort
        threshold = None

    snapshot: dict[str, Any] = {
        "_persisted_decision_type": persisted_decision_type,
        "decision_source": "policy",
        "source": "agent_runtime_policy",
        "engine_verdict": str(getattr(verdict, "decision_type", "") or ""),
        "decision_point": getattr(verdict, "decision_point", None),
        "rule_path": rule_path,
        "decision_trigger": fired,
        "policy_reasoning": getattr(verdict, "reasoning", None),
        "policy_confidence": getattr(verdict, "confidence", None),
        "policy_revision_id": getattr(verdict, "policy_revision_id", None),
        "effective_threshold": threshold,
        "has_assessment_task": decision_translation.role_has_assessment_stage(role),
        "role_fit_score": role_fit,
        "pre_screen_score": pre_screen,
        "taali_score": taali_score,
        "candidate_summary": _candidate_summary_snapshot(app),
    }
    if fired == "must_have_blocked" and app is not None:
        snapshot["decision_factors"] = blocked_must_have_requirements(app)
    return snapshot


def _queue_evidence(
    db: Session,
    *,
    agent_run: AgentRun,
    role: Role,
    application_id: int,
    decision_type: str,
    supplied: dict[str, Any] | None,
) -> dict[str, Any]:
    """Sanitize model evidence and merge an exact-match policy snapshot."""
    base = dict(supplied or {})
    for key in _POLICY_EVIDENCE_KEYS:
        base.pop(key, None)
    # Source is generic evidence in older decisions, but these reserved values
    # imply policy provenance and must not be model-selectable.
    if str(base.get("source") or "").strip().lower() in {
        "agent_runtime_policy",
        "bulk_decision",
        "score_time_decision",
        "post_handover_second_opinion",
        "pre_screen_threshold",
        "knockout_screening",
    }:
        base.pop("source", None)

    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(role.organization_id),
        )
        .one_or_none()
    )
    candidate_summary = _candidate_summary_snapshot(app)
    if candidate_summary:
        base["candidate_summary"] = candidate_summary

    snapshots = getattr(agent_run, "__engine_policy_snapshots__", None) or {}
    snapshot = snapshots.get(int(application_id))
    if isinstance(snapshot, dict) and snapshot.get("_persisted_decision_type") == decision_type:
        for key, value in snapshot.items():
            if key.startswith("_") or value is None:
                continue
            base[key] = value

    return _stamp_policy_revision_in_evidence(db, role=role, evidence=base)


# Maps each queueable decision_type to the role attribute that expresses its
# autonomy preference. A matching toggle is necessary but not sufficient for
# auto-execution: the role must also be enabled/unpaused and on-policy, and the
# human-confirm rail below always withholds irreversible reject types.
_AUTO_TOGGLE_FOR_DECISION_TYPE: dict[str, str] = {
    "advance_to_interview": "auto_advance",
    "reject": "auto_reject",
    "skip_assessment_reject": "auto_reject",
    # send_assessment / resend_assessment_invite share the same
    # auto_promote toggle: it gates every candidate-facing send the
    # agent originates. Off → queue an AgentDecision for recruiter
    # approval. On → dispatch immediately as a system action.
    "send_assessment": "auto_send_assessment",
    "resend_assessment_invite": "auto_resend_assessment",
}


# Decision types whose auto-execution is subject to the assessment auto-send
# guard (role monthly budget cap + per-day volume cap). Only ``send_assessment``
# creates a new candidate-facing assessment invite at volume; advance/reject are
# out of scope and a resend targets an already-invited candidate (no new
# Assessment row). Kept here as the single source of truth for the ``_queue``
# defense-in-depth check that mirrors the gate in ``_tool_send_assessment``.
_AUTO_SEND_GUARDED_TYPES: frozenset[str] = frozenset({"send_assessment"})


# Off-policy auto-execution guard (TAA-22 / AUDIT_02 P2-TALI-01).
#
# "Deterministic verdict, never LLM" is structural in the mainspring
# substrate (the reasoner's return type can't carry a verdict). On this
# agent surface it was convention-only: the LLM's queue_* tools hardcode a
# decision_type and (with the auto toggle on) auto-execute, without any
# server-side check that the queued type matches what the deterministic
# engine would emit. This binds the IRREVERSIBLE step to the engine: a
# hire-relevant decision_type is only auto-executed if it matches the
# engine verdict captured this cycle by evaluate_policy. Both sides speak
# the persisted-NOUN vocabulary (advance_to_interview, …): the queue tool
# passes the noun, and evaluate_policy stores the noun by translating the
# engine's verbs (queue_advance_decision, …) through
# decision_translation.resolve_persisted_decision_type before capture.
#
# Scope: only the auto-executing HIRE-PROGRESSION verdict (advance) is bound
# here. reject / skip_assessment_reject are already held for human confirmation
# (TAA-11), and send_assessment / resend are operational (re-sendable, not a
# hire/no-hire verdict) — so they stay exempt to avoid withholding legitimate
# invite sends. advance is the one irreversible-ish auto-executed verdict left
# that an off-policy LLM could push, so it must match the engine.
_ENGINE_VERDICT_EQUIV: dict[str, frozenset[str]] = {
    "advance_to_interview": frozenset({"advance_to_interview"}),
}


def _engine_verdict_for(agent_run: AgentRun, application_id: int) -> Optional[str]:
    """The deterministic engine verdict captured for this application this
    cycle by ``evaluate_policy`` (``__engine_verdicts__`` is attached to the
    AgentRun instance; it does not persist)."""
    verdicts = getattr(agent_run, "__engine_verdicts__", None) or {}
    return verdicts.get(int(application_id))


def _is_on_policy(
    agent_run: AgentRun, application_id: int, decision_type: str
) -> tuple[bool, Optional[str]]:
    """Returns (on_policy, engine_decision_type). Decision types that aren't a
    hire/no-hire verdict (e.g. resend_assessment_invite) are exempt. For
    hire-relevant types the queued type must match the captured engine
    verdict; a missing or mismatched verdict fails SAFE -> not on-policy, so
    auto-execution is withheld and the decision routes to human review."""
    expected = _ENGINE_VERDICT_EQUIV.get(decision_type)
    if expected is None:
        return True, None
    engine_dt = _engine_verdict_for(agent_run, application_id)
    return (engine_dt in expected), engine_dt


# Human-confirm rail (TAA-11 / AUDIT_01 P1-TALI-03).
#
# A reject is IRREVERSIBLE: the candidate's side effect is a Workable
# *disqualify* (``reject_application.run`` →
# ``disqualify_candidate_in_workable``), which fires the org's
# disqualify-stage rejection email. Unlike an advance (an internal
# hand-back / stage move the recruiter can undo) or an assessment send
# (re-sendable), a disqualify can't be cleanly walked back once the
# candidate has been emailed.
#
# The product non-goal is "no verdicts that bypass a human recruiter."
# ``role.auto_reject`` is opt-in, but even opted-in we do NOT let the
# agent push the irreversible Workable disqualify with zero human in the
# loop. These decision types are therefore EXCLUDED from auto-execution:
# the queue tool still records the agent's reject *recommendation* (so the
# toggle, the reasoning, and the audit row are all preserved), but the
# decision stays ``pending`` in the Decision Hub awaiting an explicit
# one-click recruiter confirmation. The recruiter's approve path runs the
# exact same ``reject_application.run`` action — the only thing the rail
# costs is one human confirmation before an irreversible candidate-facing
# action. Reversible decisions (advance / send / resend) are unaffected
# and still auto-execute under their ``auto_promote`` toggle.
_HUMAN_CONFIRM_REQUIRED_DECISION_TYPES: frozenset[str] = frozenset(
    {"reject", "skip_assessment_reject"}
)


def _auto_execute_decision(
    db: Session,
    *,
    role: Role,
    decision: Any,
    decision_type: str,
) -> bool:
    """Resolve and execute an AgentDecision immediately as a system action.

    Mirrors the side effects of ``approve_decision.run`` — same
    underlying action call, same idempotency key shape — but with
    ``actor=system`` and a ``human_disposition`` that records the
    auto-toggle that drove the call.

    Defense-in-depth for the human-confirm rail (TAA-11 / P1-TALI-03):
    an irreversible reject must never reach this auto-execute path. The
    sole caller (``_queue``) already excludes those types, but a future
    caller could regress that; refuse here so the invariant holds at the
    side-effect boundary, not just the gate above it.
    """
    if decision_type in _HUMAN_CONFIRM_REQUIRED_DECISION_TYPES:
        raise ValueError(
            f"refusing to auto-execute irreversible decision_type "
            f"'{decision_type}' — it requires explicit human confirmation "
            f"(TAA-11 / P1-TALI-03). Leave the decision pending for the "
            f"recruiter to approve."
        )
    from ..services.role_execution_guard import (
        assessment_task_is_current,
        automatic_role_action_block_reason,
        lock_live_role,
    )

    live_role = lock_live_role(
        db,
        role_id=int(role.id),
        organization_id=int(role.organization_id),
    )
    role_block = automatic_role_action_block_reason(live_role, db=db)
    auto_toggle = _AUTO_TOGGLE_FOR_DECISION_TYPE.get(decision_type)
    if (
        role_block is None
        and auto_toggle
        and not automation_enabled_for_decision(live_role, decision_type)
    ):
        role_block = f"role.{auto_toggle} is disabled"
    if role_block:
        held = dict(decision.evidence or {})
        held["auto_execute_hold"] = {
            "status": "role_not_runnable",
            "detail": role_block,
        }
        decision.evidence = held
        db.add(decision)
        return False
    # ``live_role`` is non-null when no block reason was returned. Use this
    # populate-existing row for every subsequent side effect; the Role lock
    # serializes a stale queued action against Turn off / requisition republish.
    role = live_role

    if decision_type == "advance_to_interview":
        prior_assessment = (
            db.query(Assessment)
            .filter(
                Assessment.application_id == int(decision.application_id),
                Assessment.organization_id == int(role.organization_id),
                Assessment.role_id == int(role.id),
                Assessment.is_voided.is_(False),
            )
            .order_by(Assessment.id.desc())
            .first()
        )
        if prior_assessment is not None and not assessment_task_is_current(
            db, assessment=prior_assessment, role=role
        ):
            held = dict(decision.evidence or {})
            held["auto_execute_hold"] = {
                "status": "superseded_assessment_task",
                "detail": (
                    "Assessment result belongs to a task that is no longer "
                    "active/assignable for the current role. Human review is required."
                ),
                "assessment_id": int(prior_assessment.id),
                "task_id": int(prior_assessment.task_id),
            }
            decision.evidence = held
            db.add(decision)
            return False
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
            to_stage="advanced",
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
    elif decision_type == "send_assessment":
        ev = decision.evidence or {}
        send_result = send_assessment.run(
            db,
            actor,
            organization_id=int(role.organization_id),
            application_id=int(decision.application_id),
            task_id=int(ev["task_id"]) if ev.get("task_id") is not None else None,
            duration_minutes=int(ev.get("duration_minutes") or 90),
        )
        # A billing/configuration guard can legitimately no-op the send. Do not
        # close the decision as approved when no invite exists; keep the card
        # pending with an actionable hold reason so the recovery sweep or a
        # recruiter can resolve it later.
        send_status = getattr(send_result, "status", None)
        if send_status not in ("queued", "sent", "already_exists"):
            held = dict(decision.evidence or {})
            held["auto_execute_hold"] = {
                "status": send_status or "unknown",
                "detail": getattr(send_result, "detail", None),
            }
            decision.evidence = held
            db.add(decision)
            return False
    elif decision_type == "resend_assessment_invite":
        ev = decision.evidence or {}
        assessment_id = ev.get("assessment_id")
        if assessment_id is None:
            held = dict(decision.evidence or {})
            held["auto_execute_hold"] = {
                "status": "misconfigured",
                "detail": "evidence.assessment_id is required",
            }
            decision.evidence = held
            db.add(decision)
            return False
        resend_result = resend_assessment_invite.run(
            db,
            actor,
            organization_id=int(role.organization_id),
            assessment_id=int(assessment_id),
        )
        if getattr(resend_result, "status", None) not in {"queued", "resent"}:
            held = dict(decision.evidence or {})
            held["auto_execute_hold"] = {
                "status": getattr(resend_result, "status", None) or "unknown",
                "detail": getattr(resend_result, "detail", None),
            }
            decision.evidence = held
            db.add(decision)
            return False
    decision.status = "approved"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = None
    decision.resolution_note = reason
    decision.human_disposition = "auto_approved"

    # Auto resolution must have the same external/audit consequences as a
    # human approval. Previously the local stage changed but Workable stage
    # writeback, the activity note and graph/outcome trail were skipped.
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(decision.application_id),
            CandidateApplication.organization_id == int(role.organization_id),
        )
        .first()
    )
    org = (
        db.query(Organization)
        .filter(Organization.id == int(role.organization_id))
        .first()
        if app is not None
        else None
    )
    apply_decision_side_effects(
        db,
        actor,
        decision=decision,
        app=app,
        org=org,
        role=role,
        disposition="approved",
        note=reason,
        reject_notify=False,
    )
    if app is not None:
        try:
            from . import outcome_learning

            outcome_learning.record_outcome_for_approved_decision(
                db, decision=decision, application=app
            )
        except Exception:  # pragma: no cover - learning never blocks execution
            logging.getLogger("taali.agent.autonomy").exception(
                "auto-approved outcome recording failed decision_id=%s", decision.id
            )
    return True


def maybe_auto_execute_decision(
    db: Session,
    *,
    role: Role,
    decision: Any,
    decision_type: str,
    on_policy: bool = True,
    force_human_review: bool = False,
) -> dict[str, bool | str | None]:
    """Apply the role's autonomy contract to a freshly queued decision.

    Shared by LLM-authored and deterministic decision producers. Positive,
    reversible actions execute only while the role agent is enabled, unpaused,
    on-policy and opted into the matching toggle. Rejects and caller-marked
    conflicts stay pending. Assessment sends additionally honor the spend /
    volume guard. A failed/no-op action also stays pending instead of being
    falsely marked approved.
    """
    just_created = bool(getattr(decision, "_just_created", True))
    pending = str(getattr(decision, "status", "")) == "pending"
    auto_attr = _AUTO_TOGGLE_FOR_DECISION_TYPE.get(decision_type)
    toggle_on = bool(
        auto_attr and automation_enabled_for_decision(role, decision_type)
    )
    role_running = bool(
        getattr(role, "agentic_mode_enabled", False)
        and getattr(role, "agent_paused_at", None) is None
    )
    human_confirm_required = bool(
        force_human_review
        or decision_type in _HUMAN_CONFIRM_REQUIRED_DECISION_TYPES
    )

    guard_ok = True
    guard_hold_reason: Optional[str] = None
    if (
        decision_type in _AUTO_SEND_GUARDED_TYPES
        and toggle_on
        and role_running
        and just_created
        and pending
    ):
        guard = check_auto_send(db, role=role)
        guard_ok = bool(guard.ok)
        guard_hold_reason = guard.reason
        if not guard_ok:
            evidence = dict(decision.evidence or {})
            evidence.setdefault("auto_send_hold", guard_hold_reason)
            decision.evidence = evidence
            db.add(decision)

    eligible = bool(
        auto_attr
        and toggle_on
        and role_running
        and just_created
        and pending
        and on_policy
        and not human_confirm_required
        and guard_ok
    )
    executed = False
    action_held = False
    if eligible:
        # Keep the queued decision itself durable, but isolate all action-side
        # mutations (assessment row, stage transition, audit rows, etc.) in a
        # savepoint.  Several callers intentionally continue their cohort after
        # one candidate fails.  Without this boundary, an invite broker failure
        # raised after ``send_assessment`` flushed its rows could be swallowed by
        # the caller and those partial changes would be committed with the rest
        # of the cohort.
        db.flush()
        try:
            with db.begin_nested():
                executed = bool(
                    _auto_execute_decision(
                        db,
                        role=role,
                        decision=decision,
                        decision_type=decision_type,
                    )
                )
        except Exception:
            logging.getLogger("taali.agent.autonomy").exception(
                "auto-action rolled back decision_id=%s decision_type=%s",
                getattr(decision, "id", None),
                decision_type,
            )
            evidence = dict(decision.evidence or {})
            evidence["auto_execute_hold"] = {
                "status": "action_error",
                "detail": "automatic_action_failed",
            }
            decision.evidence = evidence
            db.add(decision)
            action_held = True
        else:
            action_held = not executed

    return {
        "executed": executed,
        "human_confirm_required": bool(human_confirm_required and toggle_on),
        "off_policy_withheld": bool(
            toggle_on and role_running and just_created and pending and not on_policy
        ),
        "auto_send_held": bool(
            decision_type in _AUTO_SEND_GUARDED_TYPES
            and toggle_on
            and role_running
            and just_created
            and pending
            and on_policy
            and not human_confirm_required
            and not guard_ok
        ),
        "action_held": action_held,
        "hold_reason": guard_hold_reason,
    }


def _queue(
    db: Session,
    *,
    agent_run: AgentRun,
    role: Role,
    args: dict[str, Any],
    decision_type: str,
    idempotency_key_suffix: Optional[str] = None,
) -> Any:
    actor = Actor.agent(int(agent_run.id))
    evidence = _queue_evidence(
        db,
        agent_run=agent_run,
        role=role,
        application_id=int(args["application_id"]),
        decision_type=decision_type,
        supplied=args.get("evidence"),
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
        idempotency_key_suffix=idempotency_key_suffix,
    )
    # Per-cycle decision budget counts ACTUAL new rows, not tool calls.
    # The orchestrator's by-name counter over-counted dedup paths
    # (send_assessment with existing decision; auto_promote=True direct
    # dispatch; queue_* IntegrityError-retry). Anchoring the counter
    # here means it tracks what's truly in the queue. (Codex #179)
    just_created = bool(getattr(decision, "_just_created", True))
    if just_created:
        agent_run.decisions_emitted = int(agent_run.decisions_emitted or 0) + 1
    # TAA-22 (P2-TALI-01): bind the irreversible auto-execution to the
    # deterministic engine. A hire-relevant decision_type is only
    # auto-executed if it matches the engine verdict captured this cycle by
    # evaluate_policy; a missing/mismatched verdict fails SAFE -> the decision
    # routes to human review instead of auto-executing off-policy.
    on_policy, engine_verdict = _is_on_policy(
        agent_run, int(args["application_id"]), decision_type
    )
    autonomy = maybe_auto_execute_decision(
        db,
        role=role,
        decision=decision,
        decision_type=decision_type,
        on_policy=on_policy,
    )
    if autonomy["off_policy_withheld"]:
        logging.getLogger("taali.agent.policy").warning(
            "off_policy_auto_execute_withheld role_id=%s app_id=%s "
            "queued=%s engine=%s run_id=%s",
            role.id, args["application_id"], decision_type,
            engine_verdict, agent_run.id,
        )
    if autonomy["auto_send_held"]:
        logging.getLogger("taali.agent.autosend").info(
            "auto_send_held role_id=%s app_id=%s reason=%s run_id=%s",
            role.id, args["application_id"], autonomy["hold_reason"], agent_run.id,
        )
    return {
        "decision_id": int(decision.id),
        "status": str(decision.status),
        "decision_type": decision_type,
        # Surface the rail to the agent so it knows the reject is awaiting a
        # human confirmation rather than silently executed. ``True`` only when
        # the role would otherwise have auto-executed (toggle on) but the
        # human-confirm rail held it pending.
        "human_confirm_required": bool(autonomy["human_confirm_required"]),
        # Surface the off-policy guard (TAA-22): True when the toggle would
        # have auto-executed but the queued decision_type did not match the
        # deterministic engine verdict, so it was held pending for review.
        "off_policy_withheld": bool(autonomy["off_policy_withheld"]),
        # Surface the auto-send guard: True when auto_promote would have sent
        # immediately but the budget/volume cap held it pending for review.
        "auto_send_held": bool(autonomy["auto_send_held"]),
        "action_held": bool(autonomy["action_held"]),
    }


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


def _tool_queue_escalate_decision(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    return _queue(
        db,
        agent_run=agent_run,
        role=role,
        args=args,
        decision_type="escalate_low_confidence",
    )


def _tool_evaluate_policy(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    """Bridge: gather sub-agent outputs and run the deterministic engine.

    Production data 2026-05-21 showed cycles spinning on 3-6 sequential
    ``evaluate_policy`` calls without ever queueing a decision (21 of 22
    MAX_TOOL_ROUNDS aborts followed this pattern). Root cause: the engine
    returns ``no_action`` for borderline candidates and the prompt didn't
    tell the agent how to react. The tool description has been updated
    to spell that out; this in-tool guard catches the repeat case anyway
    by returning a sentinel response that prompts the agent to stop or
    move on. Cheaper than re-running the full sub-agent fan-out for an
    already-evaluated candidate.
    """
    import logging

    application_id = int(args["application_id"])
    skip_cache = bool(args.get("skip_cache", False))

    # Cycle-scoped already-evaluated tracker — keyed on the agent_run row
    # so each cycle starts fresh. ``__evaluated_apps__`` is an attribute
    # we attach to the AgentRun instance; it does NOT persist to the DB.
    evaluated: set[int] = getattr(agent_run, "__evaluated_apps__", set())
    if application_id in evaluated and not skip_cache:
        logging.getLogger("taali.policy.evaluation").info(
            "policy_evaluation_repeat_blocked role_id=%s app_id=%s run_id=%s",
            role.id, application_id, agent_run.id,
        )
        return {
            "decision_type": "already_evaluated_this_cycle",
            "decision_point": "guard",
            "confidence": 0.0,
            "reasoning": (
                f"Already called evaluate_policy on application {application_id} "
                f"this cycle. Don't re-evaluate the same candidate — either pick "
                f"a different candidate from your earlier find_apps_in_state "
                f"results, or call agent_run_complete to end the cycle."
            ),
            "rule_path": ["repeat_blocked"],
            "policy_revision_id": None,
            "intent_overrode": False,
            "skipped_due_to_manual": False,
            "sub_agent_outputs": {},
        }
    evaluated.add(application_id)
    agent_run.__evaluated_apps__ = evaluated  # type: ignore[attr-defined]

    # org/role/entity must be present so the sub-agents' Anthropic calls
    # (cv_scoring, pre_screen) write attributable usage_events and count
    # toward the role's monthly budget. Without org_id the wrapper drops
    # the usage_event (no org → can't bill); without role_id the spend
    # never reaches the budget guard. Sub-agents override ``feature``
    # (score / prescreen) on their own call.
    metering_context = {
        "agent_run_id": int(agent_run.id),
        "organization_id": getattr(role, "organization_id", None),
        "role_id": int(role.id),
        "entity_id": f"application:{application_id}",
    }
    verdict, sub_outputs = policy_evaluator.evaluate_for_application(
        db,
        role=role,
        application_id=application_id,
        metering_context=metering_context,
        skip_cache=skip_cache,
    )
    # TAA-22: record the deterministic engine verdict for this application so
    # the queue_* tools can bind an auto-executed decision to it (see
    # _is_on_policy). Attached to the AgentRun instance for this cycle; not
    # persisted.
    _verdicts = getattr(agent_run, "__engine_verdicts__", None)
    if _verdicts is None:
        _verdicts = {}
        agent_run.__engine_verdicts__ = _verdicts  # type: ignore[attr-defined]
    # ``verdict.decision_type`` is the engine VERB (e.g. ``queue_advance_decision``),
    # but ``_is_on_policy`` is handed the persisted NOUN the queue_* tools carry
    # (``advance_to_interview``). Translate through the same map the bulk path uses
    # so the two vocabularies line up — without this, ``queue_advance_decision`` is
    # compared to ``{"advance_to_interview"}`` and EVERY on-policy advance reads as
    # off-policy and is wrongly withheld. The no-assessment-stage switch (send →
    # advance; no task OR auto_skip_assessment) is honoured via
    # ``role_has_assessment_stage``. ``escalate_low_confidence`` translates to
    # its persisted noun so the matching escalation tool can queue it; truly
    # non-queueable verdicts (skip / no_action) translate to ``None``. Either an
    # escalation noun or None still fails an attempted auto-advance safely.
    persisted_decision_type = decision_translation.resolve_persisted_decision_type(
        str(verdict.decision_type),
        has_assessment_task=decision_translation.role_has_assessment_stage(role),
    )
    _verdicts[int(application_id)] = persisted_decision_type
    snapshots = getattr(agent_run, "__engine_policy_snapshots__", None)
    if snapshots is None:
        snapshots = {}
        agent_run.__engine_policy_snapshots__ = snapshots  # type: ignore[attr-defined]
    snapshots[int(application_id)] = _policy_snapshot_for_evaluation(
        db,
        role=role,
        application_id=application_id,
        verdict=verdict,
        sub_outputs=sub_outputs,
        persisted_decision_type=persisted_decision_type,
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
                    "error": public_sub_agent_error(sa.error),
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


def _tool_record_observation(
    db: Session, *, agent_run: AgentRun, role: Role, args: dict[str, Any]
) -> Any:
    """Append a breadcrumb to role.agent_calibration.notes (capped 10, FIFO).

    Commits immediately rather than waiting for cycle end so the note
    survives an abort. Returns the note id (sequence within cycle) and
    the current note count so the agent can see whether the budget is
    spent.
    """
    note_text = str(args.get("note") or "").strip()
    if not note_text:
        return {"status": "skipped", "reason": "empty note"}
    if len(note_text) > 280:
        note_text = note_text[:277] + "..."
    kind = str(args.get("kind") or "context").strip().lower()
    if kind not in {"pattern", "blocker", "todo", "context"}:
        kind = "context"
    entry = {
        "note": note_text,
        "kind": kind,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "agent_run_id": int(agent_run.id),
    }
    calibration.save(db, role=role, updates={"notes": [entry]})
    # This tool is deliberately a mid-cycle durability boundary: the note
    # must survive a later abort, and the next round's hard-admission session
    # must be able to lock/read the role without competing with an uncommitted
    # role update held by this cycle.  A flush alone satisfied neither
    # contract (and deadlocked the independent admission session on Postgres;
    # SQLite reports the same issue as ``database table is locked: roles``).
    db.commit()
    db.refresh(role)
    db.refresh(agent_run)
    current_count = len((role.agent_calibration or {}).get("notes") or [])
    return {
        "status": "saved",
        "kind": kind,
        "current_note_count": current_count,
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
        except Exception:  # pragma: no cover — defensive
            logging.getLogger("taali.agent.tools").exception(
                "batch CV scoring failed application_id=%s agent_run_id=%s",
                app_id,
                getattr(agent_run, "id", None),
            )
            out.append(
                {
                    "application_id": app_id,
                    "status": "error",
                    "error": "cv_scoring_failed",
                }
            )
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
# send_assessment / resend_assessment_invite are HITL gates that now flow
# through the same decisions queue as advance/reject (see PR #176) — they
# must count here too, otherwise the per-cycle decision budget doesn't
# bind them and ``decisions_emitted`` on the agent_run row under-reports.
QUEUE_DECISION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "queue_advance_decision",
        "queue_reject_decision",
        "queue_skip_assessment_reject_decision",
        "queue_escalate_decision",
        "send_assessment",
        "resend_assessment_invite",
    }
)

# Runtime-enforced governance.  Read-only grounding tools and the terminal tool
# are always available; everything below can spend money, mutate state, enqueue
# work, or contact a candidate/recruiter and therefore must pass the role's
# action allowlist.  The default mirrors the system-prompt allowlist and
# deliberately excludes the legacy create_application / post_workable_note /
# refresh_candidate_graph tools until a role opts into them explicitly.
GOVERNED_ACTION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "nl_search_candidates",
        "graph_search_candidates",
        "get_cohort_signals",
        "score_cv",
        "batch_score_cv",
        "send_assessment",
        "resend_assessment_invite",
        "create_application",
        "post_workable_note",
        "refresh_candidate_graph",
        "queue_advance_decision",
        "queue_reject_decision",
        "queue_skip_assessment_reject_decision",
        "queue_escalate_decision",
        "evaluate_policy",
        "ask_recruiter",
        "record_observation",
    }
)

EXPLICIT_OPT_IN_ACTION_TOOL_NAMES: frozenset[str] = frozenset(
    {"create_application", "post_workable_note", "refresh_candidate_graph"}
)

DEFAULT_AGENT_ACTION_ALLOWLIST: frozenset[str] = GOVERNED_ACTION_TOOL_NAMES.difference(
    EXPLICIT_OPT_IN_ACTION_TOOL_NAMES
)

_HIGH_RISK_DECISION_TOOL_NAMES: frozenset[str] = frozenset(
    {"queue_advance_decision", "send_assessment", "resend_assessment_invite"}
)
_REJECT_DECISION_TOOL_NAMES: frozenset[str] = frozenset(
    {"queue_reject_decision", "queue_skip_assessment_reject_decision"}
)
MAX_HIGH_RISK_DECISIONS_PER_CYCLE = 1
MAX_REJECT_DECISIONS_PER_CYCLE = 5


def action_allowlist_for_role(role: Role) -> frozenset[str]:
    """Resolve a role's explicit action allowlist or the safe platform default."""
    configured = getattr(role, "agent_action_allowlist", None)
    if configured is None:
        return DEFAULT_AGENT_ACTION_ALLOWLIST
    if not isinstance(configured, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(name).strip() for name in configured if str(name).strip())


def tools_for_role(role: Role) -> list[dict[str, Any]]:
    """Hide disallowed governed tools as well as blocking them at dispatch."""
    allowed = action_allowlist_for_role(role)
    return [
        tool
        for tool in AGENT_TOOLS
        if tool.get("name") not in GOVERNED_ACTION_TOOL_NAMES
        or tool.get("name") in allowed
    ]


def _governed_action_counts(agent_run: AgentRun) -> dict[str, int]:
    counts = getattr(agent_run, "__governed_action_counts__", None)
    if not isinstance(counts, dict):
        counts = {"high_risk": 0, "reject": 0}
        agent_run.__governed_action_counts__ = counts  # type: ignore[attr-defined]
    return counts


def _governed_action_key(name: str, arguments: dict[str, Any]) -> tuple[str, int]:
    """Stable per-subject key for idempotent repeats within one cycle."""
    subject_id = arguments.get("application_id")
    if subject_id is None:
        subject_id = arguments.get("assessment_id")
    try:
        normalized_id = int(subject_id or 0)
    except (TypeError, ValueError):
        normalized_id = 0
    return name, normalized_id


def _governance_block_reason(
    name: str,
    arguments: dict[str, Any],
    *,
    agent_run: AgentRun,
    role: Role,
) -> str | None:
    if name in GOVERNED_ACTION_TOOL_NAMES and name not in action_allowlist_for_role(role):
        return f"tool '{name}' is not allowed by role.agent_action_allowlist"

    if name in QUEUE_DECISION_TOOL_NAMES:
        decision_key = _governed_action_key(name, arguments)
        prior_keys = getattr(agent_run, "__governed_decision_keys__", set())
        is_exact_repeat = decision_key in prior_keys
        configured_budget = getattr(role, "agent_decision_budget_per_cycle", None)
        if (
            configured_budget is not None
            and int(agent_run.decisions_emitted or 0) >= int(configured_budget)
            and not is_exact_repeat
        ):
            return f"per-cycle decision budget reached ({int(configured_budget)})"

        counts = _governed_action_counts(agent_run)
        if (
            name in _HIGH_RISK_DECISION_TOOL_NAMES
            and counts["high_risk"] >= MAX_HIGH_RISK_DECISIONS_PER_CYCLE
            and not is_exact_repeat
        ):
            return "per-cycle high-risk action cap reached (1 send/advance)"
        if (
            name in _REJECT_DECISION_TOOL_NAMES
            and counts["reject"] >= MAX_REJECT_DECISIONS_PER_CYCLE
            and not is_exact_repeat
        ):
            return "per-cycle reject action cap reached (5)"
    return None


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
    "queue_escalate_decision": _tool_queue_escalate_decision,
    "evaluate_policy": _tool_evaluate_policy,
    # Cohort survey + batch + ask-recruiter tools (Phase 7).
    "survey_role_state": _tool_survey_role_state,
    "find_apps_in_state": _tool_find_apps_in_state,
    "read_pending_recruiter_inputs": _tool_read_pending_recruiter_inputs,
    "batch_score_cv": _tool_batch_score_cv,
    "ask_recruiter": _tool_ask_recruiter,
    "record_observation": _tool_record_observation,
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
    args = arguments or {}
    blocked = _governance_block_reason(name, args, agent_run=agent_run, role=role)
    if blocked is not None:
        return {
            "status": "blocked_by_governance",
            "tool": name,
            "reason": blocked,
            "instruction": "Choose an allowed action or call agent_run_complete.",
        }

    before = int(agent_run.decisions_emitted or 0)
    result = handler(db, agent_run=agent_run, role=role, args=args)
    new_decision = int(agent_run.decisions_emitted or 0) > before
    result_status = str(result.get("status") or "") if isinstance(result, dict) else ""
    direct_candidate_contact = (
        (name == "send_assessment" and result_status in {"queued", "sent"})
        or (
            name == "resend_assessment_invite"
            and result_status in {"queued", "resent"}
        )
    )
    if name in QUEUE_DECISION_TOOL_NAMES and (new_decision or direct_candidate_contact):
        counts = _governed_action_counts(agent_run)
        prior_keys = getattr(agent_run, "__governed_decision_keys__", set())
        prior_keys.add(_governed_action_key(name, args))
        agent_run.__governed_decision_keys__ = prior_keys  # type: ignore[attr-defined]
        if name in _HIGH_RISK_DECISION_TOOL_NAMES:
            counts["high_risk"] += 1
        elif name in _REJECT_DECISION_TOOL_NAMES:
            counts["reject"] += 1
    return result


def is_run_complete(result: Any) -> bool:
    return isinstance(result, dict) and result.get("_sentinel") == _RUN_COMPLETE_SENTINEL


__all__ = [
    "AGENT_TOOLS",
    "QUEUE_DECISION_TOOL_NAMES",
    "dispatch",
    "is_run_complete",
]
