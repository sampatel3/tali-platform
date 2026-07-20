"""Pydantic contracts for the multi-agent recruiter system (§5.1).

These are the shared data shapes that flow between the orchestrator,
sub-agents, policy engine, recruiter UI, and Graphiti. They're separate
from the legacy SubAgentRequest/SubAgentResult dataclasses in
``sub_agents/base.py`` — those stay as the runtime contract that the
existing sub-agents implement. These v2 contracts wrap the runtime
results into a richer shape that carries uncertainty, graph citations,
exemplar references, and attribution.

The conversion from legacy ``SubAgentResult`` → v2 ``SubAgentScore``
happens in ``agent_runtime.score_envelope`` and is one-way: the legacy
shape is what the LLM-side code emits, the v2 shape is what the policy
fitter, exemplar stores, and Graphiti writers consume.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Identity / canonical enums (string literals because Pydantic Literal is
# how we get JSON-schema-correctness + runtime validation in one shot).
# ---------------------------------------------------------------------------


AGENT_NAMES = ("pre_screen", "cv_scoring", "assessment_scoring", "graph_priors")
AGENT_NAME_TYPE = Literal[
    "pre_screen", "cv_scoring", "assessment_scoring", "graph_priors"
]

ATTRIBUTED_TO_TYPE = Literal[
    "pre_screen",
    "cv_scoring",
    "assessment_scoring",
    "graph_priors",
    "policy_combination",
]

# Spec §6.5 names. We keep the wire-level names from the existing
# ``decision_feedback.scope`` column (decision/role/org) because the
# whole Hub is wired to them; this alias mapping makes the spec's intent
# legible without forking the column.
SCOPE_ALIASES = {
    "this_candidate": "decision",
    "this_role": "role",
    "all_similar": "org",
}

# Spec §6.3 outputs. ``do_nothing`` and ``escalate_low_confidence`` are
# new in the v2 policy; existing wire-level types stay alongside until
# the v2 policy is the only writer. See ``decision_policy.engine`` for
# the mapping.
RECOMMENDED_ACTION_TYPE = Literal[
    "advance_stage",
    "reject_application",
    "do_nothing",
    "escalate_low_confidence",
]


# ---------------------------------------------------------------------------
# Application / graph citation / exemplar reference
# ---------------------------------------------------------------------------


class Application(BaseModel):
    """Minimal application descriptor — full row is in ``candidate_applications``."""

    model_config = ConfigDict(extra="forbid")

    application_id: int
    candidate_id: int
    role_id: int
    submitted_at: datetime
    cv_doc_ref: str | None = None
    assessment_ref: str | None = None
    referrer_id: str | None = None


class GraphCitation(BaseModel):
    """One path/cluster of graph evidence cited by a sub-agent.

    ``node_ids`` and ``edge_ids`` are Graphiti UUIDs. ``summary`` is a
    one-line description the recruiter sees in the structured-evidence
    panel ("Worked at Stripe; 4 top-quartile hires for this role family
    also worked there").
    """

    model_config = ConfigDict(extra="forbid")

    node_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)
    summary: str


class ExemplarRef(BaseModel):
    """Pointer to an exemplar retrieved at score time.

    ``exemplar_id`` is the row id in ``agent_exemplars`` (per-agent
    table). ``similarity`` is the retrieval similarity (0..1) so the
    recruiter UI can sort.
    """

    model_config = ConfigDict(extra="forbid")

    exemplar_id: int
    similarity: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Sub-agent score (richer than the legacy ``SubAgentResult``)
# ---------------------------------------------------------------------------


class SubAgentScore(BaseModel):
    """v2 contract for one sub-agent's contribution to a decision.

    Compared to the legacy ``SubAgentResult``:
    - adds ``uncertainty`` (calibrated standard-error-like number)
    - adds ``citations`` (graph nodes/edges the agent leaned on)
    - adds ``exemplars_used`` (vector-retrieved past corrections)
    - keeps ``score`` on the canonical [0, 1] scale (legacy code used
      [0, 100] for some signals; the envelope converter normalises).
    """

    model_config = ConfigDict(extra="forbid")

    agent_name: AGENT_NAME_TYPE
    score: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0, le=1.0)
    structured_evidence: dict = Field(default_factory=dict)
    citations: list[GraphCitation] = Field(default_factory=list)
    exemplars_used: list[ExemplarRef] = Field(default_factory=list)
    model_version: str
    scored_at: datetime


class CandidateScores(BaseModel):
    """All four sub-agent scores for one application, keyed by agent_name."""

    model_config = ConfigDict(extra="forbid")

    application_id: int
    scores: dict[str, SubAgentScore] = Field(default_factory=dict)

    def score_for(self, agent: str) -> SubAgentScore | None:
        return self.scores.get(agent)


# ---------------------------------------------------------------------------
# Decision (the v2 policy emits this — wire-level type is mapped from
# ``recommended_action`` by the legacy adapter).
# ---------------------------------------------------------------------------


class Decision(BaseModel):
    """One policy verdict awaiting recruiter review.

    Idempotency key format ``run_id:app_id:type`` is preserved from the
    existing decision queue (see ``AgentDecision.idempotency_key``); the
    v2 contract is informational only — the persisted row stays in
    ``agent_decisions`` with the wire-level decision_type.
    """

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    application_id: int
    recommended_action: RECOMMENDED_ACTION_TYPE
    confidence: float = Field(ge=0.0, le=1.0)
    policy_version: str
    evidence_json: dict = Field(default_factory=dict)
    status: Literal["pending", "approved", "overridden", "taught"]
    created_at: datetime


# ---------------------------------------------------------------------------
# Feedback contracts (recruiter actions)
# ---------------------------------------------------------------------------


class GraphWriteHint(BaseModel):
    """Structured graph-mutation suggestion captured from teach/override UI.

    See ``graph_writeback.contracts`` for the apply-time pipeline. The
    columns on ``decision_feedback.graph_write_hints`` carry the raw JSON
    list of these.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "assert_edge",
        "invalidate_edge",
        "update_edge_property",
        "assert_node",
    ]
    from_node_id: str | None = None
    edge_type: str | None = None
    to_node_id: str | None = None
    properties: dict | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class TeachFeedback(BaseModel):
    """v2 teach event — supersedes the loose-typed FeedbackBody.

    Captured by the TeachModal v2. ``attributed_to`` + ``direction`` +
    ``scope`` are the fields the policy fitter + exemplar stores route
    on; ``graph_write_hints`` are passed to the writeback pipeline.
    """

    model_config = ConfigDict(extra="forbid")

    decision_id: int
    failure_mode: str
    attributed_to: ATTRIBUTED_TO_TYPE
    direction: Literal["over", "under"]
    scope: Literal["this_candidate", "this_role", "all_similar"]
    free_text_reason: str
    graph_write_hints: list[GraphWriteHint] = Field(default_factory=list)
    recruiter_id: int
    submitted_at: datetime


