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
from ..mcp import handlers, operations
from ..mcp.catalog import TAALI_CHAT, ToolSpec, get_tool_spec, tools_for
from ..models.taali_chat_conversation import TaaliChatConversation
from ..models.user import User
from ..services import related_role_service as _related_roles
from ..services.sister_role_service import text_fingerprint
from .confirmations import require_later_turn_confirmation


TAALI_CHAT_SPECS: tuple[ToolSpec, ...] = tuple(tools_for(TAALI_CHAT))
_RELATED_ROLE_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "preview_related_role",
        "description": (
            "Preview a NEW related Taali role over an original Workable role's "
            "existing applicants using a complete cousin/alternate job spec. "
            "Returns shared-roster size, scorable count, and estimated AI usage. "
            "This preserves the original role; stages and candidate actions still "
            "write back to Workable. Always preview, show the result, and wait for "
            "a later explicit recruiter confirmation before create_related_role."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role_id": {
                    "type": "integer",
                    "description": "The original Workable role. Defaults to the conversation's role when scoped.",
                },
                "name": {"type": "string"},
                "job_spec_text": {
                    "type": "string",
                    "description": "The complete updated job specification, not only the differences.",
                },
            },
            "required": ["role_id", "name", "job_spec_text"],
        },
    },
    {
        "name": "create_related_role",
        "description": (
            "Create a related role and queue fresh scores for its shared roster. "
            "Only call after preview_related_role and an explicit confirmation in "
            "a NEW recruiter message; the server enforces that receipt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role_id": {"type": "integer"},
                "name": {"type": "string"},
                "job_spec_text": {"type": "string"},
                "confirmation_token": {"type": ["string", "null"]},
            },
            "required": ["role_id", "name", "job_spec_text"],
        },
    },
]

# The shared tools are generated from the canonical MCP catalogue.  These two
# origin/main role-creation operations stay chat-only until they are promoted
# into that catalogue.
TAALI_CHAT_TOOLS: list[dict[str, Any]] = [
    spec.anthropic_definition() for spec in TAALI_CHAT_SPECS
] + _RELATED_ROLE_TOOL_DEFINITIONS


def _resolve_handlers() -> dict[str, Callable[..., Any]]:
    """Resolve every chat handler from its catalog name, failing on drift.

    Implementations may live in either the shared recruiting handlers or the
    compact operational views module.  A missing or ambiguous implementation
    is an import-time configuration error instead of a runtime surprise after
    the model has already selected a tool.
    """

    result: dict[str, Callable[..., Any]] = {}
    modules = (handlers, operations)
    for spec in TAALI_CHAT_SPECS:
        matches = [
            candidate
            for module in modules
            if callable(candidate := getattr(module, spec.handler_name, None))
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Taali Chat tool {spec.name!r} must resolve exactly one handler "
                f"named {spec.handler_name!r}; found {len(matches)}"
            )
        result[spec.name] = matches[0]
    return result


_HANDLER_BY_NAME = _resolve_handlers()


def _preview_with_receipt(
    db: Session,
    *,
    user: User,
    role_id: int,
    name: str,
    job_spec_text: str,
    message: str | None = None,
) -> dict[str, Any]:
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
    safe_args = arguments or {}
    if name == "preview_related_role":
        return _preview_with_receipt(
            db,
            user=user,
            role_id=int(safe_args["role_id"]),
            name=str(safe_args.get("name") or ""),
            job_spec_text=str(safe_args.get("job_spec_text") or ""),
        )
    if name == "create_related_role":
        if conversation is None:
            return blocked_confirmation_result(
                "create_related_role", "No persisted chat confirmation is available."
            )
        role_id = int(safe_args["role_id"])
        clean_name = str(safe_args.get("name") or "").strip()
        clean_spec = str(safe_args.get("job_spec_text") or "").strip()
        check = require_later_turn_confirmation(
            db,
            conversation=conversation,
            operation="create_related_role",
            token=str(safe_args.get("confirmation_token") or "") or None,
        )
        if not check.ok:
            return blocked_confirmation_result("create_related_role", check.reason)
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
            name=clean_name,
            job_spec_text=clean_spec,
        )
        result = _related_roles.related_role_created_payload(
            related, evaluation_counts
        )
        return mark_confirmation_consumed(result, check=check)

    spec = get_tool_spec(name)
    if TAALI_CHAT not in spec.exposures:
        raise KeyError(f"unknown Taali Chat tool: {name}")
    handler = _HANDLER_BY_NAME.get(name)
    if handler is None:
        raise KeyError(f"no Taali Chat handler registered for: {name}")
    return handler(db, user, **spec.validate(safe_args))


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
