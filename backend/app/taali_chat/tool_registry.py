"""Taali Chat adapter for the canonical MCP tool catalogue.

Tool contracts live in :mod:`app.mcp.catalog`.  This module only binds those
contracts to the in-process handlers used by the chat transport.  Keeping the
adapter deliberately small prevents the public MCP and chat schemas from
silently drifting apart.
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from ..agent_chat.confirmations import (
    attach_confirmation,
    blocked_confirmation_result,
    mark_confirmation_consumed,
)
from ..domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ..mcp import handlers, operations
from ..mcp.catalog import TAALI_CHAT, ToolSpec, get_tool_spec, tools_for
from ..models.taali_chat_conversation import TaaliChatConversation
from ..models.user import User
from ..services import related_role_service as _related_roles
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
    role_id: int,
    name: str,
    job_spec_text: str,
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
    result["proposed_name"] = clean_name
    if message:
        result["message"] = message
    return attach_confirmation(
        result,
        operation="create_related_role",
        payload={
            "role_id": int(role_id),
            "name": clean_name,
            "spec_fingerprint": text_fingerprint(clean_spec),
            "max_total": int(result.get("candidates_total") or 0),
            "max_scorable": int(result.get("candidates_with_cv") or 0),
        },
    )


def _create_with_confirmation(
    db: Session,
    *,
    user: User,
    conversation: TaaliChatConversation | None,
    role_id: int,
    name: str,
    job_spec_text: str,
    confirmation_token: str | None = None,
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
    )
    if not check.ok:
        return blocked_confirmation_result("create_related_role", check.reason)
    # Re-check at the mutation boundary under the canonical source-role lock.
    # Hiring-team membership may have changed while confirmation was pending.
    require_job_permission(
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
    matches_preview = (
        int(check.payload.get("role_id") or 0) == role_id
        and str(check.payload.get("name") or "") == clean_name
        and str(check.payload.get("spec_fingerprint") or "")
        == text_fingerprint(clean_spec)
        and int(current.get("candidates_total") or 0)
        <= int(check.payload.get("max_total") or 0)
        and int(current.get("candidates_with_cv") or 0)
        <= int(check.payload.get("max_scorable") or 0)
    )
    if not matches_preview:
        return _preview_with_receipt(
            db,
            user=user,
            role_id=role_id,
            name=clean_name,
            job_spec_text=clean_spec,
            message=(
                "The name, specification, or roster changed since the preview. "
                "Please confirm this refreshed scope."
            ),
        )
    related, evaluation_counts = _related_roles.create_related_role(
        db,
        role_id=role_id,
        organization_id=int(user.organization_id),
        creator_user_id=int(user.id),
        name=clean_name,
        job_spec_text=clean_spec,
    )
    result = _related_roles.related_role_created_payload(related, evaluation_counts)
    return mark_confirmation_consumed(result, check=check)


def _resolve_handlers() -> dict[str, Callable[..., Any]]:
    """Resolve every chat catalog entry to exactly one callable handler."""

    special_handlers: dict[str, Callable[..., Any]] = {
        "preview_related_role": _preview_with_receipt,
        "create_related_role": _create_with_confirmation,
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
        return handler(db, user=user, **safe_args)
    if name == "create_related_role":
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
