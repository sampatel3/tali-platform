"""Exact provider-cost accounting for routed Anthropic attempts.

Routed calls are priced from the immutable deployment registry that authorized
the call.  Legacy calls keep using the historical pricing service; this module
only activates when adapter-owned ``metadata.ai_routing`` provenance exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_UP
from typing import Any

from .contracts import ModelDeployment, TokenPricing
from .model_registry import DEFAULT_MODEL_REGISTRY, ModelRegistry


class RoutedPricingContractError(ValueError):
    """Route provenance cannot be mapped to one authorized price contract."""

    provider_not_called = True


class RoutedPricingReceiptError(ValueError):
    """A completed provider receipt cannot be priced exactly.

    Unlike contract errors, this never claims that provider transport was
    skipped: it is raised only while interpreting an actual usage receipt.
    """


class RoutedPricingOutcomeError(RuntimeError):
    """A billed provider response violated its admitted route contract."""


@dataclass(frozen=True, slots=True)
class RoutedPricingReceipt:
    """Actual model/region price evidence from a completed response."""

    pricing: RoutedPricing
    model_id: str
    model_mismatch: bool
    region_mismatch: bool
    region_evidence_missing: bool

    @property
    def contract_mismatch(self) -> bool:
        return (
            self.model_mismatch or self.region_mismatch or self.region_evidence_missing
        )


@dataclass(frozen=True, slots=True)
class RoutedPricing:
    """Resolved immutable price contract for one physical provider attempt."""

    deployment: ModelDeployment
    registry_version: str
    region: str
    service_tier: str

    @property
    def pricing(self) -> TokenPricing:
        pricing = self.deployment.pricing
        if pricing is None:  # Registry validation should make this unreachable.
            raise RoutedPricingContractError(
                f"deployment {self.deployment.deployment_id!r} is unpriced"
            )
        return pricing

    @property
    def pricing_id(self) -> str:
        return self.pricing.pricing_id

    def cost_usd_micro(self, usage: Any) -> int:
        """Price actual provider usage, rounding up once at receipt level."""

        return routed_cost_usd_micro(
            usage=usage,
            deployment=self.deployment,
            region=self.region,
            service_tier=self.service_tier,
        )


def resolve_routed_pricing(
    metering: Any,
    *,
    model: str,
    inference_geo: Any = None,
    service_tier: str = "standard",
    registry: ModelRegistry = DEFAULT_MODEL_REGISTRY,
) -> RoutedPricing | None:
    """Resolve adapter-owned route metadata to an exact pricing contract.

    ``None`` means this is a legacy/non-routed call and its historical pricing
    path must remain untouched.  Once ``ai_routing`` is present, malformed or
    stale provenance fails closed before provider transport rather than quietly
    falling back to a second price catalogue.
    """

    if not isinstance(metering, dict):
        return None
    metadata = metering.get("metadata")
    if not isinstance(metadata, dict) or "ai_routing" not in metadata:
        return None
    route = metadata.get("ai_routing")
    if not isinstance(route, dict):
        raise RoutedPricingContractError("ai_routing metadata must be a mapping")

    deployment_id = str(route.get("deployment_id") or "").strip()
    deployment = registry.get(deployment_id)
    if deployment is None:
        raise RoutedPricingContractError(
            f"routed deployment {deployment_id!r} is not in the active registry"
        )
    if deployment.provider != "anthropic":
        raise RoutedPricingContractError(
            "Anthropic metering received a non-Anthropic routed deployment"
        )
    if str(model or "").strip() != deployment.model_id:
        raise RoutedPricingContractError(
            "routed provider model differs from deployment price authority"
        )
    route_registry_version = str(route.get("registry_version") or "").strip()
    if route_registry_version != registry.version:
        raise RoutedPricingContractError(
            "routed registry version differs from the active price authority"
        )

    normalized_tier = str(service_tier or "standard").strip().lower()
    if normalized_tier not in {"standard", "batch"}:
        raise RoutedPricingContractError(
            f"unsupported routed Anthropic service tier {service_tier!r}"
        )
    provider_region = _normalize_provider_region(inference_geo)
    route_region_value = route.get("region")
    if route_region_value is None:
        route_region = provider_region
    else:
        route_region = _normalize_region(route_region_value)
        if route_region != provider_region:
            raise RoutedPricingContractError(
                "routed region differs from the provider inference geography"
            )
    if route_region not in deployment.regions:
        raise RoutedPricingContractError(
            f"deployment {deployment_id!r} is not authorized in {route_region!r}"
        )

    result = RoutedPricing(
        deployment=deployment,
        registry_version=registry.version,
        region=route_region,
        service_tier=normalized_tier,
    )
    supplied_pricing_id = route.get("pricing_id")
    if (
        supplied_pricing_id is not None
        and str(supplied_pricing_id) != result.pricing_id
    ):
        raise RoutedPricingContractError(
            "routed pricing id differs from the deployment price authority"
        )

    # Persist the exact price provenance beside the route metadata.  The
    # wrapper owns this projection; callers cannot supply ai_routing metadata
    # through the routed adapter.
    enriched_route = {
        **route,
        "region": result.region,
        "pricing_id": result.pricing_id,
        "billed_pricing_id": result.pricing_id,
        "pricing_registry_version": result.registry_version,
        "pricing_region": result.region,
        "pricing_service_tier": result.service_tier,
        "cost_authority": "ai_routing.model_registry",
    }
    metering["metadata"] = {**metadata, "ai_routing": enriched_route}
    return result


def resolve_routed_pricing_receipt(
    metering: dict[str, Any],
    *,
    routed_pricing: RoutedPricing,
    response: Any,
    registry: ModelRegistry = DEFAULT_MODEL_REGISTRY,
) -> RoutedPricingReceipt:
    """Resolve actual response identity before writing cost receipts.

    Registered provider substitutions are still priced exactly and recorded as
    route-contract mismatches.  Unknown model/region evidence remains
    unsettled: inventing a price would be less safe than retaining the durable
    hold for reconciliation.
    """

    actual_model = str(getattr(response, "model", None) or "").strip()
    if not actual_model:
        raise RoutedPricingReceiptError(
            "routed Anthropic response omitted its executed model identity"
        )
    if registry.version != routed_pricing.registry_version:
        raise RoutedPricingReceiptError(
            "pricing registry changed between route admission and receipt"
        )
    try:
        actual_deployment = registry.resolve(actual_model)
    except Exception as exc:
        raise RoutedPricingReceiptError(
            f"executed model {actual_model!r} is outside the pricing registry"
        ) from exc
    if actual_deployment.model_id != actual_model:
        raise RoutedPricingReceiptError(
            "provider response used an alias rather than an exact model identity"
        )
    if actual_deployment.provider != "anthropic":
        raise RoutedPricingReceiptError(
            "Anthropic response resolved to a non-Anthropic deployment"
        )

    usage = getattr(response, "usage", None)
    raw_actual_region = getattr(usage, "inference_geo", None)
    region_evidence_missing = (
        raw_actual_region is None and routed_pricing.region == "us"
    )
    if raw_actual_region is None:
        actual_region = routed_pricing.region
    else:
        try:
            actual_region = _normalize_region(raw_actual_region)
        except RoutedPricingContractError as exc:
            raise RoutedPricingReceiptError(str(exc)) from exc
    actual_pricing = RoutedPricing(
        deployment=actual_deployment,
        registry_version=registry.version,
        region=actual_region,
        service_tier=routed_pricing.service_tier,
    )
    # Force price completeness checks before any settlement write, while
    # preserving the fact that provider transport has already happened.
    try:
        actual_pricing_id = actual_pricing.pricing_id
    except RoutedPricingContractError as exc:
        raise RoutedPricingReceiptError(str(exc)) from exc
    model_mismatch = (
        actual_deployment.deployment_id != routed_pricing.deployment.deployment_id
    )
    region_mismatch = actual_region != routed_pricing.region

    metadata = metering.get("metadata")
    route = metadata.get("ai_routing") if isinstance(metadata, dict) else None
    if not isinstance(route, dict):
        raise RoutedPricingReceiptError("routed pricing provenance disappeared")
    metering["metadata"] = {
        **metadata,
        "ai_routing": {
            **route,
            "executed_deployment_id": actual_deployment.deployment_id,
            "executed_model_id": actual_model,
            "billed_pricing_id": actual_pricing_id,
            "pricing_region": actual_region,
            "model_mismatch": model_mismatch,
            "region_mismatch": region_mismatch,
            "region_evidence_missing": region_evidence_missing,
        },
    }
    return RoutedPricingReceipt(
        pricing=actual_pricing,
        model_id=actual_model,
        model_mismatch=model_mismatch,
        region_mismatch=region_mismatch,
        region_evidence_missing=region_evidence_missing,
    )


def routed_cost_usd_micro(
    *,
    usage: Any,
    deployment: ModelDeployment,
    region: str = "global",
    service_tier: str = "standard",
) -> int:
    """Compute actual routed cost solely from ``deployment.pricing``.

    Anthropic's input, cache-read, and cache-write counters are disjoint.  A
    cache write's nested 1-hour count is split from the total; an absent split
    retains the established all-5-minute interpretation.  Batch discounts are
    taken from the registry's batch rates and applied uniformly to cache token
    classes, matching Anthropic's Messages Batch pricing contract.
    """

    pricing = deployment.pricing
    if pricing is None:
        raise RoutedPricingReceiptError(
            f"deployment {deployment.deployment_id!r} is unpriced"
        )
    try:
        normalized_region = _normalize_region(region)
    except RoutedPricingContractError as exc:
        raise RoutedPricingReceiptError(str(exc)) from exc
    if normalized_region not in deployment.regions:
        raise RoutedPricingReceiptError(
            f"deployment {deployment.deployment_id!r} is not authorized in "
            f"{normalized_region!r}"
        )
    normalized_tier = str(service_tier or "standard").strip().lower()
    if normalized_tier == "batch":
        input_rate = pricing.batch_input_per_million
        output_rate = pricing.batch_output_per_million
        batch_ratio = input_rate / pricing.input_per_million
    elif normalized_tier == "standard":
        input_rate = pricing.input_per_million
        output_rate = pricing.output_per_million
        batch_ratio = Decimal("1")
    else:
        raise RoutedPricingReceiptError(
            f"unsupported routed Anthropic service tier {service_tier!r}"
        )

    input_tokens = _usage_int(usage, "input_tokens")
    output_tokens = _usage_int(usage, "output_tokens")
    cache_read_tokens = _usage_int(usage, "cache_read_input_tokens")
    cache_creation_tokens = _usage_int(usage, "cache_creation_input_tokens")
    cache_creation = getattr(usage, "cache_creation", None)
    raw_1h = (
        getattr(cache_creation, "ephemeral_1h_input_tokens", None)
        if cache_creation is not None
        else None
    )
    if raw_1h is None:
        cache_creation_1h_tokens = 0
        cache_creation_5m_tokens = cache_creation_tokens
    else:
        cache_creation_1h_tokens = _coerce_nonnegative_int(
            raw_1h, field="ephemeral_1h_input_tokens"
        )
        if cache_creation_1h_tokens > cache_creation_tokens:
            raise RoutedPricingReceiptError(
                "Anthropic 1-hour cache-creation tokens exceed total "
                "cache-creation tokens"
            )
        cache_creation_5m_tokens = cache_creation_tokens - cache_creation_1h_tokens

    cost = (
        Decimal(input_tokens) * input_rate
        + Decimal(output_tokens) * output_rate
        + Decimal(cache_read_tokens) * pricing.cache_read_per_million * batch_ratio
        + Decimal(cache_creation_5m_tokens)
        * pricing.cache_write_5m_per_million
        * batch_ratio
        + Decimal(cache_creation_1h_tokens)
        * pricing.cache_write_1h_per_million
        * batch_ratio
    )
    if normalized_region == "us":
        multiplier = pricing.us_inference_multiplier
        if multiplier is None:
            raise RoutedPricingReceiptError(
                f"deployment {deployment.deployment_id!r} has no US price multiplier"
            )
        cost *= multiplier

    # USD per million tokens is numerically micro-USD per token.
    return int(cost.quantize(Decimal("1"), rounding=ROUND_UP))


def _normalize_provider_region(inference_geo: Any) -> str:
    if inference_geo is None:
        return "global"
    return _normalize_region(inference_geo)


def _normalize_region(value: Any) -> str:
    normalized = str(value or "global").strip().lower()
    if normalized not in {"global", "us"}:
        raise RoutedPricingContractError(
            f"unsupported Anthropic inference geography {value!r}"
        )
    return normalized


def _usage_int(usage: Any, field: str) -> int:
    value = getattr(usage, field, 0)
    if value is None:
        value = 0
    return _coerce_nonnegative_int(value, field=field)


def _coerce_nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RoutedPricingReceiptError(
            f"Anthropic usage field {field!r} is not an exact integer"
        )
    if value < 0:
        raise RoutedPricingReceiptError(
            f"Anthropic usage field {field!r} cannot be negative"
        )
    return value


__all__ = [
    "RoutedPricing",
    "RoutedPricingContractError",
    "RoutedPricingOutcomeError",
    "RoutedPricingReceipt",
    "RoutedPricingReceiptError",
    "resolve_routed_pricing",
    "resolve_routed_pricing_receipt",
    "routed_cost_usd_micro",
]
