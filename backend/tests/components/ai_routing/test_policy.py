from __future__ import annotations

import ast
import uuid
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.components.ai_routing.contracts import (
    Capability,
    DataClassification,
    ExecutionMode,
    ExclusionCode,
    FallbackClass,
    LifecycleState,
    NoEligibleDeploymentError,
    PlanningErrorCode,
    ReasonCode,
    RiskClass,
    RoutePlanningError,
    RouteRequest,
    TaskKey,
    WorkflowDefinition,
    WorkflowKey,
)
from app.components.ai_routing.model_registry import (
    ANTHROPIC_HAIKU_4_5,
    ANTHROPIC_SONNET_4_5,
    ANTHROPIC_SONNET_4_6,
    DEFAULT_MODEL_REGISTRY,
    ModelRegistry,
    ModelRegistryError,
)
from app.components.ai_routing.policy import RoutingPolicy
from app.components.ai_routing.snapshots import decision_snapshot, request_snapshot
from app.components.ai_routing.task_registry import DEFAULT_TASK_REGISTRY, TaskRegistry
from app.components.ai_routing.validation import (
    RegistryValidationError,
    ValidationCode,
    validate_workflow_graph,
)

PARITY = (
    (TaskKey.GENERAL_CHAT_ORCHESTRATION, ANTHROPIC_HAIKU_4_5),
    (TaskKey.ROLE_CHAT_ORCHESTRATION, ANTHROPIC_HAIKU_4_5),
    (TaskKey.AUTONOMOUS_RECRUITING_ORCHESTRATION, ANTHROPIC_HAIKU_4_5),
    (TaskKey.SEARCH_PARSE, ANTHROPIC_SONNET_4_6),
    (TaskKey.SEARCH_RERANK, ANTHROPIC_HAIKU_4_5),
    (TaskKey.SEARCH_GROUNDING, ANTHROPIC_SONNET_4_6),
)
_INVOCATION_NAMESPACE = uuid.UUID("0d931612-a7a2-57cf-9b50-1aef0a290f81")


def _id(label: str) -> str:
    return str(uuid.uuid5(_INVOCATION_NAMESPACE, label))


def _profile(task: TaskKey):
    profile = DEFAULT_TASK_REGISTRY.get(task)
    assert profile is not None
    return profile


def _task_registry(profile, *, workflows=None) -> TaskRegistry:
    return TaskRegistry(
        version="test-tasks.v1",
        profiles=(profile,),
        workflows=workflows
        or (WorkflowDefinition(profile.workflow, "test-workflow.v1"),),
    )


def _policy(profile, deployments=None) -> RoutingPolicy:
    registry = (
        DEFAULT_MODEL_REGISTRY
        if deployments is None
        else ModelRegistry(version="test-models.v1", deployments=deployments)
    )
    return RoutingPolicy(
        model_registry=registry,
        task_registry=_task_registry(profile),
        policy_version="test-policy.v1",
    )


def _clone(deployment_id: str, **changes):
    base = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_HAIKU_4_5)
    assert base is not None
    values = {
        "deployment_id": deployment_id,
        "model_id": f"test-model-{deployment_id}",
        "aliases": (f"alias-{deployment_id}",),
        "pricing": replace(
            base.pricing,
            pricing_id=f"test-pricing-{deployment_id}",
        ),
    }
    values.update(changes)
    return replace(base, **values)


def _codes(
    exc: NoEligibleDeploymentError, deployment_id: str
) -> tuple[ExclusionCode, ...]:
    return next(
        item.codes for item in exc.exclusions if item.deployment_id == deployment_id
    )


@pytest.mark.parametrize(("task", "deployment_id"), PARITY)
def test_parity_routes(task: TaskKey, deployment_id: str) -> None:
    decision = RoutingPolicy().plan(
        RouteRequest(
            task=task,
            invocation_id=_id(f"invocation:{task.value}"),
            estimated_input_tokens=1_000,
            estimated_output_tokens=100,
        )
    )

    assert decision.selected_deployment_id == deployment_id
    assert decision.attempts[0].deployment_id == deployment_id
    assert decision.reason_codes[:2] == (
        ReasonCode.PRIMARY_POLICY,
        ReasonCode.LOWEST_EXPECTED_COST,
    )
    assert str(uuid.UUID(decision.route_id)) == decision.route_id
    assert len(decision.behavior_fingerprint) == 24


