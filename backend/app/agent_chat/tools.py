"""Anthropic tool catalogue + dispatcher for the role-agent chat.

These are the *action-taking* tools the conversational agent uses to read a
role's state, run impact analysis, and change constraints — distinct from
``taali_chat`` (read-only candidate search). Every tool is implicitly scoped
to the conversation's role; the engine injects the role, so no tool takes a
role_id.

Card-producing tools return a dict with a ``type`` in :data:`CARD_TYPES`; the
engine lifts those into ``AgentConversationMessage.actions`` so the UI renders
an impact card. Tools in :data:`MUTATION_CARD_TYPES` changed state and mark
the assistant turn as an ``action``.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.agent_decision import AgentDecision
from ..models.role import Role
from . import assessments as _assessments
from . import constraints as _constraints
from . import controls as _controls
from . import draft_tasks as _draft_tasks
from . import health as _health
from . import impact as _impact
from . import rescore as _rescore
from .confirmations import (
    attach_confirmation,
    blocked_confirmation_result,
    mark_confirmation_consumed,
    require_later_turn_confirmation,
)


# Card payload ``type`` values the engine surfaces in message.actions.
CARD_TYPES = frozenset(
    {
        "threshold_simulation",
        "threshold_recommendation",
        "threshold_change",
        "constraint_change",
        "job_spec_change",
        "draft_task_review",
        "candidate_evidence",
    }
)
# Cards that represent a committed mutation (vs read-only analysis).
MUTATION_CARD_TYPES = frozenset({"threshold_change", "constraint_change", "job_spec_change"})


AGENT_CHAT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_role_overview",
        "description": (
            "Snapshot of THIS role for grounding before you act: agent on/off + "
            "pause state + monthly budget, the effective score threshold (role "
            "override / org default / mode), the recruiter constraint chips "
            "(salary caps, must-haves), the pipeline funnel counts, and how many "
            "decisions are pending. ALWAYS call this first so your numbers are real."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_candidates",
        "description": (
            "List candidates on this role by bucket, best score first. bucket: "
            "'above'/'below' (the effective threshold), 'advanced', 'rejected', "
            "'pending' (awaiting a decision), or 'all' (open apps). Each candidate "
            "returns name, pre-screen score, Taali pipeline stage, and a normalized "
            "`ats_context` for native, Workable, or Bullhorn. It also returns the "
            "synced `workable_stage` for Workable roles. Pass "
            "`workable_stage` to filter to a specific Workable stage — that's how you "
            "answer 'who's in final interview?' (Taali's pipeline_stage does NOT track "
            "Workable's interview stages; workable_stage is the source of truth). You can "
            "ALSO see the recruiter's **Workable comments / ratings** on each candidate: "
            "set `include_comments=true` to return them ([{author, created_at, body}], "
            "newest first), and `comment_contains` to filter to candidates a recruiter "
            "commented on (e.g. comment_contains='yes'). So 'top 5 in technical interview "
            "with a Yes comment' = workable_stage='technical interview', "
            "comment_contains='yes', limit=5."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bucket": {
                    "type": "string",
                    "enum": ["above", "below", "advanced", "rejected", "pending", "all"],
                    "default": "all",
                },
                "workable_stage": {
                    "type": ["string", "null"],
                    "description": "Filter to candidates whose synced Workable stage contains this (case-insensitive), e.g. 'final interview'. Applies to the open buckets (not 'rejected').",
                },
                "comment_contains": {
                    "type": ["string", "null"],
                    "description": "Filter to candidates who have a synced Workable recruiter comment/rating whose text matches this — whole-word for a single word (comment_contains='yes' matches a 'Yes' comment but not 'yesterday'), substring for a phrase ('strong hire'). Implies include_comments. Open buckets only (not 'rejected').",
                },
                "include_comments": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include each candidate's synced Workable recruiter comments [{author, created_at, body}] (newest first, capped) in the result, so you can read/cite them. Set true even without a filter to show what recruiters wrote.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            },
            "required": [],
        },
    },
    {
        "name": "sync_workable_comments",
        "description": (
            "Force an immediate Workable sync for THIS role, pulling the latest "
            "recruiter comments / ratings (and stages) for all its candidates. Use "
            "when the recruiter says comments look stale or missing, or asks you to "
            "sync / refresh Workable comments. Comments normally sync automatically "
            "every few minutes; this forces a refresh now. It's ASYNCHRONOUS — tell "
            "the recruiter it's underway and to ask again in a moment so you can "
            "re-read the freshly-synced comments with list_candidates."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "simulate_threshold",
        "description": (
            "Project the effect of moving the score threshold to a value WITHOUT "
            "committing. Returns: candidates above/below now vs at the new cutoff, "
            "how many pending advances would be retracted, how many new rejects "
            "would be carded, and who is newly cleared. Use to answer 'what happens "
            "if I drop the threshold to 65?' before doing it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "The 0-100 cutoff to simulate.",
                }
            },
            "required": ["threshold"],
        },
    },
    {
        "name": "recommend_threshold",
        "description": (
            "Recommend a score threshold. Pass target_additional to find a cutoff "
            "that clears ~N more candidates than today, or target_total for ~N "
            "total, or neither to get a sensible 'loosen it a little' suggestion. "
            "Returns the recommended cutoff, projected counts, and who it adds. Use "
            "when the recruiter wants more volume ('how do I let a few more through?')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_additional": {
                    "type": ["integer", "null"],
                    "minimum": 0,
                    "description": "Clear this many MORE than the current cutoff.",
                },
                "target_total": {
                    "type": ["integer", "null"],
                    "minimum": 0,
                    "description": "Clear ~this many total.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "set_threshold",
        "description": (
            "COMMIT a new score threshold for this role and reconcile the decision "
            "queue: retract pending advances now below the cutoff and card new "
            "rejects. Instant, no re-scoring. Pass null to clear back to the org "
            "default. Prefer simulate_threshold first and only commit when the "
            "recruiter has confirmed the direction or asked for it explicitly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": ["number", "null"],
                    "minimum": 0,
                    "maximum": 100,
                    "description": "New 0-100 cutoff, or null to clear to org default.",
                }
            },
            "required": ["threshold"],
        },
    },
    {
        "name": "add_or_update_constraint",
        "description": (
            "Add a recruiter constraint chip to this role, or edit an existing one "
            "by criterion_id. Use for salary caps, location, work authorisation, "
            "must-have skills — anything evaluated from the CV. bucket 'constraint' "
            "is a hard filter, 'must' a must-have, 'preferred' a nice-to-have. This "
            "RE-SCREENS affected candidates (changes the pre-screen prompt), so it's "
            "not instant — tell the recruiter re-screening is underway and how many "
            "candidates it covers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The constraint, e.g. 'Salary expectation <= 25,000'.",
                },
                "bucket": {
                    "type": "string",
                    "enum": ["constraint", "must", "preferred"],
                    "default": "constraint",
                },
                "criterion_id": {
                    "type": ["integer", "null"],
                    "description": "Edit an existing chip instead of adding a new one.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "remove_constraint",
        "description": (
            "Remove a recruiter constraint chip by criterion_id (get ids from "
            "get_role_overview). Re-screens if the chip was a must-have/constraint."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"criterion_id": {"type": "integer"}},
            "required": ["criterion_id"],
        },
    },
    {
        "name": "update_job_spec",
        "description": (
            "Replace THIS role's job description with a new one the recruiter "
            "pasted, and re-derive its must-have / preferred / constraint criteria "
            "from it. Use when the recruiter sends a new or updated JD in chat. A new "
            "JD re-derives EVERY criterion (the biggest change there is), so it does "
            "NOT re-screen automatically: it applies the spec + re-derives the chips "
            "instantly (no LLM) and returns the criteria diff + a re-screen cost "
            "estimate. Recruiter-added chips (salary caps, etc.) are kept. Show what "
            "changed + the cost and ASK before running rescreen_role."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_spec_text": {"type": "string", "description": "The full new job description text the recruiter pasted."},
            },
            "required": ["job_spec_text"],
        },
    },
    {
        "name": "set_agent_state",
        "description": (
            "Activate (turn on / resume) or pause this role's agent. Use when the "
            "recruiter asks to start, restart, resume, re-enable, or pause the agent. "
            "Activating needs a monthly budget set — if none, ask for one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["activate", "pause"],
                    "description": "'activate' to turn on / resume, 'pause' to pause.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "adjust_agent_settings",
        "description": (
            "Adjust this role agent's settings. Only set the fields the recruiter "
            "asks to change. monthly_budget_cents = monthly spend cap in cents "
            "(e.g. 5000 = $50/mo). auto_reject = execute reject decisions without "
            "review. auto_reject_pre_screen = narrower: only pre-screen-stage "
            "rejects execute immediately; scored-candidate rejects still queue "
            "for review. auto_promote = send assessments without review. "
            "auto_skip_assessment = bypass the assessment stage entirely; strong "
            "candidates queue as advance-to-interview decisions instead of "
            "receiving an assessment invite. Raising the "
            "budget can resume a budget-paused agent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "monthly_budget_cents": {"type": ["integer", "null"]},
                "auto_reject": {"type": ["boolean", "null"]},
                "auto_reject_pre_screen": {"type": ["boolean", "null"]},
                "auto_promote": {"type": ["boolean", "null"]},
                "auto_skip_assessment": {"type": ["boolean", "null"]},
            },
        },
    },
    {
        "name": "rescreen_role",
        "description": (
            "Run the re-screen the recruiter opted into AFTER a constraint change. "
            "A constraint/must-have edit applies immediately but does NOT re-screen "
            "automatically — re-screening re-scores the pool and costs money. First "
            "report the `would_rescreen` count + `est_cost_usd` from the "
            "constraint_change result and ask the recruiter to confirm; call this "
            "ONLY once they explicitly say yes."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "rescore_candidates",
        "description": (
            "Re-score this role's OLD-engine (v1.x) candidates with the current "
            "holistic v2.1.0 engine. Use when the recruiter wants stale scores "
            "refreshed — and let THEM steer the scope; never assume 'all'. ALWAYS "
            "call once WITHOUT confirm first (confirm=false) to preview the matched "
            "count + $ cost, show the recruiter, and call again with confirm=true "
            "ONLY after they explicitly say yes. Scope the subset: 'all', 'top_n' "
            "(highest current scores, set `limit`), 'above_threshold' / "
            "'below_threshold' (set `threshold` 0-100), or 'none'. Re-scoring spends "
            "~$0.083/candidate, so the preview-then-confirm step is mandatory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["all", "top_n", "above_threshold", "below_threshold", "none"],
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "For scope=top_n: how many of the highest-scoring stale candidates.",
                },
                "threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "For scope=above_threshold/below_threshold: the current-score cutoff.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "false = preview count+cost only (default); true = actually re-score (recruiter must have confirmed).",
                },
            },
            "required": ["scope"],
        },
    },
    {
        "name": "get_criterion_breakdown",
        "description": (
            "For ONE criterion (criterion_id from get_role_overview), how the scored "
            "candidates currently split — met / missing / unknown / not_assessed — "
            "WITH each one's stored reasoning. Read-only and free (reuses scores we "
            "already have). Use this to reason about a criteria change before "
            "spending: a widening only re-checks the previously-missing, a narrowing "
            "only the previously-met; a typo/cosmetic reword is a no-op."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"criterion_id": {"type": "integer"}},
            "required": ["criterion_id"],
        },
    },
    {
        "name": "rescreen_scoped",
        "description": (
            "Re-screen ONLY the candidates affected by a criteria change — far cheaper "
            "than the whole pool. Pass criterion_id + which stored status-group to "
            "re-check: a WIDENING re-checks ['missing'], a NARROWING ['met']. The "
            "re-screen re-judges each correctly (Saudi flips to met, India stays "
            "missing). Confirm the scope + cost with the recruiter before calling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "criterion_id": {"type": "integer"},
                "statuses": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["met", "missing", "unknown"]},
                },
            },
            "required": ["criterion_id", "statuses"],
        },
    },
    {
        "name": "search_candidates",
        "description": (
            "Natural-language semantic search over this role's candidates — the same "
            "search the Search page uses. E.g. 'candidates based in MENA', 'who stated "
            "a salary expectation', 'strong Kubernetes background'. Use it to scope a "
            "criteria change or answer the recruiter's questions about the pool. "
            "Read-only; returns matches with a short why."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "find_top_candidates",
        "description": (
            "GROUNDED top-N ranking on THIS role. Use when the recruiter asks "
            "for the 'best' or 'top N' candidates with a quality (e.g. 'top 5 "
            "with banking domain experience', 'best candidates who've led a "
            "team'). Ranks by score, then attaches to each shortlisted "
            "candidate a per-criterion verdict backed by a VERBATIM CV quote "
            "(via citations or a stored requirement assessment). Renders an "
            "evidence card; cite the quotes in your reply and never add a fact "
            "that isn't in the evidence."
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
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_draft_tasks",
        "description": (
            "List the auto-generated assessment-task DRAFTS awaiting review on "
            "THIS role (the agent authored them from the JD; they're not live "
            "until approved). Returns a review card the recruiter can approve or "
            "reject-with-feedback in-chat. Call when the recruiter asks about "
            "tasks/assessments, or proactively mention drafts when there are any."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "role_health_check",
        "description": (
            "FREE, read-only scan of what's most likely HURTING this role's "
            "decisions, ranked, so you can proactively steer the recruiter. "
            "Surfaces: a must-have almost nobody meets (killing the pool), a "
            "requirement you often can't verify from the CV, a requirement "
            "everyone meets (no signal), a score cut-off set too strict / too "
            "loose, a PATTERN of the recruiter overriding your decisions in one "
            "direction (you're mis-calibrated — the strongest signal), stale "
            "old-engine scores, and a decision backlog. Returns `findings` "
            "(ranked) + `top_finding` + `all_clear`. RUN IT when a conversation "
            "opens fresh, when the recruiter asks an open-ended 'how's this role "
            "/ what should I change / review this', or after they resolve a "
            "batch. Then LEAD with `top_finding` phrased as a question + the "
            "concrete fix you can make; one finding at a time, never a wall. "
            "Each finding carries the handles (criterion_id, threshold) to act. "
            "If `all_clear`, say the role looks healthy in a line — don't invent "
            "problems."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def _role_overview(db: Session, role: Role) -> dict[str, Any]:
    rows = _impact.load_open_candidates(db, role)
    effective = _impact.effective_threshold(db, role)
    above, below = _impact.split_by_threshold(rows, effective)

    # Full funnel: counts by pipeline_stage across all non-deleted apps.
    stage_rows = (
        db.query(CandidateApplication.pipeline_stage, func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.deleted_at.is_(None),
        )
        .group_by(CandidateApplication.pipeline_stage)
        .all()
    )
    funnel = {str(stage or "unknown"): int(n) for stage, n in stage_rows}

    # Provider-neutral external-stage breakdown of the open pool.  Keep the
    # Workable-specific field for backwards compatibility with the existing UI.
    workable_funnel: dict[str, int] = {}
    ats_stage_funnel: dict[str, int] = {}
    for r in rows:
        key = r.workable_stage or "(unsynced)"
        workable_funnel[key] = workable_funnel.get(key, 0) + 1
        provider = "bullhorn" if r.bullhorn_status else "workable" if r.workable_stage else "native"
        raw_stage = r.bullhorn_status or r.workable_stage or r.pipeline_stage or "(unsynced)"
        ats_key = f"{provider}:{raw_stage}"
        ats_stage_funnel[ats_key] = ats_stage_funnel.get(ats_key, 0) + 1

    pending_rows = (
        db.query(AgentDecision.decision_type, func.count(AgentDecision.id))
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
        )
        .group_by(AgentDecision.decision_type)
        .all()
    )
    pending_by_type = {str(t): int(n) for t, n in pending_rows}

    return {
        "role": {"id": int(role.id), "name": role.name},
        "agent": {
            "enabled": bool(role.agentic_mode_enabled),
            "paused": role.agent_paused_at is not None,
            "paused_reason": role.agent_paused_reason,
            "monthly_budget_cents": role.monthly_usd_budget_cents,
            "auto_reject": bool(role.auto_reject),
            "auto_reject_pre_screen": bool(role.auto_reject_pre_screen),
            "auto_promote": bool(role.auto_promote),
            "auto_skip_assessment": bool(role.auto_skip_assessment),
        },
        "threshold": {
            "effective": effective,
            "role_override": role.score_threshold,
            "mode": role.auto_reject_threshold_mode or "manual",
        },
        "constraints": _constraints.list_constraints(role),
        "funnel": funnel,
        "workable_stage_funnel": workable_funnel,
        "ats_stage_funnel": ats_stage_funnel,
        "open_candidates": len(rows),
        "above_threshold": len(above),
        "below_threshold": len(below),
        "pending_decisions": sum(pending_by_type.values()),
        "pending_by_type": pending_by_type,
    }


def _comment_match(comments: list[dict[str, Any]] | None, term: str) -> bool:
    """True when any of the candidate's Workable comment bodies matches ``term``.

    A single word matches whole-word (so 'yes' hits a "Yes" comment but not
    "yesterday"); a phrase matches as a case-insensitive substring.
    """
    t = (term or "").strip().lower()
    if not t:
        return False
    bodies = [str((c or {}).get("body") or "") for c in (comments or [])]
    if " " not in t and t.isalnum():
        pat = re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE)
        return any(pat.search(b) for b in bodies)
    return any(t in b.lower() for b in bodies)


def _compact_comments(
    comments: list[dict[str, Any]] | None, *, max_items: int = 3, max_len: int = 240
) -> list[dict[str, Any]]:
    """Trim a candidate's comments for the tool result — a few, newest first,
    bodies bounded so a chatty Workable thread can't blow up the turn."""
    out: list[dict[str, Any]] = []
    for c in (comments or [])[:max_items]:
        body = str((c or {}).get("body") or "").strip()
        if len(body) > max_len:
            body = body[:max_len].rstrip() + "…"
        out.append({"author": (c or {}).get("author"), "created_at": (c or {}).get("created_at"), "body": body})
    return out


