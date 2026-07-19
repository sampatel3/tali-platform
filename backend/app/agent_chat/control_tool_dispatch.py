"""Focused Agent Chat control tools."""

from __future__ import annotations
from . import controls as _controls
from . import draft_tasks as _draft_tasks
from . import health as _health
from . import recruiter_inputs as _recruiter_inputs
from .tool_dispatch_common import ToolContext, UNHANDLED

def dispatch_control_tool(name: str, ctx: ToolContext):
    args = ctx.arguments
    db = ctx.db
    role = ctx.role
    user = ctx.user
    expected_role_version = ctx.expected_role_version
    if name == "list_open_recruiter_inputs":
        return _recruiter_inputs.list_open_recruiter_inputs(
            db,
            role=role,
            limit=int(args.get("limit") or 20),
        )
    if name == "answer_recruiter_input":
        result = _recruiter_inputs.answer_recruiter_input(
            db,
            role=role,
            user=user,
            needs_input_id=int(args["needs_input_id"]),
            value=args.get("value"),
            expected_role_version=int(
                expected_role_version
                if expected_role_version is not None
                else (role.version or 1)
            ),
        )
        message = f"Recruiter question {int(args['needs_input_id'])} was answered."
        return {
            "type": "operation_receipt",
            "operation": "answer_recruiter_input",
            "status": "answered",
            "message": message,
            "result": result,
            "_terminal_message": message,
        }
    if name == "dismiss_recruiter_input":
        result = _recruiter_inputs.dismiss_recruiter_input(
            db,
            role=role,
            user=user,
            needs_input_id=int(args["needs_input_id"]),
        )
        message = f"Recruiter question {int(args['needs_input_id'])} was dismissed."
        return {
            "type": "operation_receipt",
            "operation": "dismiss_recruiter_input",
            "status": "dismissed",
            "message": message,
            "result": result,
            "_terminal_message": message,
        }
    if name == "set_agent_state":
        result = _controls.set_agent_state(
            db,
            role,
            action=str(args.get("action") or ""),
            user_id=int(user.id),
        )
        # Successful mutations already commit with their audit. Invalid/no-op
        # commands reach here with only the authorization lock outstanding.
        if db.in_transaction():
            db.commit()
        return result
    if name == "adjust_agent_settings":
        mbc = args.get("monthly_budget_cents")
        result = _controls.adjust_agent_settings(
            db,
            role,
            monthly_budget_cents=int(mbc) if mbc is not None else None,
            auto_reject=args.get("auto_reject"),
            auto_reject_pre_screen=args.get("auto_reject_pre_screen"),
            auto_promote=args.get("auto_promote"),
            auto_send_assessment=args.get("auto_send_assessment"),
            auto_resend_assessment=args.get("auto_resend_assessment"),
            auto_advance=args.get("auto_advance"),
            auto_skip_assessment=args.get("auto_skip_assessment"),
            user_id=int(user.id),
        )
        if db.in_transaction():
            db.commit()
        return result
    if name == "list_draft_tasks":
        return _draft_tasks.draft_review_card(db, role)
    if name == "role_health_check":
        return _health.role_health_check(db, role)
    if name == "sync_workable_comments":
        return _controls.sync_workable_comments(db, role, user=user)
    return UNHANDLED
