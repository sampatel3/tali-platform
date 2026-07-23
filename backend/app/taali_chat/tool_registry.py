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
from ..mcp.payloads import candidate_detail
from ..mcp.shared_reads import dispatch_shared_read, shared_read_specs_for
from ..models.role import Role
from ..models.taali_chat_conversation import TaaliChatConversation
from ..models.user import User
from ..services import related_role_service as _related_roles
from ..services.logical_role_application_authority import (
    authorize_logical_role_candidate,
)
from ..services.sister_role_service import text_fingerprint
from .confirmations import require_later_turn_confirmation
from .search_context import population_context_for_search


TAALI_CHAT_SPECS: tuple[ToolSpec, ...] = tuple(tools_for(TAALI_CHAT))
TAALI_CHAT_TOOLS: list[dict[str, Any]] = [
    spec.anthropic_definition() for spec in TAALI_CHAT_SPECS
]

# These catalogue-backed reads are the authoritative candidate-state and
# history contract shared by public MCP, both recruiter chats, and the
# autonomous agent.  Taali Chat must dispatch them through the same adapter so
# a role-bound conversation receives the same server-owned authorization scope
# as every other role agent.
_SHARED_TAALI_READ_SPECS = shared_read_specs_for(TAALI_CHAT)
_SHARED_TAALI_READ_NAMES = frozenset(
    spec.name for spec in _SHARED_TAALI_READ_SPECS
)


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
            "source_snapshot_fingerprint": str(
                result.get("source_snapshot_fingerprint") or ""
            ),
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
        and str(check.payload.get("source_snapshot_fingerprint") or "")
        == str(current.get("source_snapshot_fingerprint") or "")
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
        expected_source_snapshot_fingerprint=str(
            check.payload.get("source_snapshot_fingerprint") or ""
        ),
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
    search_context: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> Any:
    """Validate and run one Taali Chat tool call.

    Unknown or non-chat tools raise ``KeyError``.  Model-generated arguments
    are validated against the same strict contract exposed to MCP clients, so
    extra fields and invalid enum values cannot leak into domain handlers.
    """
    spec = get_tool_spec(name)
    if TAALI_CHAT not in spec.exposures:
        raise KeyError(f"unknown Taali Chat tool: {name}")
    bound_role_id = (
        int(conversation.role_id)
        if conversation is not None and conversation.role_id is not None
        else None
    )
    if name in _SHARED_TAALI_READ_NAMES:
        handler_kwargs: dict[str, Any] | None = None
        if name == "find_top_candidates":
            if search_context is None and messages is not None:
                search_context = population_context_for_search(
                    messages,
                    current_query=str(arguments.get("query") or ""),
                )
            if search_context:
                handler_kwargs = {"_search_context": search_context}
        return dispatch_shared_read(
            name,
            arguments,
            exposure=TAALI_CHAT,
            db=db,
            principal=user,
            bound_role_id=bound_role_id,
            handler_kwargs=handler_kwargs,
        )
    # These legacy catalogue names remain useful to unbound global Chat and
    # public MCP callers. In a role-bound conversation their physical
    # CandidateApplication handlers are never authoritative: bind them to the
    # same logical-role projection as the canonical candidate tools so a
    # related role cannot silently fall back to its ATS transport owner's
    # score, stage, outcome, or evidence.
    if bound_role_id is not None and name in {
        "get_application",
        "compare_applications",
        "get_candidate",
        "get_candidate_cv",
    }:
        safe_args = spec.validate(arguments)
        if name == "get_application":
            return handlers.get_role_candidate(
                db,
                user,
                role_id=bound_role_id,
                **safe_args,
            )
        if name == "compare_applications":
            return handlers.compare_role_applications(
                db, user, role_id=bound_role_id, **safe_args
            )
        return _dispatch_bound_candidate_identity_read(
            name,
            safe_args,
            db=db,
            user=user,
            role_id=bound_role_id,
        )
    handler = _HANDLER_BY_NAME.get(name)
    if handler is None:
        raise KeyError(f"no Taali Chat handler registered for: {name}")
    safe_args = spec.validate(arguments)
    if name == "preview_related_role":
        return handler(db, user=user, **safe_args)
    if name == "create_related_role":
        return handler(db, user=user, conversation=conversation, **safe_args)
    return handler(db, user, **safe_args)


def _dispatch_bound_candidate_identity_read(
    name: str,
    arguments: dict[str, Any],
    *,
    db: Session,
    user: User,
    role_id: int,
) -> dict[str, Any]:
    """Authorize identity/CV compatibility reads against one logical role."""

    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(user.organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if role is None:
        raise ValueError(f"role {role_id} not found")
    context = authorize_logical_role_candidate(
        db,
        role=role,
        candidate_id=int(arguments["candidate_id"]),
    )
    if name == "get_candidate":
        return candidate_detail(
            context.candidate,
            applications=[context.presented_application],
        )
    return handlers.get_candidate_cv(
        db,
        user,
        candidate_id=int(context.candidate_id),
    )


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
