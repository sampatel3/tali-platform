"""Focused Agent Chat application tools."""

from __future__ import annotations
import hashlib
import json
from typing import Any
from sqlalchemy.orm import Session
from ..models.role import Role
from .confirmations import (
    attach_confirmation,
    mark_confirmation_consumed,
    require_later_turn_confirmation,
)
from .command_receipts import (
    abandon_uncommitted_command,
    begin_command,
)
from .tool_dispatch_common import ToolContext, UNHANDLED

def _normalized_application_action_args(
    action: str, args: dict[str, Any]
) -> dict[str, Any]:
    if action == "create_application":
        return {
            "candidate_email": str(args.get("candidate_email") or "").strip(),
            "candidate_name": str(args.get("candidate_name") or "").strip() or None,
            "candidate_position": (
                str(args.get("candidate_position") or "").strip() or None
            ),
            "notes": str(args.get("notes") or "").strip() or None,
        }
    if action == "post_workable_note":
        return {
            "application_id": int(args["application_id"]),
            "body": str(args.get("body") or "").strip(),
        }
    raw_application_id = args.get("application_id")
    return {
        "application_id": (
            int(raw_application_id) if raw_application_id is not None else None
        )
    }

def _application_action_preview(
    action: str,
    *,
    db: Session,
    role: Role,
    user: Any,
    normalized: dict[str, Any],
    application_commands: Any,
) -> dict[str, Any]:
    if action == "create_application":
        return application_commands.preview_create_application(
            db, role, user, **normalized
        )
    if action == "post_workable_note":
        return application_commands.preview_workable_note(
            db, role, user, **normalized
        )
    return application_commands.preview_manual_run(db, role, user, **normalized)

def _dispatch_confirmed_application_action(
    action: str,
    args: dict[str, Any],
    *,
    db: Session,
    role: Role,
    user: Any,
    conversation: Any,
    binding: dict[str, int],
    application_commands: Any,
    complete_command_fn: Any,
) -> dict[str, Any]:
    normalized = _normalized_application_action_args(action, args)
    preview = _application_action_preview(
        action,
        db=db,
        role=role,
        user=user,
        normalized=normalized,
        application_commands=application_commands,
    )
    # ApplicationCreate normalizes email; bind execution to the canonical form.
    if action == "create_application":
        normalized["candidate_email"] = str(preview["candidate_email"])

    if action == "create_application" and not preview.get("can_create"):
        return {
            "type": "operation_blocked",
            "operation": action,
            "preview": preview,
            "message": f"Application creation is blocked: {preview.get('blocked_reason')}.",
        }
    if action == "post_workable_note" and not preview.get("can_queue"):
        return {
            "type": "operation_blocked",
            "operation": action,
            "preview": preview,
            "message": "This application is not linked to a Workable candidate.",
        }
    if action == "run_agent_now" and not preview.get("can_queue"):
        return {
            "type": "operation_blocked",
            "operation": action,
            "preview": preview,
            "message": f"The manual run is blocked: {preview.get('blocked_reason')}.",
        }

    suffix = normalized.get("application_id")
    if suffix is None and action == "create_application":
        suffix = hashlib.sha256(normalized["candidate_email"].encode()).hexdigest()[:16]
    operation = f"{action}:{suffix if suffix is not None else int(role.id)}"
    fingerprint = hashlib.sha256(
        json.dumps(preview, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()

    check = None
    if conversation is not None:
        check = require_later_turn_confirmation(
            db,
            conversation=conversation,
            operation=operation,
            token=str(args.get("confirmation_token") or "") or None,
            user=user,
        )
    state_matches = bool(
        check
        and check.ok
        and int(check.payload.get("role_id") or 0) == int(role.id)
        and check.payload.get("arguments") == normalized
        and check.payload.get("fingerprint") == fingerprint
    )
    if not state_matches:
        return attach_confirmation(
            {
                "type": "operation_preview",
                "operation": action,
                "preview": preview,
                "requested_action": normalized,
                "message": (
                    "No action has run. Show this exact preview and ask the recruiter "
                    "to confirm in a new message."
                ),
            },
            operation=operation,
            payload={
                **binding,
                "role_id": int(role.id),
                "arguments": normalized,
                "fingerprint": fingerprint,
            },
        )

    claim = begin_command(
        db,
        check=check,
        conversation_kind="agent",
        conversation_id=int(conversation.id),
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        requested_by_user_id=int(user.id),
        operation=operation,
        arguments=normalized,
    )
    if claim.completed_result is not None:
        return claim.completed_result

    try:
        if action == "create_application":
            result = application_commands.create_application(
                db, role, user, **normalized
            )
            message = (
                f"Application {result['application_id']} was created for "
                f"{result['candidate_email']}."
            )
        elif action == "post_workable_note":
            # Keep the newly staged receipt out of an autoflush before the durable
            # BackgroundJobRun is inserted by its independent session.
            with db.no_autoflush:
                result = application_commands.queue_workable_note(
                    db,
                    role,
                    user,
                    dispatch_key=claim.dispatch_key,
                    **normalized,
                )
            message = (
                f"The Workable note for application {result['application_id']} is queued."
            )
        else:
            with db.no_autoflush:
                result = application_commands.enqueue_manual_run(
                    db,
                    role,
                    user,
                    dispatch_key=claim.dispatch_key,
                    **normalized,
                )
            message = (
                "The focused agent run is queued."
                if normalized.get("application_id") is not None and result.get("queued")
                else "The role agent run is queued."
                if result.get("queued")
                else str(result.get("detail") or "The agent run was not queued.")
            )
    except Exception:
        abandon_uncommitted_command(db, claim)
        raise

    receipt = {
        "type": "operation_receipt",
        "operation": action,
        "status": result.get("status") or "accepted",
        "message": message,
        "result": result,
        "_terminal_message": message,
    }
    receipt = mark_confirmation_consumed(receipt, check=check)
    return complete_command_fn(db, claim, receipt)

def dispatch_application_tool(name: str, ctx: ToolContext, **dependencies):
    args = ctx.arguments
    db = ctx.db
    role = ctx.role
    user = ctx.user
    conversation = ctx.conversation
    confirmation_binding = ctx.confirmation_binding
    application_commands = dependencies["application_commands"]
    complete_command_fn = dependencies["complete_command_fn"]
    if name in {"create_application", "post_workable_note", "run_agent_now"}:
        return _dispatch_confirmed_application_action(
            name,
            args,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
            binding=confirmation_binding,
            application_commands=application_commands,
            complete_command_fn=complete_command_fn,
        )
    if name == "add_internal_note":
        application_id = int(args["application_id"])
        result = application_commands.add_internal_note(
            db,
            role,
            user,
            application_id=application_id,
            note=str(args.get("note") or ""),
            for_agent=bool(args.get("for_agent", True)),
        )
        message = f"Internal note added to application {application_id}."
        return {
            "type": "operation_receipt",
            "operation": "add_internal_note",
            "status": "added",
            "message": message,
            "result": result,
            "_terminal_message": message,
        }
    return UNHANDLED
