"""Versioned workflow and task contracts for AI-assisted operations."""

from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Mapping

from .contracts import (
    Capability,
    DataClassification,
    ExecutionMode,
    FallbackClass,
    RiskClass,
    RequestShapeContract,
    RouteStickiness,
    TaskKey,
    TaskProfile,
    WorkflowDefinition,
    WorkflowKey,
)
from .model_registry import ANTHROPIC_HAIKU_4_5, ANTHROPIC_SONNET_4_6

TASK_REGISTRY_VERSION = "tali-task-contracts-2026-07-22.v1"


class TaskRegistryError(ValueError):
    pass


class TaskRegistry:
    """Read-only workflow/task index; cross-registry checks live in validation."""

    __slots__ = ("version", "_profiles", "_workflows")

    def __init__(
        self,
        *,
        version: str,
        profiles: Iterable[TaskProfile],
        workflows: Iterable[WorkflowDefinition],
    ) -> None:
        if not version.strip():
            raise TaskRegistryError("task registry version must be non-empty")

        workflow_map: dict[WorkflowKey, WorkflowDefinition] = {}
        for workflow in workflows:
            if workflow.key in workflow_map:
                raise TaskRegistryError(f"duplicate workflow: {workflow.key.value}")
            if not workflow.version.strip():
                raise TaskRegistryError(f"workflow {workflow.key.value} has no version")
            workflow_map[workflow.key] = workflow

        profile_map: dict[TaskKey, TaskProfile] = {}
        for profile in profiles:
            if profile.key in profile_map:
                raise TaskRegistryError(f"duplicate task profile: {profile.key.value}")
            if profile.workflow not in workflow_map:
                raise TaskRegistryError(
                    f"task {profile.key.value} references unknown workflow {profile.workflow.value}"
                )
            self._validate_profile(profile)
            profile_map[profile.key] = profile

        self.version = version
        self._profiles: Mapping[TaskKey, TaskProfile] = MappingProxyType(profile_map)
        self._workflows: Mapping[WorkflowKey, WorkflowDefinition] = MappingProxyType(
            workflow_map
        )

    @staticmethod
    def _validate_profile(profile: TaskProfile) -> None:
        revisions = (
            profile.profile_version,
            profile.semantic_revision,
            profile.schema_revision,
            profile.prompt_revision,
            profile.tool_revision,
            profile.feature,
        )
        if any(not value.strip() for value in revisions):
            raise TaskRegistryError(
                f"task {profile.key.value} has an empty revision or feature"
            )
        if not profile.candidate_deployment_ids:
            raise TaskRegistryError(
                f"task {profile.key.value} has no primary candidate"
            )
        route_ids = (
            *profile.candidate_deployment_ids,
            *profile.fallback_deployment_ids,
        )
        if len(route_ids) != len(set(route_ids)):
            raise TaskRegistryError(
                f"task {profile.key.value} has duplicate route deployments"
            )
        numeric_limits = (
            profile.max_input_tokens,
            profile.max_output_tokens,
            profile.max_iterations,
            profile.max_attempts_per_iteration,
            profile.latency_slo_ms,
            profile.max_cost_micro_usd,
            profile.min_quality_tier,
        )
        if any(value <= 0 for value in numeric_limits):
            raise TaskRegistryError(f"task {profile.key.value} has invalid limits")
        minimum_attempts = 1 + len(profile.fallback_deployment_ids)
        if profile.max_attempts_per_iteration < minimum_attempts:
            raise TaskRegistryError(
                f"task {profile.key.value} cannot reach its fallback chain within "
                "max_attempts_per_iteration"
            )
        retry_classes = {
            FallbackClass.PRE_ACCEPTANCE_TRANSPORT,
            FallbackClass.RETRYABLE_TRANSPORT,
        }
        if profile.fallback_classes.intersection(retry_classes) and (
            profile.max_attempts_per_iteration < 2
        ):
            raise TaskRegistryError(
                f"task {profile.key.value} declares an unreachable transport retry"
            )
        if (
            profile.request_shape.require_tools
            and Capability.TOOLS not in profile.required_capabilities
        ):
            raise TaskRegistryError(
                f"task {profile.key.value} requires a tools request without the capability"
            )
        if (
            profile.request_shape.require_citations_document
            and Capability.CITATIONS not in profile.required_capabilities
        ):
            raise TaskRegistryError(
                f"task {profile.key.value} requires citations without the capability"
            )

    @property
    def profiles(self) -> tuple[TaskProfile, ...]:
        return tuple(
            sorted(self._profiles.values(), key=lambda profile: profile.key.value)
        )

    @property
    def workflows(self) -> tuple[WorkflowDefinition, ...]:
        return tuple(
            sorted(self._workflows.values(), key=lambda workflow: workflow.key.value)
        )

    def get(self, task: TaskKey) -> TaskProfile | None:
        return self._profiles.get(task)

    def workflow(self, key: WorkflowKey) -> WorkflowDefinition | None:
        return self._workflows.get(key)


