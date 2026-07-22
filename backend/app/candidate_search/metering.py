"""Attribution context for routed candidate-search provider calls.

Candidate search can run either inside one role or across an entire workspace.
The universal provider adapter owns hard admission and settlement. This module
only normalizes candidate-search attribution and authority requirements; it
never creates a reservation or touches provider state.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..services.pricing_service import Feature


def search_metering(
    *,
    organization_id: int,
    role_id: int | None,
    feature: Feature,
    entity_id: str | None,
    sub_feature: str,
    trace_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    base_metering: dict[str, Any] | None = None,
    require_role_authority: bool = False,
) -> dict[str, Any]:
    """Return canonical attribution for one routed search attempt.

    ``role_id=None`` is intentional for a workspace-wide candidate search: the
    adapter's durable hold still debits the organization balance without
    inventing job attribution. With a role id, central admission also checks
    the role's monthly ceiling.
    """

    resolved_trace_id = str(trace_id or f"candidate-search:{uuid.uuid4().hex}")
    base = dict(base_metering or {})
    if base.get("credit_reservation") is not None:
        raise ValueError(
            "candidate search reservations are owned by the routing adapter"
        )
    meter = {
        **base,
        "feature": feature.value,
        "organization_id": int(organization_id),
        "trace_id": resolved_trace_id,
        "metadata": {
            **dict(base.get("metadata") or {}),
            **dict(metadata or {}),
            "sub_feature": str(sub_feature),
            "admission_scope": (
                "role" if role_id is not None else "organization"
            ),
        },
    }
    if role_id is not None:
        meter["role_id"] = int(role_id)
    else:
        meter.pop("role_id", None)
    if entity_id is not None:
        meter["entity_id"] = str(entity_id)
    else:
        meter.pop("entity_id", None)
    if require_role_authority:
        meter["require_role_authority"] = True
    else:
        meter.pop("require_role_authority", None)
    return meter


__all__ = ["search_metering"]
