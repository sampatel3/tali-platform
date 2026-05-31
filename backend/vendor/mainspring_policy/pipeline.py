"""Entities flowing through a stateful pipeline.

An :class:`Entity` is any item your operation moves through stages — a loan
application, a support ticket, an invoice, a candidate. The framework is
deliberately agnostic about what the entity *is*; you describe its stages
and how to classify it, and the runtime does the rest.

A *cohort state* is the derived "where is this entity right now" label the
orchestrator reasons about (``needs_kyc``, ``ready_for_decision`` …). It is
computed from the entity's attributes — which include any signal results
written back during a prior cycle. This mirrors how a production system
persists scores onto the row and classifies off those columns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Entity:
    """One item in the pipeline."""

    id: str
    attributes: dict[str, Any] = field(default_factory=dict)
    # ``stage`` is the coarse lifecycle marker the *operator* owns (e.g.
    # "open" vs "terminal"); the fine-grained cohort state is derived by
    # the classifier. Kept separate so a human moving an entity out of the
    # operation (hand-off) is distinguishable from cohort movement.
    stage: str = "open"

    def get(self, key: str, default: Any = None) -> Any:
        return self.attributes.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.attributes[key] = value


# A classifier maps an entity to its current cohort state. Pure function of
# the entity's (attribute-persisted) state — no side effects.
Classifier = Callable[[Entity], str]


@dataclass
class PipelineSpec:
    """Declares the state machine the operator works over."""

    name: str
    states: list[str]
    # States where the operator should produce a decision this cycle.
    actionable_states: set[str]
    classify: Classifier
    # States that mean "left the operation" — never surveyed or acted on.
    terminal_states: set[str] = field(default_factory=set)

    def state_of(self, entity: Entity) -> str:
        return self.classify(entity)

    def survey(self, entities: list[Entity]) -> dict[str, list[Entity]]:
        """Group entities by cohort state. The operator's map of the world."""
        cohorts: dict[str, list[Entity]] = {s: [] for s in self.states}
        for e in entities:
            state = self.state_of(e)
            cohorts.setdefault(state, []).append(e)
        return cohorts