_WORKFLOWS = (
    WorkflowDefinition(
        WorkflowKey.GENERAL_CHAT,
        "general-chat.v1",
        (WorkflowKey.CANDIDATE_SEARCH,),
    ),
    WorkflowDefinition(
        WorkflowKey.ROLE_CHAT,
        "role-chat.v1",
        (WorkflowKey.CANDIDATE_SEARCH, WorkflowKey.CANDIDATE_SCORING),
    ),
    WorkflowDefinition(
        WorkflowKey.AUTONOMOUS_RECRUITING,
        "autonomous-recruiting.v1",
        (
            WorkflowKey.CANDIDATE_SEARCH,
            WorkflowKey.CANDIDATE_SCORING,
            WorkflowKey.CANDIDATE_GRAPH,
            WorkflowKey.OUTREACH,
        ),
    ),
    WorkflowDefinition(
        WorkflowKey.CANDIDATE_SEARCH,
        "candidate-search.v1",
        (WorkflowKey.CANDIDATE_GRAPH,),
    ),
    WorkflowDefinition(
        WorkflowKey.CANDIDATE_ASSESSMENT,
        "candidate-assessment.v1",
        (WorkflowKey.CANDIDATE_SCORING,),
    ),
    WorkflowDefinition(
        WorkflowKey.CV_INGESTION,
        "cv-ingestion.v1",
        (WorkflowKey.CANDIDATE_GRAPH, WorkflowKey.CANDIDATE_SCORING),
    ),
    WorkflowDefinition(WorkflowKey.CANDIDATE_SCORING, "candidate-scoring.v1"),
    WorkflowDefinition(WorkflowKey.CANDIDATE_GRAPH, "candidate-graph.v1"),
    WorkflowDefinition(
        WorkflowKey.ROLE_DESIGN,
        "role-design.v1",
        (WorkflowKey.CANDIDATE_SCORING,),
    ),
    WorkflowDefinition(WorkflowKey.INTERVIEW_DESIGN, "interview-design.v1"),
    WorkflowDefinition(
        WorkflowKey.SOURCING,
        "sourcing.v1",
        (WorkflowKey.CANDIDATE_SEARCH,),
    ),
    WorkflowDefinition(WorkflowKey.OUTREACH, "outreach.v1"),
)

_CHAT_FALLBACKS = frozenset(
    {FallbackClass.PRE_ACCEPTANCE_TRANSPORT, FallbackClass.RETRYABLE_TRANSPORT}
)