def test_role_authority_profile_minimum_cannot_be_weakened() -> None:
    autonomous = RoutingPolicy().plan(
        RouteRequest(
            task=TaskKey.AUTONOMOUS_RECRUITING_ORCHESTRATION,
            invocation_id=_id("autonomous-authority-minimum"),
            require_role_authority=False,
        )
    )
    strengthened_search = RoutingPolicy().plan(
        RouteRequest(
            task=TaskKey.SEARCH_RERANK,
            invocation_id=_id("search-authority-strengthened"),
            require_role_authority=True,
        )
    )

    assert autonomous.require_role_authority is True
    assert strengthened_search.require_role_authority is True


def test_role_authority_constraint_changes_behavior_fingerprint() -> None:
    without_authority = RoutingPolicy().plan(
        RouteRequest(TaskKey.SEARCH_RERANK, _id("search-without-authority"))
    )
    with_authority = RoutingPolicy().plan(
        RouteRequest(
            TaskKey.SEARCH_RERANK,
            _id("search-with-authority"),
            require_role_authority=True,
        )
    )

    assert without_authority.behavior_fingerprint != with_authority.behavior_fingerprint


def test_role_authority_constraint_is_preserved_in_content_free_snapshots() -> None:
    request = RouteRequest(
        TaskKey.SEARCH_RERANK,
        _id("search-authority-snapshot"),
        require_role_authority=True,
    )
    decision = RoutingPolicy().plan(request)

    assert request_snapshot(request)["require_role_authority"] is True
    assert decision_snapshot(decision)["require_role_authority"] is True


def test_task_keys_cover_later_transport_phases_but_fail_closed_without_profiles() -> (
    None
):
    assert TaskKey.CV_PARSE_BATCH.value == "cv_ingestion.parse_batch"
    assert TaskKey.ASSESSMENT_AGENT_CHAT.value == "candidate_assessment.agent_chat"
    with pytest.raises(RoutePlanningError) as captured:
        RoutingPolicy().plan(RouteRequest(TaskKey.CV_PARSE_BATCH, _id("batch-1")))
    assert captured.value.code is PlanningErrorCode.UNKNOWN_TASK


def test_override_alias_is_validated_and_preserves_role_model_parity() -> None:
    alias = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_SONNET_4_5).aliases[-1]
    decision = RoutingPolicy().plan(
        RouteRequest(
            TaskKey.ROLE_CHAT_ORCHESTRATION, _id("role-turn-1"), override_alias=alias
        )
    )

    assert decision.selected_deployment_id == ANTHROPIC_SONNET_4_5
    assert decision.reason_codes == (ReasonCode.VALIDATED_OVERRIDE,)


def test_unknown_and_unevaluated_overrides_fail_closed() -> None:
    with pytest.raises(RoutePlanningError) as unknown:
        RoutingPolicy().plan(
            RouteRequest(
                TaskKey.ROLE_CHAT_ORCHESTRATION,
                _id("role-turn-2"),
                override_alias="bogus",
            )
        )
    assert unknown.value.code is PlanningErrorCode.INVALID_OVERRIDE

    alias = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_SONNET_4_5).aliases[0]
    with pytest.raises(NoEligibleDeploymentError) as unevaluated:
        RoutingPolicy().plan(
            RouteRequest(TaskKey.SEARCH_PARSE, _id("search-1"), override_alias=alias)
        )
    assert ExclusionCode.NOT_TASK_EVALUATED in _codes(
        unevaluated.value, ANTHROPIC_SONNET_4_5
    )


def test_route_identity_is_deterministic_and_caller_scoped() -> None:
    policy = RoutingPolicy()
    request = RouteRequest(TaskKey.SEARCH_PARSE, _id("same-invocation"), 2_000, 200)
    first = policy.plan(request)
    second = policy.plan(request)
    other = policy.plan(replace(request, invocation_id=_id("different-invocation")))

    assert first == second
    assert first.route_id != other.route_id
    assert first.behavior_fingerprint == other.behavior_fingerprint
    promoted = RoutingPolicy(policy_version="promoted-policy.v2").plan(request)
    assert first.behavior_fingerprint != promoted.behavior_fingerprint


