"""Secret-safe canonical identity for one Claude Agent SDK query."""

from __future__ import annotations

from typing import Any

from ....services.provider_request_identity import provider_request_sha256

SDK_ALLOWED_TOOLS = [
    "mcp__sandbox__Read",
    "mcp__sandbox__Write",
    "mcp__sandbox__Edit",
    "mcp__sandbox__Bash",
]


def sdk_provider_request_sha256(
    *,
    prompt: str,
    model: str,
    system_prompt: str,
    max_turns: int,
    max_budget_usd: float,
) -> str:
    """Hash every stable provider-relevant option without hashing secrets."""

    request: dict[str, Any] = {
        "prompt": prompt,
        "model": model,
        "system_prompt": system_prompt,
        "mcp_servers": ["sandbox"],
        "allowed_tools": SDK_ALLOWED_TOOLS,
        "tools": [],
        "setting_sources": [],
        "permission_mode": "bypassPermissions",
        "max_turns": max_turns,
        "max_budget_usd": max_budget_usd,
        "environment_contract": {
            "anthropic_api_key_present": True,
            "is_sandbox": "1",
        },
    }
    return provider_request_sha256(request)


__all__ = ["SDK_ALLOWED_TOOLS", "sdk_provider_request_sha256"]
