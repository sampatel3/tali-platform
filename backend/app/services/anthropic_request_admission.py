"""Conservative credit bounds for one authorized Anthropic request."""

from __future__ import annotations

import json
from typing import Any

from .claude_model_pricing import require_priceable_claude_model
from .pricing_service import Feature, credits_charged, raw_cost_usd_micro


# Every currently enabled Claude family fits within this upper rail. Text and
# inline media normally use the much smaller serialized-byte bound below;
# remote/opaque media falls back to the full context so it cannot evade holds.
CLAUDE_CONTEXT_TOKEN_UPPER_BOUND = 1_000_000
ANTHROPIC_PROTOCOL_TOKEN_MARGIN = 8_192

_MODEL_CONTEXT_TOKEN_UPPER_BOUNDS = {
    "claude-haiku-4-5": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-opus-4-5": 200_000,
}


class AnthropicRequestAdmissionError(ValueError):
    """A request cannot be assigned a finite conservative billing bound."""


def _reject_unbudgeted_premium_features(request: dict[str, Any]) -> None:
    stream = request.get("stream")
    if stream is not None and stream is not False:
        # ``messages.create(stream=True)`` returns a raw stream and therefore
        # never reaches the response-usage settlement below our adapters.
        # The separately metered synchronous ``messages.stream()`` surface
        # remains available; async streaming still fails closed.
        raise AnthropicRequestAdmissionError(
            "Anthropic messages.create streaming is not supported"
        )
    inference_geo = request.get("inference_geo")
    if inference_geo not in (None, "", "global"):
        raise AnthropicRequestAdmissionError(
            "premium Anthropic inference geography is not budgeted"
        )
    for key in (
        "container",
        "context_management",
        "extra_body",
        "extra_headers",
        "extra_query",
        "mcp_servers",
        "server_tools",
        "speed",
    ):
        if request.get(key) not in (None, "", [], {}):
            raise AnthropicRequestAdmissionError(
                "unbudgeted Anthropic server-side feature is not supported"
            )
    request_service_tier = request.get("service_tier")
    if request_service_tier not in (None, "", "standard_only"):
        raise AnthropicRequestAdmissionError(
            "unbudgeted Anthropic service tier is not supported"
        )
    tools = request.get("tools")
    if tools is not None:
        if not isinstance(tools, (list, tuple)):
            raise AnthropicRequestAdmissionError(
                "Anthropic tools must be a finite list or tuple"
            )
        for tool in tools:
            if not isinstance(tool, dict):
                raise AnthropicRequestAdmissionError(
                    "Anthropic tool definitions must be objects"
                )
            if str(tool.get("type") or "").strip():
                raise AnthropicRequestAdmissionError(
                    "billable Anthropic server tool is not supported"
                )


def _media_block_is_remote_or_opaque(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    block_type = str(block.get("type") or "")
    if block_type == "image":
        source = block.get("source")
        return not bool(
            isinstance(source, dict)
            and source.get("type") == "base64"
            and isinstance(source.get("media_type"), str)
            and str(source.get("media_type")).startswith("image/")
            and isinstance(source.get("data"), str)
        )
    if block_type == "document":
        source = block.get("source")
        if not isinstance(source, dict) or source.get("type") != "content":
            return True
        content = source.get("content")
        return not isinstance(content, list) or any(
            not isinstance(item, dict) or item.get("type") != "text"
            for item in content
        )
    if block_type == "tool_result":
        content = block.get("content")
        return isinstance(content, (list, tuple)) and any(
            _media_block_is_remote_or_opaque(item) for item in content
        )
    return False


def _contains_remote_or_opaque_media(request: dict[str, Any]) -> bool:
    """Inspect actual Anthropic content blocks, never arbitrary JSON values."""

    system = request.get("system")
    if isinstance(system, (list, tuple)) and any(
        _media_block_is_remote_or_opaque(block) for block in system
    ):
        return True
    messages = request.get("messages")
    if not isinstance(messages, (list, tuple)):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, (list, tuple)) and any(
            _media_block_is_remote_or_opaque(block) for block in content
        ):
            return True
    return False