class OverrideFeedback(BaseModel):
    """v2 override event. ``attributed_to`` and hints are optional —
    the recruiter doesn't have to attribute on every manual flip.
    """

    model_config = ConfigDict(extra="forbid")

    decision_id: int
    manual_action: Literal[
        "advance_stage", "reject_application", "do_nothing"
    ]
    attributed_to: ATTRIBUTED_TO_TYPE | None = None
    free_text_reason: str = ""
    graph_write_hints: list[GraphWriteHint] = Field(default_factory=list)
    recruiter_id: int
    submitted_at: datetime


class HiringOutcome(BaseModel):
    """A realised hiring outcome — link target for trained models."""

    model_config = ConfigDict(extra="forbid")

    decision_id: int
    outcome_type: Literal[
        "reached_interview",
        "received_offer",
        "hired",
        "rejected_late",
        "withdrew",
    ]
    quality_signal: float | None = None  # post-hire performance, 0..1
    observed_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def scope_to_wire(spec_scope: str) -> str:
    """Map spec-side scope names to the wire-level column values."""
    return SCOPE_ALIASES.get(spec_scope, spec_scope)


def wire_to_scope(wire_scope: str) -> str:
    """Inverse: wire-level column value → spec-side display name."""
    inv = {v: k for k, v in SCOPE_ALIASES.items()}
    return inv.get(wire_scope, wire_scope)


