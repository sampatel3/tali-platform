"""Pure token and no-progress guards for autonomous agent cycles."""

from __future__ import annotations

import json
from typing import Any

from ..models.agent_run import AgentRun


def cycle_tokens(run: AgentRun) -> int:
    """Return every billed token category accumulated by one cycle."""

    return int(
        (run.input_tokens or 0)
        + (run.output_tokens or 0)
        + (run.cache_read_tokens or 0)
        + (run.cache_creation_tokens or 0)
    )


def tool_round_signature(blocks: list[dict[str, Any]]) -> str:
    """Return a stable signature of the tool calls in one model response."""

    calls = [
        {"name": block.get("name"), "input": block.get("input") or {}}
        for block in blocks
        if block.get("type") == "tool_use"
    ]
    return json.dumps(calls, sort_keys=True, separators=(",", ":"), default=str)


__all__ = ["cycle_tokens", "tool_round_signature"]
