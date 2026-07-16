"""Focused Agent Chat read tools."""

from __future__ import annotations
import re
from typing import Any
from sqlalchemy import func
from sqlalchemy.orm import Session
from ..models.candidate_application import CandidateApplication
from ..models.agent_decision import AgentDecision
from ..models.role import Role
from . import constraints as _constraints
from . import impact as _impact
from . import proactive as _proactive
from . import run_history as _run_history
from .tool_dispatch_common import ToolContext, UNHANDLED

def _role_overview(db: Session, role: Role) -> dict[str, Any]:
    from ..services.agent_policy_settings import effective_agent_policy

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

    # Budget event cards point recruiters back to this read-only overview. Keep
    # the spend lookup isolated so a metering/query problem cannot make the
    # whole role snapshot unavailable. Report the effective cap as well as the
    # raw override because unset roles still use the platform default cap.
    effective_monthly_budget_cents: int | None = None
    month_to_date_spend_cents: int | None = None
    remaining_monthly_budget_cents: int | None = None
    try:
        from ..agent_runtime import budget_guard

        effective_monthly_budget_cents = budget_guard.role_monthly_usd_cents(role)
        month_to_date_spend_cents = max(
            0, int(budget_guard.month_to_date_spend_cents(db, role=role))
        )
        if effective_monthly_budget_cents > 0:
            remaining_monthly_budget_cents = max(
                0, effective_monthly_budget_cents - month_to_date_spend_cents
            )
    except Exception:  # pragma: no cover - overview remains useful without usage data
        month_to_date_spend_cents = None
        remaining_monthly_budget_cents = None

    return {
        "role": {"id": int(role.id), "name": role.name},
        "agent": {
            "enabled": bool(role.agentic_mode_enabled),
            "paused": role.agent_paused_at is not None,
            "paused_reason": role.agent_paused_reason,
            "monthly_budget_cents": role.monthly_usd_budget_cents,
            "effective_monthly_budget_cents": effective_monthly_budget_cents,
            "month_to_date_spend_cents": month_to_date_spend_cents,
            "remaining_monthly_budget_cents": remaining_monthly_budget_cents,
            "auto_reject": bool(role.auto_reject),
            "auto_reject_pre_screen": bool(role.auto_reject_pre_screen),
            "auto_promote": bool(role.auto_promote),
            "auto_send_assessment": getattr(role, "auto_send_assessment", None),
            "auto_resend_assessment": getattr(role, "auto_resend_assessment", None),
            "auto_advance": getattr(role, "auto_advance", None),
            "auto_skip_assessment": bool(role.auto_skip_assessment),
            "effective_policy": effective_agent_policy(role),
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

def dispatch_read_tool(name: str, ctx: ToolContext):
    args = ctx.arguments
    db = ctx.db
    role = ctx.role
    if name == "get_role_overview":
        return _role_overview(db, role)
    if name == "get_helper_briefing":
        return _proactive.build_helper_briefing(db, role)
    if name == "list_recent_agent_runs":
        return _run_history.list_recent_agent_runs(
            db,
            role,
            status=args.get("status"),
            trigger=args.get("trigger"),
            limit=int(args.get("limit") or 5),
        )
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
    return UNHANDLED
