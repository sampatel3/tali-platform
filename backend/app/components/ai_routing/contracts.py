"""Provider-neutral contracts for Tali's deterministic AI control plane.

This module deliberately contains data only.  It must remain safe to import in
tests, workers, migrations, and command-line validation without constructing a
provider client or touching application state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from uuid import UUID


class WorkflowKey(str, Enum):
    GENERAL_CHAT = "general_chat"
    ROLE_CHAT = "role_chat"
    AUTONOMOUS_RECRUITING = "autonomous_recruiting"
    CANDIDATE_SEARCH = "candidate_search"
    CANDIDATE_ASSESSMENT = "candidate_assessment"
    CV_INGESTION = "cv_ingestion"
    CANDIDATE_SCORING = "candidate_scoring"
    CANDIDATE_GRAPH = "candidate_graph"
    ROLE_DESIGN = "role_design"
    INTERVIEW_DESIGN = "interview_design"
    SOURCING = "sourcing"
    OUTREACH = "outreach"


class TaskKey(str, Enum):
    GENERAL_CHAT_ORCHESTRATION = "general_chat.orchestration"
    ROLE_CHAT_ORCHESTRATION = "role_chat.orchestration"
    AUTONOMOUS_RECRUITING_ORCHESTRATION = "autonomous_recruiting.orchestration"
    SEARCH_PARSE = "candidate_search.parse"
    SEARCH_RERANK = "candidate_search.rerank"
    SEARCH_GROUNDING = "candidate_search.grounding"
    ASSESSMENT_AGENT_CHAT = "candidate_assessment.agent_chat"
    CV_PARSE_SYNC = "cv_ingestion.parse_sync"
    CV_PARSE_BATCH = "cv_ingestion.parse_batch"
    CV_SCORE_PRESCREEN = "candidate_scoring.prescreen"
    CV_SCORE_HOLISTIC = "candidate_scoring.holistic"
    GRAPH_EXTRACT = "candidate_graph.extract"
    GRAPH_EMBED = "candidate_graph.embed"
    ARCHETYPE_SYNTHESIS = "role_design.archetype_synthesis"
    PAIRWISE_JUDGE = "candidate_scoring.pairwise_judge"
    INTERVIEW_FOCUS = "interview_design.focus"
    INTERVIEW_TECH = "interview_design.technical_prompt"
    FIT_MATCHING = "candidate_scoring.fit_matching"
    SOURCING_SEARCH = "sourcing.search"
    OUTREACH_DRAFT = "outreach.draft"


class ExecutionMode(str, Enum):
    SYNC = "sync"
    STREAM = "stream"
    BATCH = "batch"
    AGENT_SDK = "agent_sdk"
    EMBEDDING = "embedding"
    COMPOSITE = "composite"


class Capability(str, Enum):
    TEXT = "text"
    VISION = "vision"
    TOOLS = "tools"
    STRICT_STRUCTURED_OUTPUT = "strict_structured_output"
    CITATIONS = "citations"
    STREAMING = "streaming"
    PROMPT_CACHING = "prompt_caching"
    LONG_CONTEXT = "long_context"
    EXTENDED_THINKING = "extended_thinking"


class RiskClass(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class LifecycleState(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class RouteStickiness(str, Enum):
    NONE = "none"
    INVOCATION = "invocation"
    ROOT_INVOCATION = "root_invocation"


class FallbackClass(str, Enum):
    PRE_ACCEPTANCE_TRANSPORT = "pre_acceptance_transport"
    RETRYABLE_TRANSPORT = "retryable_transport"
    REGISTERED_REPLACEMENT = "registered_replacement"
    SCHEMA_REPAIR = "schema_repair"
    QUALITY_ESCALATION = "quality_escalation"


class InputCostBasis(str, Enum):
    """Conservative price class for the request's estimated input tokens."""

    STANDARD = "standard"
    CACHE_WRITE_5M = "cache_write_5m"
    CACHE_WRITE_1H = "cache_write_1h"


