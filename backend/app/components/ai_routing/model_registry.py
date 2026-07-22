"""Immutable, authorized model-deployment registry.

Raw provider model identifiers and their token prices intentionally live in
this file only.  An API model catalogue is discovery data, not authorization;
only deployments declared here may be returned by the routing policy.
"""

from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType
from typing import Iterable, Mapping

from .contracts import (
    Capability,
    DataClassification,
    ExecutionMode,
    LifecycleState,
    ModelDeployment,
    RiskClass,
    TaskKey,
    TokenPricing,
)

MODEL_REGISTRY_VERSION = "anthropic-direct-2026-07-22.v2"

# Stable application IDs.  These are not provider model IDs and are safe to
# persist in policy/config records.
ANTHROPIC_HAIKU_4_5 = "anthropic.messages.haiku-4-5-20251001"
ANTHROPIC_SONNET_4_5 = "anthropic.messages.sonnet-4-5-20250929"
ANTHROPIC_SONNET_4_6 = "anthropic.messages.sonnet-4-6"


class ModelRegistryError(ValueError):
    pass


class UnknownDeploymentError(ModelRegistryError):
    pass


class ModelRegistry:
    """Read-only deployment and alias index with fail-closed resolution."""

    __slots__ = ("version", "_aliases", "_by_id", "_deployments")

    def __init__(self, *, version: str, deployments: Iterable[ModelDeployment]) -> None:
        ordered = tuple(sorted(deployments, key=lambda item: item.deployment_id))
        if not version.strip():
            raise ModelRegistryError("model registry version must be non-empty")
        if not ordered:
            raise ModelRegistryError(
                "model registry must contain at least one deployment"
            )

        by_id: dict[str, ModelDeployment] = {}
        aliases: dict[str, str] = {}
        pricing_ids: set[str] = set()
        for deployment in ordered:
            deployment_id = deployment.deployment_id.strip()
            normalized_id = self._normalize(deployment_id)
            if (
                not deployment_id
                or deployment_id in by_id
                or normalized_id in aliases
                or any(self._normalize(existing) == normalized_id for existing in by_id)
            ):
                raise ModelRegistryError(
                    f"duplicate or empty deployment id: {deployment_id!r}"
                )
            by_id[deployment_id] = deployment
            self._validate_deployment(deployment)
            if deployment.pricing is not None:
                pricing_id = deployment.pricing.pricing_id.strip()
                if pricing_id in pricing_ids:
                    raise ModelRegistryError(
                        f"pricing identity is not unique: {pricing_id!r}"
                    )
                pricing_ids.add(pricing_id)

            for identifier in (deployment.model_id, *deployment.aliases):
                normalized = self._normalize(identifier)
                if not normalized:
                    raise ModelRegistryError(f"empty alias on {deployment_id}")
                if normalized in aliases or any(
                    self._normalize(existing) == normalized for existing in by_id
                ):
                    owner = aliases.get(normalized, normalized)
                    raise ModelRegistryError(
                        f"identifier {identifier!r} is not unique ({owner}, {deployment_id})"
                    )
                aliases[normalized] = deployment_id

        for deployment in ordered:
            replacement = deployment.replacement_deployment_id
            if replacement is not None and replacement not in by_id:
                raise ModelRegistryError(
                    f"replacement {replacement!r} for {deployment.deployment_id} is not registered"
                )
            if replacement == deployment.deployment_id:
                raise ModelRegistryError(
                    f"deployment {deployment.deployment_id} cannot replace itself"
                )

        for deployment in ordered:
            seen: set[str] = set()
            current = deployment
            while current.replacement_deployment_id is not None:
                if current.deployment_id in seen:
                    raise ModelRegistryError(
                        f"replacement cycle includes {current.deployment_id}"
                    )
                seen.add(current.deployment_id)
                current = by_id[current.replacement_deployment_id]

        self.version = version
        self._deployments = ordered
        self._by_id: Mapping[str, ModelDeployment] = MappingProxyType(by_id)
        self._aliases: Mapping[str, str] = MappingProxyType(aliases)

    @staticmethod
    def _normalize(identifier: str) -> str:
        return (identifier or "").strip().lower()

    @staticmethod
    def _validate_deployment(deployment: ModelDeployment) -> None:
        required_strings = {
            "deployment_id": deployment.deployment_id,
            "provider": deployment.provider,
            "endpoint": deployment.endpoint,
            "runtime": deployment.runtime,
            "transport_contract": deployment.transport_contract,
            "retention_policy": deployment.retention_policy,
            "credential_strategy": deployment.credential_strategy,
        }
        empty = [name for name, value in required_strings.items() if not value.strip()]
        if empty:
            raise ModelRegistryError(
                f"{deployment.deployment_id!r} has empty contract fields: "
                + ", ".join(empty)
            )
        if not deployment.model_id.strip():
            raise ModelRegistryError(
                f"{deployment.deployment_id} has no exact model id"
            )
        if deployment.context_tokens <= 0 or deployment.max_output_tokens <= 0:
            raise ModelRegistryError(
                f"{deployment.deployment_id} has invalid token limits"
            )
        if deployment.quality_tier <= 0 or deployment.latency_rank <= 0:
            raise ModelRegistryError(
                f"{deployment.deployment_id} has invalid routing ranks"
            )
        if not deployment.supported_modes or not deployment.capabilities:
            raise ModelRegistryError(
                f"{deployment.deployment_id} has no usable contract"
            )
        if not deployment.regions or any(not region.strip() for region in deployment.regions):
            raise ModelRegistryError(
                f"{deployment.deployment_id} has no valid inference regions"
            )
        if deployment.lifecycle is LifecycleState.ACTIVE:
            pricing = deployment.pricing
            if pricing is None:
                raise ModelRegistryError(
                    f"active deployment {deployment.deployment_id} is unpriced"
                )
            rates = (
                pricing.input_per_million,
                pricing.output_per_million,
                pricing.cache_write_5m_per_million,
                pricing.cache_write_1h_per_million,
                pricing.cache_read_per_million,
                pricing.batch_input_per_million,
                pricing.batch_output_per_million,
            )
            if (
                not pricing.pricing_id.strip()
                or pricing.currency != "USD"
                or any(rate <= 0 for rate in rates)
            ):
                raise ModelRegistryError(
                    f"active deployment {deployment.deployment_id} has incomplete exact pricing"
                )
            multiplier = pricing.us_inference_multiplier
            if multiplier is not None and multiplier <= 0:
                raise ModelRegistryError(
                    f"{deployment.deployment_id} has an invalid US inference multiplier"
                )
            if "us" in deployment.regions and multiplier is None:
                raise ModelRegistryError(
                    f"{deployment.deployment_id} authorizes US inference without exact pricing"
                )

    @property
    def deployments(self) -> tuple[ModelDeployment, ...]:
        return self._deployments

    def get(self, deployment_id: str) -> ModelDeployment | None:
        return self._by_id.get((deployment_id or "").strip())

    def resolve(self, identifier: str) -> ModelDeployment:
        cleaned = (identifier or "").strip()
        direct = self._by_id.get(cleaned)
        if direct is not None:
            return direct
        deployment_id = self._aliases.get(self._normalize(cleaned))
        if deployment_id is None:
            raise UnknownDeploymentError(
                f"unknown or unauthorized deployment alias: {identifier!r}"
            )
        return self._by_id[deployment_id]


