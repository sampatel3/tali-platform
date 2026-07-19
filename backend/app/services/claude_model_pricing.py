"""Known Claude model families and fail-closed outbound priceability."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
import json
from typing import Any, Optional

from ..platform.config import settings


class UnpriceableClaudeModelError(ValueError):
    """A provider model has no exact internal rate contract."""


# USD per million input/output tokens, keyed by snapshot-free family alias.
# Verified 2026-07-18 against the official table:
# https://platform.claude.com/docs/en/about-claude/pricing
# Verify again before admitting a new alias. Cache reads are 0.10x input,
# 5-minute writes 1.25x, and 1-hour writes 2.00x; pricing_service applies
# those policies separately from these base input/output rates.
_MODEL_RATES: dict[str, tuple[Decimal, Decimal]] = {
    # Operational aliases selected by this codebase.
    "claude-haiku-4-5": (Decimal("1"), Decimal("5")),
    "claude-sonnet-4-5": (Decimal("3"), Decimal("15")),
    "claude-sonnet-4-6": (Decimal("3"), Decimal("15")),
    "claude-opus-4-5": (Decimal("5"), Decimal("25")),
    # Historical families remain priceable for durable usage reconciliation.
    "claude-sonnet-4": (Decimal("3"), Decimal("15")),
    "claude-opus-4-1": (Decimal("15"), Decimal("75")),
    "claude-opus-4": (Decimal("15"), Decimal("75")),
    "claude-3-haiku": (Decimal("0.25"), Decimal("1.25")),
    "claude-3-5-haiku": (Decimal("0.80"), Decimal("4")),
    "claude-3-5-sonnet": (Decimal("3"), Decimal("15")),
    "claude-3-7-sonnet": (Decimal("3"), Decimal("15")),
    "claude-3-opus": (Decimal("15"), Decimal("75")),
}

# Outbound admission is deliberately narrower than the historical price table.
# A retired model still needs an exact rate so durable usage can be reconciled,
# but having a historical rate must never make that model callable again. Add
# an id here only after its availability and price have both been reviewed.
_OUTBOUND_MODEL_IDS = frozenset(
    {
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-5",
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-6",
        "claude-opus-4-5",
        "claude-opus-4-5-20251101",
    }
)

ANTHROPIC_BATCH_MAX_REQUESTS = 100_000
ANTHROPIC_BATCH_MAX_BYTES = 256 * 1024 * 1024


def _strip_snapshot_suffix(model: str) -> str:
    """Strip a trailing ``-YYYYMMDD`` snapshot tag from a model id."""

    value = str(model or "").strip()
    parts = value.rsplit("-", 1)
    if (
        len(parts) == 2
        and len(parts[1]) == 8
        and parts[1].isascii()
        and parts[1].isdigit()
    ):
        return parts[0]
    return value


def require_priceable_claude_model(model: str | None) -> str:
    """Return the rated family only for a reviewed outbound provider id."""

    if type(model) is not str or not model or model != model.strip():
        raise UnpriceableClaudeModelError(
            "Claude model is not enabled for outbound use; review its availability "
            "and pricing before enabling it"
        )
    configured = model
    family = _strip_snapshot_suffix(configured)
    if configured not in _OUTBOUND_MODEL_IDS:
        raise UnpriceableClaudeModelError(
            "Claude model is not enabled for outbound use; review its availability "
            "and pricing before enabling it"
        )
    return family


def is_priceable_claude_model(model: str | None) -> bool:
    try:
        require_priceable_claude_model(model)
    except UnpriceableClaudeModelError:
        return False
    return True


def materialize_priceable_batch_requests(
    requests: Any,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Materialize one iterable once and validate every outbound batch entry."""

    if (
        isinstance(requests, (str, bytes, bytearray, Mapping))
        or not isinstance(requests, Iterable)
    ):
        raise ValueError("batch requests must be an iterable of objects")
    try:
        iterator = iter(requests)
    except Exception as exc:
        raise ValueError("batch requests could not be materialized") from exc
    materialized: list[dict[str, Any]] = []
    serialized_bytes = len(b'{"requests":[]}')
    request_models: dict[str, str] = {}
    for index, request in enumerate(iterator):
        if index >= ANTHROPIC_BATCH_MAX_REQUESTS:
            raise ValueError("batch request count exceeds Anthropic limit")
        if type(request) is not dict:
            raise ValueError("each batch request must be an object")
        custom_id = request.get("custom_id")
        if (
            not isinstance(custom_id, str)
            or not custom_id
            or custom_id in request_models
        ):
            raise ValueError("batch requests require unique non-empty custom_id values")
        params = request.get("params")
        if type(params) is not dict:
            raise ValueError("each batch request params value must be an object")
        model = params.get("model")
        if not isinstance(model, str):
            raise ValueError("each batch request model must be a string")
        require_priceable_claude_model(model)
        try:
            encoded = json.dumps(
                request,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("batch request must be JSON serializable") from exc
        serialized_bytes += len(encoded) + (1 if materialized else 0)
        if serialized_bytes > ANTHROPIC_BATCH_MAX_BYTES:
            raise ValueError("batch request payload exceeds Anthropic size limit")
        materialized.append(request)
        request_models[custom_id] = model
    if not materialized:
        raise ValueError("batch requests must be non-empty")
    return materialized, request_models


def validate_priceable_batch_requests(requests: Any) -> dict[str, str]:
    """Compatibility validator; callers submitting use the materialized form."""

    _, request_models = materialize_priceable_batch_requests(requests)
    return request_models


def _resolve_model_rates(model: Optional[str]) -> tuple[Decimal, Decimal]:
    """Resolve exact rates; only model-less legacy calculations use defaults."""

    if not str(model or "").strip():
        return (
            Decimal(str(settings.CLAUDE_INPUT_COST_PER_MILLION_USD)),
            Decimal(str(settings.CLAUDE_OUTPUT_COST_PER_MILLION_USD)),
        )
    family = _strip_snapshot_suffix(str(model).strip())
    try:
        return _MODEL_RATES[family]
    except KeyError as exc:
        raise UnpriceableClaudeModelError(
            "Claude model has no configured historical pricing"
        ) from exc


__all__ = [
    "UnpriceableClaudeModelError",
    "ANTHROPIC_BATCH_MAX_BYTES",
    "ANTHROPIC_BATCH_MAX_REQUESTS",
    "_MODEL_RATES",
    "_OUTBOUND_MODEL_IDS",
    "_resolve_model_rates",
    "_strip_snapshot_suffix",
    "is_priceable_claude_model",
    "materialize_priceable_batch_requests",
    "require_priceable_claude_model",
    "validate_priceable_batch_requests",
]
