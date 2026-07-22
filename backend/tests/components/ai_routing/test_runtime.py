from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.components.ai_routing.contracts import (
    NoEligibleDeploymentError,
    PlanningErrorCode,
    ReasonCode,
    RoutePlanningError,
    TaskKey,
)
from app.components.ai_routing.model_registry import (
    ANTHROPIC_HAIKU_4_5,
    ANTHROPIC_SONNET_4_5,
    ANTHROPIC_SONNET_4_6,
)
from app.components.ai_routing.runtime import (
    RoutingRuntimeConfigurationError,
    build_route_request,
    plan_route,
    route_behavior_fingerprint,
    validate_routing_configuration,
    validated_role_model_override,
)


@dataclass
class FakeRoutingSettings:
    AI_ROUTER_MODEL_OVERRIDES_JSON: str = ""
    resolved_claude_model: str = "claude-haiku-4-5-20251001"
    resolved_agent_autonomous_model: str = "claude-haiku-4-5-20251001"


def test_build_request_generates_uuid_and_root_lineage() -> None:
    request = build_route_request(
        TaskKey.SEARCH_RERANK,
        settings_obj=FakeRoutingSettings(),
        environ={},
    )

    assert str(UUID(request.invocation_id)) == request.invocation_id
    assert request.root_invocation_id == request.invocation_id
    assert request.parent_invocation_id is None
    assert request.override_alias is None


def test_non_uuid_lineage_is_rejected_before_policy_or_persistence() -> None:
    with pytest.raises(ValueError, match="UUID"):
        build_route_request(
            TaskKey.SEARCH_RERANK,
            settings_obj=FakeRoutingSettings(),
            environ={},
            invocation_id="human-readable-turn-id",
        )


def test_explicit_override_wins_over_central_and_legacy_configuration() -> None:
    configured = FakeRoutingSettings(
        AI_ROUTER_MODEL_OVERRIDES_JSON=(
            '{"general_chat.orchestration":"anthropic.messages.sonnet-4-6"}'
        ),
    )

    decision = plan_route(
        TaskKey.GENERAL_CHAT_ORCHESTRATION,
        settings_obj=configured,
        explicit_model_override="claude-sonnet-4-5",
    )

    assert decision.selected_deployment_id == ANTHROPIC_SONNET_4_5
    assert decision.selected_model_id == "claude-sonnet-4-5-20250929"
    assert decision.reason_codes == (ReasonCode.VALIDATED_OVERRIDE,)


def test_central_task_override_wins_over_legacy_setting() -> None:
    configured = FakeRoutingSettings(
        AI_ROUTER_MODEL_OVERRIDES_JSON=(
            '{"role_chat.orchestration":"anthropic.messages.sonnet-4-6"}'
        ),
    )

    decision = plan_route(
        TaskKey.ROLE_CHAT_ORCHESTRATION,
        settings_obj=configured,
    )

    assert decision.selected_deployment_id == ANTHROPIC_SONNET_4_6
    assert decision.reason_codes == (ReasonCode.VALIDATED_OVERRIDE,)


def test_legacy_settings_and_search_environment_preserve_parity() -> None:
    configured = FakeRoutingSettings(
        resolved_claude_model="sonnet-4-5",
        resolved_agent_autonomous_model="sonnet",
    )

    role = plan_route(TaskKey.ROLE_CHAT_ORCHESTRATION, settings_obj=configured)
    autonomous = plan_route(
        TaskKey.AUTONOMOUS_RECRUITING_ORCHESTRATION,
        settings_obj=configured,
    )
    parser = plan_route(
        TaskKey.SEARCH_PARSE,
        settings_obj=configured,
        environ={"CLAUDE_SEARCH_PARSER_MODEL": "sonnet"},
    )
    grounding = plan_route(
        TaskKey.SEARCH_GROUNDING,
        settings_obj=configured,
        environ={"CLAUDE_GROUNDING_MODEL": "sonnet"},
    )

    assert role.selected_deployment_id == ANTHROPIC_SONNET_4_5
    assert autonomous.selected_deployment_id == ANTHROPIC_SONNET_4_6
    assert parser.selected_deployment_id == ANTHROPIC_SONNET_4_6
    assert parser.reason_codes == (ReasonCode.VALIDATED_OVERRIDE,)
    assert grounding.selected_deployment_id == ANTHROPIC_SONNET_4_6
    assert grounding.reason_codes == (ReasonCode.VALIDATED_OVERRIDE,)


def test_profile_default_is_used_when_task_has_no_legacy_selector() -> None:
    decision = plan_route(
        TaskKey.SEARCH_RERANK,
        settings_obj=FakeRoutingSettings(),
        environ={},
    )

    assert decision.selected_deployment_id == ANTHROPIC_HAIKU_4_5
    assert decision.reason_codes[0] is ReasonCode.PRIMARY_POLICY


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "[]",
        '{"not.a.task":"haiku"}',
        '{"candidate_search.rerank":""}',
        '{"candidate_search.rerank":4}',
        '{"candidate_search.rerank":"haiku","candidate_search.rerank":"sonnet"}',
    ],
)
def test_malformed_central_override_configuration_fails_closed(raw: str) -> None:
    with pytest.raises(RoutingRuntimeConfigurationError):
        plan_route(
            TaskKey.SEARCH_RERANK,
            settings_obj=FakeRoutingSettings(AI_ROUTER_MODEL_OVERRIDES_JSON=raw),
            environ={},
        )