_ALL_DATA = frozenset(DataClassification)
_MESSAGES_MODES = frozenset(
    {ExecutionMode.SYNC, ExecutionMode.STREAM, ExecutionMode.BATCH}
)
_MESSAGES_CAPABILITIES = frozenset(
    {
        Capability.TEXT,
        Capability.VISION,
        Capability.TOOLS,
        Capability.STRICT_STRUCTURED_OUTPUT,
        Capability.CITATIONS,
        Capability.STREAMING,
        Capability.PROMPT_CACHING,
        Capability.EXTENDED_THINKING,
    }
)
_CITATION_SCHEMA_CONFLICT = (
    frozenset({Capability.CITATIONS, Capability.STRICT_STRUCTURED_OUTPUT}),
)

_HAIKU_PRICING = TokenPricing(
    pricing_id="anthropic.claude-haiku-4-5-20251001.2026-07-22",
    currency="USD",
    input_per_million=Decimal("1.00"),
    output_per_million=Decimal("5.00"),
    cache_write_5m_per_million=Decimal("1.25"),
    cache_write_1h_per_million=Decimal("2.00"),
    cache_read_per_million=Decimal("0.10"),
    batch_input_per_million=Decimal("0.50"),
    batch_output_per_million=Decimal("2.50"),
)
_SONNET_4_5_PRICING = TokenPricing(
    pricing_id="anthropic.claude-sonnet-4-5-20250929.2026-07-22",
    currency="USD",
    input_per_million=Decimal("3.00"),
    output_per_million=Decimal("15.00"),
    cache_write_5m_per_million=Decimal("3.75"),
    cache_write_1h_per_million=Decimal("6.00"),
    cache_read_per_million=Decimal("0.30"),
    batch_input_per_million=Decimal("1.50"),
    batch_output_per_million=Decimal("7.50"),
)
_SONNET_4_6_PRICING = TokenPricing(
    pricing_id="anthropic.claude-sonnet-4-6.2026-07-22",
    currency="USD",
    input_per_million=Decimal("3.00"),
    output_per_million=Decimal("15.00"),
    cache_write_5m_per_million=Decimal("3.75"),
    cache_write_1h_per_million=Decimal("6.00"),
    cache_read_per_million=Decimal("0.30"),
    batch_input_per_million=Decimal("1.50"),
    batch_output_per_million=Decimal("7.50"),
    us_inference_multiplier=Decimal("1.10"),
)

