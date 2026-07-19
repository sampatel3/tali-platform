"""Compatibility re-export for the former candidate-graph contract location."""

from ..platform.health_contracts import (
    ADMIN_HEALTH_OPENAPI as ADMIN_HEALTH_OPENAPI,
    GITHUB_HEALTH_OPENAPI as GITHUB_HEALTH_OPENAPI,
    GRAPHITI_HEALTH_OPENAPI as GRAPHITI_HEALTH_OPENAPI,
    AdminForbiddenResponse as AdminForbiddenResponse,
    AdminHealthResponse as AdminHealthResponse,
    AgentWorkerHealthResponse as AgentWorkerHealthResponse,
    GithubHealthResponse as GithubHealthResponse,
    GraphitiHealthResponse as GraphitiHealthResponse,
    IntegrationHealthResponse as IntegrationHealthResponse,
    S3HealthResponse as S3HealthResponse,
    UsageMeterHealthResponse as UsageMeterHealthResponse,
    __all__ as __all__,
)
