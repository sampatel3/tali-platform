"""Granular ``auto_reject_prescreen`` gates auto-execution of prescreen rejects.

Three flags can drive auto-execution of an AgentDecision after the
agent queues it:

- ``role.auto_reject`` (master) — fires for any reject the agent queues.
- ``role.auto_reject_prescreen`` (granular) — fires only when the
  policy verdict cited ``reject_reason='pre_screen_below_threshold'``.
- ``role.auto_promote`` — fires for advance/send_assessment decisions.

These tests pin the gate function directly so we don't have to spin up
a full agent cycle.
"""

from __future__ import annotations

from app.agent_runtime.tool_registry import _should_auto_execute
from app.models.role import Role


def _role(**overrides) -> Role:
    role = Role(
        organization_id=1,
        name="Test",
        source="manual",
    )
    for attr in ("auto_reject", "auto_reject_prescreen", "auto_promote"):
        setattr(role, attr, False)
    for k, v in overrides.items():
        setattr(role, k, v)
    return role


def test_master_auto_reject_fires_for_any_reject():
    role = _role(auto_reject=True)
    assert _should_auto_execute(
        role=role, decision_type="reject", reject_reason="role_fit_low"
    )
    assert _should_auto_execute(
        role=role,
        decision_type="skip_assessment_reject",
        reject_reason="pre_screen_below_threshold",
    )
    # Empty reject_reason still wins under master flag.
    assert _should_auto_execute(
        role=role, decision_type="reject", reject_reason=""
    )


def test_prescreen_flag_fires_only_for_prescreen_rejects():
    role = _role(auto_reject_prescreen=True)
    # Pre-screen reject: fires.
    assert _should_auto_execute(
        role=role,
        decision_type="skip_assessment_reject",
        reject_reason="pre_screen_below_threshold",
    )
    # Judgment / role-fit reject with the granular flag on: does NOT fire —
    # recruiter explicitly kept HITL on those.
    assert not _should_auto_execute(
        role=role, decision_type="reject", reject_reason="role_fit_low"
    )
    assert not _should_auto_execute(
        role=role, decision_type="reject", reject_reason=""
    )


def test_both_flags_off_means_no_auto_execute():
    role = _role()
    for reason in ("pre_screen_below_threshold", "role_fit_low", ""):
        assert not _should_auto_execute(
            role=role, decision_type="reject", reject_reason=reason
        )
        assert not _should_auto_execute(
            role=role,
            decision_type="skip_assessment_reject",
            reject_reason=reason,
        )


def test_advance_decisions_unaffected_by_reject_flags():
    role = _role(auto_reject=True, auto_reject_prescreen=True)
    assert not _should_auto_execute(
        role=role,
        decision_type="advance_to_interview",
        reject_reason="",
    )
    # Same decision_type with auto_promote on does fire.
    role = _role(auto_promote=True)
    assert _should_auto_execute(
        role=role,
        decision_type="advance_to_interview",
        reject_reason="",
    )


def test_send_assessment_uses_auto_promote():
    role = _role(auto_promote=True)
    assert _should_auto_execute(
        role=role, decision_type="send_assessment", reject_reason=""
    )
    role = _role(auto_promote=False, auto_reject=True)
    assert not _should_auto_execute(
        role=role, decision_type="send_assessment", reject_reason=""
    )
