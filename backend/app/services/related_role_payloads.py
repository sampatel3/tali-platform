"""Transport-neutral response payloads for related-role creation."""

from __future__ import annotations

from typing import Any

from ..models.role import Role
from .ats_role_lifecycle import ats_job_lifecycle
from .related_role_receipts import created_role_family


def related_role_created_payload(
    related: Role,
    evaluation_counts: dict[str, int],
) -> dict[str, Any]:
    owner = getattr(related, "ats_owner_role", None)
    source = getattr(related, "related_source_role", None) or owner
    source_role_id = related.related_source_role_id or related.ats_owner_role_id
    source_ats_provider = ats_job_lifecycle(owner).provider
    provider_label = (
        "Bullhorn"
        if source_ats_provider == "bullhorn"
        else "Workable"
        if source_ats_provider == "workable"
        else "ATS"
    )
    role_family, _family_labels = created_role_family(related, owner)
    return {
        "type": "related_role_created",
        "created": True,
        "role_id": int(related.id),
        "role_name": related.name,
        "source_role_id": (
            int(source_role_id) if source_role_id is not None else None
        ),
        "source_role_name": source.name if source is not None else None,
        "source_ats_provider": source_ats_provider,
        "role_family": role_family,
        "evaluation_counts": dict(evaluation_counts),
        "frontend_url": f"/jobs/{related.id}",
        "message": (
            f"Created {related.name} #{related.id} and queued its initial candidate "
            "snapshot for scoring. It now owns its candidate membership, funnel, "
            "decisions, scoring Agent, and budget. Future candidates and actions do "
            f"not fan out to linked roles; the {provider_label} application is only "
            "optional write-back transport and may restrict an external action."
        ),
    }


__all__ = ["related_role_created_payload"]
