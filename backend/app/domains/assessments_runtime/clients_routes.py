"""Clients API (recruiter, JWT) — the consultancy CLIENT book.

A client is the company a recruiter fills roles for. Requisitions (RoleBriefs)
are opened for a client and billed at a client rate; this router exposes
CRUD + the per-client open-requisition rollup. All endpoints are org-scoped via
the JWT'd user's organization.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.role_brief import RoleBrief
from ...models.user import User
from ...platform.database import get_db
from ...services.client_service import (
    create_client,
    get_client,
    list_clients,
    open_job_count_for_client,
    serialize_client,
    update_client,
)

router = APIRouter(tags=["Clients"])


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #
class CreateClient(BaseModel):
    name: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None


class UpdateClient(BaseModel):
    name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    status: Optional[str] = None


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post("/clients", status_code=201)
def create_client_endpoint(
    data: CreateClient,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a client (open_job_count starts at 0). 422 if name is blank."""
    client = create_client(
        db,
        organization_id=current_user.organization_id,
        name=data.name,
        contact_name=data.contact_name,
        contact_email=data.contact_email,
    )
    db.commit()
    db.refresh(client)
    return serialize_client(client, open_job_count=0)


@router.get("/clients")
def list_clients_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """All clients for the org, ordered by name, each with its open_job_count."""
    return list_clients(db, current_user.organization_id)


@router.get("/clients/{client_id}")
def get_client_endpoint(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A single client + its requisitions. 404 if not in the caller's org.

    ``open_job_count`` counts the client's PUBLISHED job pages (consistent with
    the list endpoint); the ``requisitions`` block lists every brief assigned to
    the client regardless of publish state."""
    client = get_client(db, current_user.organization_id, client_id)
    briefs = (
        db.query(RoleBrief)
        .filter(
            RoleBrief.organization_id == current_user.organization_id,
            RoleBrief.client_id == client_id,
        )
        .order_by(RoleBrief.id.desc())
        .all()
    )
    open_job_count = open_job_count_for_client(
        db, current_user.organization_id, client_id
    )
    payload: dict[str, Any] = serialize_client(client, open_job_count=open_job_count)
    payload["requisitions"] = [
        {
            "id": b.id,
            "title": b.title,
            "status": b.status,
            "completeness": int(b.completeness or 0),
        }
        for b in briefs
    ]
    return payload


@router.patch("/clients/{client_id}")
def update_client_endpoint(
    client_id: int,
    data: UpdateClient,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a client (whitelisted fields). 404 if not in the caller's org."""
    client = get_client(db, current_user.organization_id, client_id)
    fields = data.model_dump(exclude_unset=True)
    update_client(db, client, **fields)
    db.commit()
    db.refresh(client)
    # open_job_count for the single client (published job pages — consistent
    # with the list endpoint).
    open_job_count = open_job_count_for_client(
        db, current_user.organization_id, client.id
    )
    return serialize_client(client, open_job_count=open_job_count)
