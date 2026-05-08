"""All four v1 sub-agents register on import."""

from __future__ import annotations

from app.sub_agents.registry import all_sub_agents, get_sub_agent


EXPECTED = {"pre_screen", "cv_scoring", "assessment_scoring", "intent_parser"}


def test_v1_sub_agents_are_registered():
    names = {sa.name for sa in all_sub_agents()}
    missing = EXPECTED - names
    assert not missing, f"missing sub-agents: {missing}"


def test_get_sub_agent_returns_callable():
    pre = get_sub_agent("pre_screen")
    assert pre.name == "pre_screen"
    assert callable(getattr(pre, "run", None))


def test_unknown_sub_agent_raises():
    import pytest

    with pytest.raises(KeyError):
        get_sub_agent("totally_not_a_sub_agent")
