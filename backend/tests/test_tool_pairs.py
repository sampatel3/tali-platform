"""Tests for tool_use/tool_result history repair (poisoned-conversation fix)."""
from __future__ import annotations

from app.llm.tool_pairs import sanitize_tool_pairs


def _ids(content):
    return [b.get("tool_use_id") for b in content if b.get("type") == "tool_result"]


def test_orphaned_tool_use_gets_synthetic_result():
    # assistant tool_use with NO following tool_result (interrupted turn)
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_X", "name": "f", "input": {}}]},
        {"role": "user", "content": "next question"},  # NOT a tool_result
    ]
    out = sanitize_tool_pairs(msgs)
    # a synthetic tool_result message is injected right after the tool_use
    assert out[1]["role"] == "assistant"
    assert out[2]["role"] == "user"
    assert _ids(out[2]["content"]) == ["toolu_X"]
    assert out[2]["content"][0]["is_error"] is True
    # the real following user message is preserved after it
    assert out[3]["content"] == "next question"


def test_orphan_at_end_of_history():
    msgs = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_Y", "name": "f", "input": {}}]},
    ]
    out = sanitize_tool_pairs(msgs)
    assert len(out) == 2
    assert _ids(out[1]["content"]) == ["toolu_Y"]


def test_properly_paired_is_unchanged():
    msgs = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_Z", "name": "f", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_Z", "content": "ok"}]},
    ]
    out = sanitize_tool_pairs(msgs)
    assert len(out) == 2
    assert _ids(out[1]["content"]) == ["toolu_Z"]
    assert "is_error" not in out[1]["content"][0]


def test_partial_results_get_missing_one_synthesised():
    msgs = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "f", "input": {}},
            {"type": "tool_use", "id": "b", "name": "g", "input": {}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "ok"}]},
    ]
    out = sanitize_tool_pairs(msgs)
    assert set(_ids(out[1]["content"])) == {"a", "b"}  # b synthesised, a kept


def test_plain_text_history_untouched():
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]
    assert sanitize_tool_pairs(msgs) == msgs
