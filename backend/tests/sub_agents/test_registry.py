"""Only production pre-evaluation sub-agents auto-register on import.

The superseded ``intent_parser`` execution path is retired; its provider-free
compatibility facade stays unregistered. The retained ``task_selection``
prototype also stays unregistered until its outcomes have production consumers.
"""

from __future__ import annotations

from app.sub_agents.registry import all_sub_agents, get_sub_agent


EXPECTED = {"pre_screen", "cv_scoring", "assessment_scoring", "graph_priors"}


def test_v1_sub_agents_are_registered():
    names = {sa.name for sa in all_sub_agents()}
    assert names == EXPECTED


def test_get_sub_agent_returns_callable():
    pre = get_sub_agent("pre_screen")
    assert pre.name == "pre_screen"
    assert callable(getattr(pre, "run", None))


def test_unknown_sub_agent_raises():
    import pytest

    with pytest.raises(KeyError):
        get_sub_agent("totally_not_a_sub_agent")
