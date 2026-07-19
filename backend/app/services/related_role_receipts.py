"""Complete named-family data for related-role creation receipts."""

from __future__ import annotations

from sqlalchemy.orm import object_session

from ..models.role import ROLE_KIND_SISTER, Role


def created_role_family(related: Role, owner: Role) -> tuple[dict, str]:
    session = object_session(owner)
    if session is not None:
        # Creation receipts are the human confirmation of the whole shared-ATS
        # blast radius.  Never derive them from a possibly preloaded ORM
        # relationship: another committed family addition may not be present
        # in that identity-map collection.  The owner row is already the
        # serialization lock for creation; query the live, tenant-scoped family
        # and refresh any cached member identities under that authority.
        live_members = (
            session.query(Role)
            .filter(
                Role.organization_id == int(owner.organization_id),
                Role.role_kind == ROLE_KIND_SISTER,
                Role.ats_owner_role_id == int(owner.id),
                Role.deleted_at.is_(None),
            )
            .populate_existing()
            .all()
        )
    else:
        # Keep the serializer useful for detached/test objects while attached
        # production objects always take the authoritative query above.
        live_members = list(getattr(owner, "sister_roles", None) or [])
    members = {
        int(member.id): member
        for member in live_members
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
