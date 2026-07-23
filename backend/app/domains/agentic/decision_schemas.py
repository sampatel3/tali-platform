"""Core request and response schemas for agent decision routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ...schemas.role import RoleFamilyResponse


class DecisionResolutionEffectPayload(BaseModel):
    status: Literal["confirmed", "failed", "pending", "unknown"]
    action: str
    target: Optional[str] = None
    occurred_at: Optional[datetime] = None
    event_id: Optional[int] = None


class AgentDecisionPayload(BaseModel):
    id: int
    role_id: int
    application_id: int
    candidate_id: int
    agent_run_id: Optional[int]
    decision_type: str
    recommendation: str
    status: str
    reasoning: str
    decision_explanation: dict[str, Any]
    candidate_summary: Optional[str] = None
    evidence: Optional[dict[str, Any]] = None
    confidence: Optional[float] = None
    model_version: str
    prompt_version: str
    created_at: datetime
    resolved_at: Optional[datetime] = None
    resolved_by_user_id: Optional[int] = None
    resolution_note: Optional[str] = None
    override_action: Optional[str] = None
    # Approval records intent. Only an immutable, role-matched event confirms
    # that the requested action actually happened.
    resolution_effect: DecisionResolutionEffectPayload
    candidate_name: Optional[str] = None
    candidate_email: Optional[str] = None
    role_name: Optional[str] = None
    role_family: Optional[RoleFamilyResponse] = None
    applied_at: Optional[datetime] = None
    taali_score: Optional[float] = None
    score_summary: Optional[dict] = None
    requirements: Optional[list[dict[str, Any]]] = None
    workable_job_id: Optional[str] = None
    candidate_workable_stage: Optional[str] = None
    candidate_post_handover: bool = False
    is_stale: bool = False
    staleness_reasons: list[str] = []
    staleness_summary: Optional[str] = None
    age_seconds: int = 0
    confidence_band: Optional[str] = None
    cost_usd_cents: int = 0
    rescore_in_flight: bool = False


class AgentRunPayload(BaseModel):
    id: int
    role_id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    input_tokens: int
    output_tokens: int
    total_cost_micro_usd: int
    decisions_emitted: int
    tools_called: Optional[list[dict[str, Any]]]
    error: Optional[str]
    model_version: Optional[str]
    prompt_version: Optional[str]


class ApproveBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)
    workable_target_stage: Optional[str] = Field(default=None, max_length=200)


class DecisionAcceptedResult(BaseModel):
    """Public receipt for a decision durably accepted for execution."""

    decision_id: int
    accepted: Literal[True] = True


class OverrideBody(BaseModel):
    override_action: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=2000)
    workable_target_stage: Optional[str] = Field(default=None, max_length=200)


class DiscardBody(BaseModel):
    role_id: int
    expected_version: int = Field(ge=1)


class RunNowBody(BaseModel):
    application_id: Optional[int] = None


class RoleVersionCommand(BaseModel):
    expected_version: int = Field(ge=1)


class AgentStatusActivity(BaseModel):
    event_type: str
    reason: Optional[str] = None
    actor_type: str
    application_id: Optional[int] = None
    candidate_name: Optional[str] = None
    created_at: datetime


class AgentStatusCurrentRun(BaseModel):
    id: int
    started_at: datetime
    status: str
    decisions_emitted: int
    tools_called: Optional[list[dict[str, Any]]] = None


class AgentStatusPausedBy(BaseModel):
    user_id: Optional[int] = None
    name: Optional[str] = None
    is_current_user: bool
    changed_at: Optional[datetime] = None
    attribution: Literal["verified", "inferred", "unavailable"]
    source: Literal[
        "role_change_event",
        "legacy_unique_member",
        "legacy_history",
        "workspace_control",
    ]


class AgentStatusPendingBreakdown(BaseModel):
    total: int
    decisions: int
    questions: int


class AgentStatusPayload(BaseModel):
    role_id: int
    enabled: bool
    can_control_agent: bool = False
    paused: bool = False
    pause_scope: Optional[Literal["workspace", "role"]] = None
    paused_at: Optional[datetime] = None
    paused_reason: Optional[str] = None
    paused_by: Optional[AgentStatusPausedBy] = None
    role_paused_at: Optional[datetime] = None
    role_paused_reason: Optional[str] = None
    role_paused_by: Optional[AgentStatusPausedBy] = None
    workspace_paused: bool = False
    workspace_paused_at: Optional[datetime] = None
    workspace_paused_reason: Optional[str] = None
    workspace_paused_by: Optional[AgentStatusPausedBy] = None
    workspace_control_version: int = 1
    last_run_at: Optional[datetime] = None
    bootstrap_status: Optional[str] = None
    bootstrap_error: Optional[str] = None
    bootstrap_started_at: Optional[datetime] = None
    bootstrap_completed_at: Optional[datetime] = None
    pending_decisions: int
    pending_breakdown: AgentStatusPendingBreakdown
    monthly_budget_cents: Optional[int] = None
    monthly_spent_cents: int
    current_run: Optional[AgentStatusCurrentRun] = None
    last_activity: Optional[AgentStatusActivity] = None


__all__ = [
    "AgentDecisionPayload",
    "AgentRunPayload",
    "AgentStatusActivity",
    "AgentStatusCurrentRun",
    "AgentStatusPausedBy",
    "AgentStatusPayload",
    "AgentStatusPendingBreakdown",
    "ApproveBody",
    "DecisionAcceptedResult",
    "DecisionResolutionEffectPayload",
    "DiscardBody",
    "OverrideBody",
    "RoleVersionCommand",
    "RunNowBody",
]
