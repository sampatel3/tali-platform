"""Anthropic tool catalogue + dispatcher for the role-agent chat.

These are the *action-taking* tools the conversational agent uses to read a
role's state, run impact analysis, and change constraints — distinct from
``taali_chat`` (read-only candidate search). Every tool is implicitly scoped
to the conversation's role; the engine injects the role, so no tool takes a
role_id.

Card-producing tools return a dict with a ``type`` in :data:`CARD_TYPES`; the
engine lifts those into ``AgentConversationMessage.actions`` so the UI renders
an impact card. Tools in :data:`MUTATION_CARD_TYPES` changed state and mark
the assistant turn as an ``action``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ..models.role import Role
from ..services.agent_control_ats_fence import fence_agent_chat_pause_tool
from ..services.role_change_audit import (
    add_role_change_event,
    capture_role_change_snapshot,
    latest_role_change_actor,
)
from ..services.role_concurrency import assert_role_version, bump_role_version
from . import application_commands as _application_commands
from . import assessments as _assessments
from . import decision_commands as _decision_commands
from . import decision_receipt_recovery as _decision_receipt_recovery
from . import decision_teach as _decision_teach
from . import proactive as _proactive
from . import recruiter_inputs as _recruiter_inputs
from . import run_history as _run_history
from .command_receipts import (
    complete_command,
)

from .application_tool_dispatch import dispatch_application_tool
from .candidate_tool_dispatch import dispatch_candidate_tool
from .control_tool_dispatch import dispatch_control_tool
from .decision_tool_definitions import (
    _APPLICATION_TOOL_DEFINITIONS,
    _DECISION_TOOL_DEFINITIONS,
)
from .decision_tool_dispatch import dispatch_decision_tool
from .read_tool_dispatch import _role_overview, dispatch_read_tool
from .related_role_tool_dispatch import dispatch_related_role_tool
from .rescreen_tool_dispatch import dispatch_rescreen_tool
from .role_config_tool_dispatch import dispatch_role_config_tool
from .role_tool_definitions import ROLE_TOOL_DEFINITIONS
from .runtime_tool_definitions import RUNTIME_TOOL_DEFINITIONS
from .tool_dispatch_common import ToolContext, UNHANDLED, _confirmation_binding


# Card payload ``type`` values the engine surfaces in message.actions.
CARD_TYPES = frozenset(
    {
        "threshold_simulation",
        "threshold_recommendation",
        "threshold_change",
        "constraint_change",
        "job_spec_change",
        "related_role_draft",
        "related_role_preview",
        "related_role_created",
        "draft_task_review",
        "candidate_evidence",
        "candidate_report",
        "decision_action_preview",
        "operation_preview",
        "operation_receipt",
        "helper_prompt",
    }
)
# Cards that represent a committed mutation (vs read-only analysis).
MUTATION_CARD_TYPES = frozenset(
    {
        "threshold_change",
        "constraint_change",
        "job_spec_change",
        "related_role_draft",
        "related_role_created",
        "operation_receipt",
        "candidate_report",
    }
)


# Permission is re-evaluated at the tool boundary, not only when the message is
# accepted.  Chat turns run asynchronously, so a recruiter can be removed from
# a configured hiring team while a model call is in flight.  Taking the shared
# Role lock here closes that time-of-check/time-of-use gap and serializes every
# chat mutation with the direct role/agent routes.
_MUTATION_PERMISSIONS: dict[str, JobPermission] = {
    "set_threshold": JobPermission.EDIT_ROLE,
    "add_or_update_constraint": JobPermission.EDIT_ROLE,
    "remove_constraint": JobPermission.EDIT_ROLE,
    "update_job_spec": JobPermission.EDIT_ROLE,
    "start_related_role_draft": JobPermission.EDIT_ROLE,
    "create_related_role": JobPermission.EDIT_ROLE,
    "rescreen_role": JobPermission.CONTROL_AGENT,
    "rescore_candidates": JobPermission.CONTROL_AGENT,
    "rescreen_scoped": JobPermission.CONTROL_AGENT,
    "set_agent_state": JobPermission.CONTROL_AGENT,
    "adjust_agent_settings": JobPermission.CONTROL_AGENT,
    "sync_workable_comments": JobPermission.CONTROL_AGENT,
    "answer_recruiter_input": JobPermission.CONTROL_AGENT,
    "dismiss_recruiter_input": JobPermission.CONTROL_AGENT,
    "approve_decision": JobPermission.CONTROL_AGENT,
    "override_decision": JobPermission.CONTROL_AGENT,
    "snooze_decision": JobPermission.CONTROL_AGENT,
    "re_evaluate_decision": JobPermission.CONTROL_AGENT,
    "teach_decision": JobPermission.CONTROL_AGENT,
    "create_application": JobPermission.CONTROL_AGENT,
    "add_internal_note": JobPermission.CONTROL_AGENT,
    "post_workable_note": JobPermission.CONTROL_AGENT,
    "run_agent_now": JobPermission.CONTROL_AGENT,
    "create_top_candidates_report": JobPermission.CONTROL_AGENT,
}
MUTATION_TOOL_NAMES = frozenset(_MUTATION_PERMISSIONS)


def _locked_authorized_role(
    db: Session,
    *,
    role: Role,
    user: Any,
    permission: JobPermission,
    expected_role_version: int | None = None,
    lock_for_update: bool = True,
) -> Role:
    """Return the current, locked role after the canonical per-job check."""

    # The engine intentionally catches tool authorization errors and continues
    # the conversation. Keep the check in a savepoint so a denied FOR UPDATE is
    # released immediately without rolling back the already-persisted tool-use
    # message in the outer chat transaction.
    with db.begin_nested():
        locked = require_job_permission(
            db,
            current_user=user,
            role_id=int(role.id),
            permission=permission,
            lock_for_update=lock_for_update,
        )
        # ``role`` was loaded before the (potentially slow) model call.
        # SQLAlchemy's identity map can otherwise return that stale Python
        # object. Refresh while the savepoint owns FOR UPDATE, then compare the
        # immutable turn cursor. A conflict rolls back the savepoint, releasing
        # the lock without discarding already-persisted chat tool plumbing.
        db.refresh(locked)
        if expected_role_version is not None:
            assert_role_version(
                locked,
                expected_version=int(expected_role_version),
                current_role=lambda: {
                    "id": int(locked.id),
                    "version": int(locked.version or 1),
                    "name": locked.name,
                    "agentic_mode_enabled": bool(locked.agentic_mode_enabled),
                    "monthly_usd_budget_cents": locked.monthly_usd_budget_cents,
                    "score_threshold": locked.score_threshold,
                },
                changed_by=lambda: latest_role_change_actor(
                    db,
                    organization_id=int(locked.organization_id),
                    role_id=int(locked.id),
                ),
            )
    return locked


def _audit_role_mutation(
    db: Session,
    *,
    role: Role,
    before: dict[str, Any],
    from_version: int,
    actor_user_id: int,
    action: str,
    reason: str = "agent chat",
    allow_empty_changes: bool = False,
) -> bool:
    """Version and audit a shared Role/configuration change atomically."""

    try:
        changed = capture_role_change_snapshot(role) != before
        if changed or allow_empty_changes:
            to_version = bump_role_version(role)
            add_role_change_event(
                db,
                role=role,
                before=before,
                action=action,
                actor_user_id=int(actor_user_id),
                from_version=int(from_version),
                to_version=int(to_version),
                reason=reason,
                allow_empty_changes=allow_empty_changes,
            )
        # End the locked write transaction before the next external model call.
        # The Role update, any derived/reconciled rows, its version, and audit
        # event commit together.
        db.commit()
        db.refresh(role)
        return changed
    except Exception:
        # The engine converts tool exceptions into a model-visible error and
        # keeps the turn alive. Explicit rollback is therefore essential: no
        # later conversation commit may persist a Role mutation without audit.
        db.rollback()
        raise


AGENT_CHAT_TOOLS: list[dict[str, Any]] = [
    *ROLE_TOOL_DEFINITIONS,
    *RUNTIME_TOOL_DEFINITIONS,
]

# Recruiter questions live in their own command module so the HTTP cards and
# typed chat share the same validation/write-back action without bloating this
# dispatcher further.
AGENT_CHAT_TOOLS.extend(_recruiter_inputs.RECRUITER_INPUT_TOOL_DEFINITIONS)
AGENT_CHAT_TOOLS.extend(_DECISION_TOOL_DEFINITIONS)
AGENT_CHAT_TOOLS.extend(_APPLICATION_TOOL_DEFINITIONS)
AGENT_CHAT_TOOLS.append(_proactive.HELPER_TOOL_DEFINITION)
AGENT_CHAT_TOOLS.append(_run_history.RUN_HISTORY_TOOL_DEFINITION)

# A model round may fan out reads, but it may request at most one stateful
# command.  This prevents ambiguous ordering (for example, changing a policy
# and approving a decision against its pre-change snapshot in the same batch).
MUTATING_TOOL_NAMES = frozenset(
    {
        "sync_workable_comments",
        "set_threshold",
        "add_or_update_constraint",
        "remove_constraint",
        "update_job_spec",
        "start_related_role_draft",
        "create_related_role",
        "set_agent_state",
        "adjust_agent_settings",
        "rescreen_role",
        "rescore_candidates",
        "rescreen_scoped",
        "answer_recruiter_input",
        "dismiss_recruiter_input",
        "approve_decision",
        "override_decision",
        "snooze_decision",
        "re_evaluate_decision",
        "teach_decision",
        "create_application",
        "add_internal_note",
        "post_workable_note",
        "run_agent_now",
        "create_top_candidates_report",
    }
)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def dispatch_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    db: Session,
    role: Role,
    user: Any,
    conversation: Any = None,
    expected_role_version: int | None = None,
) -> Any:
    """Authorize once, then route one tool to its focused implementation."""
    args = arguments or {}
    fence_agent_chat_pause_tool(db, role=role, user=user, tool_name=name, arguments=args)
    permission = _MUTATION_PERMISSIONS.get(name)
    if permission is not None:
        role = _locked_authorized_role(
            db,
            role=role,
            user=user,
            permission=permission,
            expected_role_version=expected_role_version,
            # Reject execution later re-checks permission while taking the
            # canonical owner-family lock. Keeping this preview check read-only
            # avoids a related-role -> owner lock inversion.
            lock_for_update=name not in {"approve_decision", "override_decision"},
        )
    context = ToolContext(
        arguments=args,
        db=db,
        role=role,
        user=user,
        conversation=conversation,
        organization_id=int(role.organization_id),
        confirmation_binding=_confirmation_binding(
            role=role, user=user, conversation=conversation
        ),
        expected_role_version=expected_role_version,
    )

    result = dispatch_read_tool(name, context)
    if result is not UNHANDLED:
        return result
    result = dispatch_decision_tool(
        name,
        context,
        decision_commands=_decision_commands,
        decision_teach=_decision_teach,
        decision_receipt_recovery=_decision_receipt_recovery,
        complete_command_fn=complete_command,
    )
    if result is not UNHANDLED:
        return result
    result = dispatch_application_tool(
        name,
        context,
        application_commands=_application_commands,
        complete_command_fn=complete_command,
    )
    if result is not UNHANDLED:
        return result
    for handler in (dispatch_control_tool, dispatch_candidate_tool):
        result = handler(name, context)
        if result is not UNHANDLED:
            return result
    result = dispatch_role_config_tool(
        name, context, audit_role_mutation=_audit_role_mutation
    )
    if result is not UNHANDLED:
        return result
    result = dispatch_related_role_tool(
        name, context, complete_command_fn=complete_command
    )
    if result is not UNHANDLED:
        return result
    result = dispatch_rescreen_tool(name, context, assessments=_assessments)
    if result is not UNHANDLED:
        return result
    raise KeyError(f"Unknown agent-chat tool: {name}")


__all__ = [
    "AGENT_CHAT_TOOLS",
    "CARD_TYPES",
    "MUTATING_TOOL_NAMES",
    "MUTATION_CARD_TYPES",
    "MUTATION_TOOL_NAMES",
    "_role_overview",
    "dispatch_tool",
]
