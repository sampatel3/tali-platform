"""Taali Chat adapter for the canonical MCP tool catalogue.

Tool contracts live in :mod:`app.mcp.catalog`.  This module only binds those
contracts to the in-process handlers used by the chat transport.  Keeping the
adapter deliberately small prevents the public MCP and chat schemas from
silently drifting apart.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..agent_chat.confirmations import (
    attach_confirmation,
    blocked_confirmation_result,
    mark_confirmation_consumed,
)
from ..agent_chat.command_receipts import (
    abandon_uncommitted_command,
    begin_command,
    complete_command,
)
from ..domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ..mcp import handlers, operations
from ..mcp.catalog import TAALI_CHAT, ToolSpec, get_tool_spec, tools_for
from ..models.taali_chat_conversation import TaaliChatConversation
from ..models.user import User
from ..services.candidate_report_command import (
    CandidateReportKind,
    execute_confirmed_candidate_report,
)
from ..services import related_role_service as _related_roles
from ..services.related_role_paid_work_authorization import (
    RELATED_ROLE_PAID_SCOPE_CHANGED,
    related_role_create_authority,
    require_related_role_publish_authority,
    select_related_role_monthly_budget,
)
from ..services.sister_role_service import text_fingerprint
from .confirmations import require_later_turn_confirmation


TAALI_CHAT_SPECS: tuple[ToolSpec, ...] = tuple(tools_for(TAALI_CHAT))
TAALI_CHAT_TOOLS: list[dict[str, Any]] = [
    spec.anthropic_definition() for spec in TAALI_CHAT_SPECS
]


def _preview_with_receipt(
    db: Session,
    *,
    user: User,
    conversation: TaaliChatConversation | None = None,
    role_id: int,
    name: str,
    job_spec_text: str,
    monthly_budget_cents: int | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    # Global chat can reference any role ID in the workspace. Apply the same
    # per-job edit policy used by the role page before disclosing a creation
    # preview, without retaining a write lock for this read-only operation.
    require_job_permission(
        db,
        current_user=user,
        role_id=int(role_id),
        permission=JobPermission.EDIT_ROLE,
        lock_for_update=False,
    )
    clean_name = str(name or "").strip()
    clean_spec = str(job_spec_text or "").strip()
    if not clean_name:
        raise ValueError("Give the related role a name.")
    if len(clean_spec) < 80:
        raise ValueError(
            "Paste the complete updated job specification (at least 80 characters)."
        )
    result = _related_roles.preview_related_role(
        db,
        role_id=int(role_id),
        organization_id=int(user.organization_id),
    )
    result = select_related_role_monthly_budget(result, monthly_budget_cents)
    result["proposed_name"] = clean_name
    if message and result["initial_scope_fits_selected_budget"]:
        result["message"] = message
    if not result["initial_scope_fits_selected_budget"]:
        result["confirmation_blocked"] = "initial_scope_over_monthly_cap"
        return result
    binding = {
        "organization_id": int(user.organization_id),
        "requested_by_user_id": int(user.id),
    }
    if conversation is not None:
        binding["conversation_id"] = int(conversation.id)
    authority = related_role_create_authority(result)
    return attach_confirmation(
        result,
        operation="create_related_role",
        payload={
            **binding,
            **authority,
            "role_id": int(role_id),
            "role_version": int(result["source_role_version"]),
            "name": clean_name,
            "spec_fingerprint": text_fingerprint(clean_spec),
            "max_total": authority["approved_max_candidates_total"],
            "max_scorable": authority["approved_max_scoreable_count"],
        },
    )


def _paid_scope_matches(
    *,
    check,
    source_role,
    current: dict[str, Any],
    selected_cap: int,
) -> bool:
    if int(check.payload.get("approved_monthly_budget_cents") or 0) != selected_cap:
        return False
    try:
        require_related_role_publish_authority(
            authority=check.payload,
            source_role=source_role,
            candidates_total=int(current.get("candidates_total") or 0),
            scoreable_count=int(current.get("candidates_scoreable") or 0),
            current_default_monthly_budget_cents=int(
                current.get("proposed_monthly_budget_cents") or 0
            ),
        )
    except HTTPException:
        return False
    return True


def _create_with_confirmation(
    db: Session,
    *,
    user: User,
    conversation: TaaliChatConversation | None,
    role_id: int,
    name: str,
    job_spec_text: str,
    confirmation_token: str | None = None,
    monthly_budget_cents: int | None = None,
) -> dict[str, Any]:
    if conversation is None:
        return blocked_confirmation_result(
            "create_related_role", "No persisted chat confirmation is available."
        )
    clean_name = name.strip()
    clean_spec = job_spec_text.strip()
    check = require_later_turn_confirmation(
        db,
        conversation=conversation,
        operation="create_related_role",
        token=confirmation_token,
        user=user,
    )
    if not check.ok:
        return blocked_confirmation_result("create_related_role", check.reason)
    if conversation.role_id is not None and int(conversation.role_id) != int(role_id):
        return blocked_confirmation_result(
            "create_related_role",
            "The preview belongs to a different role conversation.",
        )
    # Re-check at the mutation boundary under the canonical source-role lock.
    # Hiring-team membership may have changed while confirmation was pending.
    source_role = require_job_permission(
        db,
        current_user=user,
        role_id=int(role_id),
        permission=JobPermission.EDIT_ROLE,
    )
    current = _related_roles.preview_related_role(
        db,
        role_id=role_id,
        organization_id=int(user.organization_id),
    )
    confirmed_cap = int(check.payload.get("approved_monthly_budget_cents") or 0)
    selected_cap = int(
        monthly_budget_cents if monthly_budget_cents is not None else confirmed_cap
    )
    matches_preview = (
        int(check.payload.get("role_id") or 0) == role_id
        and int(check.payload.get("role_version") or 0)
        == int(source_role.version or 1)
        and str(check.payload.get("name") or "") == clean_name
        and str(check.payload.get("spec_fingerprint") or "")
        == text_fingerprint(clean_spec)
        and _paid_scope_matches(
            check=check,
            source_role=source_role,
            current=current,
            selected_cap=selected_cap,
        )
    )
    if not matches_preview:
        return _preview_with_receipt(
            db,
            user=user,
            conversation=conversation,
            role_id=role_id,
            name=clean_name,
            job_spec_text=clean_spec,
            monthly_budget_cents=monthly_budget_cents,
            message=(
                "The name, specification, source version, roster, or monthly cap "
                "changed since the preview. Please confirm this refreshed scope."
            ),
        )
    claim = begin_command(
        db,
        check=check,
        conversation_kind="taali",
        conversation_id=int(conversation.id),
        organization_id=int(user.organization_id),
        role_id=int(role_id),
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
            role_id=int(role_id),
            organization_id=int(user.organization_id),
        )
        require_related_role_publish_authority(
            authority=check.payload,
            source_role=source_role,
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
            role_id=role_id,
            organization_id=int(user.organization_id),
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
            return _preview_with_receipt(
                db,
                user=user,
                conversation=conversation,
                role_id=role_id,
                name=clean_name,
                job_spec_text=clean_spec,
                monthly_budget_cents=monthly_budget_cents,
                message=(
                    "The source, roster, or monthly cap changed while the role was "
                    "being prepared. Nothing was created; confirm this refreshed scope."
                ),
            )
        raise
    except Exception:
        abandon_uncommitted_command(db, claim)
        raise
    result = _related_roles.related_role_created_payload(related, evaluation_counts)
    result = mark_confirmation_consumed(result, check=check)
    return complete_command(db, claim, result)


def _create_candidate_report_with_confirmation(
    db: Session,
    *,
    kind: CandidateReportKind,
    user: User,
    conversation: TaaliChatConversation | None,
    role_id: int,
    confirmation_token: str | None,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    operation_name = (
        "create_top_candidates_report"
        if kind == "top_candidates"
        else "create_screen_pool_report"
    )
    if conversation is None:
        return blocked_confirmation_result(
            operation_name, "No persisted chat confirmation is available."
        )
    if (
        int(conversation.user_id) != int(user.id)
        or int(conversation.organization_id) != int(user.organization_id)
    ):
        return blocked_confirmation_result(
            operation_name,
            "The preview belongs to a different recruiter or organization.",
        )
    if conversation.role_id is not None and int(conversation.role_id) != int(role_id):
        return blocked_confirmation_result(
            operation_name,
            "The preview belongs to a different role conversation.",
        )

    # Report publication is role-scoped. Re-run the canonical permission check
    # on both preview and confirmed calls; on confirmation it takes the shared
    # role lock so a concurrent hiring-team revocation cannot race the write.
    role = require_job_permission(
        db,
        current_user=user,
        role_id=int(role_id),
        permission=JobPermission.CONTROL_AGENT,
    )
    binding = {
        "conversation_id": int(conversation.id),
        "organization_id": int(conversation.organization_id),
        "requested_by_user_id": int(user.id),
    }
    token = str(confirmation_token or "").strip() or None
    return execute_confirmed_candidate_report(
        db,
        kind=kind,
        role=role,
        user=user,
        conversation_kind="taali",
        conversation_id=int(conversation.id),
        binding=binding,
        arguments=arguments,
        resolve_confirmation=lambda operation: require_later_turn_confirmation(
            db,
            conversation=conversation,
            operation=operation,
            token=token,
            user=user,
        ),
    )


def _create_top_candidates_report(
    db: Session,
    *,
    user: User,
    conversation: TaaliChatConversation | None,
    role_id: int,
    query: str,
    limit: int = 10,
    rank_by: str = "taali",
    confirmation_token: str | None = None,
) -> dict[str, Any]:
    return _create_candidate_report_with_confirmation(
        db,
        kind="top_candidates",
        user=user,
        conversation=conversation,
        role_id=role_id,
        confirmation_token=confirmation_token,
        arguments={"query": query, "limit": limit, "rank_by": rank_by},
    )


def _create_screen_pool_report(
    db: Session,
    *,
    user: User,
    conversation: TaaliChatConversation | None,
    role_id: int,
    requirement_text: str,
    limit: int = 20,
    offset: int = 0,
    deep_verify: bool = False,
    confirmation_token: str | None = None,
) -> dict[str, Any]:
    return _create_candidate_report_with_confirmation(
        db,
        kind="screen_pool",
        user=user,
        conversation=conversation,
        role_id=role_id,
        confirmation_token=confirmation_token,
        arguments={
            "requirement_text": requirement_text,
            "limit": limit,
            "offset": offset,
            "deep_verify": deep_verify,
        },
    )


def _resolve_handlers() -> dict[str, Callable[..., Any]]:
    """Resolve every chat catalog entry to exactly one callable handler."""

    special_handlers: dict[str, Callable[..., Any]] = {
        "preview_related_role": _preview_with_receipt,
        "create_related_role": _create_with_confirmation,
        "create_top_candidates_report": _create_top_candidates_report,
        "create_screen_pool_report": _create_screen_pool_report,
    }
    result: dict[str, Callable[..., Any]] = {}
    modules = (handlers, operations)
    for spec in TAALI_CHAT_SPECS:
        matches = [
            candidate
            for module in modules
            if callable(candidate := getattr(module, spec.handler_name, None))
        ]
        special = special_handlers.get(spec.name)
        if special is not None:
            matches.append(special)
        if len(matches) != 1:
            raise RuntimeError(
                f"Taali Chat tool {spec.name!r} must resolve exactly one handler "
                f"named {spec.handler_name!r}; found {len(matches)}"
            )
        result[spec.name] = matches[0]
    return result


_HANDLER_BY_NAME = _resolve_handlers()


def dispatch_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    db: Session,
    user: User,
    conversation: TaaliChatConversation | None = None,
) -> Any:
    """Validate and run one Taali Chat tool call.

    Unknown or non-chat tools raise ``KeyError``.  Model-generated arguments
    are validated against the same strict contract exposed to MCP clients, so
    extra fields and invalid enum values cannot leak into domain handlers.
    """
    spec = get_tool_spec(name)
    if TAALI_CHAT not in spec.exposures:
        raise KeyError(f"unknown Taali Chat tool: {name}")
    handler = _HANDLER_BY_NAME.get(name)
    if handler is None:
        raise KeyError(f"no Taali Chat handler registered for: {name}")
    safe_args = spec.validate(arguments)
    if name == "preview_related_role":
        return handler(
            db,
            user=user,
            conversation=conversation,
            **safe_args,
        )
    if name in {
        "create_related_role",
        "create_top_candidates_report",
        "create_screen_pool_report",
    }:
        return handler(db, user=user, conversation=conversation, **safe_args)
    return handler(db, user, **safe_args)


def persistence_policy_for(name: str) -> str:
    """Return how a tool result may be stored in the chat transcript."""

    spec = get_tool_spec(name)
    if TAALI_CHAT not in spec.exposures:
        raise KeyError(f"unknown Taali Chat tool: {name}")
    return spec.persistence


__all__ = [
    "TAALI_CHAT_SPECS",
    "TAALI_CHAT_TOOLS",
    "dispatch_tool",
    "persistence_policy_for",
]
