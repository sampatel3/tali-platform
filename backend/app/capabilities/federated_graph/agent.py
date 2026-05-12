"""Federated graph — anonymized cross-org signals.

Extends `graph_priors` with skill→outcome priors aggregated under
differential-privacy bounds across organisations that have a contract
in place. When inactive (the default), only the org's local Graphiti
contributes — exactly the v1/v2 behaviour.

Risk: high. Requires sign-off from `legal`, `privacy_dpo`, and
`infosec` before any rollout.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._stub_helpers import CapabilityContext


CAPABILITY = "federated_graph"


@dataclass
class FederatedSignal:
    skill: str
    cross_org_hire_rate: float
    sample_size: int
    differential_privacy_epsilon: float


@dataclass
class FederatedReport:
    signals: list[FederatedSignal] = field(default_factory=list)
    notes: str = ""


def fetch_federated_signals(
    ctx: CapabilityContext, *, role_family: str | None
) -> FederatedReport | None:
    if not ctx.is_active(CAPABILITY):
        return None
    return None  # TODO: cross-org aggregation under DP bounds


__all__ = ["CAPABILITY", "FederatedReport", "FederatedSignal", "fetch_federated_signals"]