def test_pin_reuses_a_prior_selected_deployment_and_rejects_conflicts() -> None:
    policy = RoutingPolicy()
    initial = policy.plan(
        RouteRequest(
            TaskKey.ROLE_CHAT_ORCHESTRATION,
            _id("role-turn-pin"),
            override_alias="sonnet-4-6",
        )
    )
    pinned = policy.plan(
        RouteRequest(
            TaskKey.ROLE_CHAT_ORCHESTRATION,
            _id("role-turn-pin"),
            pinned_deployment_id=initial.selected_deployment_id,
        )
    )
    assert pinned.selected_deployment_id == ANTHROPIC_SONNET_4_6
    assert pinned.reason_codes == (ReasonCode.PINNED_DEPLOYMENT,)
    assert pinned.pin_key == _id("role-turn-pin")

    with pytest.raises(RoutePlanningError) as conflict:
        policy.plan(
            RouteRequest(
                TaskKey.ROLE_CHAT_ORCHESTRATION,
                _id("role-turn-pin"),
                override_alias="haiku",
                pinned_deployment_id=ANTHROPIC_SONNET_4_6,
            )
        )
    assert conflict.value.code is PlanningErrorCode.CONFLICTING_SELECTION


def test_additional_capability_and_capability_conflict_are_enforced() -> None:
    with pytest.raises(NoEligibleDeploymentError) as missing:
        RoutingPolicy().plan(
            RouteRequest(
                TaskKey.ROLE_CHAT_ORCHESTRATION,
                _id("long-context-haiku"),
                additional_capabilities=frozenset({Capability.LONG_CONTEXT}),
            )
        )
    assert ExclusionCode.CAPABILITY in _codes(missing.value, ANTHROPIC_HAIKU_4_5)

    with pytest.raises(NoEligibleDeploymentError) as conflict:
        RoutingPolicy().plan(
            RouteRequest(
                TaskKey.SEARCH_GROUNDING,
                _id("citation-schema-conflict"),
                additional_capabilities=frozenset(
                    {Capability.STRICT_STRUCTURED_OUTPUT}
                ),
            )
        )
    assert ExclusionCode.CAPABILITY_CONFLICT in _codes(
        conflict.value, ANTHROPIC_SONNET_4_6
    )


def test_context_mode_data_risk_region_provider_and_cost_filters() -> None:
    constrained = _clone(
        "test.constrained",
        context_tokens=50_000,
        supported_modes=frozenset({ExecutionMode.SYNC}),
        allowed_data_classes=frozenset({DataClassification.PUBLIC}),
        max_risk=RiskClass.LOW,
    )
    policy = _policy(
        _profile(TaskKey.GENERAL_CHAT_ORCHESTRATION),
        (*DEFAULT_MODEL_REGISTRY.deployments, constrained),
    )
    with pytest.raises(NoEligibleDeploymentError) as rejected:
        policy.plan(
            RouteRequest(
                TaskKey.GENERAL_CHAT_ORCHESTRATION,
                _id("constrained-override"),
                estimated_input_tokens=60_000,
                override_alias=constrained.aliases[0],
            )
        )
    codes = _codes(rejected.value, constrained.deployment_id)
    assert {
        ExclusionCode.EXECUTION_MODE,
        ExclusionCode.CONTEXT_LIMIT,
        ExclusionCode.DATA_CLASSIFICATION,
        ExclusionCode.RISK,
    }.issubset(codes)

    with pytest.raises(NoEligibleDeploymentError) as region:
        RoutingPolicy().plan(
            RouteRequest(TaskKey.ROLE_CHAT_ORCHESTRATION, _id("region"), region="us")
        )
    assert ExclusionCode.REGION in _codes(region.value, ANTHROPIC_HAIKU_4_5)

    with pytest.raises(NoEligibleDeploymentError) as provider:
        RoutingPolicy().plan(
            RouteRequest(
                TaskKey.SEARCH_PARSE,
                _id("provider"),
                provider_denylist=frozenset({"anthropic"}),
            )
        )
    assert ExclusionCode.PROVIDER_DENIED in _codes(provider.value, ANTHROPIC_SONNET_4_6)

    with pytest.raises(NoEligibleDeploymentError) as cost:
        RoutingPolicy().plan(
            RouteRequest(
                TaskKey.SEARCH_PARSE, _id("cost"), 1_000, 100, max_cost_micro_usd=1
            )
        )
    assert ExclusionCode.COST_CEILING in _codes(cost.value, ANTHROPIC_SONNET_4_6)


