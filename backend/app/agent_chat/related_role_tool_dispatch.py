"""Focused Agent Chat related_role tools."""

from __future__ import annotations

from fastapi import HTTPException

from ..models.organization import Organization
from ..services import related_role_service as _related_roles
from ..services.related_role_paid_work_authorization import (
    RELATED_ROLE_PAID_SCOPE_CHANGED,
    related_role_create_authority,
    require_related_role_publish_authority,
    select_related_role_monthly_budget,
)
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


def _paid_preview(
    ctx: ToolContext,
    *,
    name: str,
    job_spec_text: str,
    monthly_budget_cents: int | None,
    message: str | None = None,
):
    result = _related_roles.preview_related_role(
        ctx.db,
        role_id=int(ctx.role.id),
        organization_id=ctx.organization_id,
    )
    result = select_related_role_monthly_budget(result, monthly_budget_cents)
    result["proposed_name"] = name
    if message and result["initial_scope_fits_selected_budget"]:
        result["message"] = message
    return result


def _attach_paid_confirmation(
    result: dict,
    *,
    binding: dict,
    name: str,
    job_spec_text: str,
):
    if not result.get("initial_scope_fits_selected_budget"):
        result["confirmation_blocked"] = "initial_scope_over_monthly_cap"
        return result
    authority = related_role_create_authority(result)
    return attach_confirmation(
        result,
        operation="create_related_role",
        payload={
            **binding,
            **authority,
            "role_id": int(result["source_role_id"]),
            "role_version": int(result["source_role_version"]),
            "name": name,
            "spec_fingerprint": text_fingerprint(job_spec_text),
            "max_total": authority["approved_max_candidates_total"],
            "max_scorable": authority["approved_max_scoreable_count"],
        },
    )


def _paid_scope_matches(ctx: ToolContext, check, current: dict, selected_cap: int) -> bool:
    if int(check.payload.get("approved_monthly_budget_cents") or 0) != selected_cap:
        return False
    try:
        require_related_role_publish_authority(
            authority=check.payload,
            source_role=ctx.role,
            candidates_total=int(current.get("candidates_total") or 0),
            scoreable_count=int(current.get("candidates_scoreable") or 0),
            current_default_monthly_budget_cents=int(
                current.get("proposed_monthly_budget_cents") or 0
            ),
        )
    except HTTPException:
        return False
    return True


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
        result = _paid_preview(
            ctx,
            name=clean_name,
            job_spec_text=clean_spec,
            monthly_budget_cents=(
                int(args["monthly_budget_cents"])
                if args.get("monthly_budget_cents") is not None
                else None
            ),
        )
        return _attach_paid_confirmation(
            result,
            binding=confirmation_binding,
            name=clean_name,
            job_spec_text=clean_spec,
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
        current = _paid_preview(
            ctx,
            name=clean_name,
            job_spec_text=clean_spec,
            monthly_budget_cents=None,
        )
        confirmed_cap = int(check.payload.get("approved_monthly_budget_cents") or 0)
        selected_cap = int(args.get("monthly_budget_cents") or confirmed_cap)
        matches_preview = (
            int(check.payload.get("role_id") or 0) == int(role.id)
            and int(check.payload.get("role_version") or 0) == int(role.version or 1)
            and str(check.payload.get("name") or "") == clean_name
            and str(check.payload.get("spec_fingerprint") or "")
            == text_fingerprint(clean_spec)
            and _paid_scope_matches(ctx, check, current, selected_cap)
        )
        if not matches_preview:
            refreshed = _paid_preview(
                ctx,
                name=clean_name,
                job_spec_text=clean_spec,
                monthly_budget_cents=(
                    int(args["monthly_budget_cents"])
                    if args.get("monthly_budget_cents") is not None
                    else None
                ),
                message=(
                    "The name, specification, source version, roster, or monthly cap "
                    "changed since the preview. Please confirm this refreshed scope."
                ),
            )
            return _attach_paid_confirmation(
                refreshed,
                binding=confirmation_binding,
                name=clean_name,
                job_spec_text=clean_spec,
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
                "monthly_budget_cents": confirmed_cap,
            },
        )
        if claim.completed_result is not None:
            return claim.completed_result

        def authorize_created_scope(related, counts):
            post = _related_roles.preview_related_role(
                db,
                role_id=int(role.id),
                organization_id=org_id,
            )
            require_related_role_publish_authority(
                authority=check.payload,
                source_role=role,
                related_role=related,
                candidates_total=int(counts.get("total") or 0),
                scoreable_count=int(counts.get("pending") or 0),
                current_default_monthly_budget_cents=int(
                    post.get("proposed_monthly_budget_cents") or 0
                ),
            )

        try:
            related, evaluation_counts = _related_roles.create_related_role(
                db,
                role_id=int(role.id),
                organization_id=org_id,
                creator_user_id=int(user.id),
                name=clean_name,
                job_spec_text=clean_spec,
                commit=False,
                monthly_budget_cents=confirmed_cap,
                authorize_evaluation_counts=authorize_created_scope,
            )
        except HTTPException as exc:
            abandon_uncommitted_command(db, claim)
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if detail.get("code") == RELATED_ROLE_PAID_SCOPE_CHANGED:
                refreshed = _paid_preview(
                    ctx,
                    name=clean_name,
                    job_spec_text=clean_spec,
                    monthly_budget_cents=(
                        int(args["monthly_budget_cents"])
                        if args.get("monthly_budget_cents") is not None
                        else None
                    ),
                    message=(
                        "The source, roster, or monthly cap changed while the role "
                        "was being prepared. Nothing was created; confirm this "
                        "refreshed scope."
                    ),
                )
                return _attach_paid_confirmation(
                    refreshed,
                    binding=confirmation_binding,
                    name=clean_name,
                    job_spec_text=clean_spec,
                )
            raise
        except Exception:
            abandon_uncommitted_command(db, claim)
            raise
        result = _related_roles.related_role_created_payload(
            related, evaluation_counts
        )
        result = mark_confirmation_consumed(result, check=check)
        return complete_command(db, claim, result)
    return UNHANDLED