class ReasonCode(str, Enum):
    PRIMARY_POLICY = "route.selected.primary_policy.v1"
    LOWEST_EXPECTED_COST = "route.selected.lowest_expected_cost.v1"
    LOWEST_LATENCY = "route.selected.lowest_latency.v1"
    STABLE_TIEBREAK = "route.selected.stable_tiebreak.v1"
    VALIDATED_OVERRIDE = "route.selected.validated_override.v1"
    PINNED_DEPLOYMENT = "route.selected.pinned_deployment.v1"
    PROFILE_FALLBACK = "route.attempt.profile_fallback.v1"


@dataclass(frozen=True, slots=True)
class RequestShapeContract:
    """Provider-neutral semantic requirements for the rendered model request."""

    require_tools: bool = False
    require_forced_tool_choice: bool = False
    require_citations_document: bool = False

    def __post_init__(self) -> None:
        values = (
            self.require_tools,
            self.require_forced_tool_choice,
            self.require_citations_document,
        )
        if any(not isinstance(value, bool) for value in values):
            raise TypeError("request-shape requirements must be bools")
        if self.require_forced_tool_choice and not self.require_tools:
            raise ValueError("forced tool choice requires tools")


class ExclusionCode(str, Enum):
    TASK_POLICY = "route.excluded.task_policy.v1"
    LIFECYCLE = "route.excluded.lifecycle.v1"
    EXECUTION_MODE = "route.excluded.execution_mode.v1"
    TRANSPORT_CONTRACT = "route.excluded.transport_contract.v1"
    CAPABILITY = "route.excluded.capability.v1"
    CAPABILITY_CONFLICT = "route.excluded.capability_conflict.v1"
    CONTEXT_LIMIT = "route.excluded.context_limit.v1"
    OUTPUT_LIMIT = "route.excluded.output_limit.v1"
    DATA_CLASSIFICATION = "route.excluded.data_classification.v1"
    REGION = "route.excluded.region.v1"
    PROVIDER_NOT_ALLOWED = "route.excluded.provider_not_allowed.v1"
    PROVIDER_DENIED = "route.excluded.provider_denied.v1"
    RISK = "route.excluded.risk.v1"
    NOT_TASK_EVALUATED = "route.excluded.not_task_evaluated.v1"
    QUALITY_FLOOR = "route.excluded.quality_floor.v1"
    TENANT_NOT_ALLOWED = "route.excluded.tenant_not_allowed.v1"
    TENANT_BLOCKED = "route.excluded.tenant_blocked.v1"
    COST_CEILING = "route.excluded.cost_ceiling.v1"
    OVERRIDE_MISMATCH = "route.excluded.override_mismatch.v1"
    PIN_MISMATCH = "route.excluded.pin_mismatch.v1"


class PlanningErrorCode(str, Enum):
    UNKNOWN_TASK = "route.error.unknown_task.v1"
    INVALID_OVERRIDE = "route.error.invalid_override.v1"
    INVALID_PIN = "route.error.invalid_pin.v1"
    CONFLICTING_SELECTION = "route.error.conflicting_selection.v1"
    PROFILE_LIMIT = "route.error.profile_limit.v1"
    NO_ELIGIBLE_DEPLOYMENT = "route.error.no_eligible_deployment.v1"


@dataclass(frozen=True, slots=True)
class TokenPricing:
    pricing_id: str
    currency: str
    input_per_million: Decimal
    output_per_million: Decimal
    cache_write_5m_per_million: Decimal
    cache_write_1h_per_million: Decimal
    cache_read_per_million: Decimal
    batch_input_per_million: Decimal
    batch_output_per_million: Decimal
    us_inference_multiplier: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ModelDeployment:
    deployment_id: str
    provider: str
    endpoint: str
    runtime: str
    transport_contract: str
    model_id: str
    aliases: tuple[str, ...]
    supported_modes: frozenset[ExecutionMode]
    capabilities: frozenset[Capability]
    capability_conflicts: tuple[frozenset[Capability], ...]
    context_tokens: int
    max_output_tokens: int
    lifecycle: LifecycleState
    replacement_deployment_id: str | None
    pricing: TokenPricing | None
    allowed_data_classes: frozenset[DataClassification]
    regions: frozenset[str]
    retention_policy: str
    credential_strategy: str
    max_risk: RiskClass
    evaluated_tasks: frozenset[TaskKey]
    quality_tier: int
    latency_rank: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "aliases", tuple(self.aliases))
        object.__setattr__(self, "supported_modes", frozenset(self.supported_modes))
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(
            self,
            "capability_conflicts",
            tuple(frozenset(conflict) for conflict in self.capability_conflicts),
        )
        object.__setattr__(
            self, "allowed_data_classes", frozenset(self.allowed_data_classes)
        )
        object.__setattr__(self, "regions", frozenset(self.regions))
        object.__setattr__(self, "evaluated_tasks", frozenset(self.evaluated_tasks))


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    key: WorkflowKey
    version: str
    child_workflows: tuple[WorkflowKey, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "child_workflows", tuple(self.child_workflows))


