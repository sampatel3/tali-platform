"""Bind auto-executed hiring actions to deterministic engine verdicts."""

from __future__ import annotations

from typing import Optional

from ..models.agent_run import AgentRun


# Only hire-progression decisions require a matching deterministic verdict.
# Rejects remain on the human-confirm rail; invite operations are resendable.
_ENGINE_VERDICT_EQUIV: dict[str, frozenset[str]] = {
    "advance_to_interview": frozenset({"advance_to_interview"}),
}


def _engine_verdict_for(
    agent_run: AgentRun,
    application_id: int,
) -> Optional[str]:
    """Return the engine verdict captured for this application this cycle."""
    verdicts = getattr(agent_run, "__engine_verdicts__", None) or {}
    return verdicts.get(int(application_id))


def _is_on_policy(
    agent_run: AgentRun,
    application_id: int,
    decision_type: str,
) -> tuple[bool, Optional[str]]:
    """Require a matching engine verdict for hire-relevant decisions."""
    expected = _ENGINE_VERDICT_EQUIV.get(decision_type)
    if expected is None:
        return True, None
    engine_decision_type = _engine_verdict_for(agent_run, application_id)
    return engine_decision_type in expected, engine_decision_type


__all__ = ["_engine_verdict_for", "_is_on_policy"]
