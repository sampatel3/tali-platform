"""Org-scoped, feature-gated Bullhorn connect, sync, mapping, and diagnostics."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ...components.integrations.bullhorn import bootstrap as bootstrap_mod
from ...components.integrations.bullhorn import stage_map as stage_map_mod
from ...deps import get_current_user, require_org_owner
from ...domains.assessments_runtime.pipeline_service import SYNC_MAPPABLE_STAGES
from ...models.ats_stage_map import AtsStageMap
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.user import User
from ...platform.admin_auth import require_admin_secret
from ...platform.config import settings
from ...platform.database import get_db
from .connect import BullhornConnectError, build_connect_auth
from .connect_lifecycle import BullhornConnectBusy, connect_and_start_full_sync
from .schemas import (
    ConnectRequest,
    StageMapReplaceRequest,
    SyncCancelRequest,
    SyncRequest,
)

# ``build_connect_auth`` is re-exported so tests monkeypatch
# ``routes.build_connect_auth`` to point the connect at the fake server.
__all__ = ["router", "build_connect_auth"]

logger = logging.getLogger("taali.bullhorn.routes")

router = APIRouter(prefix="/bullhorn", tags=["Bullhorn"])


# ---------------------------------------------------------------------------
# gating + org resolution helpers
# ---------------------------------------------------------------------------


def _assert_enabled() -> None:
    """503 unless the Bullhorn integration flag is on (Workable-gate analog)."""
    if not settings.BULLHORN_ENABLED:
        raise HTTPException(status_code=503, detail="Bullhorn integration is disabled")


def _get_org(db: Session, current_user: User) -> Organization:
    org = (
        db.query(Organization)
        .filter(Organization.id == current_user.organization_id)
        .first()
    )
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


def _assert_connected(org: Organization) -> None:
    if not (org.bullhorn_connected and org.bullhorn_refresh_token and org.bullhorn_username):
        raise HTTPException(status_code=400, detail="Bullhorn is not connected")


def _sync_in_progress(org: Organization) -> bool:
    """True when a sync is live for this org.

    Bullhorn has no per-run table — the runner writes a live progress JSON and
    clears it (``bullhorn_sync_progress = None``) when the run finalizes. A
    present progress dict that hasn't reached the terminal ``completed`` phase
    means a run is in flight.
    """
    progress = org.bullhorn_sync_progress
    if not isinstance(progress, dict):
        return False
    return progress.get("phase") != "completed"


def _fetched_status_list(org: Organization) -> set[str]:
    """The org's remote status strings, as cached on connect.

    ``seed_stage_map_from_categorization`` stamps the confirmed/placed status
    onto ``org.bullhorn_config`` but not the whole list; the full list is only
    known at connect. We keep the check simple (mirroring how the Workable config
    PATCH validates against known values): validate a mapping's ``remote_status``
    only when we HAVE a cached list — otherwise accept it (the sync surfaces any
    genuinely-unknown status as needs-mapping regardless).
    """
    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
    cached = config.get("status_list")
    if isinstance(cached, list):
        return {str(s).strip() for s in cached if str(s).strip()}
    return set()


# ---------------------------------------------------------------------------
# 1. POST /connect
# ---------------------------------------------------------------------------


@router.post("/connect")
def connect_bullhorn(
    body: ConnectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    """One-time Bullhorn connect. Password used in-memory only; never persisted.

    On success: discovered rest_url + connection flag stored, encrypted creds
    written, stage map seeded from categorization settings, and the remote status
    list cached (for the stage-map editor + needs-mapping surfacing). The response
    is credential-free.
    """
    _assert_enabled()
    org = _get_org(db, current_user)
    try:
        outcome = connect_and_start_full_sync(db, org, body)
    except BullhornConnectBusy as exc:
        raise HTTPException(
            status_code=503 if exc.lock_unavailable else 409,
            detail="Bullhorn is busy. Connection will not change; retry shortly.",
        ) from exc
    except BullhornConnectError as exc:
        # Recruiter-safe message; no credential ever reaches here.
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.public_message) from None

    return {
        "status": "connected",
        "bullhorn_connected": True,
        "rest_url": outcome.connect.rest_url,
        "statuses_count": len(outcome.connect.statuses),
        "seeded_stage_rows": outcome.connect.seeded_rows,
        "unmapped_status_count": outcome.unmapped_status_count,
        "initial_sync": outcome.initial_sync,
    }


# ---------------------------------------------------------------------------
# 2. GET /status
# ---------------------------------------------------------------------------


@router.get("/status")
def bullhorn_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Connection + last-sync + subscription health + unmapped-status count."""
    _assert_enabled()
    org = _get_org(db, current_user)

    connected = bool(org.bullhorn_connected)
    unmapped = stage_map_mod.unmapped_statuses(db, org) if connected else []

    return {
        "bullhorn_connected": connected,
        "bullhorn_rest_url": org.bullhorn_rest_url,
        "last_sync_at": org.bullhorn_last_sync_at,
        "last_sync_status": org.bullhorn_last_sync_status,
        "last_sync_summary": org.bullhorn_last_sync_summary or {},
        "sync_in_progress": _sync_in_progress(org),
        "sync_progress": org.bullhorn_sync_progress or {},
        "initial_sync": bootstrap_mod.initial_sync_status(org),
        # Subscription health: the incremental event poll keeps a subscription id
        # + a checkpoint requestId; presence of the subscription is the health
        # signal the connect UI shows.
        "event_subscription_active": bool(org.bullhorn_event_subscription_id),
        "event_subscription_id": org.bullhorn_event_subscription_id,
        "unmapped_status_count": len(unmapped),
        "unmapped_statuses": unmapped,
    }