def test_multiple_approved_candidates_are_ranked_by_cost_then_latency() -> None:
    profile = replace(
        _profile(TaskKey.ROLE_CHAT_ORCHESTRATION),
        candidate_deployment_ids=(ANTHROPIC_SONNET_4_6, ANTHROPIC_HAIKU_4_5),
    )
    decision = _policy(profile).plan(
        RouteRequest(profile.key, _id("cost-ranking"), 10_000, 1_000)
    )
    assert decision.selected_deployment_id == ANTHROPIC_HAIKU_4_5
    assert ReasonCode.LOWEST_EXPECTED_COST in decision.reason_codes

    same_price_slower = _clone("test.slower", latency_rank=9)
    profile = replace(
        profile,
        candidate_deployment_ids=(ANTHROPIC_HAIKU_4_5, same_price_slower.deployment_id),
    )
    decision = _policy(
        profile, (*DEFAULT_MODEL_REGISTRY.deployments, same_price_slower)
    ).plan(RouteRequest(profile.key, _id("latency-ranking"), 10_000, 1_000))
    assert decision.selected_deployment_id == ANTHROPIC_HAIKU_4_5
    assert ReasonCode.LOWEST_LATENCY in decision.reason_codes


def test_fallback_attempts_follow_validated_profile_order() -> None:
    fallback_2 = _clone("test.fallback-2")
    fallback_1 = _clone(
        "test.fallback-1",
        replacement_deployment_id=fallback_2.deployment_id,
    )
    primary = _clone(
        "test.primary",
        replacement_deployment_id=fallback_1.deployment_id,
    )
    profile = replace(
        _profile(TaskKey.ROLE_CHAT_ORCHESTRATION),
        candidate_deployment_ids=(primary.deployment_id,),
        fallback_deployment_ids=(
            fallback_1.deployment_id,
            fallback_2.deployment_id,
        ),
        fallback_classes=frozenset({FallbackClass.REGISTERED_REPLACEMENT}),
        max_attempts_per_iteration=3,
    )
    decision = _policy(profile, (primary, fallback_1, fallback_2)).plan(
        RouteRequest(profile.key, _id("fallback-order"), 1_000, 100)
    )

    assert tuple(item.deployment_id for item in decision.attempts) == (
        primary.deployment_id,
        fallback_1.deployment_id,
        fallback_2.deployment_id,
    )
    assert tuple(item.ordinal for item in decision.attempts) == (1, 2, 3)
    assert decision.attempts[1].reason is ReasonCode.PROFILE_FALLBACK


def test_override_and_pin_publish_only_reachable_authorized_fallbacks() -> None:
    fallback_2 = _clone("test.override-fallback-2")
    fallback_1 = _clone(
        "test.override-fallback-1",
        replacement_deployment_id=fallback_2.deployment_id,
    )
    primary = _clone(
        "test.override-primary",
        replacement_deployment_id=fallback_1.deployment_id,
    )
    standalone_override = _clone("test.standalone-override")
    profile = replace(
        _profile(TaskKey.ROLE_CHAT_ORCHESTRATION),
        candidate_deployment_ids=(primary.deployment_id,),
        fallback_deployment_ids=(
            fallback_1.deployment_id,
            fallback_2.deployment_id,
        ),
        fallback_classes=frozenset({FallbackClass.REGISTERED_REPLACEMENT}),
        max_attempts_per_iteration=3,
    )
    policy = _policy(
        profile,
        (primary, fallback_1, fallback_2, standalone_override),
    )

    overridden = policy.plan(
        RouteRequest(
            profile.key,
            _id("standalone-override-fallbacks"),
            override_alias=standalone_override.aliases[0],
        )
    )
    assert tuple(item.deployment_id for item in overridden.attempts) == (
        standalone_override.deployment_id,
    )

    pinned = policy.plan(
        RouteRequest(
            profile.key,
            _id("pinned-reachable-fallbacks"),
            pinned_deployment_id=fallback_1.deployment_id,
        )
    )
    assert tuple(item.deployment_id for item in pinned.attempts) == (
        fallback_1.deployment_id,
        fallback_2.deployment_id,
    )