@dataclass(frozen=True, slots=True)
class TaskProfile:
    key: TaskKey
    workflow: WorkflowKey
    profile_version: str
    semantic_revision: str
    schema_revision: str
    prompt_revision: str
    tool_revision: str
    execution_mode: ExecutionMode
    required_capabilities: frozenset[Capability]
    risk: RiskClass
    data_classification: DataClassification
    max_input_tokens: int
    max_output_tokens: int
    max_iterations: int
    latency_slo_ms: int
    max_cost_micro_usd: int
    min_quality_tier: int
    stickiness: RouteStickiness
    candidate_deployment_ids: tuple[str, ...]
    fallback_deployment_ids: tuple[str, ...]
    fallback_classes: frozenset[FallbackClass]
    feature: str
    require_role_authority: bool = False
    require_same_transport_fallback: bool = True
    max_attempts_per_iteration: int = 1
    retry_backoff_base_ms: int = 0
    retry_backoff_max_ms: int = 0
    request_shape: RequestShapeContract = RequestShapeContract()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "required_capabilities", frozenset(self.required_capabilities)
        )
        object.__setattr__(
            self, "candidate_deployment_ids", tuple(self.candidate_deployment_ids)
        )
        object.__setattr__(
            self, "fallback_deployment_ids", tuple(self.fallback_deployment_ids)
        )
        object.__setattr__(self, "fallback_classes", frozenset(self.fallback_classes))
        if not isinstance(self.require_role_authority, bool):
            raise TypeError("require_role_authority must be a bool")
        if self.max_attempts_per_iteration <= 0:
            raise ValueError("max_attempts_per_iteration must be positive")
        if self.retry_backoff_base_ms < 0:
            raise ValueError("retry_backoff_base_ms must be non-negative")
        if self.retry_backoff_max_ms < self.retry_backoff_base_ms:
            raise ValueError(
                "retry_backoff_max_ms must be at least retry_backoff_base_ms"
            )
        if not isinstance(self.request_shape, RequestShapeContract):
            raise TypeError("request_shape must be a RequestShapeContract")


