"""Explicit organization authority and metering for admin Graphiti calls."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from ..models.organization import Organization
from ..platform.database import SessionLocal
from ..services.metered_async_anthropic_client import (
    GraphMeteringContext,
    graph_metering_ctx,
)


def require_admin_graph_organization(organization_id: int) -> int:
    """Return an existing positive workspace id or reject before Graphiti."""

    if type(organization_id) is not int or organization_id <= 0:
        raise ValueError("organization_id must be a positive integer")
    value = organization_id
    with SessionLocal() as db:
        if db.query(Organization.id).filter(Organization.id == value).first() is None:
            raise LookupError("organization does not exist")
    return value


@contextmanager
def attributed_admin_graph_call(
    organization_id: int,
    *,
    operation: str,
) -> Iterator[None]:
    """Hard-admit every provider call made by one explicit admin operation."""

    org_id = require_admin_graph_organization(organization_id)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=org_id,
            episode_name=f"admin:{operation}",
            trace_id=f"admin-graphiti:{org_id}:{operation}",
            require_hard_admission=True,
            require_role_admission=False,
        )
    )
    try:
        yield
    finally:
        graph_metering_ctx.reset(token)


__all__ = ["attributed_admin_graph_call", "require_admin_graph_organization"]