# ---------------------------------------------------------------------------
# Amendment A1: recruiter intent as first-class
# ---------------------------------------------------------------------------


class StructuredIntent(BaseModel):
    """Structured fields inside a ``RoleIntent`` row.

    All lists default to empty; over-structuring is worse than under at
    this stage (A1.3). Free-form notes ride alongside on the parent
    ``RoleIntent`` row's ``free_text`` field.
    """

    model_config = ConfigDict(extra="forbid")

    soft_signals: list[str] = Field(default_factory=list)
    deal_breakers: list[str] = Field(default_factory=list)
    growth_expectations: str | None = None
    # backfill / new_headcount / replacement / pivot — left free-form
    # because the vocabulary will evolve before it's worth enumerating.
    context_for_opening: str | None = None
    weighting_notes: str | None = None
    must_haves_missing_from_spec: list[str] = Field(default_factory=list)


class RoleIntentRecord(BaseModel):
    """A single versioned RoleIntent row, as it appears to consumers."""

    model_config = ConfigDict(extra="forbid")

    intent_id: int
    role_id: int
    version: int
    structured: StructuredIntent
    free_text: str | None = None
    # None means there is no text or the predecessor prefix did not prove an
    # append boundary; consumers must not infer one from paragraph separators.
    latest_free_text: str | None = None
    valid_from: datetime
    valid_to: datetime | None = None
    authored_by_user_id: int | None = None
    authored_at: datetime


# ---------------------------------------------------------------------------
# Amendment A2: task selection + assessment lifecycle
# ---------------------------------------------------------------------------


TASK_SELECTION_DECISION_TYPE = Literal["send_task", "skip_task", "request_artifacts"]


class TaskSelection(BaseModel):
    """One decision from the task-selection sub-agent.

    Mirrors §A2.4. ``chosen_template_id`` is the Tali ``tasks.id`` (we
    don't duplicate templates as a separate table — existing
    ``tasks.is_template=True`` rows are the canonical templates).
    """

    model_config = ConfigDict(extra="forbid")

    application_id: int
    decision: TASK_SELECTION_DECISION_TYPE
    chosen_template_id: int | None = None
    skip_reason: str | None = None
    requested_artifacts: list[str] = Field(default_factory=list)
    reasoning: str
    citations: list[GraphCitation] = Field(default_factory=list)
    selected_at: datetime
    agent_version: str
    uncertainty: float = Field(ge=0.0, le=1.0, default=0.0)


class TaskSelectionFeedback(BaseModel):
    """Captured when the recruiter overrides the task-selection agent."""

    model_config = ConfigDict(extra="forbid")

    selection_id: str
    override_decision: Literal["different_template", "force_send", "force_skip"]
    chosen_template_id: int | None = None
    reason: str
    recruiter_id: int


__all__ = [
    "AGENT_NAMES",
    "AGENT_NAME_TYPE",
    "ATTRIBUTED_TO_TYPE",
    "RECOMMENDED_ACTION_TYPE",
    "SCOPE_ALIASES",
    "Application",
    "CandidateScores",
    "Decision",
    "ExemplarRef",
    "GraphCitation",
    "GraphWriteHint",
    "HiringOutcome",
    "OverrideFeedback",
    "RoleIntentRecord",
    "StructuredIntent",
    "SubAgentScore",
    "TASK_SELECTION_DECISION_TYPE",
    "TaskSelection",
    "TaskSelectionFeedback",
    "TeachFeedback",
    "scope_to_wire",
    "wire_to_scope",
]