_PROFILES = (
    TaskProfile(
        key=TaskKey.GENERAL_CHAT_ORCHESTRATION,
        workflow=WorkflowKey.GENERAL_CHAT,
        profile_version="general-chat-orchestration.v1",
        semantic_revision="general-chat.v1",
        schema_revision="anthropic-events.v1",
        prompt_revision="taali-system.v1",
        tool_revision="taali-tools.v1",
        execution_mode=ExecutionMode.STREAM,
        required_capabilities=frozenset(
            {
                Capability.TEXT,
                Capability.TOOLS,
                Capability.STREAMING,
                Capability.PROMPT_CACHING,
            }
        ),
        risk=RiskClass.HIGH,
        data_classification=DataClassification.RESTRICTED,
        max_input_tokens=180_000,
        max_output_tokens=4_096,
        max_iterations=8,
        latency_slo_ms=45_000,
        max_cost_micro_usd=5_000_000,
        min_quality_tier=1,
        stickiness=RouteStickiness.INVOCATION,
        candidate_deployment_ids=(ANTHROPIC_HAIKU_4_5,),
        fallback_deployment_ids=(),
        fallback_classes=_CHAT_FALLBACKS,
        feature="taali_chat",
        max_attempts_per_iteration=2,
        retry_backoff_base_ms=250,
        retry_backoff_max_ms=1_000,
    ),
    TaskProfile(
        key=TaskKey.ROLE_CHAT_ORCHESTRATION,
        workflow=WorkflowKey.ROLE_CHAT,
        profile_version="role-chat-orchestration.v1",
        semantic_revision="role-chat.v1",
        schema_revision="anthropic-messages.v1",
        prompt_revision="role-agent-chat.v1",
        tool_revision="role-agent-tools.v1",
        execution_mode=ExecutionMode.SYNC,
        required_capabilities=frozenset(
            {Capability.TEXT, Capability.TOOLS, Capability.PROMPT_CACHING}
        ),
        risk=RiskClass.HIGH,
        data_classification=DataClassification.RESTRICTED,
        max_input_tokens=180_000,
        max_output_tokens=4_096,
        max_iterations=8,
        latency_slo_ms=45_000,
        max_cost_micro_usd=5_000_000,
        min_quality_tier=1,
        stickiness=RouteStickiness.INVOCATION,
        candidate_deployment_ids=(ANTHROPIC_HAIKU_4_5,),
        fallback_deployment_ids=(),
        fallback_classes=_CHAT_FALLBACKS,
        feature="agent_chat",
        max_attempts_per_iteration=2,
        retry_backoff_base_ms=250,
        retry_backoff_max_ms=1_000,
    ),
    TaskProfile(
        key=TaskKey.AUTONOMOUS_RECRUITING_ORCHESTRATION,
        workflow=WorkflowKey.AUTONOMOUS_RECRUITING,
        profile_version="autonomous-recruiting-orchestration.v1",
        semantic_revision="autonomous-agent.v1",
        schema_revision="anthropic-messages.v1",
        prompt_revision="agent-runtime.v1",
        tool_revision="agent-runtime-tools.v1",
        execution_mode=ExecutionMode.SYNC,
        required_capabilities=frozenset(
            {Capability.TEXT, Capability.TOOLS, Capability.PROMPT_CACHING}
        ),
        risk=RiskClass.CRITICAL,
        data_classification=DataClassification.RESTRICTED,
        max_input_tokens=180_000,
        max_output_tokens=2_048,
        max_iterations=18,
        latency_slo_ms=60_000,
        max_cost_micro_usd=5_000_000,
        min_quality_tier=1,
        stickiness=RouteStickiness.INVOCATION,
        candidate_deployment_ids=(ANTHROPIC_HAIKU_4_5,),
        fallback_deployment_ids=(),
        fallback_classes=frozenset({FallbackClass.RETRYABLE_TRANSPORT}),
        feature="agent_autonomous",
        require_role_authority=True,
        max_attempts_per_iteration=2,
        retry_backoff_base_ms=500,
        retry_backoff_max_ms=2_000,
    ),
    TaskProfile(
        key=TaskKey.SEARCH_PARSE,
        workflow=WorkflowKey.CANDIDATE_SEARCH,
        profile_version="candidate-search-parse.v1",
        semantic_revision="candidate-search-query.v1",
        schema_revision="parsed-filter.v1",
        prompt_revision="search-parser.v1",
        tool_revision="parsed-filter-tool.v1",
        execution_mode=ExecutionMode.SYNC,
        required_capabilities=frozenset(
            {
                Capability.TEXT,
                Capability.TOOLS,
                Capability.STRICT_STRUCTURED_OUTPUT,
                Capability.PROMPT_CACHING,
            }
        ),
        risk=RiskClass.MODERATE,
        data_classification=DataClassification.CONFIDENTIAL,
        # Routing uses a byte-level upper bound before provider tokenization.
        # The real parser prompt + forced-tool schema is ~12.8k serialized
        # bytes, while still comfortably inside Sonnet's context window.
        max_input_tokens=16_000,
        max_output_tokens=512,
        max_iterations=1,
        latency_slo_ms=15_000,
        max_cost_micro_usd=100_000,
        min_quality_tier=2,
        stickiness=RouteStickiness.INVOCATION,
        candidate_deployment_ids=(ANTHROPIC_SONNET_4_6,),
        fallback_deployment_ids=(),
        fallback_classes=frozenset(),
        feature="search_parse",
        request_shape=RequestShapeContract(
            require_tools=True,
            require_forced_tool_choice=True,
        ),
    ),
    TaskProfile(
        key=TaskKey.SEARCH_RERANK,
        workflow=WorkflowKey.CANDIDATE_SEARCH,
        profile_version="candidate-search-rerank.v1",
        semantic_revision="candidate-rerank.v1",
        schema_revision="candidate-rerank-json.v1",
        prompt_revision="candidate-rerank.v1",
        tool_revision="none.v1",
        execution_mode=ExecutionMode.SYNC,
        required_capabilities=frozenset(
            {Capability.TEXT, Capability.PROMPT_CACHING}
        ),
        risk=RiskClass.HIGH,
        data_classification=DataClassification.RESTRICTED,
        max_input_tokens=16_000,
        max_output_tokens=256,
        max_iterations=1,
        latency_slo_ms=15_000,
        max_cost_micro_usd=100_000,
        min_quality_tier=1,
        stickiness=RouteStickiness.INVOCATION,
        candidate_deployment_ids=(ANTHROPIC_HAIKU_4_5,),
        fallback_deployment_ids=(),
        fallback_classes=frozenset({FallbackClass.RETRYABLE_TRANSPORT}),
        feature="cv_rerank",
        max_attempts_per_iteration=2,
        retry_backoff_base_ms=250,
        retry_backoff_max_ms=1_000,
    ),
    TaskProfile(
        key=TaskKey.SEARCH_GROUNDING,
        workflow=WorkflowKey.CANDIDATE_SEARCH,
        profile_version="candidate-search-grounding.v1",
        semantic_revision="candidate-grounding.v2",
        schema_revision="citation-markers.v2",
        prompt_revision="candidate-grounding.v2",
        tool_revision="citations.v1",
        execution_mode=ExecutionMode.SYNC,
        required_capabilities=frozenset({Capability.TEXT, Capability.CITATIONS}),
        risk=RiskClass.HIGH,
        data_classification=DataClassification.RESTRICTED,
        max_input_tokens=32_000,
        max_output_tokens=700,
        max_iterations=1,
        latency_slo_ms=45_000,
        max_cost_micro_usd=250_000,
        min_quality_tier=2,
        stickiness=RouteStickiness.INVOCATION,
        candidate_deployment_ids=(ANTHROPIC_SONNET_4_6,),
        fallback_deployment_ids=(),
        fallback_classes=frozenset({FallbackClass.RETRYABLE_TRANSPORT}),
        feature="candidate_grounding",
        max_attempts_per_iteration=3,
        retry_backoff_base_ms=500,
        retry_backoff_max_ms=4_000,
        request_shape=RequestShapeContract(require_citations_document=True),
    ),
)

DEFAULT_TASK_REGISTRY = TaskRegistry(
    version=TASK_REGISTRY_VERSION,
    profiles=_PROFILES,
    workflows=_WORKFLOWS,
)
