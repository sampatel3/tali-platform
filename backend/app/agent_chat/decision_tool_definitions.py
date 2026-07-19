"""Decision and application Agent Chat tool contracts."""

from __future__ import annotations

from typing import Any

from ..models.decision_feedback import (
    ATTRIBUTED_TO_VALUES,
    FAILURE_MODES,
    FEEDBACK_DIRECTIONS,
    FEEDBACK_SCOPES,
)

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
            "Add an internal recruiter note to ONE application on THIS role. It never "
            "writes to the ATS. Set for_agent=true (default) to make it standing context "
            "for future agent cycles. Immediate; only call for an explicit recruiter note."
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
        "name": "post_workable_note",
        "description": (
            "Queue a note to ONE linked candidate's Workable activity feed. The first "
            "call checks linkage/writeback readiness and previews the exact body; only a "
            "later explicit recruiter confirmation may enqueue the serialized provider "
            "write. Never claim it posted until the background job completes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
                "body": {"type": "string", "minLength": 1, "maxLength": 8000},
            },
            "required": ["application_id", "body"],
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
