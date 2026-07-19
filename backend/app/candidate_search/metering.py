"""Hard admission for paid candidate-search provider calls.

Candidate search can run either inside one role or across an entire workspace.
Both shapes must reserve organization credits before touching Anthropic; a
role-scoped search additionally enforces that role's monthly budget.  Keeping
the construction here makes the parser, reranker, and citation-grounding paths
use the same fail-closed contract.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..services.pricing_service import Feature
from ..services.provider_request_identity import provider_request_sha256
from ..services.provider_usage_admission import (
    reserve_provider_usage,
    with_credit_reservation,
)


def admitted_search_metering(
    *,
    organization_id: int,
    role_id: int | None,
    feature: Feature,
    entity_id: str | None,
    sub_feature: str,
    trace_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    base_metering: dict[str, Any] | None = None,
    provider_request: dict[str, Any],
) -> dict[str, Any]:
    """Reserve one bounded provider attempt and return its metering payload.

    ``role_id=None`` is intentional for a workspace-wide candidate search: the
    durable hold still serializes against and debits the organization balance,
    but does not invent job attribution.  With a role id, the shared admission
    service also checks the role's monthly ceiling.
    """

    base = dict(base_metering or {})
    resolved_trace_id = str(trace_id or f"candidate-search:{uuid.uuid4().hex}")
    model = provider_request.get("model")
    if type(model) is not str or not model.strip():
        raise ValueError("candidate search provider model is required")
    reservation = reserve_provider_usage(
        organization_id=organization_id,
        role_id=role_id,
        feature=feature,
        trace_id=resolved_trace_id,
        entity_id=entity_id,
        user_id=base.get("user_id"),
        candidate_id=base.get("candidate_id"),
        provider="anthropic",
        model=model.strip(),
        request_sha256=provider_request_sha256(provider_request),
        sub_feature=str(sub_feature),
        metadata={
            **dict(metadata or {}),
            "admission_scope": "role" if role_id is not None else "organization",
        },
    )
    meter = {
        **base,
        "feature": feature.value,
        "organization_id": organization_id,
        "trace_id": resolved_trace_id,
    }
    if role_id is not None:
        meter["role_id"] = role_id
    else:
        meter.pop("role_id", None)
    if entity_id is not None:
        meter["entity_id"] = entity_id
    return with_credit_reservation(meter, reservation)


__all__ = ["admitted_search_metering"]
