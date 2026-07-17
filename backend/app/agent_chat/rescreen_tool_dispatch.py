"""Focused Agent Chat rescreen tools."""

from __future__ import annotations
from typing import Any
from sqlalchemy.orm import Session
from ..models.role import Role
from . import constraints as _constraints
from . import impact as _impact
from . import rescore as _rescore
from .confirmations import (
    attach_confirmation,
    blocked_confirmation_result,
    mark_confirmation_consumed,
    require_later_turn_confirmation,
)
from .command_receipts import (
    abandon_uncommitted_command,
    begin_command,
    complete_command,
)
from .tool_dispatch_common import ToolContext, UNHANDLED

def _maybe_report_rescreen(db: Session, *, role: Role, conversation: Any, result: Any) -> None:
    """When a constraint edit kicked a re-screen, schedule the proactive
    "re-screen complete" impact message. Captures the qualified-pool baseline
    now (scores are still the old, visible values until the re-score lands).

    No-op without a conversation or when nothing was re-screened. In eager
    (test) execution the conversation isn't committed yet, so the task no-ops —
    the live path runs on the worker after the request commits (countdown)."""
    if conversation is None or not isinstance(result, dict):
        return
    if result.get("type") == "related_role_rescore_started":
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

def dispatch_rescreen_tool(name: str, ctx: ToolContext, *, assessments: Any):
    args = ctx.arguments
    db = ctx.db
    role = ctx.role
    user = ctx.user
    conversation = ctx.conversation
    org_id = ctx.organization_id
    confirmation_binding = ctx.confirmation_binding
    _assessments = assessments
    if name == "rescreen_role":
        claim = None
        if conversation is not None:
            check = require_later_turn_confirmation(
                db,
                conversation=conversation,
                operation="rescreen_role",
                token=str(args.get("confirmation_token") or "") or None,
                user=user,
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
                    payload={
                        **confirmation_binding,
                        "role_id": int(role.id),
                        "max_count": int(current.get("count") or 0),
                    },
                )
            claim = begin_command(
                db,
                check=check,
                conversation_kind="agent",
                conversation_id=int(conversation.id),
                organization_id=org_id,
                role_id=int(role.id),
                requested_by_user_id=int(user.id),
                operation="rescreen_role",
                arguments={"scope": "role"},
            )
            if claim.completed_result is not None:
                return claim.completed_result
        try:
            result = _constraints.rescreen_role(db, role)
            _maybe_report_rescreen(
                db, role=role, conversation=conversation, result=result
            )
        except Exception:
            if claim is not None:
                abandon_uncommitted_command(db, claim)
            raise
        if conversation is not None:
            result = mark_confirmation_consumed(result, check=check)
            return complete_command(db, claim, result)
        return result
    if name == "rescore_candidates":
        confirm = bool(args.get("confirm") or False)
        claim = None
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
                user=user,
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
                    payload={
                        **confirmation_binding,
                        "role_id": int(role.id),
                        "max_count": int(current.get("selected_count") or 0),
                        **common,
                    },
                )
            claim = begin_command(
                db,
                check=check,
                conversation_kind="agent",
                conversation_id=int(conversation.id),
                organization_id=org_id,
                role_id=int(role.id),
                requested_by_user_id=int(user.id),
                operation="rescore_candidates",
                arguments=common,
            )
            if claim.completed_result is not None:
                return claim.completed_result
        try:
            result = _rescore.rescore_candidates(
                db,
                role,
                confirm=confirm,
                # Confirmed chat work may release its receipt advisory lock when
                # enqueue_score commits the first job. Reuse active jobs on the
                # initial run too, so a concurrent crash-recovery owner cannot
                # race the original into duplicate paid jobs for later rows.
                reuse_active_jobs=claim is not None,
                **common,
            )
        except Exception:
            if claim is not None:
                abandon_uncommitted_command(db, claim)
            raise
        if confirm and conversation is not None:
            result = mark_confirmation_consumed(result, check=check)
            return complete_command(db, claim, result)
        if not confirm and isinstance(result, dict) and result.get("type") == "rescore_preview":
            result = attach_confirmation(
                result,
                operation="rescore_candidates",
                payload={
                    **confirmation_binding,
                    "role_id": int(role.id),
                    "max_count": int(result.get("selected_count") or 0),
                    **common,
                },
            )
        return result
    if name == "get_criterion_breakdown":
        return _assessments.criterion_breakdown(db, role, int(args["criterion_id"]))
    if name == "rescreen_scoped":
        claim = None
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
                user=user,
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
                        **confirmation_binding,
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
                        **confirmation_binding,
                        "role_id": int(role.id),
                        "criterion_id": int(args["criterion_id"]),
                        "statuses": list(statuses),
                        "max_count": len(ids),
                    },
                )
            claim = begin_command(
                db,
                check=check,
                conversation_kind="agent",
                conversation_id=int(conversation.id),
                organization_id=org_id,
                role_id=int(role.id),
                requested_by_user_id=int(user.id),
                operation="rescreen_scoped",
                # The confirmed scope is the criterion/status predicate. The
                # live candidate ids may shrink before a replay, but that must
                # still resolve to the original completed command receipt.
                arguments={
                    "criterion_id": int(args["criterion_id"]),
                    "statuses": list(statuses),
                },
            )
            if claim.completed_result is not None:
                return claim.completed_result
        try:
            result = _constraints.rescreen_role(
                db,
                role,
                application_ids=ids,
                reason=f"agent_chat:scoped_rescreen:crit_{args['criterion_id']}",
            )
            _maybe_report_rescreen(
                db, role=role, conversation=conversation, result=result
            )
        except Exception:
            if claim is not None:
                abandon_uncommitted_command(db, claim)
            raise
        if conversation is not None:
            result = mark_confirmation_consumed(result, check=check)
            return complete_command(db, claim, result)
        return result
    return UNHANDLED