def test_validation_rejects_profile_fallback_outside_replacement_chain() -> None:
    fallback = _clone("test.unreachable-fallback")
    primary = _clone("test.no-replacement-primary")
    profile = replace(
        _profile(TaskKey.ROLE_CHAT_ORCHESTRATION),
        candidate_deployment_ids=(primary.deployment_id,),
        fallback_deployment_ids=(fallback.deployment_id,),
        fallback_classes=frozenset({FallbackClass.REGISTERED_REPLACEMENT}),
        max_attempts_per_iteration=2,
    )

    with pytest.raises(RegistryValidationError) as unreachable:
        _policy(profile, (primary, fallback))

    assert unreachable.value.code is ValidationCode.INCOMPATIBLE_FALLBACK


def test_tenant_policy_can_remove_a_primary_candidate_without_bespoke_code() -> None:
    profile = replace(
        _profile(TaskKey.ROLE_CHAT_ORCHESTRATION),
        candidate_deployment_ids=(ANTHROPIC_HAIKU_4_5, ANTHROPIC_SONNET_4_6),
    )
    decision = _policy(profile).plan(
        RouteRequest(
            profile.key,
            _id("tenant-policy"),
            tenant_blocked_deployments=frozenset({ANTHROPIC_HAIKU_4_5}),
        )
    )
    assert decision.selected_deployment_id == ANTHROPIC_SONNET_4_6
    assert ExclusionCode.TENANT_BLOCKED in next(
        item.codes
        for item in decision.exclusions
        if item.deployment_id == ANTHROPIC_HAIKU_4_5
    )


def test_token_pricing_is_exact_and_region_multiplier_is_applied() -> None:
    haiku = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_HAIKU_4_5)
    sonnet = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_SONNET_4_6)
    assert haiku.pricing.input_per_million == Decimal("1.00")
    assert haiku.pricing.output_per_million == Decimal("5.00")
    assert sonnet.pricing.batch_input_per_million == Decimal("1.50")

    decision = RoutingPolicy().plan(
        RouteRequest(
            TaskKey.ROLE_CHAT_ORCHESTRATION,
            _id("us-cost"),
            1_000,
            100,
            override_alias="sonnet-4-6",
            region="us",
        )
    )
    assert decision.attempts[0].expected_cost_micro_usd == 4_950


def test_contracts_are_deeply_immutable() -> None:
    deployment = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_HAIKU_4_5)
    profile = _profile(TaskKey.SEARCH_RERANK)
    assert isinstance(deployment.capabilities, frozenset)
    assert isinstance(profile.candidate_deployment_ids, tuple)
    with pytest.raises(FrozenInstanceError):
        deployment.model_id = "changed"
    with pytest.raises(FrozenInstanceError):
        profile.max_iterations = 99


def test_registry_rejects_duplicate_aliases_and_unpriced_active_deployments() -> None:
    duplicate = _clone("test.duplicate", aliases=("haiku",))
    with pytest.raises(ModelRegistryError):
        ModelRegistry(
            version="duplicate.v1",
            deployments=(*DEFAULT_MODEL_REGISTRY.deployments, duplicate),
        )

    unpriced = _clone("test.unpriced", pricing=None)
    with pytest.raises(ModelRegistryError, match="unpriced"):
        ModelRegistry(version="unpriced.v1", deployments=(unpriced,))


