"""Complete named-family data for related-role creation receipts."""

from __future__ import annotations

from ..models.role import Role


def created_role_family(related: Role, owner: Role) -> tuple[dict, str]:
    members = {
        int(member.id): member
        for member in list(getattr(owner, "sister_roles", None) or [])
        if int(getattr(member, "organization_id", 0) or 0)
        == int(getattr(owner, "organization_id", 0) or 0)
        and getattr(member, "deleted_at", None) is None
    }
    members[int(related.id)] = related
    ordered = sorted(
        members.values(),
        key=lambda member: (str(member.name or "").casefold(), int(member.id)),
    )
    family = {
        "owner": {"id": int(owner.id), "name": owner.name},
        "related": [
            {"id": int(member.id), "name": member.name} for member in ordered
        ],
    }
    labels = ", ".join(
        [f"{owner.name} #{owner.id}"]
        + [f"{member.name} #{member.id}" for member in ordered]
    )
    return family, labels
