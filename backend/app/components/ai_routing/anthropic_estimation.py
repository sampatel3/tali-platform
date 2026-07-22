"""Content-free planning estimates for Anthropic Messages requests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_UP
from typing import Any

from .contracts import InputCostBasis
from .model_registry import ModelDeployment


@dataclass(frozen=True, slots=True)
class AnthropicRequestEstimate:
    """Safe numeric projection of a request; prompt content is never retained."""

    input_tokens: int
    output_tokens: int
    input_cost_basis: InputCostBasis = InputCostBasis.STANDARD

    def __post_init__(self) -> None:
        if self.input_tokens < 0:
            raise ValueError("input_tokens must be non-negative")
        if self.output_tokens <= 0:
            raise ValueError("output_tokens must be positive")
        if not isinstance(self.input_cost_basis, InputCostBasis):
            raise TypeError("input_cost_basis must be an InputCostBasis")


def conservative_input_tokens(kwargs: dict[str, Any]) -> int:
    """Return a safe byte-level upper bound for request input tokens.

    Anthropic tokenization operates over UTF-8 bytes; one token cannot encode
    less than a byte. Counting every serialized byte as a token plus structural
    overhead intentionally overestimates normal language by roughly 3–4x.
    """

    payload = {
        key: kwargs[key]
        for key in ("system", "messages", "tools", "tool_choice")
        if key in kwargs and kwargs[key] is not None
    }
    encoded = json.dumps(
        payload,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    collection_items = sum(
        len(value) if isinstance(value, (list, tuple)) else 1
        for value in payload.values()
    )
    return len(encoded) + 128 + (64 * collection_items)


def _cache_cost_basis(value: Any) -> InputCostBasis:
    """Return the highest cache-write price class present in a JSON-like value."""

    highest = InputCostBasis.STANDARD
    if isinstance(value, dict):
        control = value.get("cache_control")
        if isinstance(control, dict):
            ttl = str(control.get("ttl") or "5m").strip().lower()
            # Unknown future TTLs are priced at today's highest registered
            # cache-write class. The provider may reject them, but admission
            # must never under-reserve if it accepts them.
            highest = (
                InputCostBasis.CACHE_WRITE_5M
                if ttl == "5m"
                else InputCostBasis.CACHE_WRITE_1H
            )
        for child in value.values():
            nested = _cache_cost_basis(child)
            if nested is InputCostBasis.CACHE_WRITE_1H:
                return nested
            if nested is InputCostBasis.CACHE_WRITE_5M:
                highest = nested
    elif isinstance(value, (list, tuple)):
        for child in value:
            nested = _cache_cost_basis(child)
            if nested is InputCostBasis.CACHE_WRITE_1H:
                return nested
            if nested is InputCostBasis.CACHE_WRITE_5M:
                highest = nested
    return highest


def estimate_anthropic_messages(
    *,
    messages: Any,
    max_tokens: int,
    system: Any = None,
    tools: Any = None,
    tool_choice: Any = None,
) -> AnthropicRequestEstimate:
    """Estimate the exact request shape before model selection and persistence.

    Every serialized UTF-8 byte is counted as one token, plus structural
    overhead. If any block is cacheable, all estimated input is priced at the
    request's highest cache-write rate. This intentionally over-reserves on a
    cache hit/mixed request; the metering boundary settles actual usage later.
    """

    kwargs = {
        "messages": messages,
        "max_tokens": max_tokens,
        "system": system,
        "tools": tools,
        "tool_choice": tool_choice,
    }
    try:
        output_tokens = int(max_tokens)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_tokens must be a positive integer") from exc
    payload = {
        key: value
        for key, value in kwargs.items()
        if key in {"messages", "system", "tools", "tool_choice"}
        and value is not None
    }
    return AnthropicRequestEstimate(
        input_tokens=conservative_input_tokens(payload),
        output_tokens=output_tokens,
        input_cost_basis=_cache_cost_basis(payload),
    )


def conservative_raw_cost_micro_usd(
    deployment: ModelDeployment,
    *,
    input_tokens: int,
    output_tokens: int,
    input_cost_basis: InputCostBasis = InputCostBasis.STANDARD,
    region: str = "global",
) -> int:
    """Price an upper-bound request without assuming a cache hit."""

    pricing = deployment.pricing
    if pricing is None:
        raise ValueError(
            f"deployment {deployment.deployment_id!r} has no exact pricing"
        )
    input_rate = {
        InputCostBasis.STANDARD: pricing.input_per_million,
        InputCostBasis.CACHE_WRITE_5M: max(
            pricing.input_per_million, pricing.cache_write_5m_per_million
        ),
        InputCostBasis.CACHE_WRITE_1H: max(
            pricing.input_per_million, pricing.cache_write_1h_per_million
        ),
    }[input_cost_basis]
    raw = Decimal(input_tokens) * input_rate + Decimal(
        output_tokens
    ) * pricing.output_per_million
    if region.strip().lower() == "us" and pricing.us_inference_multiplier is not None:
        raw *= pricing.us_inference_multiplier
    return int(raw.quantize(Decimal("1"), rounding=ROUND_UP))


__all__ = [
    "AnthropicRequestEstimate",
    "conservative_input_tokens",
    "conservative_raw_cost_micro_usd",
    "estimate_anthropic_messages",
]