@pytest.mark.parametrize(
    "identifier",
    ["not-a-registered-model", "claude-3-5-haiku-latest"],
)
def test_unknown_or_retired_override_fails_before_execution(identifier: str) -> None:
    with pytest.raises(RoutePlanningError) as exc_info:
        plan_route(
            TaskKey.SEARCH_RERANK,
            settings_obj=FakeRoutingSettings(),
            explicit_model_override=identifier,
        )

    assert exc_info.value.code is PlanningErrorCode.INVALID_OVERRIDE


def test_central_unknown_override_fails_before_execution() -> None:
    configured = FakeRoutingSettings(
        AI_ROUTER_MODEL_OVERRIDES_JSON=(
            '{"candidate_search.rerank":"not-a-registered-model"}'
        ),
    )

    with pytest.raises(RoutePlanningError) as exc_info:
        plan_route(TaskKey.SEARCH_RERANK, settings_obj=configured, environ={})

    assert exc_info.value.code is PlanningErrorCode.INVALID_OVERRIDE


def test_legacy_retired_override_fails_before_execution() -> None:
    configured = FakeRoutingSettings(
        resolved_claude_model="claude-3-5-haiku-latest",
    )

    with pytest.raises(RoutePlanningError) as exc_info:
        plan_route(TaskKey.ROLE_CHAT_ORCHESTRATION, settings_obj=configured)

    assert exc_info.value.code is PlanningErrorCode.INVALID_OVERRIDE


def test_task_incompatible_override_fails_closed() -> None:
    with pytest.raises(NoEligibleDeploymentError) as exc_info:
        plan_route(
            TaskKey.SEARCH_GROUNDING,
            settings_obj=FakeRoutingSettings(),
            environ={},
            explicit_model_override="haiku",
        )

    assert exc_info.value.code is PlanningErrorCode.NO_ELIGIBLE_DEPLOYMENT


def test_lineage_and_prior_decision_pin_are_preserved() -> None:
    configured = FakeRoutingSettings()
    conversation_id = str(uuid4())
    request_id = str(uuid4())
    turn_1_id = str(uuid4())
    turn_2_id = str(uuid4())
    first = plan_route(
        TaskKey.GENERAL_CHAT_ORCHESTRATION,
        settings_obj=configured,
        invocation_id=turn_1_id,
        root_invocation_id=conversation_id,
        parent_invocation_id=request_id,
    )
    pinned = plan_route(
        TaskKey.GENERAL_CHAT_ORCHESTRATION,
        settings_obj=configured,
        invocation_id=turn_2_id,
        root_invocation_id=conversation_id,
        parent_invocation_id=turn_1_id,
        pinned_deployment_id=first.selected_deployment_id,
    )

    assert first.root_invocation_id == conversation_id
    assert first.parent_invocation_id == request_id
    assert first.pin_key == turn_1_id
    assert pinned.selected_deployment_id == first.selected_deployment_id
    assert pinned.reason_codes == (ReasonCode.PINNED_DEPLOYMENT,)
    assert pinned.root_invocation_id == conversation_id
    assert pinned.parent_invocation_id == turn_1_id
    assert pinned.pin_key == turn_2_id


def test_behavior_fingerprint_is_stable_and_route_sensitive() -> None:
    default = FakeRoutingSettings()
    sonnet = FakeRoutingSettings(
        AI_ROUTER_MODEL_OVERRIDES_JSON=(
            '{"general_chat.orchestration":"anthropic.messages.sonnet-4-6"}'
        ),
    )

    first = route_behavior_fingerprint(TaskKey.GENERAL_CHAT_ORCHESTRATION, default)
    second = route_behavior_fingerprint(TaskKey.GENERAL_CHAT_ORCHESTRATION, default)
    changed = route_behavior_fingerprint(TaskKey.GENERAL_CHAT_ORCHESTRATION, sonnet)

    assert first == second
    assert changed != first


def test_reserved_future_task_remains_fail_closed() -> None:
    with pytest.raises(RoutePlanningError) as exc_info:
        plan_route(
            TaskKey.ASSESSMENT_AGENT_CHAT,
            settings_obj=FakeRoutingSettings(),
        )

    assert exc_info.value.code is PlanningErrorCode.UNKNOWN_TASK


def test_raw_string_task_is_rejected() -> None:
    with pytest.raises(TypeError, match="TaskKey"):
        build_route_request(  # type: ignore[arg-type]
            "candidate_search.rerank",
            settings_obj=FakeRoutingSettings(),
        )


def test_role_model_value_policy_accepts_only_authorized_task_values() -> None:
    assert (
        validated_role_model_override(
            TaskKey.AUTONOMOUS_RECRUITING_ORCHESTRATION,
            "claude-sonnet-4-5",
        )
        == "claude-sonnet-4-5"
    )

    with pytest.raises(RoutingRuntimeConfigurationError):
        validated_role_model_override(
            TaskKey.AUTONOMOUS_RECRUITING_ORCHESTRATION,
            "not-a-registered-model",
        )
    with pytest.raises(RoutingRuntimeConfigurationError, match="does not permit"):
        validated_role_model_override(TaskKey.SEARCH_PARSE, "sonnet")


def test_full_runtime_validation_covers_legacy_search_selectors() -> None:
    with pytest.raises(
        RoutingRuntimeConfigurationError,
        match="legacy model selector for candidate_search.parse",
    ):
        validate_routing_configuration(
            FakeRoutingSettings(),
            environ={"CLAUDE_SEARCH_PARSER_MODEL": "haiku"},
        )
