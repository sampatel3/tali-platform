"""Small tenant-scoped role lookup and readiness helpers."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.role import Role


def role_has_job_spec(role: Role) -> bool:
    return bool(
        (role.job_spec_file_url or "").strip()
        or (role.job_spec_text or "").strip()
        or (role.description or "").strip()
    )


def get_role(role_id: int, org_id: int, db: Session) -> Role:
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == org_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


__all__ = ["get_role", "role_has_job_spec"]
