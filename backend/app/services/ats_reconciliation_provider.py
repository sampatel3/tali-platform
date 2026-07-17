"""Conservative, read-only ATS status classification for reconciliation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Protocol

from sqlalchemy.orm import Session

from ..components.integrations.bullhorn.stage_map import resolve_stage
from ..components.integrations.resolver import resolve_application_ats_provider
from ..models.organization import Organization
from .document_service import sanitize_text_for_storage


class ReconciliationSnapshot(Protocol):
    organization_id: int
    identity: Any


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _brief_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("kind") or value.get("slug") or value.get("name")
    return sanitize_text_for_storage(str(value or "").strip())[:200]


def _workable_observation(payload: dict[str, Any], target: str) -> dict[str, Any]:
    remote_id = str(payload.get("id") or "").strip()
    if not remote_id or remote_id != target:
        raise RuntimeError("Workable did not return the exact candidate requested")
    stage = _brief_text(
        payload.get("stage_kind")
        or payload.get("stage")
        or payload.get("stage_name")
        or payload.get("status")
    )
    stage_key = _normalized(stage).replace("-", "_").replace(" ", "_")
    disqualified = payload.get("disqualified")
    if disqualified is True or stage_key in {"rejected", "disqualified", "declined"}:
        outcome = "rejected"
    elif stage_key in {"hired", "withdrawn", "archived", "offer"}:
        outcome = "unsupported"
    elif disqualified is False or stage_key:
        outcome = "open"
    else:
        outcome = "unknown"
    return {
        "remote_outcome": outcome,
        "remote_status": stage or ("disqualified" if disqualified is True else "open"),
        "evidence": {
            "candidate_id": remote_id,
            "disqualified": disqualified if isinstance(disqualified, bool) else None,
            "stage": stage,
            "updated_at": _brief_text(payload.get("updated_at")),
        },
    }


def _bullhorn_observation(
    db: Session,
    org: Organization,
    payload: dict[str, Any],
    target: str,
) -> dict[str, Any]:
    remote_id = str(payload.get("id") or "").strip()
    if not remote_id or remote_id != target:
        raise RuntimeError("Bullhorn did not return the exact submission requested")
    remote_status = _brief_text(payload.get("status"))
    mapping = resolve_stage(db, org, remote_status)
    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
    placed_status = str(config.get("confirmedJobResponseStatus") or "").strip()
    if placed_status and remote_status == placed_status:
        outcome = "unsupported"
    elif payload.get("isDeleted") is True or mapping is None:
        outcome = "unknown"
    else:
        outcome = "rejected" if mapping.is_reject else "open"
    return {
        "remote_outcome": outcome,
        "remote_status": remote_status,
        "evidence": {
            "job_submission_id": remote_id,
            "status": remote_status,
            "is_deleted": payload.get("isDeleted") is True,
            "date_last_modified": payload.get("dateLastModified"),
            "mapping": (
                {
                    "taali_stage": mapping.taali_stage,
                    "is_reject": mapping.is_reject,
                }
                if mapping is not None
                else None
            ),
        },
    }


def read_provider_observation(
    db: Session, snapshot: ReconciliationSnapshot
) -> dict[str, Any]:
    """Read only the exact remote target; the caller holds no app row lock."""

    org = (
        db.query(Organization)
        .filter(Organization.id == snapshot.organization_id)
        .one_or_none()
    )
    if org is None:
        raise RuntimeError("Workspace is unavailable")
    # Resolve through an exact synthetic linkage. Passing ``None`` here would
    # invoke org-level Workable precedence and make a Bullhorn receipt in a
    # dual-connected workspace impossible to reconcile.
    exact_link = SimpleNamespace(
        workable_candidate_id=(
            snapshot.identity.provider_target_id
            if snapshot.identity.provider == "workable"
            else None
        ),
        bullhorn_job_submission_id=(
            snapshot.identity.provider_target_id
            if snapshot.identity.provider == "bullhorn"
            else None
        ),
    )
    provider = resolve_application_ats_provider(org, db, exact_link)
    if provider is None or _normalized(getattr(provider, "ats", "")) != snapshot.identity.provider:
        raise RuntimeError("The exact ATS provider is no longer connected")
    # Snapshot every scalar provider credential/config field on the detached
    # org, then release the read transaction and its pooled connection before
    # HTTP. Bullhorn token rotation remains durable because its provider hook
    # persists through the existing independent credential-generation CAS.
    db.expunge(org)
    db.commit()
    if db.in_transaction():  # pragma: no cover - defensive SQLAlchemy contract
        raise RuntimeError("Provider lookup transaction did not close")
    target = snapshot.identity.provider_target_id
    if snapshot.identity.provider == "workable":
        return _workable_observation(provider.get_candidate(target), target)
    if snapshot.identity.provider == "bullhorn":
        exact = provider.get_job_submission_status(target)
        if not isinstance(exact, dict) or str(exact.get("id")) != target:
            raise RuntimeError("Bullhorn did not return the exact submission requested")
        return _bullhorn_observation(db, org, exact, target)
    raise RuntimeError("Unsupported ATS provider")


__all__ = ["read_provider_observation"]
