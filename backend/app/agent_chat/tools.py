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

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.agent_decision import AgentDecision
from ..models.role import Role
from . import assessments as _assessments
from . import constraints as _constraints
from . import controls as _controls
from . import impact as _impact


# Card payload ``type`` values the engine surfaces in message.actions.
CARD_TYPES = frozenset(
    {
        "threshold_simulation",
        "threshold_recommendation",
        "threshold_change",
        "constraint_change",
    }
)
# Cards that represent a committed mutation (vs read-only analysis).
MUTATION_CARD_TYPES = frozenset({"threshold_change", "constraint_change"})


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
            "'pending' (awaiting a decision), or 'all' (open apps). Returns name + "
            "pre-screen score + stage. Use to name the specific people a change moves."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bucket": {
                    "type": "string",
                    "enum": ["above", "below", "advanced", "rejected", "pending", "all"],
                    "default": "all",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            },
            "required": [],
        },
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
            "review. auto_promote = send assessments without review. Raising the "
            "budget can resume a budget-paused agent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "monthly_budget_cents": {"type": ["integer", "null"]},
                "auto_reject": {"type": ["boolean", "null"]},
                "auto_promote": {"type": ["boolean", "null"]},
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
            "auto_promote": bool(role.auto_promote),
        },
        "threshold": {
            "effective": effective,
            "role_override": role.score_threshold,
            "mode": role.auto_reject_threshold_mode or "manual",
        },
        "constraints": _constraints.list_constraints(role),
        "funnel": funnel,
        "open_candidates": len(rows),
        "above_threshold": len(above),
        "below_threshold": len(below),
        "pending_decisions": sum(pending_by_type.values()),
        "pending_by_type": pending_by_type,
    }


def _list_candidates(db: Session, role: Role, *, bucket: str, limit: int) -> dict[str, Any]:
    rows = _impact.load_open_candidates(db, role)
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

    chosen = sorted(chosen, key=lambda r: (r.score if r.score is not None else -1), reverse=True)
    return {
        "bucket": bucket,
        "count": len(chosen),
        "effective_threshold": effective,
        "candidates": [
            {
                "application_id": r.application_id,
                "name": r.candidate_name,
                "score": r.score,
                "stage": r.pipeline_stage,
                "pending_decision": r.pending_decision_type,
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
            db, role, bucket=str(args.get("bucket") or "all"), limit=int(args.get("limit") or 20)
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
        return result
    if name == "remove_constraint":
        result = _constraints.remove_constraint(
            db, role, int(args["criterion_id"]), trigger_rescreen=False
        )
        if result.get("invalidates_scores"):
            result["would_rescreen"] = _constraints.estimate_rescreen(db, role)
        return result
    if name == "rescreen_role":
        result = _constraints.rescreen_role(db, role)
        _maybe_report_rescreen(db, role=role, conversation=conversation, result=result)
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
        result = _constraints.rescreen_role(
            db, role, application_ids=ids,
            reason=f"agent_chat:scoped_rescreen:crit_{args['criterion_id']}",
        )
        _maybe_report_rescreen(db, role=role, conversation=conversation, result=result)
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
    if name == "set_agent_state":
        return _controls.set_agent_state(db, role, action=str(args.get("action") or ""))
    if name == "adjust_agent_settings":
        mbc = args.get("monthly_budget_cents")
        return _controls.adjust_agent_settings(
            db,
            role,
            monthly_budget_cents=int(mbc) if mbc is not None else None,
            auto_reject=args.get("auto_reject"),
            auto_promote=args.get("auto_promote"),
        )

    raise KeyError(f"unknown tool: {name}")


__all__ = ["AGENT_CHAT_TOOLS", "CARD_TYPES", "MUTATION_CARD_TYPES", "dispatch_tool"]
