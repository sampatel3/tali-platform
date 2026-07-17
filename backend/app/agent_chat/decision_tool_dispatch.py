"""Focused Agent Chat decision tools."""

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
    lookup_command,
)
from .tool_dispatch_common import ToolContext, UNHANDLED

def _decision_fingerprint(snapshot: dict[str, Any]) -> str:
    """Stable state proof for a decision-action preview."""
    keys = (
        "decision_id",
        "application_id",
        "decision_type",
        "recommendation",
        "role_family",
        "status",
        "created_at",
        "is_stale",
        "staleness_reasons",
        "approval_requires_workable_stage",
        "supported_alternatives",
    )
    body = {key: snapshot.get(key) for key in keys}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()

def _normalized_decision_action_args(
    action: str,
    args: dict[str, Any],
    *,
    decision_teach: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {"decision_id": int(args["decision_id"])}
    if action == "approve_decision":
        out.update(
            {
                "note": str(args.get("note") or "").strip() or None,
                "workable_target_stage": (
                    str(args.get("workable_target_stage") or "").strip() or None
                ),
            }
        )
    elif action == "override_decision":
        out.update(
            {
                "alternative": str(args.get("alternative") or "").strip(),
                "note": str(args.get("note") or "").strip(),
                "workable_target_stage": (
                    str(args.get("workable_target_stage") or "").strip() or None
                ),
            }
        )
    elif action == "teach_decision":
        out.update(
            decision_teach.normalize_teach_payload(
                failure_mode=str(args.get("failure_mode") or ""),
                correction_text=str(args.get("correction_text") or ""),
                scope=str(args.get("scope") or ""),
                attributed_to=(
                    str(args.get("attributed_to") or "").strip() or None
                ),
                direction=str(args.get("direction") or "").strip() or None,
            )
        )
    return out

def _decision_preview(
    *,
    action: str,
    snapshot: dict[str, Any],
    normalized_args: dict[str, Any],
    binding: dict[str, int],
    reason: str | None = None,
) -> dict[str, Any]:
    labels = {
        "approve_decision": "approve the recommendation",
        "override_decision": "override the recommendation",
        "re_evaluate_decision": "re-evaluate the decision",
        "teach_decision": "send the decision back with structured feedback",
    }
    operation = f"{action}:{int(snapshot['decision_id'])}"
    return attach_confirmation(
        {
            "type": "decision_action_preview",
            "operation": action,
            "decision": snapshot,
            "requested_action": normalized_args,
            "message": reason
            or (
                f"Preview: {labels[action]} for {snapshot.get('candidate_name') or 'this candidate'}. "
                "No action has run. Ask the recruiter to confirm in a new message."
            ),
        },
        operation=operation,
        payload={
            **binding,
            "role_id": int(binding.get("role_id") or 0),
            "decision_id": int(snapshot["decision_id"]),
            "arguments": normalized_args,
            "fingerprint": _decision_fingerprint(snapshot),
        },
    )

def _dispatch_confirmed_decision_action(
    action: str,
    args: dict[str, Any],
    *,
    db: Session,
    role: Role,
    user: Any,
    conversation: Any,
    binding: dict[str, int],
    decision_commands: Any,
    decision_teach: Any,
    decision_receipt_recovery: Any,
    complete_command_fn: Any,
) -> dict[str, Any]:
    """Preview, bind, re-check, then execute one high-impact decision action."""
    normalized = _normalized_decision_action_args(
        action,
        args,
        decision_teach=decision_teach,
    )
    decision_id = int(normalized["decision_id"])
    operation = f"{action}:{decision_id}"
    check = None
    if conversation is not None:
        check = require_later_turn_confirmation(
            db,
            conversation=conversation,
            operation=operation,
            token=str(args.get("confirmation_token") or "") or None,
            user=user,
        )
        if check.ok:
            prior = lookup_command(
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
            if prior is not None:
                if prior.completed_result is not None:
                    return prior.completed_result
                # A canonical route may have committed its decision/feedback
                # row before this chat worker could complete the receipt. Read
                # that durable state; never re-run an ambiguous provider action.
                recovered = decision_receipt_recovery.recover_confirmed_action(
                    db,
                    role,
                    user,
                    action=action,
                    arguments=normalized,
                )
                recovered_status = str(recovered.get("status") or "recovered")
                if recovered_status == "review_required":
                    message = (
                        f"Decision {decision_id} was not replayed because its prior "
                        "provider outcome is ambiguous. Review it before trying again."
                    )
                elif action == "teach_decision":
                    message = f"Feedback for decision {decision_id} was already recorded."
                elif action == "re_evaluate_decision":
                    message = f"Decision {decision_id} re-evaluation was already accepted."
                else:
                    message = f"Decision {decision_id} was already accepted for processing."
                receipt = mark_confirmation_consumed(
                    {
                        "type": "operation_receipt",
                        "operation": action,
                        "status": recovered_status,
                        "message": message,
                        "result": recovered,
                        "_terminal_message": message,
                    },
                    check=check,
                )
                return complete_command_fn(db, prior, receipt)

    snapshot = (
        decision_teach.get_teachable_decision(db, role, user, decision_id)
        if action == "teach_decision"
        else decision_commands.get_pending_decision(db, role, user, decision_id)
    )

    if action == "approve_decision":
        if not snapshot.get("can_approve"):
            return {
                "type": "decision_action_blocked",
                "operation": action,
                "decision": snapshot,
                "message": "This decision type has no executable approval action.",
            }
        if snapshot.get("is_stale"):
            return {
                "type": "decision_action_blocked",
                "operation": action,
                "decision": snapshot,
                "message": "This decision is stale. Re-evaluate it before approval.",
            }
        if (
            snapshot.get("approval_requires_workable_stage")
            and not normalized.get("workable_target_stage")
        ):
            return {
                "type": "decision_action_blocked",
                "operation": action,
                "decision": snapshot,
                "message": "Choose the destination Workable stage before approval.",
            }
    elif action == "override_decision":
        if normalized.get("alternative") not in snapshot.get("supported_alternatives", []):
            raise ValueError(
                f"unsupported alternative; choose one of {snapshot.get('supported_alternatives', [])}"
            )
        if not normalized.get("note"):
            raise ValueError("an override reason is required")
        if (
            normalized.get("alternative") == "advance"
            and snapshot.get("approval_requires_workable_stage")
            and not normalized.get("workable_target_stage")
        ):
            return {
                "type": "decision_action_blocked",
                "operation": action,
                "decision": snapshot,
                "message": "Choose the destination Workable stage before advancing.",
            }

    if conversation is None:
        return _decision_preview(
            action=action,
            snapshot=snapshot,
            normalized_args=normalized,
            binding={**binding, "role_id": int(role.id)},
        )
    payload_args = check.payload.get("arguments") if check.ok else None
    state_matches = bool(
        check.ok
        and int(check.payload.get("role_id") or 0) == int(role.id)
        and int(check.payload.get("decision_id") or 0) == decision_id
        and payload_args == normalized
        and check.payload.get("fingerprint") == _decision_fingerprint(snapshot)
    )
    if not state_matches:
        return _decision_preview(
            action=action,
            snapshot=snapshot,
            normalized_args=normalized,
            binding={**binding, "role_id": int(role.id)},
            reason=(
                "The prior preview is missing, expired, changed, or was not explicitly "
                "confirmed. Here is a fresh preview; no action has run."
            ),
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
        if action == "approve_decision":
            result = decision_commands.approve_decision(
                db,
                role,
                user,
                **normalized,
                expected_role_family=snapshot.get("role_family"),
                expected_decision_type=snapshot.get("decision_type"),
            )
            message = f"Decision {decision_id} was accepted for processing."
        elif action == "override_decision":
            result = decision_commands.override_decision(
                db,
                role,
                user,
                **normalized,
                expected_role_family=snapshot.get("role_family"),
                expected_decision_type=snapshot.get("decision_type"),
            )
            message = f"Decision {decision_id} override was accepted for processing."
        elif action == "teach_decision":
            result = decision_commands.teach_decision(db, role, user, **normalized)
            if result.get("cosign_required"):
                message = (
                    f"Feedback for decision {decision_id} was recorded and now requires "
                    "an admin co-sign before organization-wide learning."
                )
            else:
                message = f"Decision {decision_id} was sent back with recruiter feedback."
        else:
            result = decision_commands.re_evaluate_decision(
                db, role, user, decision_id=decision_id
            )
            message = f"Decision {decision_id} re-evaluation was queued."
    except Exception:
        abandon_uncommitted_command(db, claim)
        raise

    receipt = {
        "type": "operation_receipt",
        "operation": action,
        "status": result.get("status") or ("queued" if result.get("queued") else "accepted"),
        "message": message,
        "result": result,
        "_terminal_message": message,
    }
    receipt = mark_confirmation_consumed(receipt, check=check)
    return complete_command_fn(db, claim, receipt)

def dispatch_decision_tool(name: str, ctx: ToolContext, **dependencies):
    args = ctx.arguments
    db = ctx.db
    role = ctx.role
    user = ctx.user
    conversation = ctx.conversation
    confirmation_binding = ctx.confirmation_binding
    decision_commands = dependencies["decision_commands"]
    decision_teach = dependencies["decision_teach"]
    decision_receipt_recovery = dependencies["decision_receipt_recovery"]
    complete_command_fn = dependencies["complete_command_fn"]
    if name == "list_pending_decisions":
        return decision_commands.list_pending_decisions(
            db,
            role,
            user,
            include_snoozed=bool(args.get("include_snoozed") or False),
            limit=int(args.get("limit") or 20),
        )
    if name in {
        "approve_decision",
        "override_decision",
        "re_evaluate_decision",
        "teach_decision",
    }:
        return _dispatch_confirmed_decision_action(
            name,
            args,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
            binding=confirmation_binding,
            decision_commands=decision_commands,
            decision_teach=decision_teach,
            decision_receipt_recovery=decision_receipt_recovery,
            complete_command_fn=complete_command_fn,
        )
    if name == "snooze_decision":
        decision_id = int(args["decision_id"])
        result = decision_commands.snooze_decision(
            db,
            role,
            user,
            decision_id=decision_id,
        )
        message = f"Decision {decision_id} is snoozed for one hour."
        return {
            "type": "operation_receipt",
            "operation": "snooze_decision",
            "status": "snoozed",
            "message": message,
            "result": result,
            "_terminal_message": message,
        }
    return UNHANDLED
