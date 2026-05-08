"""Pydantic v2 validation for ``decision_policies.policy_json``.

This is OUR schema, not LLM output — ``extra="forbid"`` everywhere so a
typo on a retune fails loud at write time rather than silently being
ignored at evaluate time.

The schema is dispatched on ``schema_version`` so future migrations are
additive: a v2 schema lives alongside v1, the engine reads
``schema_version`` first, parses through the matching model, and
evaluation is identical from there. Today there is only ``v1``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION_V1: Literal["v1"] = "v1"


# Canonical decision-point names. Engine iterates over these in priority
# order at evaluate time. Adding a fourth point in v2 is purely additive:
# extend ``DECISION_POINT_NAMES`` and the engine handles it without
# touching the schema dispatcher.
DECISION_POINT_NAMES = ("send_assessment", "advance_to_interview", "reject")


# Rule action verbs the engine knows how to act on. Anything else fails
# validation. The verbs map 1:1 to ``DecisionType`` values the engine
# emits, except ``skip`` and ``auto_reject`` which short-circuit.
RULE_ACTIONS = (
    "queue_send_assessment",
    "queue_advance_decision",
    "queue_reject_decision",
    "queue_skip_assessment_reject_decision",
    "auto_reject",
    "skip",
    "no_action",
)


class Rule(BaseModel):
    """One conditional rule inside a decision point.

    ``if`` is a free-form expression that the engine's tiny evaluator
    parses (see ``engine._eval_condition``). Keeping it string-shaped
    rather than AST-shaped lets recruiters paste plain English on the
    Hub later without us teaching them an AST.
    """

    model_config = ConfigDict(extra="forbid")

    if_: str = Field(alias="if")
    then: str
    priority: int = Field(ge=0, le=10_000)
    reason_template: str | None = None

    @field_validator("then")
    @classmethod
    def _then_is_known_action(cls, v: str) -> str:
        if v not in RULE_ACTIONS:
            raise ValueError(
                f"unknown rule action {v!r}; expected one of {RULE_ACTIONS}"
            )
        return v


class DecisionPoint(BaseModel):
    """One decision point (send_assessment / advance / reject).

    ``thresholds`` are the recruiter-readable knobs the retuner shifts.
    ``weights`` map signal names to floats summing to 1.0. ``rules`` are
    walked in priority-descending order; the first match wins.
    ``confidence_floor`` is the minimum confidence the verdict must
    exceed before the decision queues — below it, the engine queues a
    ``no_action`` outcome with the reasoning trace intact.
    """

    model_config = ConfigDict(extra="forbid")

    thresholds: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list)
    confidence_floor: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "DecisionPoint":
        if not self.weights:
            return self
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"weights must sum to 1.0 ±0.01; got {total} for keys {list(self.weights)}"
            )
        return self


class GraphPriorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    neighbourhood_size: int = Field(default=20, ge=1, le=200)
    min_neighbours_for_prior: int = Field(default=5, ge=0, le=200)
    decay_days: int = Field(default=365, ge=1, le=3650)


class IntentOverridesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    honor_strictness_modifiers: bool = True
    # Cap on threshold delta a single intent payload can shift any single
    # threshold. Prevents "I want this to be very strict" from collapsing
    # the whole policy.
    max_threshold_shift: float = Field(default=20.0, ge=0.0, le=100.0)


class ManualActionWindowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookback_hours: int = Field(default=72, ge=0, le=24 * 30)
    skip_decision_types_on_recent_manual: list[str] = Field(
        default_factory=lambda: list(DECISION_POINT_NAMES)
    )


class PolicyMetadata(BaseModel):
    """Provenance fields. None of these are required for evaluation —
    they tell the Hub *why* this revision exists.
    """

    model_config = ConfigDict(extra="forbid")

    trained_from_feedback_ids: list[int] = Field(default_factory=list)
    trained_from_manual_decision_count: int = 0
    trained_at: str | None = None
    notes: str | None = None


class PolicyJson(BaseModel):
    """The ``policy_json`` blob persisted to ``decision_policies``.

    Engine consumes this through ``model_validate`` so we never operate
    on raw dicts. Any field outside this schema is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = SCHEMA_VERSION_V1
    decision_points: dict[str, DecisionPoint]
    graph_prior_config: GraphPriorConfig = Field(default_factory=GraphPriorConfig)
    intent_overrides: IntentOverridesConfig = Field(
        default_factory=IntentOverridesConfig
    )
    manual_action_window: ManualActionWindowConfig = Field(
        default_factory=ManualActionWindowConfig
    )
    metadata: PolicyMetadata = Field(default_factory=PolicyMetadata)

    @field_validator("decision_points")
    @classmethod
    def _decision_points_known(cls, v: dict[str, DecisionPoint]) -> dict[str, DecisionPoint]:
        unknown = set(v.keys()) - set(DECISION_POINT_NAMES)
        if unknown:
            raise ValueError(
                f"unknown decision_point names {sorted(unknown)}; "
                f"expected subset of {DECISION_POINT_NAMES}"
            )
        return v


__all__ = [
    "DECISION_POINT_NAMES",
    "RULE_ACTIONS",
    "SCHEMA_VERSION_V1",
    "DecisionPoint",
    "GraphPriorConfig",
    "IntentOverridesConfig",
    "ManualActionWindowConfig",
    "PolicyJson",
    "PolicyMetadata",
    "Rule",
]