def _list_candidates(
    db: Session,
    role: Role,
    *,
    bucket: str,
    limit: int,
    workable_stage: str | None = None,
    comment_contains: str | None = None,
    include_comments: bool = False,
) -> dict[str, Any]:
    # Only pay for the comment JSON read when the recruiter is filtering or asking
    # to see comments; a comment filter implies returning the matched comments.
    cc = (comment_contains or "").strip()
    want_comments = bool(cc) or bool(include_comments)
    rows = _impact.load_open_candidates(db, role, with_comments=want_comments)
    effective = _impact.effective_threshold(db, role)
    above, below = _impact.split_by_threshold(rows, effective)

    if bucket == "above":
        chosen = above
    elif bucket == "below":
        chosen = below
    elif bucket == "advanced":
        chosen = [r for r in rows if r.pipeline_stage == "advanced"]
    elif bucket == "rejected":
        # Rejected apps aren't "open" so they aren't in `rows`; query directly.
        return _list_resolved(db, role, outcome="rejected", limit=limit)
    elif bucket == "pending":
        chosen = [r for r in rows if r.has_pending_decision]
    else:
        chosen = rows

    # Optional filter by the SYNCED Workable stage (e.g. "Final Interview",
    # "Technical Interview"). Taali's pipeline_stage doesn't track Workable's
    # internal interview stages — `workable_stage` is the source of truth — so
    # this lets the agent answer "who's in final interview" directly.
    wk_filter = (workable_stage or "").strip().lower()
    if wk_filter:
        chosen = [r for r in chosen if wk_filter in (r.workable_stage or "").lower()]

    # Optional filter by a recruiter's synced Workable comment (e.g. a "Yes"
    # verdict). The data is always synced onto the candidate — this just lets the
    # agent reach it.
    if cc:
        chosen = [r for r in chosen if _comment_match(r.comments, cc)]

    chosen = sorted(chosen, key=lambda r: (r.score if r.score is not None else -1), reverse=True)
    from ..services.ats_context_service import application_ats_context

    apps_by_id = {
        int(app.id): app
        for app in db.query(CandidateApplication)
        .filter(CandidateApplication.id.in_([r.application_id for r in chosen[: int(limit)]] or [-1]))
        .all()
    }
    return {
        "bucket": bucket,
        "workable_stage_filter": workable_stage or None,
        "comment_filter": cc or None,
        "count": len(chosen),
        "effective_threshold": effective,
        "candidates": [
            {
                "application_id": r.application_id,
                "name": r.candidate_name,
                "score": r.score,
                "stage": r.pipeline_stage,
                "workable_stage": r.workable_stage,
                "bullhorn_status": r.bullhorn_status,
                "ats_context": application_ats_context(apps_by_id[r.application_id]),
                "pending_decision": r.pending_decision_type,
                **({"comments": _compact_comments(r.comments)} if want_comments else {}),
            }
            for r in chosen[: int(limit)]
        ],
    }


