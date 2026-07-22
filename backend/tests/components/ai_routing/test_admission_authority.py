from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.components.ai_routing.admission import (
    ProviderAttemptAdmissionError,
    admit_attempt,
)
from app.components.ai_routing.anthropic_estimation import AnthropicRequestEstimate
from app.components.ai_routing.execution import RoutingAttribution
from app.components.ai_routing.execution_types import PlannedPhysicalAttempt
from app.components.ai_routing.model_registry import (
    ANTHROPIC_HAIKU_4_5,
    DEFAULT_MODEL_REGISTRY,
)


class _Reservation:
    external_ref = "test-reservation"

    def as_metering_payload(self) -> dict[str, str]:
        return {"external_ref": "test-reservation"}


@pytest.mark.parametrize(
    ("decision_requires_authority", "caller_meter_value"),
    ((True, False), (False, True)),
)
def test_admission_uses_immutable_decision_authority(
    monkeypatch,
    decision_requires_authority: bool,
    caller_meter_value: bool,
) -> None:
    captured: dict = {}

    def _reserve(**kwargs):
        captured.update(kwargs)
        return _Reservation()

    monkeypatch.setattr(
        "app.components.ai_routing.admission.reserve_provider_usage",
        _reserve,
    )
    deployment = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_HAIKU_4_5)
    assert deployment is not None
    execution = SimpleNamespace(
        attribution=RoutingAttribution(organization_id=1, role_id=2),
        decision=SimpleNamespace(
            feature="agent_autonomous",
            route_id="route-id",
            require_role_authority=decision_requires_authority,
        ),
        request=SimpleNamespace(region=None),
        selected_deployment=deployment,
        invocation_id="invocation-id",
        operation="autonomous_cycle",
        authorize_estimated_attempt=lambda **_kwargs: None,
    )

    admit_attempt(
        execution,
        PlannedPhysicalAttempt(
            ordinal=1,
            iteration_ordinal=1,
            attempt_in_iteration=1,
            deployment=deployment,
            trace_id="attempt-trace",
        ),
        {"role_id": 2, "require_role_authority": caller_meter_value},
        request_estimate=AnthropicRequestEstimate(
            input_tokens=10,
            output_tokens=10,
        ),
    )

    assert captured["require_role_authority"] is decision_requires_authority


def test_admission_canonicalizes_omitted_user_and_rejects_explicit_mismatch(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.components.ai_routing.admission.reserve_provider_usage",
        lambda **_kwargs: _Reservation(),
    )
    deployment = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_HAIKU_4_5)
    assert deployment is not None
    execution = SimpleNamespace(
        attribution=RoutingAttribution(
            organization_id=1,
            user_id=9,
            role_id=2,
        ),
        decision=SimpleNamespace(
            feature="agent_chat",
            route_id="route-id",
            require_role_authority=False,
        ),
        request=SimpleNamespace(region=None),
        invocation_id="invocation-id",
        operation="nested_search",
        authorize_estimated_attempt=lambda **_kwargs: None,
    )
    plan = PlannedPhysicalAttempt(
        ordinal=1,
        iteration_ordinal=1,
        attempt_in_iteration=1,
        deployment=deployment,
        trace_id="attempt-trace",
    )
    estimate = AnthropicRequestEstimate(input_tokens=10, output_tokens=10)

    admission = admit_attempt(
        execution,
        plan,
        {"organization_id": 1, "role_id": 2},
        request_estimate=estimate,
    )
    assert admission.metering["user_id"] == 9

    with pytest.raises(ProviderAttemptAdmissionError, match="user"):
        admit_attempt(
            execution,
            plan,
            {"organization_id": 1, "role_id": 2, "user_id": 10},
            request_estimate=estimate,
        )
