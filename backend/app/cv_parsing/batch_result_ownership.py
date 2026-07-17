"""Tenant and entity ownership validation for CV batch result entries."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional

logger = logging.getLogger("taali.cv_parsing.batch")


@dataclass(frozen=True)
class BatchResultOwnership:
    context: dict
    organization_id: int


def validate_batch_result_ownership(
    *,
    context: dict,
    custom_id: str,
    application_id: int,
    anchor_organization_id: Optional[int],
) -> Optional[BatchResultOwnership]:
    """Return trusted result attribution, or fail closed on any mismatch."""
    per_context = context.get(custom_id)
    if not isinstance(per_context, dict):
        logger.warning(
            "cv_parse batch result missing durable context custom_id=%s",
            custom_id,
        )
        return None
    try:
        context_organization_id = int(per_context.get("organization_id"))
    except (TypeError, ValueError):
        context_organization_id = None
    expected_entity_id = f"application:{application_id}"
    normalized_anchor_id = (
        int(anchor_organization_id)
        if anchor_organization_id is not None
        else None
    )
    if (
        per_context.get("entity_id") != expected_entity_id
        or context_organization_id is None
        or (
            normalized_anchor_id is not None
            and context_organization_id != normalized_anchor_id
        )
    ):
        logger.warning(
            "cv_parse batch result ownership mismatch custom_id=%s "
            "entity_id=%s context_org=%s anchor_org=%s",
            custom_id,
            per_context.get("entity_id"),
            context_organization_id,
            normalized_anchor_id,
        )
        return None
    return BatchResultOwnership(
        context=per_context,
        organization_id=(
            normalized_anchor_id
            if normalized_anchor_id is not None
            else context_organization_id
        ),
    )


__all__ = ["BatchResultOwnership", "validate_batch_result_ownership"]
