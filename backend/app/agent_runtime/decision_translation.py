"""Map a deterministic engine verdict to a persisted ``AgentDecision`` type.

The decision-policy engine emits *verbs* (``queue_send_assessment``,
``queue_reject_decision``, …); ``AgentDecision.decision_type`` stores the
*noun* (``send_assessment``, ``reject``, …). This module is the single
place that translation happens, including the one product rule that needs
runtime context: **a ``send_assessment`` verdict becomes ``advance`` when
the role has no assessment task** — there's nothing to send, so a strong
candidate goes straight to interview rather than being left undecided.
"""
from __future__ import annotations

# engine verdict -> persisted AgentDecision.decision_type
_ENGINE_TO_PERSISTED: dict[str, str] = {
    "queue_send_assessment": "send_assessment",
    "queue_advance_decision": "advance_to_interview",
    "queue_reject_decision": "reject",
    "queue_skip_assessment_reject_decision": "skip_assessment_reject",
    "auto_reject": "reject",
}

# Verdicts that should produce a queued decision. ``skip`` / ``no_action``
# / ``escalate_low_confidence`` are deliberately excluded — the bulk pass
# leaves those to the LLM agent / recruiter.
QUEUEABLE_VERDICTS = frozenset(_ENGINE_TO_PERSISTED)


def resolve_persisted_decision_type(
    engine_verdict: str, *, has_assessment_task: bool
) -> str | None:
    """Return the ``AgentDecision.decision_type`` for an engine verdict.

    Returns ``None`` for non-queueable verdicts. Applies the no-task
    switch: ``send_assessment`` with no task -> ``advance_to_interview``.
    """
    persisted = _ENGINE_TO_PERSISTED.get(engine_verdict)
    if persisted is None:
        return None
    if persisted == "send_assessment" and not has_assessment_task:
        return "advance_to_interview"
    return persisted


__all__ = ["QUEUEABLE_VERDICTS", "resolve_persisted_decision_type"]
