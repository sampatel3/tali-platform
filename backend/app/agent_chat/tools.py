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

import hashlib
import json
import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ..models.candidate_application import CandidateApplication
from ..models.agent_decision import AgentDecision
from ..models.decision_feedback import (
    ATTRIBUTED_TO_VALUES,
    FAILURE_MODES,
    FEEDBACK_DIRECTIONS,
    FEEDBACK_SCOPES,
)
from ..models.org_criterion import BUCKET_MUST, CRITERION_BUCKETS
from ..models.organization import Organization
from ..models.role import Role
from ..models.role_criterion import CRITERION_SOURCE_DERIVED
from ..services import related_role_service as _related_roles
from ..services.role_change_audit import (
    ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED,
    ROLE_CHANGE_ACTION_UPDATED,
    add_role_change_event,
    capture_role_change_snapshot,
    latest_role_change_actor,
)
from ..services.role_concurrency import assert_role_version, bump_role_version
from ..services.requisition_template_service import resolve_template
from ..services.sister_role_service import text_fingerprint
from . import application_commands as _application_commands
from . import assessments as _assessments
from . import constraints as _constraints
from . import controls as _controls
from . import decision_commands as _decision_commands
from . import decision_teach as _decision_teach
from . import draft_tasks as _draft_tasks
from . import health as _health
from . import impact as _impact
from . import proactive as _proactive
from . import recruiter_inputs as _recruiter_inputs
from . import rescore as _rescore
from . import run_history as _run_history
from .confirmations import (
    attach_confirmation,
    blocked_confirmation_result,
    mark_confirmation_consumed,
    require_later_turn_confirmation,
)


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
}
MUTATION_TOOL_NAMES = frozenset(_MUTATION_PERMISSIONS)


def _locked_authorized_role(
    db: Session,
    *,
    role: Role,
    user: Any,
    permission: JobPermission,
    expected_role_version: int | None = None,
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


_DECISION_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_pending_decisions",
        "description": (
            "List the live pending decision cards for THIS role, with candidate, "
            "reasoning, staleness, supported alternatives and exact decision ids. "
            "Snoozed items are hidden unless include_snoozed=true. Call this before "
            "taking a decision action; never guess an id or alternative."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "include_snoozed": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            },
            "required": [],
        },
    },
    {
        "name": "approve_decision",
        "description": (
            "Approve ONE pending agent recommendation on THIS role. This can send an "
            "assessment, reject a candidate, resend an invite, or advance them, so the "
            "first call only creates an exact server preview. Execute only after the "
            "recruiter explicitly confirms in a later message. Workable advances require "
            "workable_target_stage. Stale decisions must be re-evaluated first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "integer"},
                "note": {"type": ["string", "null"], "maxLength": 2000},
                "workable_target_stage": {"type": ["string", "null"], "maxLength": 200},
            },
            "required": ["decision_id"],
        },
    },
    {
        "name": "override_decision",
        "description": (
            "Reject ONE pending recommendation and take a supported alternative on THIS "
            "role. Use the exact alternative returned by list_pending_decisions and give "
            "a brief recruiter reason. The first call creates a bound preview; execute "
            "only after explicit confirmation in a later message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "integer"},
                "alternative": {
                    "type": "string",
                    "enum": ["reject", "advance", "send_assessment", "skip_assessment_advance"],
                },
                "note": {"type": "string", "minLength": 1, "maxLength": 2000},
                "workable_target_stage": {"type": ["string", "null"], "maxLength": 200},
            },
            "required": ["decision_id", "alternative", "note"],
        },
    },
    {
        "name": "snooze_decision",
        "description": (
            "Hide ONE live pending decision for the canonical one-hour snooze window. "
            "Low-risk and immediate, but only call when the recruiter explicitly asks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"decision_id": {"type": "integer"}},
            "required": ["decision_id"],
        },
    },
    {
        "name": "re_evaluate_decision",
        "description": (
            "Refresh ONE pending decision against current inputs. It may queue a paid CV "
            "rescore or a focused agent cycle, so the first call creates a server preview "
            "and execution requires explicit confirmation in a later recruiter message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"decision_id": {"type": "integer"}},
            "required": ["decision_id"],
        },
    },
    {
        "name": "teach_decision",
        "description": (
            "Send ONE pending decision back with structured recruiter feedback so the "
            "agent can correct the decision and, for role/org scope, learn from it. The "
            "first call creates an exact preview and requires explicit confirmation in "
            "a later message. Org-scope feedback remains inert until an admin co-signs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "integer"},
                "failure_mode": {"type": "string", "enum": list(FAILURE_MODES)},
                "correction_text": {"type": "string", "minLength": 1, "maxLength": 8000},
                "scope": {"type": "string", "enum": list(FEEDBACK_SCOPES)},
                "attributed_to": {
                    "type": ["string", "null"],
                    "enum": [*ATTRIBUTED_TO_VALUES, None],
                },
                "direction": {
                    "type": ["string", "null"],
                    "enum": [*FEEDBACK_DIRECTIONS, None],
                },
            },
            "required": ["decision_id", "failure_mode", "correction_text", "scope"],
        },
    },
]


_APPLICATION_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "create_application",
        "description": (
            "Create ONE candidate application on THIS role by email, reusing an existing "
            "organization candidate when present. The first call is a no-write preview "
            "showing dedup/profile effects; execute only after explicit confirmation in "
            "a later recruiter message. Never use an email inferred from CV prose."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate_email": {"type": "string", "format": "email"},
                "candidate_name": {"type": ["string", "null"]},
                "candidate_position": {"type": ["string", "null"]},
                "notes": {"type": ["string", "null"]},
            },
            "required": ["candidate_email"],
        },
    },
    {
        "name": "add_internal_note",
        "description": (
            "Add an internal recruiter note to ONE application on THIS role. It stays "
            "in Taali and is never sent to Workable, Bullhorn, or the candidate. Set "
            "for_agent=true (default) to make it standing context for future agent "
            "cycles. Immediate; only call for an explicit recruiter note."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
                "note": {"type": "string", "minLength": 1},
                "for_agent": {"type": "boolean", "default": True},
            },
            "required": ["application_id", "note"],
        },
    },
    {
        "name": "run_agent_now",
        "description": (
            "Run a one-shot autonomous cycle now for THIS role, optionally focused on "
            "one role-scoped application. It can spend credits and emit decisions, so "
            "the first call only previews scope/state; enqueue only after an explicit "
            "confirmation in a later recruiter message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_id": {"type": ["integer", "null"]},
            },
            "required": [],
        },
    },
]


