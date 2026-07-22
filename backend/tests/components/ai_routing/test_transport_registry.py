from __future__ import annotations

from dataclasses import replace

import pytest

from app.components.ai_routing.contracts import ExecutionMode
from app.components.ai_routing.model_registry import DEFAULT_MODEL_REGISTRY
from app.components.ai_routing.task_registry import DEFAULT_TASK_REGISTRY
from app.components.ai_routing.transport_registry import (
    DEFAULT_TRANSPORT_ADAPTER_REGISTRY,
    TransportAdapterRegistry,
    TransportRegistryError,
)


def test_default_transport_registry_closes_every_executable_task_route() -> None:
    DEFAULT_TRANSPORT_ADAPTER_REGISTRY.validate_control_plane(
        DEFAULT_MODEL_REGISTRY,
        DEFAULT_TASK_REGISTRY,
    )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("provider", "other", "provider"),
        ("runtime", "other", "runtime"),
        ("credential_strategy", "other", "credential_strategy"),
        ("supported_modes", frozenset({ExecutionMode.BATCH}), "execution_mode"),
    ),
)
def test_transport_registry_rejects_contract_drift(
    field: str,
    value: object,
    match: str,
) -> None:
    registration = DEFAULT_TRANSPORT_ADAPTER_REGISTRY.registrations[0]
    registry = TransportAdapterRegistry(
        (replace(registration, **{field: value}),)
    )

    with pytest.raises(TransportRegistryError, match=match):
        registry.validate_control_plane(
            DEFAULT_MODEL_REGISTRY,
            DEFAULT_TASK_REGISTRY,
        )


def test_transport_registry_rejects_missing_contract() -> None:
    registration = DEFAULT_TRANSPORT_ADAPTER_REGISTRY.registrations[0]
    registry = TransportAdapterRegistry(
        (replace(registration, transport_contract="other_messages_v1"),)
    )

    with pytest.raises(TransportRegistryError, match="no approved adapter"):
        registry.validate_control_plane(
            DEFAULT_MODEL_REGISTRY,
            DEFAULT_TASK_REGISTRY,
        )
