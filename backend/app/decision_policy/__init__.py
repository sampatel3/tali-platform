"""Deterministic, versioned verdict layer for the orchestrator agent.

Public surface:

- ``schema.PolicyJson`` — Pydantic validation for ``policy_json``.
- ``engine.evaluate(inputs, *, db)`` — pure-Python verdict function.
- ``engine.load_active_policy(db, org_id, role_id)`` — pick the row.
- ``intent.apply_intent_overrides(policy, intent_dict)`` — ephemeral
  per-cycle overlay of recruiter intent on a base policy.
- ``bootstrap.bootstrap_org(db, org_id)`` — idempotent default seeder.

Phase 5 adds ``feedback_aggregator``, ``retroactive_eval``, ``retuner``,
and ``diff`` — the learning loop that produces new policy revisions
from recruiter feedback and manual actions.
"""

from .engine import DecisionInputs, PolicyDecision, evaluate, load_active_policy
from .schema import PolicyJson

__all__ = [
    "DecisionInputs",
    "PolicyDecision",
    "evaluate",
    "load_active_policy",
    "PolicyJson",
]
