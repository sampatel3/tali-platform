from app.agent_runtime import tool_registry
from app.agent_runtime.tool_descriptions import (
    QUEUE_EVIDENCE_DESC,
    QUEUE_REASONING_DESC,
)


def test_registry_preserves_description_aliases():
    assert tool_registry._QUEUE_REASONING_DESC == QUEUE_REASONING_DESC
    assert tool_registry._QUEUE_EVIDENCE_DESC == QUEUE_EVIDENCE_DESC


def test_all_queue_decisions_share_plain_english_reasoning_contract():
    queue_tools = [
        tool
        for tool in tool_registry.AGENT_TOOLS
        if tool["name"].startswith("queue_") and tool["name"].endswith("_decision")
    ]

    assert len(queue_tools) == 4
    for tool in queue_tools:
        properties = tool["input_schema"]["properties"]
        assert properties["reasoning"]["description"] == QUEUE_REASONING_DESC
        assert properties["evidence"]["description"] == QUEUE_EVIDENCE_DESC

    assert "never snake_case" in QUEUE_REASONING_DESC
    assert "never key=value pairs" in QUEUE_REASONING_DESC
