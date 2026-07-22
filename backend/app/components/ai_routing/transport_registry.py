"""Approved transport/credential factories for immutable route deployments.

Feature modules never select a provider SDK, API key strategy, or adapter.
They request the native Messages compatibility surface for an already-planned
route; this registry resolves the exact transport contract declared by the
deployment. A future provider can implement the same compatibility boundary
without changing task orchestration code.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .adapters.anthropic_messages import RoutedAnthropicClient
from .contracts import ExecutionMode
from .execution import RouteExecution, RouteExecutionError
from .model_registry import ModelDeployment, ModelRegistry
from .task_registry import TaskRegistry


class TransportRegistryError(RouteExecutionError):
    """A route references no exact, approved runtime adapter."""

    provider_not_called = True


TransportFactory = Callable[[RouteExecution], Any]


@dataclass(frozen=True, slots=True)
class TransportAdapterRegistration:
    transport_contract: str
    provider: str
    runtime: str
    credential_strategy: str
    supported_modes: frozenset[ExecutionMode]
    factory: TransportFactory

    def __post_init__(self) -> None:
        for label, value in (
            ("transport_contract", self.transport_contract),
            ("provider", self.provider),
            ("runtime", self.runtime),
            ("credential_strategy", self.credential_strategy),
        ):
            if not value.strip():
                raise ValueError(f"transport registration {label} is empty")
        if not self.supported_modes:
            raise ValueError("transport registration must support an execution mode")
        object.__setattr__(self, "supported_modes", frozenset(self.supported_modes))


class TransportAdapterRegistry:
    """Immutable adapter index keyed by the deployment transport contract."""

    def __init__(self, registrations: tuple[TransportAdapterRegistration, ...]):
        by_contract: dict[str, TransportAdapterRegistration] = {}
        for registration in registrations:
            key = registration.transport_contract.strip()
            if key in by_contract:
                raise ValueError(f"duplicate transport adapter contract: {key}")
            by_contract[key] = registration
        if not by_contract:
            raise ValueError("transport adapter registry cannot be empty")
        self._by_contract: Mapping[str, TransportAdapterRegistration] = (
            MappingProxyType(by_contract)
        )

    @property
    def registrations(self) -> tuple[TransportAdapterRegistration, ...]:
        return tuple(
            self._by_contract[key] for key in sorted(self._by_contract)
        )

    def resolve(self, execution: RouteExecution) -> TransportAdapterRegistration:
        deployment = execution.selected_deployment
        registration = self.resolve_deployment(
            deployment,
            execution_mode=execution.decision.execution_mode,
        )
        return registration

    def resolve_deployment(
        self,
        deployment: ModelDeployment,
        *,
        execution_mode: ExecutionMode,
    ) -> TransportAdapterRegistration:
        """Resolve an exact deployment contract without constructing a client."""

        registration = self._by_contract.get(deployment.transport_contract)
        if registration is None:
            raise TransportRegistryError(
                f"no approved adapter for {deployment.transport_contract!r}"
            )
        mismatches: list[str] = []
        if registration.provider != deployment.provider:
            mismatches.append("provider")
        if registration.runtime != deployment.runtime:
            mismatches.append("runtime")
        if registration.credential_strategy != deployment.credential_strategy:
            mismatches.append("credential_strategy")
        if execution_mode not in registration.supported_modes:
            mismatches.append("execution_mode")
        if mismatches:
            raise TransportRegistryError(
                f"adapter contract mismatch for {deployment.deployment_id}: "
                + ", ".join(mismatches)
            )
        return registration

    def validate_control_plane(
        self,
        model_registry: ModelRegistry,
        task_registry: TaskRegistry,
    ) -> None:
        """Fail startup if any executable task route lacks an exact adapter."""

        for profile in task_registry.profiles:
            deployment_ids = (
                *profile.candidate_deployment_ids,
                *profile.fallback_deployment_ids,
            )
            for deployment_id in deployment_ids:
                deployment = model_registry.get(deployment_id)
                if deployment is None:
                    raise TransportRegistryError(
                        f"task {profile.key.value} references unknown deployment "
                        f"{deployment_id!r}"
                    )
                try:
                    self.resolve_deployment(
                        deployment,
                        execution_mode=profile.execution_mode,
                    )
                except TransportRegistryError as exc:
                    raise TransportRegistryError(
                        f"task {profile.key.value} has no executable transport: {exc}"
                    ) from exc

    def connect(self, execution: RouteExecution) -> Any:
        return self.resolve(execution).factory(execution)


def _anthropic_messages_factory(execution: RouteExecution) -> RoutedAnthropicClient:
    # Credential and SDK construction remain behind this adapter boundary.
    # SDK retries are disabled because the route executor owns every physical
    # attempt, reservation, backoff, and ambiguity decision.
    from ...services.claude_client_resolver import get_metered_client

    transport = get_metered_client(
        organization_id=execution.attribution.organization_id,
        timeout=max(execution.decision.limits.latency_slo_ms / 1000.0, 0.001),
        max_retries=0,
    )
    return RoutedAnthropicClient(transport, execution)


DEFAULT_TRANSPORT_ADAPTER_REGISTRY = TransportAdapterRegistry(
    (
        TransportAdapterRegistration(
            transport_contract="anthropic_messages_v1",
            provider="anthropic",
            runtime="anthropic_api",
            credential_strategy="organization_or_platform_api_key",
            supported_modes=frozenset(
                {ExecutionMode.SYNC, ExecutionMode.STREAM}
            ),
            factory=_anthropic_messages_factory,
        ),
    )
)

# Import-time registry construction remains pure. Startup calls this explicit
# closure check before accepting traffic, while tests can validate custom
# registries without constructing provider SDK clients.


def routed_messages_client(
    execution: RouteExecution,
    *,
    registry: TransportAdapterRegistry = DEFAULT_TRANSPORT_ADAPTER_REGISTRY,
) -> Any:
    """Return the only approved Messages surface for this route decision."""

    return registry.connect(execution)


__all__ = [
    "DEFAULT_TRANSPORT_ADAPTER_REGISTRY",
    "TransportAdapterRegistration",
    "TransportAdapterRegistry",
    "TransportRegistryError",
    "routed_messages_client",
]