_CHAT_AGENT_TASKS = frozenset(
    {
        TaskKey.GENERAL_CHAT_ORCHESTRATION,
        TaskKey.ROLE_CHAT_ORCHESTRATION,
        TaskKey.AUTONOMOUS_RECRUITING_ORCHESTRATION,
    }
)

DEFAULT_MODEL_REGISTRY = ModelRegistry(
    version=MODEL_REGISTRY_VERSION,
    deployments=(
        ModelDeployment(
            deployment_id=ANTHROPIC_HAIKU_4_5,
            provider="anthropic",
            endpoint="messages",
            runtime="anthropic_api",
            transport_contract="anthropic_messages_v1",
            model_id="claude-haiku-4-5-20251001",
            aliases=("haiku", "haiku-4-5", "claude-haiku-4-5", "fast"),
            supported_modes=_MESSAGES_MODES,
            capabilities=_MESSAGES_CAPABILITIES,
            capability_conflicts=_CITATION_SCHEMA_CONFLICT,
            context_tokens=200_000,
            max_output_tokens=64_000,
            lifecycle=LifecycleState.ACTIVE,
            replacement_deployment_id=None,
            pricing=_HAIKU_PRICING,
            allowed_data_classes=_ALL_DATA,
            regions=frozenset({"global"}),
            retention_policy="anthropic_commercial_api",
            credential_strategy="organization_or_platform_api_key",
            max_risk=RiskClass.CRITICAL,
            evaluated_tasks=_CHAT_AGENT_TASKS
            | frozenset(
                {
                    TaskKey.SEARCH_RERANK,
                    TaskKey.CV_PARSE_SYNC,
                    TaskKey.CV_PARSE_BATCH,
                    TaskKey.CV_SCORE_PRESCREEN,
                }
            ),
            quality_tier=1,
            latency_rank=1,
        ),
        ModelDeployment(
            deployment_id=ANTHROPIC_SONNET_4_5,
            provider="anthropic",
            endpoint="messages",
            runtime="anthropic_api",
            transport_contract="anthropic_messages_v1",
            model_id="claude-sonnet-4-5-20250929",
            aliases=("sonnet-4-5", "claude-sonnet-4-5"),
            supported_modes=_MESSAGES_MODES,
            capabilities=_MESSAGES_CAPABILITIES,
            capability_conflicts=_CITATION_SCHEMA_CONFLICT,
            context_tokens=200_000,
            max_output_tokens=64_000,
            lifecycle=LifecycleState.ACTIVE,
            replacement_deployment_id=None,
            pricing=_SONNET_4_5_PRICING,
            allowed_data_classes=_ALL_DATA,
            regions=frozenset({"global"}),
            retention_policy="anthropic_commercial_api",
            credential_strategy="organization_or_platform_api_key",
            max_risk=RiskClass.CRITICAL,
            evaluated_tasks=_CHAT_AGENT_TASKS
            | frozenset(
                {
                    TaskKey.ASSESSMENT_AGENT_CHAT,
                    TaskKey.SEARCH_GROUNDING,
                }
            ),
            quality_tier=2,
            latency_rank=2,
        ),
        ModelDeployment(
            deployment_id=ANTHROPIC_SONNET_4_6,
            provider="anthropic",
            endpoint="messages",
            runtime="anthropic_api",
            transport_contract="anthropic_messages_v1",
            model_id="claude-sonnet-4-6",
            aliases=("sonnet", "sonnet-4-6"),
            supported_modes=_MESSAGES_MODES,
            capabilities=_MESSAGES_CAPABILITIES | frozenset({Capability.LONG_CONTEXT}),
            capability_conflicts=_CITATION_SCHEMA_CONFLICT,
            context_tokens=1_000_000,
            max_output_tokens=128_000,
            lifecycle=LifecycleState.ACTIVE,
            replacement_deployment_id=None,
            pricing=_SONNET_4_6_PRICING,
            allowed_data_classes=_ALL_DATA,
            regions=frozenset({"global", "us"}),
            retention_policy="anthropic_commercial_api",
            credential_strategy="organization_or_platform_api_key",
            max_risk=RiskClass.CRITICAL,
            evaluated_tasks=_CHAT_AGENT_TASKS
            | frozenset(
                {
                    TaskKey.SEARCH_PARSE,
                    TaskKey.SEARCH_GROUNDING,
                    TaskKey.ASSESSMENT_AGENT_CHAT,
                    TaskKey.CV_SCORE_HOLISTIC,
                    TaskKey.ARCHETYPE_SYNTHESIS,
                    TaskKey.PAIRWISE_JUDGE,
                }
            ),
            quality_tier=2,
            latency_rank=2,
        ),
    ),
)
