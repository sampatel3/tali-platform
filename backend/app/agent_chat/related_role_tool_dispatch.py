"""Focused Agent Chat related_role tools."""

from __future__ import annotations
from ..models.organization import Organization
from ..services import related_role_service as _related_roles
from ..services.requisition_template_service import resolve_template
from ..services.sister_role_service import text_fingerprint
from .confirmations import (
    attach_confirmation,
    blocked_confirmation_result,
    mark_confirmation_consumed,
    require_later_turn_confirmation,
)
from .command_receipts import (
    abandon_uncommitted_command,
    begin_agent_turn_command,
    begin_command,
)
from .tool_dispatch_common import ToolContext, UNHANDLED

def dispatch_related_role_tool(name: str, ctx: ToolContext, *, complete_command_fn):
    args = ctx.arguments
    db = ctx.db
    role = ctx.role
    user = ctx.user
    conversation = ctx.conversation
    org_id = ctx.organization_id
    confirmation_binding = ctx.confirmation_binding
    complete_command = complete_command_fn
    if name == "start_related_role_draft":
        if conversation is None:
            raise ValueError(
                "A persisted recruiter turn is required to start a related-role draft."
            )
        clean_name = str(args.get("name") or "").strip() or None
        clean_spec = (
            str(args.get("job_spec_text") or "").strip() or None
            if args.get("job_spec_text") is not None
            else None
        )
        claim = None
        try:
            # Keep the draft and command receipt in a savepoint.  The chat
            # engine catches tool exceptions so a failed completion must not
            # leak a draft into the outer transcript transaction.
            with db.begin_nested():
                claim = begin_agent_turn_command(
                    db,
                    conversation=conversation,
                    organization_id=org_id,
                    role_id=int(role.id),
                    requested_by_user_id=int(user.id),
                    operation="start_related_role_draft",
                    arguments={
                        "name": clean_name,
                        "job_spec_text": clean_spec,
                    },
                )
                if claim.completed_result is not None:
                    result = claim.completed_result
                else:
                    org = (
                        db.query(Organization)
                        .filter(Organization.id == int(org_id))
                        .one()
                    )
                    brief = _related_roles.create_related_role_draft(
                        db,
                        role_id=int(role.id),
                        organization_id=org_id,
                        creator_user_id=int(user.id),
                        template=resolve_template(org),
                        name=clean_name,
                        job_spec_text=clean_spec,
                        commit=False,
                    )
                    result = _related_roles.related_role_draft_payload(brief)
                    result["_terminal_message"] = str(result["message"])
                    result = complete_command(db, claim, result)
            return result
        except Exception:
            if claim is not None:
                abandon_uncommitted_command(db, claim)
            raise
    if name == "preview_related_role":
        clean_name = str(args.get("name") or "").strip()
        clean_spec = str(args.get("job_spec_text") or "").strip()
        if not clean_name:
            raise ValueError("Give the related role a name.")
        if len(clean_spec) < 80:
            raise ValueError(
                "Paste the complete updated job specification (at least 80 characters)."
            )
        result = _related_roles.preview_related_role(
            db, role_id=int(role.id), organization_id=org_id
        )
        result.update({"proposed_name": clean_name})
        return attach_confirmation(
            result,
            operation="create_related_role",
            payload={
                **confirmation_binding,
                "role_id": int(role.id),
                "name": clean_name,
                "spec_fingerprint": text_fingerprint(clean_spec),
                "max_total": int(result.get("candidates_total") or 0),
                "max_scorable": int(result.get("candidates_with_cv") or 0),
            },
        )
    if name == "create_related_role":
        clean_name = str(args.get("name") or "").strip()
        clean_spec = str(args.get("job_spec_text") or "").strip()
        if conversation is None:
            return blocked_confirmation_result(
                "create_related_role", "No persisted chat confirmation is available."
            )
        check = require_later_turn_confirmation(
            db,
            conversation=conversation,
            operation="create_related_role",
            token=str(args.get("confirmation_token") or "") or None,
            user=user,
        )
        if not check.ok:
            return blocked_confirmation_result("create_related_role", check.reason)
        current = _related_roles.preview_related_role(
            db, role_id=int(role.id), organization_id=org_id
        )
        matches_preview = (
            int(check.payload.get("role_id") or 0) == int(role.id)
            and str(check.payload.get("name") or "") == clean_name
            and str(check.payload.get("spec_fingerprint") or "")
            == text_fingerprint(clean_spec)
            and int(current.get("candidates_total") or 0)
            <= int(check.payload.get("max_total") or 0)
            and int(current.get("candidates_with_cv") or 0)
            <= int(check.payload.get("max_scorable") or 0)
        )
        if not matches_preview:
            current.update(
                {
                    "proposed_name": clean_name,
                    "message": (
                        "The name, specification, or roster changed since the preview. "
                        "Please confirm this refreshed scope."
                    ),
                }
            )
            return attach_confirmation(
                current,
                operation="create_related_role",
                payload={
                    **confirmation_binding,
                    "role_id": int(role.id),
                    "name": clean_name,
                    "spec_fingerprint": text_fingerprint(clean_spec),
                    "max_total": int(current.get("candidates_total") or 0),
                    "max_scorable": int(current.get("candidates_with_cv") or 0),
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
            operation="create_related_role",
            arguments={
                "name": clean_name,
                "spec_fingerprint": text_fingerprint(clean_spec),
            },
        )
        if claim.completed_result is not None:
            return claim.completed_result
        try:
            related, evaluation_counts = _related_roles.create_related_role(
                db,
                role_id=int(role.id),
                organization_id=org_id,
                creator_user_id=int(user.id),
                name=clean_name,
                job_spec_text=clean_spec,
                commit=False,
            )
        except Exception:
            abandon_uncommitted_command(db, claim)
            raise
        result = _related_roles.related_role_created_payload(
            related, evaluation_counts
        )
        result = mark_confirmation_consumed(result, check=check)
        return complete_command(db, claim, result)
    return UNHANDLED
