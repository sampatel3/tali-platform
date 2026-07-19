"""Typed API contracts for authenticated operator health probes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class AgentWorkerHealthResponse(BaseModel):
    """Aggregate worker heartbeat data emitted by the runtime health service."""

    model_config = ConfigDict(extra="allow")

    ready: bool
    reason: str | None = None
    age_seconds: float | None = None
    failed_queues: list[str] | None = None
    queues: dict[str, dict[str, Any]] | None = None
    capability_reporting: bool | None = None
    detail: str | None = None


class S3HealthResponse(BaseModel):
    """Optional object-storage diagnostics included on operator health."""

    # Do not reflect arbitrary provider response fields through operator APIs.
    model_config = ConfigDict(extra="ignore")

    available: bool
    ok: bool | None = None
    configured: bool | None = None
    bucket: str | None = None
    region: str | None = None
    status: str | None = None
    reason: str | None = None
    provider_code: str | None = None
    provider_status_code: int | None = None


class UsageMeterHealthResponse(BaseModel):
    # Health payloads are operational diagnostics. Preserve additive fields so
    # introducing an OpenAPI response model cannot narrow the runtime contract.
    model_config = ConfigDict(extra="allow")

    mode: Literal["live", "shadow", "shadow_emergency_override"]
    live: bool
    ready: bool
    production_emergency_override: bool


class IntegrationHealthResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    e2b_configured: bool
    claude_configured: bool
    workable_configured: bool
    workable_connector_enabled: bool
    workable_oauth_app_configured: bool
    bullhorn_connector_enabled: bool
    stripe_configured: bool
    resend_configured: bool


class AdminHealthResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["healthy", "degraded"]
    service: Literal["taali-api"]
    database: bool
    redis: bool
    agent_worker: AgentWorkerHealthResponse
    s3: S3HealthResponse
    usage_meter: UsageMeterHealthResponse
    integrations: IntegrationHealthResponse


class GraphitiHealthResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["ok", "initializing", "unconfigured", "error"]


class AdminForbiddenResponse(BaseModel):
    detail: Literal["Forbidden"]


class GithubHealthResponse(BaseModel):
    """Result of the non-mutating GitHub credential probe."""

    model_config = ConfigDict(extra="allow")

    ok: bool
    status_code: int | None = None
    detail: str
    org: str
    mock: bool | None = None


ADMIN_HEALTH_OPENAPI: dict[str, Any] = {
    "response_model": AdminHealthResponse,
    # Optional provider fields must remain absent when the underlying probe did
    # not return them; adding null keys would change the established payload.
    "response_model_exclude_unset": True,
    "responses": {
        403: {
            "model": AdminForbiddenResponse,
            "description": "Missing or invalid admin secret.",
        },
    },
}


GITHUB_HEALTH_OPENAPI: dict[str, Any] = {
    "response_model": GithubHealthResponse,
    "response_model_exclude_unset": True,
    "responses": {
        403: {
            "model": AdminForbiddenResponse,
            "description": "Missing or invalid admin secret.",
        },
    },
}


GRAPHITI_HEALTH_OPENAPI: dict[str, Any] = {
    "response_model": GraphitiHealthResponse,
    "responses": {
        403: {
            "model": AdminForbiddenResponse,
            "description": "Missing or invalid admin secret.",
        },
        503: {
            "model": GraphitiHealthResponse,
            "description": "Graphiti is initializing or its live probe failed.",
        },
    },
}


__all__ = [
    "ADMIN_HEALTH_OPENAPI",
    "AdminHealthResponse",
    "AdminForbiddenResponse",
    "GITHUB_HEALTH_OPENAPI",
    "GRAPHITI_HEALTH_OPENAPI",
    "GraphitiHealthResponse",
    "GithubHealthResponse",
]