def _list_resolved(db: Session, role: Role, *, outcome: str, limit: int) -> dict[str, Any]:
    from ..models.candidate import Candidate

    rows = (
        db.query(CandidateApplication, Candidate.full_name)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.application_outcome == outcome,
            CandidateApplication.deleted_at.is_(None),
        )
        .limit(int(limit))
        .all()
    )
    return {
        "bucket": outcome,
        "count": len(rows),
        "candidates": [
            {
                "application_id": int(app.id),
                "name": (name or "Unnamed candidate"),
                "score": float(app.pre_screen_score_100)
                if app.pre_screen_score_100 is not None
                else None,
                "stage": app.pipeline_stage,
            }
            for app, name in rows
        ],
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _maybe_report_rescreen(db: Session, *, role: Role, conversation: Any, result: Any) -> None:
    """When a constraint edit kicked a re-screen, schedule the proactive
    "re-screen complete" impact message. Captures the qualified-pool baseline
    now (scores are still the old, visible values until the re-score lands).

    No-op without a conversation or when nothing was re-screened. In eager
    (test) execution the conversation isn't committed yet, so the task no-ops —
    the live path runs on the worker after the request commits (countdown)."""
    if conversation is None or not isinstance(result, dict):
        return
    if int(result.get("rescreening_count") or 0) <= 0:
        return
    rows = _impact.load_open_candidates(db, role)
    threshold = _impact.effective_threshold(db, role)
    above, _below = _impact.split_by_threshold(rows, threshold)
    try:
        from ..tasks.agent_chat_tasks import report_rescreen_impact

        report_rescreen_impact.apply_async(
            kwargs={
                "conversation_id": int(conversation.id),
                "role_id": int(role.id),
                "baseline_qualified": int(len(above)),
            },
            countdown=20,
        )
    except Exception:  # pragma: no cover — never fail the edit on dispatch
        import logging

        logging.getLogger("taali.agent_chat").exception(
            "failed to enqueue rescreen impact report for role_id=%s", role.id
        )


def dispatch_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    db: Session,
    role: Role,
    user: Any,
    conversation: Any = None,
) -> Any:
    """Run one tool against the conversation's role. Raises on unknown tool
    or bad arguments; the engine converts exceptions to a tool_result error."""
    args = arguments or {}
    org_id = int(role.organization_id)

    if name == "get_role_overview":
        return _role_overview(db, role)
    if name == "list_candidates":
        return _list_candidates(
            db,
            role,
            bucket=str(args.get("bucket") or "all"),
            limit=int(args.get("limit") or 20),
            workable_stage=args.get("workable_stage"),
            comment_contains=args.get("comment_contains"),
            include_comments=bool(args.get("include_comments") or False),
        )
    if name == "simulate_threshold":
        return _impact.simulate_threshold(db, role, float(args["threshold"]))
    if name == "recommend_threshold":
        ta = args.get("target_additional")
        tt = args.get("target_total")
        return _impact.recommend_threshold(
            db,
            role,
            target_additional=int(ta) if ta is not None else None,
            target_total=int(tt) if tt is not None else None,
        )
    if name == "set_threshold":
        raw = args.get("threshold")
        return _impact.apply_threshold(
            db,
            role,
            float(raw) if raw is not None else None,
            organization_id=org_id,
        )
    if name == "add_or_update_constraint":
        cid = args.get("criterion_id")
        result = _constraints.add_or_update_constraint(
            db,
            role,
            text=str(args.get("text") or ""),
            bucket=str(args.get("bucket") or "constraint"),
            criterion_id=int(cid) if cid is not None else None,
            trigger_rescreen=False,  # P0: never auto-spend — the recruiter opts in
        )
        if result.get("invalidates_scores"):
            result["would_rescreen"] = _constraints.estimate_rescreen(db, role)
            estimate = result["would_rescreen"]
            result = attach_confirmation(
                result,
                operation="rescreen_role",
                payload={"role_id": int(role.id), "max_count": int(estimate.get("count") or 0)},
            )
        return result
    if name == "remove_constraint":
        result = _constraints.remove_constraint(
            db, role, int(args["criterion_id"]), trigger_rescreen=False
        )
        if result.get("invalidates_scores"):
            result["would_rescreen"] = _constraints.estimate_rescreen(db, role)
            estimate = result["would_rescreen"]
            result = attach_confirmation(
                result,
                operation="rescreen_role",
                payload={"role_id": int(role.id), "max_count": int(estimate.get("count") or 0)},
            )
        return result
    if name == "update_job_spec":
        result = _constraints.update_job_spec(
            db, role, job_spec_text=str(args.get("job_spec_text") or "")
        )
        if isinstance(result, dict) and result.get("would_rescreen"):
            estimate = result["would_rescreen"]
            result = attach_confirmation(
                result,
                operation="rescreen_role",
                payload={"role_id": int(role.id), "max_count": int(estimate.get("count") or 0)},
            )
        return result
    if name == "rescreen_role":
        if conversation is not None:
            check = require_later_turn_confirmation(
                db,
                conversation=conversation,
                operation="rescreen_role",
                token=str(args.get("confirmation_token") or "") or None,
            )
            if not check.ok:
                return blocked_confirmation_result("rescreen_role", check.reason)
            if int(check.payload.get("role_id") or 0) != int(role.id):
                return blocked_confirmation_result("rescreen_role", "The preview belongs to a different role.")
            current = _constraints.estimate_rescreen(db, role)
            if int(current.get("count") or 0) > int(check.payload.get("max_count") or 0):
                return attach_confirmation(
                    {
                        "type": "rescreen_preview",
                        "would_rescreen": current,
                        "message": "The candidate count increased since approval; please confirm the updated scope.",
                    },
                    operation="rescreen_role",
                    payload={"role_id": int(role.id), "max_count": int(current.get("count") or 0)},
                )
        result = _constraints.rescreen_role(db, role)
        _maybe_report_rescreen(db, role=role, conversation=conversation, result=result)
        if conversation is not None:
            result = mark_confirmation_consumed(result, check=check)
        return result
    if name == "rescore_candidates":
        confirm = bool(args.get("confirm") or False)
        common = {
            "scope": str(args.get("scope") or "all"),
            "limit": int(args["limit"]) if args.get("limit") is not None else 10,
            "threshold": float(args["threshold"]) if args.get("threshold") is not None else None,
        }
        if confirm and conversation is not None:
            check = require_later_turn_confirmation(
                db,
                conversation=conversation,
                operation="rescore_candidates",
                token=str(args.get("confirmation_token") or "") or None,
            )
            if not check.ok:
                return blocked_confirmation_result("rescore_candidates", check.reason)
            current = _rescore.rescore_candidates(db, role, confirm=False, **common)
            approved_scope = {
                "scope": check.payload.get("scope"),
                "limit": check.payload.get("limit"),
                "threshold": check.payload.get("threshold"),
            }
            if (
                int(check.payload.get("role_id") or 0) != int(role.id)
                or approved_scope != common
                or int(current.get("selected_count") or 0) > int(check.payload.get("max_count") or 0)
            ):
                return attach_confirmation(
                    current,
                    operation="rescore_candidates",
                    payload={"role_id": int(role.id), "max_count": int(current.get("selected_count") or 0), **common},
                )
        result = _rescore.rescore_candidates(
            db,
            role,
            confirm=confirm,
            **common,
        )
        if confirm and conversation is not None:
            result = mark_confirmation_consumed(result, check=check)
        if not confirm and isinstance(result, dict) and result.get("type") == "rescore_preview":
            result = attach_confirmation(
                result,
                operation="rescore_candidates",
                payload={"role_id": int(role.id), "max_count": int(result.get("selected_count") or 0), **common},
            )
        return result
    if name == "get_criterion_breakdown":
        return _assessments.criterion_breakdown(db, role, int(args["criterion_id"]))
    if name == "rescreen_scoped":
        statuses = tuple(str(s) for s in (args.get("statuses") or []))
        affected = _assessments.affected_applications(
            db, role, int(args["criterion_id"]), statuses=statuses
        )
        ids = [a["application_id"] for a in affected]
        if not ids:
            return {"type": "rescreen_started", "rescreening_count": 0, "scoped": True}
        if conversation is not None:
            check = require_later_turn_confirmation(
                db,
                conversation=conversation,
                operation="rescreen_scoped",
                token=str(args.get("confirmation_token") or "") or None,
            )
            if not check.ok:
                return attach_confirmation(
                    {
                        "type": "rescreen_preview",
                        "criterion_id": int(args["criterion_id"]),
                        "statuses": list(statuses),
                        "selected_count": len(ids),
                        "est_cost_usd": round(len(ids) * 0.05, 2),
                        "message": check.reason,
                    },
                    operation="rescreen_scoped",
                    payload={
                        "role_id": int(role.id),
                        "criterion_id": int(args["criterion_id"]),
                        "statuses": list(statuses),
                        "max_count": len(ids),
                    },
                )
            approved_statuses = tuple(str(s) for s in (check.payload.get("statuses") or []))
            if (
                int(check.payload.get("role_id") or 0) != int(role.id)
                or int(check.payload.get("criterion_id") or 0) != int(args["criterion_id"])
                or approved_statuses != statuses
                or len(ids) > int(check.payload.get("max_count") or 0)
            ):
                return attach_confirmation(
                    {
                        "type": "rescreen_preview",
                        "selected_count": len(ids),
                        "est_cost_usd": round(len(ids) * 0.05, 2),
                        "message": "The scope increased since approval; please confirm the updated count.",
                    },
                    operation="rescreen_scoped",
                    payload={
                        "role_id": int(role.id),
                        "criterion_id": int(args["criterion_id"]),
                        "statuses": list(statuses),
                        "max_count": len(ids),
                    },
                )
        result = _constraints.rescreen_role(
            db, role, application_ids=ids,
            reason=f"agent_chat:scoped_rescreen:crit_{args['criterion_id']}",
        )
        _maybe_report_rescreen(db, role=role, conversation=conversation, result=result)
        if conversation is not None:
            result = mark_confirmation_consumed(result, check=check)
        return result
    if name == "search_candidates":
        # Reuse the Search page's candidate search (Graphiti/GraphRAG via the MCP
        # handlers). Lazy import keeps the graph deps out of the module load path;
        # graceful fallback when the vector layer isn't configured.
        try:
            from ..mcp import handlers as _mcp_handlers

            return _mcp_handlers.nl_search_candidates(
                db, user, query=str(args.get("query") or ""), role_id=int(role.id)
            )
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the turn
            return {"available": False, "error": f"search unavailable: {type(exc).__name__}"}
    if name == "find_top_candidates":
        # Grounded top-N for this role. Tagged as a card so the engine lifts
        # it into message.actions for the evidence-card UI (and the model
        # still narrates the verbatim quotes in the result).
        from ..mcp import handlers as _mcp_handlers

        payload = _mcp_handlers.find_top_candidates(
            db,
            user,
            query=str(args.get("query") or ""),
            limit=int(args.get("limit") or 10),
            rank_by=str(args.get("rank_by") or "taali"),
            role_id=int(role.id),
        )
        return {"type": "candidate_evidence", **payload}
    if name == "set_agent_state":
        return _controls.set_agent_state(db, role, action=str(args.get("action") or ""))
    if name == "adjust_agent_settings":
        mbc = args.get("monthly_budget_cents")
        return _controls.adjust_agent_settings(
            db,
            role,
            monthly_budget_cents=int(mbc) if mbc is not None else None,
            auto_reject=args.get("auto_reject"),
            auto_reject_pre_screen=args.get("auto_reject_pre_screen"),
            auto_promote=args.get("auto_promote"),
            auto_skip_assessment=args.get("auto_skip_assessment"),
        )
    if name == "list_draft_tasks":
        return _draft_tasks.draft_review_card(db, role)
    if name == "role_health_check":
        return _health.role_health_check(db, role)
    if name == "sync_workable_comments":
        return _controls.sync_workable_comments(db, role, user=user)

    raise KeyError(f"unknown tool: {name}")


__all__ = ["AGENT_CHAT_TOOLS", "CARD_TYPES", "MUTATION_CARD_TYPES", "dispatch_tool"]
