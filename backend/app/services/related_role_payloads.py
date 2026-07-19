"""Stable response payloads for related-role creation workflows."""

from __future__ import annotations

from typing import Any

from ..models.role import Role
from ..models.role_brief import RoleBrief
from .ats_role_lifecycle import ats_job_lifecycle
from .related_role_receipts import created_role_family


def related_role_draft_payload(brief: RoleBrief) -> dict[str, Any]:
    source = brief.source_role
    return {
        "type": "related_role_draft",
        "created": True,
        "brief_id": int(brief.id),
        "source_role_id": int(brief.source_role_id),
        "source_role_name": source.name if source is not None else None,
        "proposed_name": brief.title,
        "completeness": int(brief.completeness or 0),
        "frontend_url": f"/requisitions?brief={brief.id}",
        "message": (
            "Created a pre-populated related-role draft in the job-creation chat. "
            "Review the cloned specification, describe any differences, then confirm creation and scoring there."
        ),
    }


def related_role_created_payload(
    related: Role, evaluation_counts: dict[str, int]
) -> dict[str, Any]:
    owner = getattr(related, "ats_owner_role", None)
    source_ats_provider = ats_job_lifecycle(owner).provider
    provider_label = "Bullhorn" if source_ats_provider == "bullhorn" else "Workable"
    role_family, family_labels = created_role_family(related, owner)
    return {
        "type": "related_role_created",
        "created": True,
        "role_id": int(related.id),
        "role_name": related.name,
        "source_role_id": int(related.ats_owner_role_id),
        "source_role_name": owner.name,
        "source_ats_provider": source_ats_provider,
        "role_family": role_family,
        "evaluation_counts": dict(evaluation_counts),
        "frontend_url": f"/jobs/{related.id}",
        "message": (
            f"Created {related.name} #{related.id} and queued its shared candidate roster for scoring. "
            "It now has its own Taali funnel, scoring Agent, and budget. "
            f"The {provider_label} application remains shared across all linked roles: "
            f"{family_labels}. Rejecting in any linked role rejects the candidate across all linked roles."
        ),
    }


__all__ = ["related_role_created_payload", "related_role_draft_payload"]