@dataclass(frozen=True, slots=True)
class RouteRequest:
    task: TaskKey
    invocation_id: str
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_input_cost_basis: InputCostBasis = InputCostBasis.STANDARD
    root_invocation_id: str | None = None
    parent_invocation_id: str | None = None
    override_alias: str | None = None
    pinned_deployment_id: str | None = None
    additional_capabilities: frozenset[Capability] = frozenset()
    data_classification: DataClassification | None = None
    risk: RiskClass | None = None
    region: str | None = None
    provider_allowlist: frozenset[str] | None = None
    provider_denylist: frozenset[str] = frozenset()
    tenant_allowed_deployments: frozenset[str] | None = None
    tenant_blocked_deployments: frozenset[str] = frozenset()
    max_cost_micro_usd: int | None = None
    require_role_authority: bool = False

    def __post_init__(self) -> None:
        def canonical_uuid(value: str, *, field: str) -> str:
            try:
                return str(UUID(str(value)))
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError(f"{field} must be a UUID string") from exc

        invocation_id = canonical_uuid(self.invocation_id, field="invocation_id")
        root_invocation_id = (
            canonical_uuid(self.root_invocation_id, field="root_invocation_id")
            if self.root_invocation_id is not None
            else None
        )
        parent_invocation_id = (
            canonical_uuid(self.parent_invocation_id, field="parent_invocation_id")
            if self.parent_invocation_id is not None
            else None
        )
        if parent_invocation_id is None and root_invocation_id not in {
            None,
            invocation_id,
        }:
            raise ValueError("a root invocation must reference itself")
        if parent_invocation_id is not None:
            if root_invocation_id is None:
                raise ValueError("a child invocation requires root_invocation_id")
            if parent_invocation_id == invocation_id:
                raise ValueError("an invocation cannot be its own parent")
        object.__setattr__(self, "invocation_id", invocation_id)
        object.__setattr__(self, "root_invocation_id", root_invocation_id)
        object.__setattr__(self, "parent_invocation_id", parent_invocation_id)
        if self.estimated_input_tokens < 0 or self.estimated_output_tokens < 0:
            raise ValueError("token estimates must be non-negative")
        if not isinstance(self.estimated_input_cost_basis, InputCostBasis):
            raise TypeError("estimated_input_cost_basis must be an InputCostBasis")
        if self.max_cost_micro_usd is not None and self.max_cost_micro_usd < 0:
            raise ValueError("max_cost_micro_usd must be non-negative")
        if not isinstance(self.require_role_authority, bool):
            raise TypeError("require_role_authority must be a bool")
        object.__setattr__(
            self, "additional_capabilities", frozenset(self.additional_capabilities)
        )
        object.__setattr__(self, "provider_denylist", frozenset(self.provider_denylist))
        object.__setattr__(
            self,
            "tenant_blocked_deployments",
            frozenset(self.tenant_blocked_deployments),
        )
        if self.provider_allowlist is not None:
            object.__setattr__(
                self, "provider_allowlist", frozenset(self.provider_allowlist)
            )
        if self.tenant_allowed_deployments is not None:
            object.__setattr__(
                self,
                "tenant_allowed_deployments",
                frozenset(self.tenant_allowed_deployments),
            )


@dataclass(frozen=True, slots=True)
class EligibleDeployment:
    deployment_id: str
    model_id: str
    provider: str
    expected_cost_micro_usd: int
    latency_rank: int


@dataclass(frozen=True, slots=True)
class ExcludedDeployment:
    deployment_id: str
    codes: tuple[ExclusionCode, ...]


@dataclass(frozen=True, slots=True)
class RouteAttempt:
    ordinal: int
    deployment_id: str
    model_id: str
    expected_cost_micro_usd: int
    reason: ReasonCode


@dataclass(frozen=True, slots=True)
class RouteLimits:
    max_input_tokens: int
    max_output_tokens: int
    max_iterations: int
    latency_slo_ms: int
    max_cost_micro_usd: int
    max_attempts_per_iteration: int = 1
    retry_backoff_base_ms: int = 0
    retry_backoff_max_ms: int = 0


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route_id: str
    behavior_fingerprint: str
    invocation_id: str
    root_invocation_id: str
    parent_invocation_id: str | None
    workflow: WorkflowKey
    task: TaskKey
    execution_mode: ExecutionMode
    required_capabilities: frozenset[Capability]
    request_shape: RequestShapeContract
    risk: RiskClass
    data_classification: DataClassification
    registry_version: str
    task_registry_version: str
    policy_version: str
    profile_version: str
    semantic_revision: str
    schema_revision: str
    prompt_revision: str
    tool_revision: str
    feature: str
    require_role_authority: bool
    selected_deployment_id: str
    selected_model_id: str
    eligible_deployments: tuple[EligibleDeployment, ...]
    exclusions: tuple[ExcludedDeployment, ...]
    attempts: tuple[RouteAttempt, ...]
    limits: RouteLimits
    fallback_classes: frozenset[FallbackClass]
    reason_codes: tuple[ReasonCode, ...]
    stickiness: RouteStickiness
    pin_key: str | None


class RoutePlanningError(ValueError):
    def __init__(self, code: PlanningErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class NoEligibleDeploymentError(RoutePlanningError):
    def __init__(self, exclusions: tuple[ExcludedDeployment, ...]) -> None:
        super().__init__(
            PlanningErrorCode.NO_ELIGIBLE_DEPLOYMENT,
            "no registered deployment satisfies the complete route contract",
        )
        self.exclusions = exclusions