# ---------------------------------------------------------------------------
# 3. POST /sync, GET /sync/status, POST /sync/cancel
# ---------------------------------------------------------------------------


@router.post("/sync")
def run_bullhorn_sync(
    body: SyncRequest | None = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    """Kick off a Bullhorn full sync in the background (mutex-aware).

    If a run is already in flight for this org, returns 202 ``already_running``
    and does NOT enqueue a second — mirroring Workable's in-progress short-circuit
    (Bullhorn's per-org mutex in the sync runner is the hard guard; this is the
    fast pre-check off the live progress marker).
    """
    _assert_enabled()
    org = _get_org(db, current_user)
    _assert_connected(org)

    if _sync_in_progress(org):
        progress = org.bullhorn_sync_progress or {}
        return JSONResponse(
            status_code=202,
            content={
                "status": "already_running",
                "phase": progress.get("phase"),
                "message": (
                    "A Bullhorn sync is already in progress. Polling the existing "
                    "background run instead of starting a new one."
                ),
            },
        )

    mode = (body.mode if body is not None else "full") or "full"
    sync_signal = bootstrap_mod.start_manual_full_sync(db, org, mode=mode)
    return {
        "status": "started",
        "mode": mode,
        "run_id": sync_signal["run_id"],
        "tracking_status": sync_signal["status"],
        "message": "Sync started in the background. Poll /bullhorn/sync/status for progress.",
    }


@router.get("/sync/status")
def bullhorn_sync_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Live sync progress for this org (from ``bullhorn_sync_progress``)."""
    _assert_enabled()
    org = _get_org(db, current_user)
    return {
        "sync_in_progress": _sync_in_progress(org),
        "sync_progress": org.bullhorn_sync_progress or {},
        "initial_sync": bootstrap_mod.initial_sync_status(org),
        "last_sync_at": org.bullhorn_last_sync_at,
        "last_sync_status": org.bullhorn_last_sync_status,
        "last_sync_summary": org.bullhorn_last_sync_summary or {},
        "db_snapshot": _db_snapshot(db, org.id),
    }


@router.post("/sync/cancel")
def cancel_bullhorn_sync(
    body: SyncCancelRequest | None = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    """Request cancellation of an in-flight sync.

    Sets ``cancel_requested`` in the live progress JSON — the runner checkpoints
    it at the next JobOrder/submission boundary and stops cleanly (the same
    checkpoint mechanism ``BullhornSyncService`` reads). No-op when idle.
    """
    _assert_enabled()
    org = _get_org(db, current_user)

    progress = org.bullhorn_sync_progress
    if not isinstance(progress, dict) or progress.get("phase") == "completed":
        return {"status": "ok", "message": "No sync in progress."}

    # Reassign the dict so SQLAlchemy tracks the JSON mutation by identity.
    updated = dict(progress)
    updated["cancel_requested"] = True
    org.bullhorn_sync_progress = updated
    db.add(org)
    db.commit()
    return {
        "status": "ok",
        "message": "Cancel requested. The sync will stop at the next safe checkpoint.",
    }


# ---------------------------------------------------------------------------
# 4. GET /stage-map, PUT /stage-map
# ---------------------------------------------------------------------------


@router.get("/stage-map")
def get_stage_map(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List this org's Bullhorn stage-map rows + the unmapped remote statuses."""
    _assert_enabled()
    org = _get_org(db, current_user)
    rows = (
        db.query(AtsStageMap)
        .filter(
            AtsStageMap.org_id == org.id,
            AtsStageMap.ats == stage_map_mod.ATS_BULLHORN,
        )
        .order_by(AtsStageMap.remote_status.asc())
        .all()
    )
    from ...components.integrations.bullhorn.write_back import resolved_write_targets
    return {
        "pipeline_stages": list(SYNC_MAPPABLE_STAGES),
        "mappings": [
            {
                "remote_status": r.remote_status,
                "taali_stage": r.taali_stage,
                "is_reject": bool(r.is_reject),
            }
            for r in rows
        ],
        "unmapped_statuses": stage_map_mod.unmapped_statuses(db, org),
        "resolved_write_targets": resolved_write_targets(db, org),
    }


@router.put("/stage-map")
def replace_stage_map(
    body: StageMapReplaceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    """Replace ALL of this org's Bullhorn stage-map rows.

    Validates each ``taali_stage`` against ``PIPELINE_STAGES``. A
    ``remote_status`` is rejected only when the org HAS a cached remote status
    list and the value isn't in it (mirrors how the Workable config PATCH
    validates against known values — kept simple; the sync surfaces genuinely
    unknown statuses as needs-mapping regardless).
    """
    _assert_enabled()
    org = _get_org(db, current_user)

    known_statuses = _fetched_status_list(org)
    seen: set[str] = set()
    cleaned: list[tuple[str, str, bool]] = []
    for row in body.mappings:
        remote = row.remote_status.strip()
        stage = row.taali_stage.strip()
        if not remote or not stage:
            continue
        if stage not in SYNC_MAPPABLE_STAGES:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown Taali stage '{stage}'. Must be one of {list(SYNC_MAPPABLE_STAGES)}.",
            )
        if known_statuses and remote not in known_statuses:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown Bullhorn status '{remote}' for this workspace.",
            )
        if remote in seen:
            # First occurrence wins; skip later dups so the insert can't violate
            # the (org_id, ats, remote_status) unique constraint.
            continue
        seen.add(remote)
        cleaned.append((remote, stage, bool(row.is_reject)))

    ambiguity = stage_map_mod.write_target_ambiguity_detail(org, cleaned)
    if ambiguity:
        raise HTTPException(
            status_code=422,
            detail=ambiguity,
        )

    # Replace-org-mappings: drop this org's Bullhorn rows, then insert the new set
    # in one transaction.
    db.query(AtsStageMap).filter(
        AtsStageMap.org_id == org.id,
        AtsStageMap.ats == stage_map_mod.ATS_BULLHORN,
    ).delete(synchronize_session=False)
    for remote, stage, is_reject in cleaned:
        db.add(
            AtsStageMap(
                org_id=org.id,
                ats=stage_map_mod.ATS_BULLHORN,
                remote_status=remote,
                taali_stage=stage,
                is_reject=is_reject,
            )
        )
    db.commit()

    return {
        "status": "ok",
        "mappings_count": len(cleaned),
        "unmapped_statuses": stage_map_mod.unmapped_statuses(db, org),
    }