AGENT_CHAT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_role_overview",
        "description": (
            "Snapshot of THIS role for grounding before you act: agent on/off + "
            "pause state + monthly budget, the effective score threshold (role "
            "override / org default / mode), the recruiter constraint chips "
            "(salary caps, must-haves), the pipeline funnel counts, and how many "
            "decisions are pending. ALWAYS call this first so your numbers are real."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_candidates",
        "description": (
            "List candidates on this role by bucket, best score first. bucket: "
            "'above'/'below' (the effective threshold), 'advanced', 'rejected', "
            "'pending' (awaiting a decision), or 'all' (open apps). Each candidate "
            "returns name, pre-screen score, Taali pipeline stage, and a normalized "
            "`ats_context` for native, Workable, or Bullhorn. It also returns the "
            "synced `workable_stage` for Workable roles. Pass "
            "`workable_stage` to filter to a specific Workable stage — that's how you "
            "answer 'who's in final interview?' (Taali's pipeline_stage does NOT track "
            "Workable's interview stages; workable_stage is the source of truth). You can "
            "ALSO see the recruiter's **Workable comments / ratings** on each candidate: "
            "set `include_comments=true` to return them ([{author, created_at, body}], "
            "newest first), and `comment_contains` to filter to candidates a recruiter "
            "commented on (e.g. comment_contains='yes'). So 'top 5 in technical interview "
            "with a Yes comment' = workable_stage='technical interview', "
            "comment_contains='yes', limit=5."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bucket": {
                    "type": "string",
                    "enum": ["above", "below", "advanced", "rejected", "pending", "all"],
                    "default": "all",
                },
                "workable_stage": {
                    "type": ["string", "null"],
                    "description": "Filter to candidates whose synced Workable stage contains this (case-insensitive), e.g. 'final interview'. Applies to the open buckets (not 'rejected').",
                },
                "comment_contains": {
                    "type": ["string", "null"],
                    "description": "Filter to candidates who have a synced Workable recruiter comment/rating whose text matches this — whole-word for a single word (comment_contains='yes' matches a 'Yes' comment but not 'yesterday'), substring for a phrase ('strong hire'). Implies include_comments. Open buckets only (not 'rejected').",
                },
                "include_comments": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include each candidate's synced Workable recruiter comments [{author, created_at, body}] (newest first, capped) in the result, so you can read/cite them. Set true even without a filter to show what recruiters wrote.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            },
            "required": [],
        },
    },
    {
        "name": "sync_workable_comments",
        "description": (
            "Force an immediate Workable sync for THIS role, pulling the latest "
            "recruiter comments / ratings (and stages) for all its candidates. Use "
            "when the recruiter says comments look stale or missing, or asks you to "
            "sync / refresh Workable comments. Comments normally sync automatically "
            "every few minutes; this forces a refresh now. It's ASYNCHRONOUS — tell "
            "the recruiter it's underway and to ask again in a moment so you can "
            "re-read the freshly-synced comments with list_candidates."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "simulate_threshold",
        "description": (
            "Project the effect of moving the score threshold to a value WITHOUT "
            "committing. Returns: candidates above/below now vs at the new cutoff, "
            "how many pending advances would be retracted, how many new rejects "
            "would be carded, and who is newly cleared. Use to answer 'what happens "
            "if I drop the threshold to 65?' before doing it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "The 0-100 cutoff to simulate.",
                }
            },
            "required": ["threshold"],
        },
    },
    {
        "name": "recommend_threshold",
        "description": (
            "Recommend a score threshold. Pass target_additional to find a cutoff "
            "that clears ~N more candidates than today, or target_total for ~N "
            "total, or neither to get a sensible 'loosen it a little' suggestion. "
            "Returns the recommended cutoff, projected counts, and who it adds. Use "
            "when the recruiter wants more volume ('how do I let a few more through?')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_additional": {
                    "type": ["integer", "null"],
                    "minimum": 0,
                    "description": "Clear this many MORE than the current cutoff.",
                },
                "target_total": {
                    "type": ["integer", "null"],
                    "minimum": 0,
                    "description": "Clear ~this many total.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "set_threshold",
        "description": (
            "COMMIT a new score threshold for this role and reconcile the decision "
            "queue: retract pending advances now below the cutoff and card new "
            "rejects. Instant, no re-scoring. Pass null to clear back to the org "
            "default. Prefer simulate_threshold first and only commit when the "
            "recruiter has confirmed the direction or asked for it explicitly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": ["number", "null"],
                    "minimum": 0,
                    "maximum": 100,
                    "description": "New 0-100 cutoff, or null to clear to org default.",
                }
            },
            "required": ["threshold"],
        },
    },
    {
        "name": "add_or_update_constraint",
        "description": (
            "Add a recruiter constraint chip to this role, or edit an existing one "
            "by criterion_id. Use for salary caps, location, work authorisation, "
            "must-have skills — anything evaluated from the CV. bucket 'constraint' "
            "is a hard filter, 'must' a must-have, 'preferred' a nice-to-have. This "
            "changes the screening policy immediately but NEVER starts a paid re-screen. "
            "For hard/must changes the result contains an exact whole-pool preview and "
            "server confirmation receipt. Show the count/cost, then wait for a later "
            "recruiter confirmation before calling rescreen_role."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The constraint, e.g. 'Salary expectation <= 25,000'.",
                },
                "bucket": {
                    "type": "string",
                    "enum": ["constraint", "must", "preferred"],
                    "default": "constraint",
                },
                "criterion_id": {
                    "type": ["integer", "null"],
                    "description": "Edit an existing chip instead of adding a new one.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "remove_constraint",
        "description": (
            "Remove a recruiter constraint chip by criterion_id (get ids from "
            "get_role_overview). Removal is immediate. A removed must-have/constraint "
            "returns a paid re-screen preview but does not start the re-screen; wait for "
            "the recruiter's confirmation in a later message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"criterion_id": {"type": "integer"}},
            "required": ["criterion_id"],
        },
    },
    {
        "name": "update_job_spec",
        "description": (
            "Replace THIS role's job description with a new one the recruiter "
            "pasted, and re-derive its must-have / preferred / constraint criteria "
            "from it. Use when the recruiter sends a new or updated JD in chat. A new "
            "JD re-derives EVERY criterion (the biggest change there is), so it does "
            "NOT re-screen automatically: it applies the spec + re-derives the chips "
            "instantly (no LLM) and returns the criteria diff + a re-screen cost "
            "estimate. Recruiter-added chips (salary caps, etc.) are kept. Show what "
            "changed + the cost and ASK before running rescreen_role."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_spec_text": {"type": "string", "description": "The full new job description text the recruiter pasted."},
            },
            "required": ["job_spec_text"],
        },
    },
    {
        "name": "start_related_role_draft",
        "description": (
            "Start the existing conversational job-creation flow as a NEW related-role "
            "draft cloned from this ATS role. Its full specification, structured fields, "
            "and labelled responsibilities are copied; intake reads that source and asks "
            "only for genuine gaps before the recruiter describes just the differences. "
            "Use this for open-ended cousin/sister-role requests or whenever the recruiter "
            "wants to refine the new role conversationally. This creates only a draft and "
            "does not spend scoring usage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": ["string", "null"],
                    "description": "Optional proposed name; defaults to '<original> · Related'.",
                },
                "job_spec_text": {
                    "type": ["string", "null"],
                    "description": "Optional complete revised spec. Omit to clone the original verbatim and refine it in chat.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "preview_related_role",
        "description": (
            "Preview creating a NEW related Taali role over this ATS role's "
            "existing candidate pool, using a complete cousin/alternate job spec. "
            "Returns the shared roster size, scorable count, and estimated AI "
            "usage without creating anything. Use this instead of update_job_spec "
            "when the recruiter wants a separate role/view while preserving this "
            "original role. Always show the preview and wait for a later explicit "
            "confirmation before calling create_related_role."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name for the new related role.",
                },
                "job_spec_text": {
                    "type": "string",
                    "description": "The complete updated/cousin job specification, not only the differences.",
                },
            },
            "required": ["name", "job_spec_text"],
        },
    },
    {
        "name": "create_related_role",
        "description": (
            "Create the related role and queue fresh scores for the shared roster. "
            "It is a full Taali role with independent scoring, assessments, Agent policy, and budget. "
            "Rejection and advancement affect every linked role because they share one ATS application. "
            "This paid mutation requires a preview and explicit confirmation in a later message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "job_spec_text": {"type": "string"},
                "confirmation_token": {
                    "type": ["string", "null"],
                    "description": "Opaque token from the preview, when available.",
                },
            },
            "required": ["name", "job_spec_text"],
        },
    },
    {
        "name": "set_agent_state",
        "description": (
            "Activate (turn on / resume) or pause this role's agent. Use when the "
            "recruiter asks to start, restart, resume, re-enable, or pause the agent. "
            "First activation preserves the role's action-level automation policy "
            "and persists one durable "
            "command that generates, battle-tests and approves the assessment before "
            "turning the role on; it never needs a second draft-approval click. The "
            "role stays honestly off while that work or production readiness retries. "
            "Activating needs a monthly budget. A manual pause never clears until an "
            "explicit resume."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["activate", "pause"],
                    "description": "'activate' to turn on / resume, 'pause' to pause.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "adjust_agent_settings",
        "description": (
            "Adjust this role agent's settings. Only set the fields the recruiter "
            "asks to change. monthly_budget_cents = monthly spend cap in cents "
            "(e.g. 5000 = $50/mo). auto_reject_pre_screen controls the cheap "
            "deterministic screening gate; auto_reject independently controls "
            "deterministic full CV/role-fit rejects. Assessment-stage and LLM-only "
            "rejects require confirmation. auto_send_assessment, "
            "auto_resend_assessment, and auto_advance independently control "
            "the reversible positive actions. auto_promote remains a legacy "
            "aggregate alias for clients that have not set granular choices. "
            "auto_skip_assessment = bypass the assessment stage entirely; strong "
            "candidates queue as advance-to-interview decisions instead of "
            "receiving an assessment invite. Raising the "
            "budget can resume an automatic budget/credit hold after readiness "
            "passes, but never clears a recruiter-authored manual pause."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "monthly_budget_cents": {"type": ["integer", "null"]},
                "auto_reject": {"type": ["boolean", "null"]},
                "auto_reject_pre_screen": {"type": ["boolean", "null"]},
                "auto_promote": {"type": ["boolean", "null"]},
                "auto_send_assessment": {"type": ["boolean", "null"]},
                "auto_resend_assessment": {"type": ["boolean", "null"]},
                "auto_advance": {"type": ["boolean", "null"]},
                "auto_skip_assessment": {"type": ["boolean", "null"]},
            },
        },
    },
    {
        "name": "rescreen_role",
        "description": (
            "Run the re-screen the recruiter opted into AFTER a constraint change. "
            "A constraint/must-have edit applies immediately but does NOT re-screen "
            "automatically — re-screening re-scores the pool and costs money. First "
            "report the `would_rescreen` count + `est_cost_usd` from the "
            "constraint_change result and ask the recruiter to confirm; call this "
            "ONLY once they explicitly say yes."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "rescore_candidates",
        "description": (
            "Re-score this role's OLD-engine (v1.x) candidates with the current "
            "holistic v2.1.0 engine. Use when the recruiter wants stale scores "
            "refreshed — and let THEM steer the scope; never assume 'all'. ALWAYS "
            "call once WITHOUT confirm first (confirm=false) to preview the matched "
            "count + $ cost, show the recruiter, and call again with confirm=true "
            "ONLY after they explicitly say yes. Scope the subset: 'all', 'top_n' "
            "(highest current scores, set `limit`), 'above_threshold' / "
            "'below_threshold' (set `threshold` 0-100), or 'none'. Re-scoring spends "
            "~$0.083/candidate, so the preview-then-confirm step is mandatory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["all", "top_n", "above_threshold", "below_threshold", "none"],
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "For scope=top_n: how many of the highest-scoring stale candidates.",
                },
                "threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "For scope=above_threshold/below_threshold: the current-score cutoff.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "false = preview count+cost only (default); true = actually re-score (recruiter must have confirmed).",
                },
            },
            "required": ["scope"],
        },
    },
    {
        "name": "get_criterion_breakdown",
        "description": (
            "For ONE criterion (criterion_id from get_role_overview), how the scored "
            "candidates currently split — met / missing / unknown / not_assessed — "
            "WITH each one's stored reasoning. Read-only and free (reuses scores we "
            "already have). Use this to reason about a criteria change before "
            "spending: a widening only re-checks the previously-missing, a narrowing "
            "only the previously-met; a typo/cosmetic reword is a no-op."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"criterion_id": {"type": "integer"}},
            "required": ["criterion_id"],
        },
    },
    {
        "name": "rescreen_scoped",
        "description": (
            "Re-screen ONLY the candidates affected by a criteria change — far cheaper "
            "than the whole pool. Pass criterion_id + which stored status-group to "
            "re-check: a WIDENING re-checks ['missing'], a NARROWING ['met']. The "
            "re-screen re-judges each correctly (Saudi flips to met, India stays "
            "missing). Confirm the scope + cost with the recruiter before calling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "criterion_id": {"type": "integer"},
                "statuses": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["met", "missing", "unknown"]},
                },
            },
            "required": ["criterion_id", "statuses"],
        },
    },
    {
        "name": "search_candidates",
        "description": (
            "Exhaustive/deterministic natural-language retrieval over this role's "
            "candidate pool. Reserve it for explicit all/every requests and pool "
            "scoping, e.g. 'all candidates based in MENA' or 'every candidate with a "
            "stated salary'. Report database/returned/verification coverage honestly; "
            "never imply unchecked qualitative matches passed or failed. For bounded "
            "qualitative discovery, use find_top_candidates instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "find_top_candidates",
        "description": (
            "Default for BOUNDED qualitative candidate discovery on THIS role, even "
            "without 'top'/'best' wording (e.g. 'who has banking experience?', 'show "
            "people who've led a team'). Uses the requested limit or defaults to 10, "
            "treats unhedged qualities as required and only explicit preferences as optional, then returns grounded required matches with per-criterion cited CV "
            "evidence plus grounding coverage and degradation warnings. Renders an "
            "evidence card with a secure 30-day read-only report link. Cite only "
            "available evidence; never brand unchecked results grounded. For a bare "
            "'top 10 report', pass query='candidates' and limit=10; the role's stored "
            "scorecard evidence is reused when available. Make every query self-contained across follow-ups and surface criteria_unchecked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
                "rank_by": {
                    "type": "string",
                    "enum": [
                        "taali",
                        "pre_screen",
                        "rank",
                        "cv_match",
                        "workable",
                        "assessment",
                        "role_fit",
                    ],
                    "default": "taali",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_draft_tasks",
        "description": (
            "List auto-generated assessment-task drafts on THIS role. If the result "
            "has automatic_activation=true, the saved Turn-on command is validating "
            "and approving the draft: report progress and NEVER ask for a second "
            "approval or revision click. Otherwise this is an optional manual review "
            "card the recruiter can approve or reject-with-feedback when they ask "
            "about tasks/assessments."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "role_health_check",
        "description": (
            "FREE, read-only scan of what's most likely HURTING this role's "
            "decisions, ranked, so you can proactively steer the recruiter. "
            "Surfaces: a must-have almost nobody meets (killing the pool), a "
            "requirement you often can't verify from the CV, a requirement "
            "everyone meets (no signal), a score cut-off set too strict / too "
            "loose, a PATTERN of the recruiter overriding your decisions in one "
            "direction (you're mis-calibrated — the strongest signal), stale "
            "old-engine scores, and a decision backlog. Returns `findings` "
            "(ranked) + `top_finding` + `all_clear`. RUN IT when a conversation "
            "opens fresh, when the recruiter asks an open-ended 'how's this role "
            "/ what should I change / review this', or after they resolve a "
            "batch. Then LEAD with `top_finding` phrased as a question + the "
            "concrete fix you can make; one finding at a time, never a wall. "
            "Each finding carries the handles (criterion_id, threshold) to act. "
            "If `all_clear`, say the role looks healthy in a line — don't invent "
            "problems."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
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
    }
)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def _role_overview(db: Session, role: Role) -> dict[str, Any]:
    rows = _impact.load_open_candidates(db, role)
    effective = _impact.effective_threshold(db, role)
    above, below = _impact.split_by_threshold(rows, effective)

    # Full funnel: counts by pipeline_stage across all non-deleted apps.
    stage_rows = (
        db.query(CandidateApplication.pipeline_stage, func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.deleted_at.is_(None),
        )
        .group_by(CandidateApplication.pipeline_stage)
        .all()
    )
    funnel = {str(stage or "unknown"): int(n) for stage, n in stage_rows}

    # Provider-neutral external-stage breakdown of the open pool.  Keep the
    # Workable-specific field for backwards compatibility with the existing UI.
    workable_funnel: dict[str, int] = {}
    ats_stage_funnel: dict[str, int] = {}
    for r in rows:
        key = r.workable_stage or "(unsynced)"
        workable_funnel[key] = workable_funnel.get(key, 0) + 1
        provider = "bullhorn" if r.bullhorn_status else "workable" if r.workable_stage else "native"
        raw_stage = r.bullhorn_status or r.workable_stage or r.pipeline_stage or "(unsynced)"
        ats_key = f"{provider}:{raw_stage}"
        ats_stage_funnel[ats_key] = ats_stage_funnel.get(ats_key, 0) + 1

    pending_rows = (
        db.query(AgentDecision.decision_type, func.count(AgentDecision.id))
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
        )
        .group_by(AgentDecision.decision_type)
        .all()
    )
    pending_by_type = {str(t): int(n) for t, n in pending_rows}

    # Budget event cards point recruiters back to this read-only overview. Keep
    # the spend lookup isolated so a metering/query problem cannot make the
    # whole role snapshot unavailable. Report the effective cap as well as the
    # raw override because unset roles still use the platform default cap.
    effective_monthly_budget_cents: int | None = None
    month_to_date_spend_cents: int | None = None
    remaining_monthly_budget_cents: int | None = None
    try:
        from ..agent_runtime import budget_guard

        effective_monthly_budget_cents = budget_guard.role_monthly_usd_cents(role)
        month_to_date_spend_cents = max(
            0, int(budget_guard.month_to_date_spend_cents(db, role=role))
        )
        if effective_monthly_budget_cents > 0:
            remaining_monthly_budget_cents = max(
                0, effective_monthly_budget_cents - month_to_date_spend_cents
            )
    except Exception:  # pragma: no cover - overview remains useful without usage data
        month_to_date_spend_cents = None
        remaining_monthly_budget_cents = None

    return {
        "role": {"id": int(role.id), "name": role.name},
        "agent": {
            "enabled": bool(role.agentic_mode_enabled),
            "paused": role.agent_paused_at is not None,
            "paused_reason": role.agent_paused_reason,
            "monthly_budget_cents": role.monthly_usd_budget_cents,
            "effective_monthly_budget_cents": effective_monthly_budget_cents,
            "month_to_date_spend_cents": month_to_date_spend_cents,
            "remaining_monthly_budget_cents": remaining_monthly_budget_cents,
            "auto_reject": bool(role.auto_reject),
            "auto_reject_pre_screen": bool(role.auto_reject_pre_screen),
            "auto_promote": bool(role.auto_promote),
            "auto_send_assessment": getattr(role, "auto_send_assessment", None),
            "auto_resend_assessment": getattr(role, "auto_resend_assessment", None),
            "auto_advance": getattr(role, "auto_advance", None),
            "auto_skip_assessment": bool(role.auto_skip_assessment),
        },
        "threshold": {
            "effective": effective,
            "role_override": role.score_threshold,
            "mode": role.auto_reject_threshold_mode or "manual",
        },
        "constraints": _constraints.list_constraints(role),
        "funnel": funnel,
        "workable_stage_funnel": workable_funnel,
        "ats_stage_funnel": ats_stage_funnel,
        "open_candidates": len(rows),
        "above_threshold": len(above),
        "below_threshold": len(below),
        "pending_decisions": sum(pending_by_type.values()),
        "pending_by_type": pending_by_type,
    }


def _comment_match(comments: list[dict[str, Any]] | None, term: str) -> bool:
    """True when any of the candidate's Workable comment bodies matches ``term``.

    A single word matches whole-word (so 'yes' hits a "Yes" comment but not
    "yesterday"); a phrase matches as a case-insensitive substring.
    """
    t = (term or "").strip().lower()
    if not t:
        return False
    bodies = [str((c or {}).get("body") or "") for c in (comments or [])]
    if " " not in t and t.isalnum():
        pat = re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE)
        return any(pat.search(b) for b in bodies)
    return any(t in b.lower() for b in bodies)


def _compact_comments(
    comments: list[dict[str, Any]] | None, *, max_items: int = 3, max_len: int = 240
) -> list[dict[str, Any]]:
    """Trim a candidate's comments for the tool result — a few, newest first,
    bodies bounded so a chatty Workable thread can't blow up the turn."""
    out: list[dict[str, Any]] = []
    for c in (comments or [])[:max_items]:
        body = str((c or {}).get("body") or "").strip()
        if len(body) > max_len:
            body = body[:max_len].rstrip() + "…"
        out.append({"author": (c or {}).get("author"), "created_at": (c or {}).get("created_at"), "body": body})
    return out


def _list_candidates(
    db: Session,
    role: Role,
    *,
    bucket: str,
    limit: int,
    workable_stage: str | None = None,
    comment_contains: str | None = None,
    include_comments: bool = False,
) -> dict[str, Any]:
    # Only pay for the comment JSON read when the recruiter is filtering or asking
    # to see comments; a comment filter implies returning the matched comments.
    cc = (comment_contains or "").strip()
    want_comments = bool(cc) or bool(include_comments)
    rows = _impact.load_open_candidates(db, role, with_comments=want_comments)
    effective = _impact.effective_threshold(db, role)
    above, below = _impact.split_by_threshold(rows, effective)

    if bucket == "above":
        chosen = above
    elif bucket == "below":
        chosen = below
    elif bucket == "advanced":
        chosen = [r for r in rows if r.pipeline_stage == "advanced"]
    elif bucket == "rejected":
        # Rejected apps aren't "open" so they aren't in `rows`; query directly.
        return _list_resolved(db, role, outcome="rejected", limit=limit)
    elif bucket == "pending":
        chosen = [r for r in rows if r.has_pending_decision]
    else:
        chosen = rows

    # Optional filter by the SYNCED Workable stage (e.g. "Final Interview",
    # "Technical Interview"). Taali's pipeline_stage doesn't track Workable's
    # internal interview stages — `workable_stage` is the source of truth — so
    # this lets the agent answer "who's in final interview" directly.
    wk_filter = (workable_stage or "").strip().lower()
    if wk_filter:
        chosen = [r for r in chosen if wk_filter in (r.workable_stage or "").lower()]

    # Optional filter by a recruiter's synced Workable comment (e.g. a "Yes"
    # verdict). The data is always synced onto the candidate — this just lets the
    # agent reach it.
    if cc:
        chosen = [r for r in chosen if _comment_match(r.comments, cc)]

    chosen = sorted(chosen, key=lambda r: (r.score if r.score is not None else -1), reverse=True)
    from ..services.ats_context_service import application_ats_context

    apps_by_id = {
        int(app.id): app
        for app in db.query(CandidateApplication)
        .filter(CandidateApplication.id.in_([r.application_id for r in chosen[: int(limit)]] or [-1]))
        .all()
    }
    return {
        "bucket": bucket,
        "workable_stage_filter": workable_stage or None,
        "comment_filter": cc or None,
        "count": len(chosen),
        "effective_threshold": effective,
        "candidates": [
            {
                "application_id": r.application_id,
                "name": r.candidate_name,
                "score": r.score,
                "stage": r.pipeline_stage,
                "workable_stage": r.workable_stage,
                "bullhorn_status": r.bullhorn_status,
                "ats_context": application_ats_context(apps_by_id[r.application_id]),
                "pending_decision": r.pending_decision_type,
                **({"comments": _compact_comments(r.comments)} if want_comments else {}),
            }
            for r in chosen[: int(limit)]
        ],
    }


def _list_resolved(db: Session, role: Role, *, outcome: str, limit: int) -> dict[str, Any]:
    from ..models.candidate import Candidate

    rows = (
        db.query(CandidateApplication, Candidate.full_name)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.application_outcome == outcome,
            CandidateApplication.deleted_at.is_(None),
        )
        .limit(int(limit))
        .all()
    )
    return {
        "bucket": outcome,
        "count": len(rows),
        "candidates": [
            {
                "application_id": int(app.id),
                "name": (name or "Unnamed candidate"),
                "score": float(app.pre_screen_score_100)
                if app.pre_screen_score_100 is not None
                else None,
                "stage": app.pipeline_stage,
            }
            for app, name in rows
        ],
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _confirmation_binding(*, role: Role, user: Any, conversation: Any) -> dict[str, int]:
    """Authorization boundary persisted into every server preview receipt."""
    binding = {"organization_id": int(role.organization_id)}
    # Production Agent Chat is authenticated. Keeping read/helper dispatches
    # compatible with a missing synthetic user lets lower-level tests and
    # offline evaluators exercise read tools without manufacturing an actor;
    # command services themselves still enforce the real actor boundary.
    user_id = getattr(user, "id", None)
    if user_id is not None:
        binding["requested_by_user_id"] = int(user_id)
    if conversation is not None:
        binding["conversation_id"] = int(conversation.id)
    return binding


def _decision_fingerprint(snapshot: dict[str, Any]) -> str:
    """Stable state proof for a decision-action preview."""
    keys = (
        "decision_id",
        "application_id",
        "decision_type",
        "recommendation", "role_family",
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
    action: str, args: dict[str, Any]
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
            _decision_teach.normalize_teach_payload(
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
) -> dict[str, Any]:
    """Preview, bind, re-check, then execute one high-impact decision action."""
    normalized = _normalized_decision_action_args(action, args)
    decision_id = int(normalized["decision_id"])
    snapshot = (
        _decision_teach.get_teachable_decision(db, role, user, decision_id)
        if action == "teach_decision"
        else _decision_commands.get_pending_decision(db, role, user, decision_id)
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

    operation = f"{action}:{decision_id}"
    if conversation is None:
        return _decision_preview(
            action=action,
            snapshot=snapshot,
            normalized_args=normalized,
            binding={**binding, "role_id": int(role.id)},
        )
    check = require_later_turn_confirmation(
        db,
        conversation=conversation,
        operation=operation,
        token=str(args.get("confirmation_token") or "") or None,
        user=user,
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

    if action == "approve_decision":
        result = _decision_commands.approve_decision(
            db, role, user, **normalized
        )
        message = f"Decision {decision_id} was accepted for processing."
    elif action == "override_decision":
        result = _decision_commands.override_decision(
            db, role, user, **normalized
        )
        message = f"Decision {decision_id} override was accepted for processing."
    elif action == "teach_decision":
        result = _decision_commands.teach_decision(db, role, user, **normalized)
        if result.get("cosign_required"):
            message = (
                f"Feedback for decision {decision_id} was recorded and now requires "
                "an admin co-sign before organization-wide learning."
            )
        else:
            message = f"Decision {decision_id} was sent back with recruiter feedback."
    else:
        result = _decision_commands.re_evaluate_decision(
            db, role, user, decision_id=decision_id
        )
        message = f"Decision {decision_id} re-evaluation was queued."

    receipt = {
        "type": "operation_receipt",
        "operation": action,
        "status": result.get("status") or ("queued" if result.get("queued") else "accepted"),
        "message": message,
        "result": result,
        "_terminal_message": message,
    }
    return mark_confirmation_consumed(receipt, check=check)


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
) -> dict[str, Any]:
    if action == "create_application":
        return _application_commands.preview_create_application(
            db, role, user, **normalized
        )
    if action == "post_workable_note":
        return _application_commands.preview_workable_note(
            db, role, user, **normalized
        )
    return _application_commands.preview_manual_run(db, role, user, **normalized)


def _dispatch_confirmed_application_action(
    action: str,
    args: dict[str, Any],
    *,
    db: Session,
    role: Role,
    user: Any,
    conversation: Any,
    binding: dict[str, int],
) -> dict[str, Any]:
    normalized = _normalized_application_action_args(action, args)
    preview = _application_action_preview(
        action,
        db=db,
        role=role,
        user=user,
        normalized=normalized,
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
            "message": str(
                preview.get("blocked_reason")
                or "Standalone ATS notes are disabled; save this as an internal Taali note."
            ),
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

    if action == "create_application":
        result = _application_commands.create_application(
            db, role, user, **normalized
        )
        message = (
            f"Application {result['application_id']} was created for "
            f"{result['candidate_email']}."
        )
    elif action == "post_workable_note":
        result = _application_commands.queue_workable_note(
            db, role, user, **normalized
        )
        message = (
            f"The Workable note for application {result['application_id']} is queued."
        )
    else:
        result = _application_commands.enqueue_manual_run(
            db, role, user, **normalized
        )
        message = (
            "The focused agent run is queued."
            if normalized.get("application_id") is not None and result.get("queued")
            else "The role agent run is queued."
            if result.get("queued")
            else str(result.get("detail") or "The agent run was not queued.")
        )

    receipt = {
        "type": "operation_receipt",
        "operation": action,
        "status": result.get("status") or "accepted",
        "message": message,
        "result": result,
        "_terminal_message": message,
    }
    return mark_confirmation_consumed(receipt, check=check)


def _maybe_report_rescreen(db: Session, *, role: Role, conversation: Any, result: Any) -> None:
    """When a constraint edit kicked a re-screen, schedule the proactive
    "re-screen complete" impact message. Captures the qualified-pool baseline
    now (scores are still the old, visible values until the re-score lands).

    No-op without a conversation or when nothing was re-screened. In eager
    (test) execution the conversation isn't committed yet, so the task no-ops —
    the live path runs on the worker after the request commits (countdown)."""
    if conversation is None or not isinstance(result, dict):
        return
    if int(result.get("rescreening_count") or 0) <= 0:
        return
    rows = _impact.load_open_candidates(db, role)
    threshold = _impact.effective_threshold(db, role)
    above, _below = _impact.split_by_threshold(rows, threshold)
    try:
        from ..tasks.agent_chat_tasks import report_rescreen_impact

        report_rescreen_impact.apply_async(
            kwargs={
                "conversation_id": int(conversation.id),
                "role_id": int(role.id),
                "baseline_qualified": int(len(above)),
            },
            countdown=20,
        )
    except Exception:  # pragma: no cover — never fail the edit on dispatch
        import logging

        logging.getLogger("taali.agent_chat").exception(
            "failed to enqueue rescreen impact report for role_id=%s", role.id
        )


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
    """Run one tool against the conversation's role. Raises on unknown tool
    or bad arguments; the engine converts exceptions to a tool_result error."""
    args = arguments or {}
    permission = _MUTATION_PERMISSIONS.get(name)
    if permission is not None:
        role = _locked_authorized_role(
            db,
            role=role,
            user=user,
            permission=permission,
            expected_role_version=expected_role_version,
        )
    org_id = int(role.organization_id)
    confirmation_binding = _confirmation_binding(
        role=role, user=user, conversation=conversation
    )

    if name == "get_role_overview":
        return _role_overview(db, role)
    if name == "get_helper_briefing":
        return _proactive.build_helper_briefing(db, role)
    if name == "list_recent_agent_runs":
        return _run_history.list_recent_agent_runs(
            db,
            role,
            status=args.get("status"),
            trigger=args.get("trigger"),
            limit=int(args.get("limit") or 5),
        )
    if name == "list_pending_decisions":
        return _decision_commands.list_pending_decisions(
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
        )
    if name == "snooze_decision":
        decision_id = int(args["decision_id"])
        result = _decision_commands.snooze_decision(
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
    if name in {"create_application", "post_workable_note", "run_agent_now"}:
        return _dispatch_confirmed_application_action(
            name,
            args,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
            binding=confirmation_binding,
        )
    if name == "add_internal_note":
        application_id = int(args["application_id"])
        result = _application_commands.add_internal_note(
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
    if name == "list_candidates":
        return _list_candidates(
            db,
            role,
            bucket=str(args.get("bucket") or "all"),
            limit=int(args.get("limit") or 20),
            workable_stage=args.get("workable_stage"),
            comment_contains=args.get("comment_contains"),
            include_comments=bool(args.get("include_comments") or False),
        )
    if name == "simulate_threshold":
        return _impact.simulate_threshold(db, role, float(args["threshold"]))
    if name == "recommend_threshold":
        ta = args.get("target_additional")
        tt = args.get("target_total")
        return _impact.recommend_threshold(
            db,
            role,
            target_additional=int(ta) if ta is not None else None,
            target_total=int(tt) if tt is not None else None,
        )
    if name == "set_threshold":
        audit_before = capture_role_change_snapshot(role)
        audit_from = int(role.version or 1)
        raw = args.get("threshold")
        result = _impact.apply_threshold(
            db,
            role,
            float(raw) if raw is not None else None,
            organization_id=org_id,
        )
        _audit_role_mutation(
            db,
            role=role,
            before=audit_before,
            from_version=audit_from,
            actor_user_id=int(user.id),
            action=ROLE_CHANGE_ACTION_UPDATED,
        )
        return result
    if name == "add_or_update_constraint":
        cid = args.get("criterion_id")
        criterion_id = int(cid) if cid is not None else None
        requested_text = str(args.get("text") or "").strip()
        requested_bucket = str(args.get("bucket") or "constraint")
        existing = None
        if criterion_id is not None:
            existing = next(
                (
                    criterion
                    for criterion in (role.criteria or [])
                    if int(criterion.id) == criterion_id
                    and criterion.deleted_at is None
                    and criterion.source != CRITERION_SOURCE_DERIVED
                ),
                None,
            )
        if (
            existing is not None
            and bool(requested_text)
            and requested_bucket in CRITERION_BUCKETS
            and existing.text == requested_text
            and existing.bucket == requested_bucket
            and existing.must_have == (requested_bucket == BUCKET_MUST)
        ):
            # The model can repeat an identical tool call in adjacent rounds.
            # Preserve the response shape while consuming no Role revision and
            # creating no misleading related-table audit boundary.
            db.commit()
            return {
                "type": "constraint_change",
                "action": "updated",
                "criterion": {
                    "id": int(existing.id),
                    "text": existing.text,
                    "bucket": existing.bucket,
                },
                "invalidates_scores": False,
                "rescreening_count": 0,
            }
        audit_from = int(role.version or 1)
        result = _constraints.add_or_update_constraint(
            db,
            role,
            text=requested_text,
            bucket=requested_bucket,
            criterion_id=criterion_id,
            trigger_rescreen=False,  # P0: never auto-spend — the recruiter opts in
        )
        audit_before = capture_role_change_snapshot(role)
        changed_criterion_id = int(result["criterion"]["id"])
        _audit_role_mutation(
            db,
            role=role,
            before=audit_before,
            from_version=audit_from,
            actor_user_id=int(user.id),
            action="role_criteria_updated",
            reason=(
                f"agent chat criterion {result['action']}: "
                f"criterion_id={changed_criterion_id}"
            ),
            allow_empty_changes=True,
        )
        if result.get("invalidates_scores"):
            result["would_rescreen"] = _constraints.estimate_rescreen(db, role)
            estimate = result["would_rescreen"]
            result = attach_confirmation(
                result,
                operation="rescreen_role",
                payload={
                    **confirmation_binding,
                    "role_id": int(role.id),
                    "max_count": int(estimate.get("count") or 0),
                },
            )
        return result
    if name == "remove_constraint":
        audit_from = int(role.version or 1)
        result = _constraints.remove_constraint(
            db, role, int(args["criterion_id"]), trigger_rescreen=False
        )
        audit_before = capture_role_change_snapshot(role)
        removed_criterion_id = int(result["criterion"]["id"])
        _audit_role_mutation(
            db,
            role=role,
            before=audit_before,
            from_version=audit_from,
            actor_user_id=int(user.id),
            action="role_criteria_updated",
            reason=(
                "agent chat criterion removed: "
                f"criterion_id={removed_criterion_id}"
            ),
            allow_empty_changes=True,
        )
        if result.get("invalidates_scores"):
            result["would_rescreen"] = _constraints.estimate_rescreen(db, role)
            estimate = result["would_rescreen"]
            result = attach_confirmation(
                result,
                operation="rescreen_role",
                payload={
                    **confirmation_binding,
                    "role_id": int(role.id),
                    "max_count": int(estimate.get("count") or 0),
                },
            )
        return result
    if name == "update_job_spec":
        audit_before = capture_role_change_snapshot(role)
        audit_from = int(role.version or 1)
        result = _constraints.update_job_spec(
            db, role, job_spec_text=str(args.get("job_spec_text") or "")
        )
        if isinstance(result, dict) and bool(result.get("applied")):
            _audit_role_mutation(
                db,
                role=role,
                before=audit_before,
                from_version=audit_from,
                actor_user_id=int(user.id),
                action=ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED,
            )
        else:
            # Invalid/no-op input still ends the authorization lock before the
            # model is called again; no Role version is consumed.
            db.commit()
        if isinstance(result, dict) and result.get("would_rescreen"):
            estimate = result["would_rescreen"]
            result = attach_confirmation(
                result,
                operation="rescreen_role",
                payload={
                    **confirmation_binding,
                    "role_id": int(role.id),
                    "max_count": int(estimate.get("count") or 0),
                },
            )
        return result
    if name == "start_related_role_draft":
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
            name=(str(args.get("name") or "").strip() or None),
            job_spec_text=(
                str(args.get("job_spec_text") or "").strip()
                if args.get("job_spec_text") is not None
                else None
            ),
        )
        return _related_roles.related_role_draft_payload(brief)
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
        related, evaluation_counts = _related_roles.create_related_role(
            db,
            role_id=int(role.id),
            organization_id=org_id,
            creator_user_id=int(user.id),
            name=clean_name,
            job_spec_text=clean_spec,
        )
        result = _related_roles.related_role_created_payload(
            related, evaluation_counts
        )
        return mark_confirmation_consumed(result, check=check)
    if name == "rescreen_role":
        if conversation is not None:
            check = require_later_turn_confirmation(
                db,
                conversation=conversation,
                operation="rescreen_role",
                token=str(args.get("confirmation_token") or "") or None,
                user=user,
            )
            if not check.ok:
                return blocked_confirmation_result("rescreen_role", check.reason)
            if int(check.payload.get("role_id") or 0) != int(role.id):
                return blocked_confirmation_result("rescreen_role", "The preview belongs to a different role.")
            current = _constraints.estimate_rescreen(db, role)
            if int(current.get("count") or 0) > int(check.payload.get("max_count") or 0):
                return attach_confirmation(
                    {
                        "type": "rescreen_preview",
                        "would_rescreen": current,
                        "message": "The candidate count increased since approval; please confirm the updated scope.",
                    },
                    operation="rescreen_role",
                    payload={
                        **confirmation_binding,
                        "role_id": int(role.id),
                        "max_count": int(current.get("count") or 0),
                    },
                )
        result = _constraints.rescreen_role(db, role)
        _maybe_report_rescreen(db, role=role, conversation=conversation, result=result)
        if conversation is not None:
            result = mark_confirmation_consumed(result, check=check)
        return result
    if name == "rescore_candidates":
        confirm = bool(args.get("confirm") or False)
        common = {
            "scope": str(args.get("scope") or "all"),
            "limit": int(args["limit"]) if args.get("limit") is not None else 10,
            "threshold": float(args["threshold"]) if args.get("threshold") is not None else None,
        }
        if confirm and conversation is not None:
            check = require_later_turn_confirmation(
                db,
                conversation=conversation,
                operation="rescore_candidates",
                token=str(args.get("confirmation_token") or "") or None,
                user=user,
            )
            if not check.ok:
                return blocked_confirmation_result("rescore_candidates", check.reason)
            current = _rescore.rescore_candidates(db, role, confirm=False, **common)
            approved_scope = {
                "scope": check.payload.get("scope"),
                "limit": check.payload.get("limit"),
                "threshold": check.payload.get("threshold"),
            }
            if (
                int(check.payload.get("role_id") or 0) != int(role.id)
                or approved_scope != common
                or int(current.get("selected_count") or 0) > int(check.payload.get("max_count") or 0)
            ):
                return attach_confirmation(
                    current,
                    operation="rescore_candidates",
                    payload={
                        **confirmation_binding,
                        "role_id": int(role.id),
                        "max_count": int(current.get("selected_count") or 0),
                        **common,
                    },
                )
        result = _rescore.rescore_candidates(
            db,
            role,
            confirm=confirm,
            **common,
        )
        if confirm and conversation is not None:
            result = mark_confirmation_consumed(result, check=check)
        if not confirm and isinstance(result, dict) and result.get("type") == "rescore_preview":
            result = attach_confirmation(
                result,
                operation="rescore_candidates",
                payload={
                    **confirmation_binding,
                    "role_id": int(role.id),
                    "max_count": int(result.get("selected_count") or 0),
                    **common,
                },
            )
        return result
    if name == "get_criterion_breakdown":
        return _assessments.criterion_breakdown(db, role, int(args["criterion_id"]))
    if name == "rescreen_scoped":
        statuses = tuple(str(s) for s in (args.get("statuses") or []))
        affected = _assessments.affected_applications(
            db, role, int(args["criterion_id"]), statuses=statuses
        )
        ids = [a["application_id"] for a in affected]
        if not ids:
            return {"type": "rescreen_started", "rescreening_count": 0, "scoped": True}
        if conversation is not None:
            check = require_later_turn_confirmation(
                db,
                conversation=conversation,
                operation="rescreen_scoped",
                token=str(args.get("confirmation_token") or "") or None,
                user=user,
            )
            if not check.ok:
                return attach_confirmation(
                    {
                        "type": "rescreen_preview",
                        "criterion_id": int(args["criterion_id"]),
                        "statuses": list(statuses),
                        "selected_count": len(ids),
                        "est_cost_usd": round(len(ids) * 0.05, 2),
                        "message": check.reason,
                    },
                    operation="rescreen_scoped",
                    payload={
                        **confirmation_binding,
                        "role_id": int(role.id),
                        "criterion_id": int(args["criterion_id"]),
                        "statuses": list(statuses),
                        "max_count": len(ids),
                    },
                )
            approved_statuses = tuple(str(s) for s in (check.payload.get("statuses") or []))
            if (
                int(check.payload.get("role_id") or 0) != int(role.id)
                or int(check.payload.get("criterion_id") or 0) != int(args["criterion_id"])
                or approved_statuses != statuses
                or len(ids) > int(check.payload.get("max_count") or 0)
            ):
                return attach_confirmation(
                    {
                        "type": "rescreen_preview",
                        "selected_count": len(ids),
                        "est_cost_usd": round(len(ids) * 0.05, 2),
                        "message": "The scope increased since approval; please confirm the updated count.",
                    },
                    operation="rescreen_scoped",
                    payload={
                        **confirmation_binding,
                        "role_id": int(role.id),
                        "criterion_id": int(args["criterion_id"]),
                        "statuses": list(statuses),
                        "max_count": len(ids),
                    },
                )
        result = _constraints.rescreen_role(
            db, role, application_ids=ids,
            reason=f"agent_chat:scoped_rescreen:crit_{args['criterion_id']}",
        )
        _maybe_report_rescreen(db, role=role, conversation=conversation, result=result)
        if conversation is not None:
            result = mark_confirmation_consumed(result, check=check)
        return result
    if name == "search_candidates":
        # Reuse the Search page's candidate search (Graphiti/GraphRAG via the MCP
        # handlers). Lazy import keeps the graph deps out of the module load path;
        # graceful fallback when the vector layer isn't configured.
        try:
            from ..mcp import handlers as _mcp_handlers

            return _mcp_handlers.nl_search_candidates(
                db, user, query=str(args.get("query") or ""), role_id=int(role.id)
            )
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the turn
            return {"available": False, "error": f"search unavailable: {type(exc).__name__}"}
    if name == "find_top_candidates":
        # Evidence-aware bounded ranking for this role. Tagged as a card so
        # the engine lifts it into message.actions for the evidence-card UI;
        # the model narrates only evidence and coverage actually returned.
        from ..mcp import handlers as _mcp_handlers

        payload = _mcp_handlers.find_top_candidates(
            db,
            user,
            query=str(args.get("query") or ""),
            limit=int(args.get("limit") or 10),
            rank_by=str(args.get("rank_by") or "taali"),
            role_id=int(role.id),
        )
        return {"type": "candidate_evidence", **payload}
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

    raise KeyError(f"unknown tool: {name}")


__all__ = [
    "AGENT_CHAT_TOOLS",
    "CARD_TYPES",
    "MUTATING_TOOL_NAMES",
    "MUTATION_CARD_TYPES",
    "MUTATION_TOOL_NAMES",
    "dispatch_tool",
]