def test_registry_rejects_unknown_or_transport_incompatible_fallbacks() -> None:
    profile = replace(
        _profile(TaskKey.ROLE_CHAT_ORCHESTRATION),
        fallback_deployment_ids=("missing.deployment",),
    )
    with pytest.raises(RegistryValidationError) as unknown:
        _policy(profile)
    assert unknown.value.code is ValidationCode.UNKNOWN_DEPLOYMENT

    other_transport = _clone("test.other-transport", transport_contract="other_v1")
    profile = replace(profile, fallback_deployment_ids=(other_transport.deployment_id,))
    with pytest.raises(RegistryValidationError) as incompatible:
        _policy(profile, (*DEFAULT_MODEL_REGISTRY.deployments, other_transport))
    assert incompatible.value.code is ValidationCode.INCOMPATIBLE_FALLBACK

    policy = _policy(
        _profile(TaskKey.ROLE_CHAT_ORCHESTRATION),
        (*DEFAULT_MODEL_REGISTRY.deployments, other_transport),
    )
    with pytest.raises(NoEligibleDeploymentError) as override:
        policy.plan(
            RouteRequest(
                TaskKey.ROLE_CHAT_ORCHESTRATION,
                _id("cross-transport-override"),
                override_alias=other_transport.aliases[0],
            )
        )
    assert ExclusionCode.TRANSPORT_CONTRACT in _codes(
        override.value, other_transport.deployment_id
    )


def test_registry_rejects_cross_transport_fallback_mode_until_supported() -> None:
    profile = replace(
        _profile(TaskKey.ROLE_CHAT_ORCHESTRATION),
        require_same_transport_fallback=False,
    )

    with pytest.raises(RegistryValidationError) as unsupported:
        _policy(profile)

    assert unsupported.value.code is ValidationCode.INCOMPATIBLE_FALLBACK


def test_replacement_closure_must_be_fully_compatible() -> None:
    retired = _clone(
        "test.retired-large",
        lifecycle=LifecycleState.RETIRED,
        replacement_deployment_id=ANTHROPIC_HAIKU_4_5,
        context_tokens=1_000_000,
    )
    with pytest.raises(RegistryValidationError) as incompatible:
        _policy(
            _profile(TaskKey.ROLE_CHAT_ORCHESTRATION),
            (DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_HAIKU_4_5), retired),
        )
    assert incompatible.value.code is ValidationCode.INCOMPATIBLE_REPLACEMENT


def test_workflow_graph_rejects_cycles_and_excessive_depth() -> None:
    cyclic = TaskRegistry(
        version="cycle.v1",
        profiles=(),
        workflows=(
            WorkflowDefinition(
                WorkflowKey.GENERAL_CHAT, "v1", (WorkflowKey.ROLE_CHAT,)
            ),
            WorkflowDefinition(
                WorkflowKey.ROLE_CHAT, "v1", (WorkflowKey.GENERAL_CHAT,)
            ),
        ),
    )
    with pytest.raises(RegistryValidationError) as cycle:
        validate_workflow_graph(cyclic)
    assert cycle.value.code is ValidationCode.WORKFLOW_CYCLE

    deep = TaskRegistry(
        version="depth.v1",
        profiles=(),
        workflows=(
            WorkflowDefinition(
                WorkflowKey.GENERAL_CHAT, "v1", (WorkflowKey.ROLE_CHAT,)
            ),
            WorkflowDefinition(
                WorkflowKey.ROLE_CHAT, "v1", (WorkflowKey.AUTONOMOUS_RECRUITING,)
            ),
            WorkflowDefinition(
                WorkflowKey.AUTONOMOUS_RECRUITING,
                "v1",
                (WorkflowKey.CANDIDATE_SEARCH,),
            ),
            WorkflowDefinition(WorkflowKey.CANDIDATE_SEARCH, "v1"),
        ),
    )
    with pytest.raises(RegistryValidationError) as depth:
        validate_workflow_graph(deep, max_depth=3)
    assert depth.value.code is ValidationCode.WORKFLOW_DEPTH


def test_pure_core_has_no_provider_network_or_database_imports_and_stays_small() -> (
    None
):
    core = Path(__file__).parents[3] / "app" / "components" / "ai_routing"
    banned = {"anthropic", "httpx", "requests", "sqlalchemy", "boto3", "redis"}
    for name in (
        "contracts.py",
        "model_registry.py",
        "task_registry.py",
        "policy.py",
        "validation.py",
    ):
        path = core / name
        assert len(path.read_text().splitlines()) < 500, name
        tree = ast.parse(path.read_text())
        imported_roots = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots |= {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert imported_roots.isdisjoint(banned), (name, imported_roots & banned)