def _block_cache_ttl(block: Any) -> str | None:
    if not isinstance(block, dict) or "cache_control" not in block:
        return None
    cache_control = block["cache_control"]
    if not isinstance(cache_control, dict) or cache_control.get("type") != "ephemeral":
        raise AnthropicRequestAdmissionError(
            "unsupported Anthropic cache-control type"
        )
    if "ttl" in cache_control and cache_control["ttl"] not in ("5m", "1h"):
        raise AnthropicRequestAdmissionError("unsupported Anthropic cache-control TTL")
    return "1h" if cache_control.get("ttl") == "1h" else "5m"


def _cache_write_ttl(request: dict[str, Any]) -> str | None:
    """Inspect request-level automatic caching and explicit protocol blocks."""

    blocks: list[Any] = []
    system = request.get("system")
    blocks.extend(system if isinstance(system, (list, tuple)) else [system])
    tools = request.get("tools")
    if isinstance(tools, (list, tuple)):
        blocks.extend(tools)
    messages = request.get("messages")
    if isinstance(messages, (list, tuple)):
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            blocks.extend(
                content if isinstance(content, (list, tuple)) else [content]
            )
    nested_tool_content = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            content = block.get("content")
            if isinstance(content, (list, tuple)):
                nested_tool_content.extend(content)
    blocks.extend(nested_tool_content)
    # Anthropic's top-level cache_control enables automatic caching. It has
    # the same 5m/1h write multipliers as explicit block breakpoints, so it
    # must participate in the conservative hold before the SDK is touched.
    ttl = _block_cache_ttl(request)
    for block in blocks:
        candidate = _block_cache_ttl(block)
        if candidate == "1h":
            ttl = candidate
        elif candidate is not None and ttl is None:
            ttl = candidate
    return ttl


def _serialized_request_bytes(request: dict[str, Any]) -> int | None:
    try:
        encoded = json.dumps(
            request,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except Exception:
        return None
    return len(encoded)


def anthropic_request_credit_upper_bound(
    request: dict[str, Any],
    *,
    feature: Feature | str,
    service_tier: str = "standard",
) -> int:
    """Return a hard credit bound for any successful authorized response.

    UTF-8 bytes are a conservative token upper bound for inline request data.
    An explicit protocol margin covers provider framing. Opaque/remote media
    uses the full known context rail. Prompt-cache writes are priced as though
    every possible input token used the most expensive requested TTL.
    """

    if not isinstance(request, dict):
        raise AnthropicRequestAdmissionError("Anthropic request must be an object")
    _reject_unbudgeted_premium_features(request)
    model = str(request.get("model") or "")
    family = require_priceable_claude_model(model)
    context_upper = _MODEL_CONTEXT_TOKEN_UPPER_BOUNDS.get(
        family, CLAUDE_CONTEXT_TOKEN_UPPER_BOUND
    )
    max_tokens = request.get("max_tokens")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise AnthropicRequestAdmissionError(
            "Anthropic request requires a positive max_tokens bound"
        )

    output_upper = min(int(max_tokens), context_upper)
    remaining_context = max(context_upper - output_upper, 0)
    serialized_bytes = _serialized_request_bytes(request)
    if (
        serialized_bytes is None
        or _contains_remote_or_opaque_media(request)
    ):
        input_upper = remaining_context
    else:
        input_upper = min(
            serialized_bytes + ANTHROPIC_PROTOCOL_TOKEN_MARGIN,
            remaining_context,
        )

    cache_ttl = _cache_write_ttl(request)
    raw_kwargs: dict[str, Any] = {
        "input_tokens": input_upper if cache_ttl is None else 0,
        "output_tokens": output_upper,
        "cache_creation_tokens": input_upper if cache_ttl is not None else 0,
        "model": model,
        "service_tier": service_tier,
    }
    if cache_ttl == "1h":
        raw_kwargs["cache_creation_1h_tokens"] = input_upper
    raw_bound = raw_cost_usd_micro(**raw_kwargs)
    return credits_charged(feature=feature, cost_usd_micro=raw_bound)


__all__ = [
    "ANTHROPIC_PROTOCOL_TOKEN_MARGIN",
    "CLAUDE_CONTEXT_TOKEN_UPPER_BOUND",
    "AnthropicRequestAdmissionError",
    "anthropic_request_credit_upper_bound",
]