# ---------------------------------------------------------------------------
# 5. GET /admin/diagnostic
# ---------------------------------------------------------------------------


@router.get(
    "/admin/diagnostic",
    dependencies=[Depends(_assert_enabled), Depends(require_admin_secret)],
)
def admin_bullhorn_diagnostic(
    email: str = Query(..., description="User email whose org to diagnose"),
    db: Session = Depends(get_db),
):
    """Admin-gated Bullhorn diagnostic using the dedicated operator secret.

    Redacts every credential: only booleans (has-secret / has-refresh-token) and
    non-secret state (connection, subscription, checkpoint, last sync) are
    returned, plus a live REST session ping result.
    """
    email_clean = (email or "").strip().lower()
    if not email_clean:
        raise HTTPException(status_code=400, detail="email required")
    user = db.query(User).filter(User.email == email_clean).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User not found: {email_clean}")
    org = db.query(Organization).filter(Organization.id == user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    return {
        "org_id": org.id,
        "bullhorn_connected": bool(org.bullhorn_connected),
        # REDACTED: presence-only booleans, never the ciphertext or plaintext.
        "has_client_id": bool(org.bullhorn_client_id),
        "has_client_secret": bool(org.bullhorn_client_secret),
        "has_refresh_token": bool(org.bullhorn_refresh_token),
        "username": org.bullhorn_username,
        "rest_url": org.bullhorn_rest_url,
        "session_ping": _admin_session_ping(org),
        "event_subscription_id": org.bullhorn_event_subscription_id,
        "event_request_id_checkpoint": org.bullhorn_event_request_id,
        "last_sync_at": org.bullhorn_last_sync_at,
        "last_sync_status": org.bullhorn_last_sync_status,
        "last_sync_summary": org.bullhorn_last_sync_summary or {},
        "unmapped_status_count": len(stage_map_mod.unmapped_statuses(db, org)),
    }


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _db_snapshot(db: Session, org_id: int) -> dict:
    return {
        "roles_active": db.query(Role)
        .filter(Role.organization_id == org_id, Role.deleted_at.is_(None))
        .count(),
        "applications_active": db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .count(),
        "candidates_active": db.query(Candidate)
        .filter(Candidate.organization_id == org_id, Candidate.deleted_at.is_(None))
        .count(),
    }


def _admin_session_ping(org: Organization) -> dict:
    """Build an authed client from stored creds and ping — for the admin view.

    Best-effort: any failure is reported as ``{"ok": False, "error": ...}`` with
    the message redacted of query strings (which could carry a token). Never
    raises; never logs a credential.
    """
    if not (org.bullhorn_connected and org.bullhorn_refresh_token):
        return {"ok": False, "error": "not connected"}
    try:
        from ...components.integrations.bullhorn.sync_runner import _build_service

        service = _build_service(org)
        result = service.ping()
        return {"ok": True, "session_expires": result.get("sessionExpires")}
    except Exception as exc:  # noqa: BLE001 — diagnostic must never raise
        from ...components.integrations.bullhorn.errors import redact_exc

        return {"ok": False, "error": redact_exc(exc)}
