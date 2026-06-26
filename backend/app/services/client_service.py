"""Consultancy CLIENTS service — CRUD, serialization, open-job counts, and the
per-requisition margin helper.

A client is an org-scoped account a recruiter fills roles for. Requisitions
(RoleBriefs) point at a client via ``client_id`` and carry a ``client_rate``;
margin (``client_rate - cost``) is computed on read, never stored. Mutators
flush but do NOT commit — the caller owns the transaction.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.client import Client
from ..models.role_brief import BRIEF_STATUS_APPLIED, RoleBrief

# Fields a recruiter may set on a client via create / PATCH.
_EDITABLE_FIELDS = frozenset({"name", "contact_name", "contact_email", "status"})


def compute_margin(
    client_rate: Optional[int],
    salary_min: Optional[int],
    salary_max: Optional[int],
) -> tuple[Optional[int], Optional[int]]:
    """Per-requisition economics.

    ``cost = salary_max if salary_max else salary_min``. When both
    ``client_rate`` and ``cost`` are present and ``client_rate > 0``::

        margin = client_rate - cost
        margin_pct = round(100 * margin / client_rate)

    Otherwise both are ``None``. Returns ``(margin, margin_pct)``.
    """
    cost = salary_max if salary_max else salary_min
    if client_rate and cost is not None and client_rate > 0:
        margin = client_rate - cost
        margin_pct = round(100 * margin / client_rate)
        return margin, margin_pct
    return None, None


def _open_job_counts(db: Session, organization_id: int) -> dict[int, int]:
    """``{client_id: open_requisition_count}`` for the whole org in ONE query.

    Open = a RoleBrief with this ``client_id`` whose status is not 'applied'
    (an applied brief has been materialized onto a live role and is no longer an
    open requisition for the client)."""
    rows = (
        db.query(RoleBrief.client_id, func.count(RoleBrief.id))
        .filter(
            RoleBrief.organization_id == organization_id,
            RoleBrief.client_id.isnot(None),
            RoleBrief.status != BRIEF_STATUS_APPLIED,
        )
        .group_by(RoleBrief.client_id)
        .all()
    )
    return {client_id: count for client_id, count in rows}


def serialize_client(client: Client, *, open_job_count: int = 0) -> dict[str, Any]:
    """A serialized client: id, name, contact_name, contact_email, status,
    open_job_count."""
    return {
        "id": client.id,
        "name": client.name,
        "contact_name": client.contact_name,
        "contact_email": client.contact_email,
        "status": client.status,
        "open_job_count": open_job_count,
    }


def get_client(db: Session, organization_id: int, client_id: int) -> Client:
    client = (
        db.query(Client)
        .filter(Client.id == client_id, Client.organization_id == organization_id)
        .first()
    )
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def list_clients(db: Session, organization_id: int) -> list[dict[str, Any]]:
    """All clients for the org, ordered by name, each with its open_job_count
    (no N+1: a single grouped count query backs all of them)."""
    clients = (
        db.query(Client)
        .filter(Client.organization_id == organization_id)
        .order_by(Client.name)
        .all()
    )
    counts = _open_job_counts(db, organization_id)
    return [
        serialize_client(c, open_job_count=counts.get(c.id, 0)) for c in clients
    ]


def create_client(
    db: Session,
    *,
    organization_id: int,
    name: str,
    contact_name: Optional[str] = None,
    contact_email: Optional[str] = None,
) -> Client:
    if not name or not name.strip():
        raise HTTPException(status_code=422, detail="Client name is required")
    client = Client(
        organization_id=organization_id,
        name=name.strip(),
        contact_name=contact_name,
        contact_email=contact_email,
    )
    db.add(client)
    db.flush()
    return client


def update_client(db: Session, client: Client, **fields) -> Client:
    """Set whitelisted client fields (ignores unknown keys)."""
    if "name" in fields:
        name = fields["name"]
        if name is None or not str(name).strip():
            raise HTTPException(status_code=422, detail="Client name is required")
        fields["name"] = str(name).strip()
    for key, value in fields.items():
        if key in _EDITABLE_FIELDS:
            setattr(client, key, value)
    db.flush()
    return client
