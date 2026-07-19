"""Exact Voyage embedding rates and outbound model admission."""

from __future__ import annotations

from decimal import Decimal, ROUND_UP
from typing import Optional


# $ per million input tokens. Older accessible models remain exact so existing
# indexes can be queried/rebuilt without a forced vector-space migration.
VOYAGE_MODEL_RATES: dict[str, Decimal] = {
    "voyage-4-large": Decimal("0.12"),
    "voyage-4": Decimal("0.06"),
    "voyage-4-lite": Decimal("0.02"),
    "voyage-code-3": Decimal("0.18"),
    "voyage-3-large": Decimal("0.18"),
    "voyage-3.5": Decimal("0.06"),
    "voyage-3.5-lite": Decimal("0.02"),
    "voyage-3": Decimal("0.06"),
    "voyage-3-lite": Decimal("0.02"),
    "voyage-multilingual-2": Decimal("0.12"),
    "voyage-large-2-instruct": Decimal("0.12"),
    "voyage-large-2": Decimal("0.12"),
    "voyage-2": Decimal("0.10"),
    "voyage-code-2": Decimal("0.12"),
    "voyage-finance-2": Decimal("0.12"),
    "voyage-law-2": Decimal("0.12"),
}


class UnpriceableVoyageModelError(ValueError):
    """Outbound Voyage model has no exact reviewed embedding rate."""


def is_voyage_model(model: Optional[str]) -> bool:
    """Recognize historical Voyage rows for provider reconciliation."""

    return bool(model) and model.lower().strip().startswith("voyage")


def is_priceable_voyage_model(model: Optional[str]) -> bool:
    """Return whether an outbound text-embedding model has an exact rate."""

    return bool(model) and model.lower().strip() in VOYAGE_MODEL_RATES


def require_priceable_voyage_model(model: Optional[str]) -> str:
    """Normalize an exact outbound model or fail without echoing input."""

    if type(model) is not str or not model:
        raise UnpriceableVoyageModelError(
            "Voyage embedding model has no exact reviewed pricing"
        )
    normalized = model.lower().strip()
    if normalized not in VOYAGE_MODEL_RATES:
        raise UnpriceableVoyageModelError(
            "Voyage embedding model has no exact reviewed pricing"
        )
    return normalized


def voyage_cost_micro(*, model: Optional[str], input_tokens: int) -> int:
    """Price only exact known Voyage rows; unresolved history fails visibly."""

    tokens = int(input_tokens or 0)
    if tokens < 0:
        raise ValueError("input_tokens must be non-negative")
    rate = VOYAGE_MODEL_RATES[require_priceable_voyage_model(model)]
    return int((Decimal(tokens) * rate).quantize(Decimal("1"), rounding=ROUND_UP))


__all__ = [
    "UnpriceableVoyageModelError",
    "VOYAGE_MODEL_RATES",
    "is_priceable_voyage_model",
    "is_voyage_model",
    "require_priceable_voyage_model",
    "voyage_cost_micro",
]
