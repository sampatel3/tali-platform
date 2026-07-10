"""Curated multi-candidate client submittal pack (WS2).

An agency recruiter shares a *curated set* of candidates for one role as a
single client-facing link — the agency submittal. Built by composing existing
machinery: per-candidate client-safe scrubbing
(``application_detail_payload(client_safe=True)``) frozen into a multi-candidate
snapshot (mirroring ``TopCandidatesReport``) behind an unguessable token
(mirroring ``ShareLink``).

Deliberate platform decision (HANDOFF v2 §3): share links replace PDFs — there
is no PDF export here. No LLM calls, no metering, no Celery.

Endpoints:
- ``POST   /api/v1/roles/{role_id}/submittal-packs`` — mint (freeze snapshot)
- ``GET    /api/v1/roles/{role_id}/submittal-packs`` — audit list for the role
- ``DELETE /api/v1/submittal-packs/{pack_id}``       — revoke (org-scoped)
- ``GET    /submittal/{token}``  (public, no auth, gated by expiry + revoke)
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user, get_optional_current_user
from ...domains.assessments_runtime.role_support import (
    application_detail_payload,
    get_role,
)
from ...models.candidate_application import CandidateApplication
from ...models.submittal_pack import SubmittalPack
from ...models.user import User
from ...platform.database import get_db


router = APIRouter(tags=["Submittal packs"])


# Same preset vocabulary as the share modal, minus single-view (a curated
# client submittal is meant to be re-opened by the hiring team).
_EXPIRY_PRESETS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

# Hard cap on candidates per submittal — a client shortlist, not a data dump.
_MAX_CANDIDATES = 20


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _generate_token() -> str:
    return f"sub_{secrets.token_urlsafe(24)}"


def _candidate_entry(app: CandidateApplication, note: str | None) -> dict[str, Any]:
    """Freeze one client-safe candidate entry from the scrubbed payload.

    Takes ONLY fields the client-safe payload already exposes — name, the
    ``client_share_summary`` header (verdict / band / score / highlights), and
    the optional recruiter note. Recruiter-internal fields stripped by
    ``application_detail_payload(client_safe=True)`` are never read here.
    """
    payload = application_detail_payload(app, include_cv_text=False, client_safe=True)
    summary = payload.get("client_share_summary")
    summary = summary if isinstance(summary, dict) else {}
    return {
        "application_id": app.id,
        "candidate_name": payload.get("candidate_name"),
        "client_share_summary": summary,
        "verdict": summary.get("verdict"),
        "verdict_band": summary.get("verdict_band"),
        "score_100": summary.get("score_100"),
        "highlights": summary.get("highlights") or [],
        "note": (str(note).strip() or None) if note else None,
    }


def _serialize_pack(pack: SubmittalPack) -> dict[str, Any]:
    now = _utcnow()
    expires_at = _as_aware(pack.expires_at)
    revoked = pack.revoked_at is not None
    expired = expires_at is not None and expires_at <= now
    snapshot = pack.snapshot if isinstance(pack.snapshot, dict) else {}
    candidates = snapshot.get("candidates")
    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    return {
        "id": pack.id,
        "role_id": pack.role_id,
        "token": pack.token,
        "title": pack.title,
        "url_path": f"/submittal/{pack.token}",
        "candidate_count": candidate_count,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "revoked_at": pack.revoked_at.isoformat() if pack.revoked_at else None,
        "view_count": pack.view_count,
        "last_viewed_at": pack.last_viewed_at.isoformat() if pack.last_viewed_at else None,
        "created_at": pack.created_at.isoformat() if pack.created_at else None,
        "active": not (revoked or expired),
        "revoked": revoked,
        "expired": expired,
    }


class CreateSubmittalPackPayload(BaseModel):
    application_ids: list[int] = Field(
        ...,
        description="Ordered candidate application ids to include (1–20).",
    )
    title: str | None = Field(
        None, description="Client-facing title; defaults to the role title."
    )
    notes: dict[str, str] | None = Field(
        None,
        description="Optional map application_id (as string) → short recruiter "
        "note. CLIENT-VISIBLE.",
    )
    expires_in: str = Field(
        "7d", description="Expiry preset key: '24h' | '7d' | '30d'."
    )


@router.post("/roles/{role_id}/submittal-packs")
def create_submittal_pack(
    role_id: int,
    payload: CreateSubmittalPackPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.expires_in not in _EXPIRY_PRESETS:
        raise HTTPException(status_code=400, detail="Invalid expiry preset")

    application_ids = payload.application_ids or []
    if not application_ids:
        raise HTTPException(status_code=400, detail="At least one candidate is required")
    if len(application_ids) > _MAX_CANDIDATES:
        raise HTTPException(
            status_code=400,
            detail=f"A submittal pack can hold at most {_MAX_CANDIDATES} candidates",
        )

    # 404 if the role isn't the caller's org's — reuse the shared guard.
    role = get_role(role_id, current_user.organization_id, db)

    # De-dupe while preserving submitted order.
    ordered_ids: list[int] = []
    seen: set[int] = set()
    for app_id in application_ids:
        if app_id not in seen:
            seen.add(app_id)
            ordered_ids.append(app_id)

    apps = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id.in_(ordered_ids),
            CandidateApplication.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role.id,
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )
    by_id = {app.id: app for app in apps}
    # Any id not resolvable to a live application on this role+org is a 404 —
    # a foreign / cross-org / wrong-role id must not silently drop out.
    missing = [app_id for app_id in ordered_ids if app_id not in by_id]
    if missing:
        raise HTTPException(
            status_code=404,
            detail="One or more candidates do not belong to this role",
        )

    notes = payload.notes or {}
    candidates = [
        _candidate_entry(by_id[app_id], notes.get(str(app_id)))
        for app_id in ordered_ids
    ]

    org = getattr(role, "organization", None)
    org_name = getattr(org, "name", None)
    role_title = getattr(role, "name", None)
    title = (payload.title or "").strip() or role_title

    snapshot = {
        "role": {"title": role_title},
        "organization": {"name": org_name},
        "candidates": candidates,
    }

    pack = SubmittalPack(
        organization_id=current_user.organization_id,
        role_id=role.id,
        created_by_user_id=current_user.id,
        token=_generate_token(),
        title=title,
        snapshot=snapshot,
        expires_at=_utcnow() + _EXPIRY_PRESETS[payload.expires_in],
    )
    db.add(pack)
    db.commit()
    db.refresh(pack)

    return {
        "id": pack.id,
        "token": pack.token,
        "url_path": f"/submittal/{pack.token}",
        "expires_at": _as_aware(pack.expires_at).isoformat() if pack.expires_at else None,
    }


@router.get("/roles/{role_id}/submittal-packs")
def list_submittal_packs(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 404 if the role isn't the caller's org's.
    role = get_role(role_id, current_user.organization_id, db)
    packs = (
        db.query(SubmittalPack)
        .filter(
            SubmittalPack.role_id == role.id,
            SubmittalPack.organization_id == current_user.organization_id,
        )
        .order_by(SubmittalPack.created_at.desc())
        .all()
    )
    return {"packs": [_serialize_pack(pack) for pack in packs]}


@router.delete("/submittal-packs/{pack_id}")
def revoke_submittal_pack(
    pack_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pack = (
        db.query(SubmittalPack)
        .filter(
            SubmittalPack.id == pack_id,
            SubmittalPack.organization_id == current_user.organization_id,
        )
        .first()
    )
    if pack is None:
        raise HTTPException(status_code=404, detail="Submittal pack not found")
    if pack.revoked_at is None:
        pack.revoked_at = _utcnow()
        db.commit()
        db.refresh(pack)
    return _serialize_pack(pack)


# Public route — no auth, no /api/v1 prefix. Mounted from main.py at the app
# root so the client-facing URL works in any browser without a recruiter
# session. Mirrors GET /share/{token} / GET /report/{token}.
public_router = APIRouter(tags=["Submittal packs (public)"])


@public_router.get("/submittal/{token}")
def view_submittal_pack(
    token: str,
    db: Session = Depends(get_db),
    _user: User | None = Depends(get_optional_current_user),
):
    pack = (
        db.query(SubmittalPack)
        .filter(SubmittalPack.token == token)
        .first()
    )
    if pack is None:
        raise HTTPException(status_code=404, detail="Submittal pack not found")
    if pack.revoked_at is not None:
        raise HTTPException(status_code=410, detail="Submittal pack has been revoked")
    expires_at = _as_aware(pack.expires_at)
    now = _utcnow()
    if expires_at is not None and expires_at <= now:
        raise HTTPException(status_code=410, detail="Submittal pack has expired")

    pack.view_count = (pack.view_count or 0) + 1
    pack.last_viewed_at = now
    db.commit()

    snapshot = pack.snapshot if isinstance(pack.snapshot, dict) else {}
    return {
        "title": pack.title,
        "role": snapshot.get("role") or {},
        "organization": snapshot.get("organization") or {},
        "created_at": pack.created_at.isoformat() if pack.created_at else None,
        "candidates": snapshot.get("candidates") or [],
    }
