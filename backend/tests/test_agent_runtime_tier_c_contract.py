"""Contract tests for explicitly opted-in autonomous agent actions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent_runtime import tool_registry
from app.agent_runtime.system_prompt import build_system_prompt
from app.models.organization import Organization
from app.models.role import Role


def _make_role(db, *, allowlist: list[str] | None) -> Role:
    org = Organization(name="Tier C Org", slug=f"tier-c-{id(db)}-{len(db.new)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Tier C Test Role",
        source="manual",
        job_spec_text="Hire a platform engineer.",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5_000,
        agent_action_allowlist=allowlist,
    )
    db.add(role)
    db.flush()
    return role


def _prompt_text(role: Role) -> str:
    return "\n".join(
        block["text"]
        for block in build_system_prompt(role=role, trigger_context="test")
    )


@pytest.mark.parametrize(
    "tool_name",
    sorted(tool_registry.EXPLICIT_OPT_IN_ACTION_TOOL_NAMES),
)
def test_explicitly_allowlisted_tier_c_tool_is_exposed_and_described(
    db, tool_name: str
):
    allowlist = sorted(tool_registry.DEFAULT_AGENT_ACTION_ALLOWLIST | {tool_name})
    role = _make_role(db, allowlist=allowlist)

    exposed = {tool["name"] for tool in tool_registry.tools_for_role(role)}
    prompt = _prompt_text(role)

    assert tool_name in exposed
    assert f"- {tool_name}:" in prompt
    assert "ROLE-SPECIFIC OPT-IN ACTIONS:" in prompt
    assert "AVAILABLE TOOLS FOR THIS ROLE (authoritative):" in prompt


def test_default_role_hides_tier_c_from_schemas_and_prompt(db):
    role = _make_role(db, allowlist=None)
    exposed = {tool["name"] for tool in tool_registry.tools_for_role(role)}
    prompt = _prompt_text(role)

    assert exposed.isdisjoint(tool_registry.EXPLICIT_OPT_IN_ACTION_TOOL_NAMES)
    assert "ROLE-SPECIFIC OPT-IN ACTIONS:" not in prompt
    for tool_name in tool_registry.EXPLICIT_OPT_IN_ACTION_TOOL_NAMES:
        assert tool_name not in prompt


def test_low_confidence_escalation_is_in_default_role_contract(db):
    """Escalation is a governed HITL lane, not a legacy Tier-C opt-in."""
    role = _make_role(db, allowlist=None)
    exposed = {tool["name"] for tool in tool_registry.tools_for_role(role)}
    prompt = _prompt_text(role)

    assert "queue_escalate_decision" in tool_registry.DEFAULT_AGENT_ACTION_ALLOWLIST
    assert "queue_escalate_decision" in exposed
    assert "queue_escalate_decision" in prompt
    assert "escalate_low_confidence" in prompt


@pytest.mark.parametrize(
    "tool_name",
    sorted(tool_registry.EXPLICIT_OPT_IN_ACTION_TOOL_NAMES),
)
def test_dispatch_still_blocks_tier_c_without_explicit_opt_in(db, tool_name: str):
    role = _make_role(db, allowlist=None)

    result = tool_registry.dispatch(
        tool_name,
        {},
        db=db,
        agent_run=SimpleNamespace(),
        role=role,
    )

    assert result == {
        "status": "blocked_by_governance",
        "tool": tool_name,
        "reason": f"tool '{tool_name}' is not allowed by role.agent_action_allowlist",
        "instruction": "Choose an allowed action or call agent_run_complete.",
    }
