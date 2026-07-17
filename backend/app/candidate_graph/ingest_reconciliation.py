"""Owner-visible, attested recovery for ambiguous graph-ingest operations.

An operation reaches ``reconciliation_required`` only after the durable
provider-start marker was crossed and its final outcome could not be proved.
This module never guesses: a workspace owner may confirm that the *entire
exact operation* is present, or attest that it is entirely absent and thereby
authorize the ordinary outbox dispatcher to try it again. Partial or uncertain
provider state remains fenced for support review.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models.graph_ingest_dispatch import (
    GRAPH_INGEST_COMPLETE,
    GRAPH_INGEST_PENDING,
    GRAPH_INGEST_RECONCILIATION,
    GRAPH_INGEST_STATUSES,
    GRAPH_INGEST_WORK_KINDS,
    GraphIngestDispatch,
)
from .ingest_manifest import (
    public_operation_manifest,
    validate_operation_manifest,
)


CONFIRM_ENTIRE_OPERATION_PRESENT = "confirm_entire_operation_present"
RETRY_AFTER_ENTIRE_OPERATION_ABSENT = "retry_after_entire_operation_absent"

_MAX_HISTORY_ENTRIES = 100
_MAX_HISTORY_BYTES = 256 * 1024
_MAX_SOURCE_REFS = 100
_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_KIND = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")
_UUIDISH = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HISTORY_KEYS = frozenset(
    {
        "version",
        "action",
        "actor_id",
        "actor_type",
        "resolved_at",
        "attestation",
        "prior_state",
    }
)
_ATTESTATION_KEYS = frozenset(
    {
        "entire_exact_operation_present",
        "entire_exact_operation_absent",
    }
)
_PRIOR_STATE_V1_KEYS = frozenset(
    {
        "operation_id",
        "organization_id",
        "work_kind",
        "entity_id",
        "source_refs_sha256",
        "status",
        "dispatch_attempts",
        "dispatch_nonce",
        "worker_attempt_nonce",
        "next_attempt_at",
        "dispatched_at",
        "claimed_at",
        "provider_attempt_started_at",
        "completed_at",
        "last_error_code",
        "created_at",
        "updated_at",
    }
)
_PRIOR_STATE_V2_KEYS = _PRIOR_STATE_V1_KEYS | {"operation_manifest_sha256"}
_CURSOR_KEYS = frozenset({"version", "completed_at", "operation_id"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)[:64]


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _source_refs_fingerprint(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _conflict(
            "Stored graph source evidence is malformed; no evidence was overwritten."
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _public_source_refs(value: Any) -> tuple[list[dict[str, Any]], str]:
    """Return only the documented secret-free ``kind``/``id`` identities."""

    if not isinstance(value, list) or len(value) > _MAX_SOURCE_REFS:
        return [], "support_review_required"
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"kind", "id"}:
            return [], "support_review_required"
        kind = item.get("kind")
        entity_id = item.get("id")
        if (
            not isinstance(kind, str)
            or _SAFE_KIND.fullmatch(kind) is None
            or isinstance(entity_id, bool)
            or not isinstance(entity_id, int)
            or entity_id < 0
        ):
            return [], "support_review_required"
        result.append({"kind": kind, "id": entity_id})
    return result, "available"


def _safe_iso_text(value: Any, *, optional: bool = True) -> bool:
    if value is None:
        return optional
    if not isinstance(value, str) or not value or len(value) > 64:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _validate_history_item(item: Any) -> None:
    """Accept only the bounded schema written below; never echo arbitrary JSON."""

    if not isinstance(item, dict) or set(item) != _HISTORY_KEYS:
        raise _conflict(
            "Stored graph reconciliation history is malformed; no evidence was overwritten."
        )
    version = item.get("version")
    if version not in {1, 2}:
        raise _conflict(
            "Stored graph reconciliation history has an unsupported version; no evidence was overwritten."
        )
    if item.get("action") not in {
        CONFIRM_ENTIRE_OPERATION_PRESENT,
        RETRY_AFTER_ENTIRE_OPERATION_ABSENT,
    }:
        raise _conflict(
            "Stored graph reconciliation history has an invalid action; no evidence was overwritten."
        )
    if (
        isinstance(item.get("actor_id"), bool)
        or not isinstance(item.get("actor_id"), int)
        or int(item["actor_id"]) <= 0
        or item.get("actor_type") != "workspace_owner"
        or not _safe_iso_text(item.get("resolved_at"), optional=False)
    ):
        raise _conflict(
            "Stored graph reconciliation actor evidence is malformed; no evidence was overwritten."
        )

    attestation = item.get("attestation")
    if not isinstance(attestation, dict) or set(attestation) != _ATTESTATION_KEYS:
        raise _conflict(
            "Stored graph reconciliation attestation is malformed; no evidence was overwritten."
        )
    present = attestation.get("entire_exact_operation_present")
    absent = attestation.get("entire_exact_operation_absent")
    if (
        not isinstance(present, bool)
        or not isinstance(absent, bool)
        or present == absent
    ):
        raise _conflict(
            "Stored graph reconciliation attestation is ambiguous; no evidence was overwritten."
        )

    prior = item.get("prior_state")
    expected_prior_keys = (
        _PRIOR_STATE_V1_KEYS if version == 1 else _PRIOR_STATE_V2_KEYS
    )
    if not isinstance(prior, dict) or set(prior) != expected_prior_keys:
        raise _conflict(
            "Stored graph reconciliation state evidence is malformed; no evidence was overwritten."
        )
    integer_fields = (
        "organization_id",
        "entity_id",
        "dispatch_attempts",
    )
    if any(
        isinstance(prior.get(field), bool)
        or not isinstance(prior.get(field), int)
        or int(prior[field]) < (1 if field == "organization_id" else 0)
        for field in integer_fields
    ):
        raise _conflict(
            "Stored graph reconciliation numeric evidence is malformed; no evidence was overwritten."
        )
    if (
        not isinstance(prior.get("operation_id"), str)
        or _UUIDISH.fullmatch(prior["operation_id"]) is None
        or prior.get("work_kind") not in GRAPH_INGEST_WORK_KINDS
        or prior.get("status") not in GRAPH_INGEST_STATUSES
        or not isinstance(prior.get("source_refs_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", prior["source_refs_sha256"]) is None
    ):
        raise _conflict(
            "Stored graph reconciliation identity evidence is malformed; no evidence was overwritten."
        )
    if version == 2 and (
        not isinstance(prior.get("operation_manifest_sha256"), str)
        or re.fullmatch(
            r"[0-9a-f]{64}",
            prior["operation_manifest_sha256"],
        )
        is None
    ):
        raise _conflict(
            "Stored graph manifest identity evidence is malformed; no evidence was overwritten."
        )
    for field in ("dispatch_nonce", "worker_attempt_nonce"):
        value = prior.get(field)
        if value is not None and (
            not isinstance(value, str) or _UUIDISH.fullmatch(value) is None
        ):
            raise _conflict(
                "Stored graph reconciliation attempt evidence is malformed; no evidence was overwritten."
            )
    for field in (
        "next_attempt_at",
        "dispatched_at",
        "claimed_at",
        "provider_attempt_started_at",
        "completed_at",
        "created_at",
        "updated_at",
    ):
        if not _safe_iso_text(prior.get(field)):
            raise _conflict(
                "Stored graph reconciliation timestamp evidence is malformed; no evidence was overwritten."
            )
    last_error_code = prior.get("last_error_code")
    if last_error_code is not None and (
        not isinstance(last_error_code, str)
        or _SAFE_CODE.fullmatch(last_error_code) is None
    ):
        raise _conflict(
            "Stored graph reconciliation error evidence is unsafe; no evidence was overwritten."
        )


def _validated_history(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > _MAX_HISTORY_ENTRIES:
        raise _conflict(
            "Stored graph reconciliation history is too large or malformed; no evidence was overwritten."
        )
    for item in value:
        _validate_history_item(item)
    try:
        size = len(
            json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise _conflict(
            "Stored graph reconciliation history is malformed; no evidence was overwritten."
        ) from exc
    if size > _MAX_HISTORY_BYTES:
        raise _conflict(
            "Stored graph reconciliation history is too large; no evidence was overwritten."
        )
    return deepcopy(value)


def _prior_state(row: GraphIngestDispatch) -> dict[str, Any]:
    return {
        "operation_id": str(row.operation_id),
        "organization_id": int(row.organization_id),
        "work_kind": str(row.work_kind),
        "entity_id": int(row.entity_id),
        "source_refs_sha256": _source_refs_fingerprint(row.source_refs),
        "operation_manifest_sha256": str(row.operation_manifest_sha256),
        "status": str(row.status),
        "dispatch_attempts": int(row.dispatch_attempts or 0),
        "dispatch_nonce": row.dispatch_nonce,
        "worker_attempt_nonce": row.worker_attempt_nonce,
        "next_attempt_at": _iso(row.next_attempt_at),
        "dispatched_at": _iso(row.dispatched_at),
        "claimed_at": _iso(row.claimed_at),
        "provider_attempt_started_at": _iso(row.provider_attempt_started_at),
        "completed_at": _iso(row.completed_at),
        "last_error_code": row.last_error_code,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _resolution_entry(
    row: GraphIngestDispatch,
    *,
    action: str,
    actor_id: int,
) -> dict[str, Any]:
    present = action == CONFIRM_ENTIRE_OPERATION_PRESENT
    entry = {
        "version": 2,
        "action": action,
        "actor_id": int(actor_id),
        "actor_type": "workspace_owner",
        "resolved_at": _iso(_now()),
        "attestation": {
            "entire_exact_operation_present": present,
            "entire_exact_operation_absent": not present,
        },
        "prior_state": _prior_state(row),
    }
    _validate_history_item(entry)
    return entry


def owner_authorized_exact_replay(row: GraphIngestDispatch) -> bool:
    """Prove the latest full v2 owner evidence authorizes this exact payload."""

    try:
        history = _validated_history(row.reconciliation_history)
    except HTTPException:
        return False
    if not history:
        return False
    latest = history[-1]
    prior_state = latest["prior_state"]
    try:
        return bool(
            latest["version"] == 2
            and latest["action"] == RETRY_AFTER_ENTIRE_OPERATION_ABSENT
            and latest["attestation"]["entire_exact_operation_present"] is False
            and latest["attestation"]["entire_exact_operation_absent"] is True
            and prior_state["operation_id"] == str(row.operation_id)
            and prior_state["organization_id"] == int(row.organization_id)
            and prior_state["work_kind"] == str(row.work_kind)
            and prior_state["entity_id"] == int(row.entity_id)
            and prior_state["source_refs_sha256"]
            == _source_refs_fingerprint(row.source_refs)
            and prior_state["operation_manifest_sha256"]
            == row.operation_manifest_sha256
        )
    except (HTTPException, TypeError, ValueError):
        return False


def _public_error_code(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and _SAFE_CODE.fullmatch(value) is not None:
        return value
    return "support_review_required"


def _manifest_evidence(
    row: GraphIngestDispatch,
) -> tuple[str, dict[str, Any] | None]:
    if row.operation_manifest is None and row.operation_manifest_sha256 is None:
        return "legacy_unavailable", None
    try:
        manifest = validate_operation_manifest(
            row.operation_manifest,
            row.operation_manifest_sha256,
            work_kind=str(row.work_kind),
            entity_id=int(row.entity_id),
        )
    except ValueError:
        return "support_review_required", None
    if int(manifest["episode_count"]) < 1:
        # An empty manifest proves a terminal no-provider no-op, not an
        # ambiguous provider operation an owner can safely attest to.
        return "support_review_required", None
    return (
        "available",
        public_operation_manifest(manifest, str(row.operation_manifest_sha256)),
    )


def _encode_reconciliation_cursor(row: GraphIngestDispatch) -> str:
    raw = json.dumps(
        {
            "version": 1,
            "completed_at": _iso(row.completed_at),
            "operation_id": str(row.operation_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_reconciliation_cursor(value: str) -> tuple[datetime | None, str]:
    encoded = str(value or "")
    if not encoded or len(encoded) > 512 or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
        raise HTTPException(status_code=422, detail="Invalid graph reconciliation cursor")
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode(
                "utf-8"
            )
        )
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Invalid graph reconciliation cursor",
        ) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != _CURSOR_KEYS
        or payload.get("version") != 1
        or not isinstance(payload.get("operation_id"), str)
        or _UUIDISH.fullmatch(payload["operation_id"]) is None
    ):
        raise HTTPException(status_code=422, detail="Invalid graph reconciliation cursor")
    timestamp_text = payload.get("completed_at")
    if timestamp_text is None:
        timestamp = None
    elif isinstance(timestamp_text, str) and _safe_iso_text(
        timestamp_text,
        optional=False,
    ):
        timestamp = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
    else:
        raise HTTPException(status_code=422, detail="Invalid graph reconciliation cursor")
    return timestamp, payload["operation_id"]


def public_reconciliation_evidence(row: GraphIngestDispatch) -> dict[str, Any]:
    source_refs, source_evidence_state = _public_source_refs(row.source_refs)
    history_state = "available"
    history_count = 0
    last_resolution = None
    try:
        history = _validated_history(row.reconciliation_history)
        history_count = len(history)
        if history:
            latest = history[-1]
            last_resolution = {
                "action": latest["action"],
                "actor_id": latest["actor_id"],
                "resolved_at": latest["resolved_at"],
                "attestation": deepcopy(latest["attestation"]),
            }
    except HTTPException:
        history_state = "support_review_required"
    manifest_state, manifest_evidence = _manifest_evidence(row)
    attempt_nonce = row.worker_attempt_nonce
    attempt_fence_available = bool(
        isinstance(attempt_nonce, str)
        and _UUIDISH.fullmatch(attempt_nonce)
        and row.provider_attempt_started_at is not None
        and manifest_state == "available"
    )
    result = {
        "operation_id": str(row.operation_id),
        "work_kind": str(row.work_kind),
        "entity_id": int(row.entity_id),
        "source_refs": source_refs,
        "source_evidence_state": source_evidence_state,
        "source_refs_sha256": _source_refs_fingerprint(row.source_refs),
        "status": str(row.status),
        "dispatch_attempts": int(row.dispatch_attempts or 0),
        "expected_attempt_nonce": attempt_nonce if attempt_fence_available else None,
        "attempt_fence_available": attempt_fence_available,
        "dispatched_at": _iso(row.dispatched_at),
        "claimed_at": _iso(row.claimed_at),
        "provider_attempt_started_at": _iso(row.provider_attempt_started_at),
        "reconciliation_required_at": _iso(row.completed_at),
        "last_error_code": _public_error_code(row.last_error_code),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "reconciliation_history_state": history_state,
        "reconciliation_count": history_count,
        "last_resolution": last_resolution,
        "operation_manifest_state": manifest_state,
    }
    if manifest_evidence is not None:
        result.update(manifest_evidence)
    else:
        result.update(
            operation_manifest_sha256=None,
            operation_episode_count=None,
            operation_episodes=[],
        )
    return result


def list_reconciliation_operations(
    db: Session,
    *,
    organization_id: int,
    limit: int = 20,
    offset: int = 0,
    cursor: str | None = None,
) -> dict[str, Any]:
    page_limit = max(1, min(int(limit), 100))
    normalized_offset = max(0, int(offset))
    if cursor and normalized_offset:
        raise HTTPException(
            status_code=422,
            detail="Graph reconciliation cursor cannot be combined with offset",
        )
    query = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.organization_id == int(organization_id),
            GraphIngestDispatch.status == GRAPH_INGEST_RECONCILIATION,
        )
        .order_by(
            GraphIngestDispatch.completed_at.asc().nullsfirst(),
            GraphIngestDispatch.operation_id.asc(),
        )
    )
    if cursor:
        completed_at, operation_id = _decode_reconciliation_cursor(cursor)
        if completed_at is None:
            query = query.filter(
                or_(
                    and_(
                        GraphIngestDispatch.completed_at.is_(None),
                        GraphIngestDispatch.operation_id > operation_id,
                    ),
                    GraphIngestDispatch.completed_at.is_not(None),
                )
            )
        else:
            query = query.filter(
                or_(
                    GraphIngestDispatch.completed_at > completed_at,
                    and_(
                        GraphIngestDispatch.completed_at == completed_at,
                        GraphIngestDispatch.operation_id > operation_id,
                    ),
                )
            )
    elif normalized_offset:
        query = query.offset(normalized_offset)
    rows = query.limit(page_limit + 1).all()
    page = rows[:page_limit]
    has_more = len(rows) > page_limit
    return {
        "operations": [public_reconciliation_evidence(row) for row in page],
        "has_more": has_more,
        "limit": page_limit,
        "offset": normalized_offset,
        "next_cursor": (
            _encode_reconciliation_cursor(page[-1]) if has_more and page else None
        ),
    }


def get_reconciliation_operation(
    db: Session,
    *,
    organization_id: int,
    operation_id: str,
) -> dict[str, Any]:
    row = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.operation_id == str(operation_id),
            GraphIngestDispatch.organization_id == int(organization_id),
            GraphIngestDispatch.status == GRAPH_INGEST_RECONCILIATION,
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Graph operation not found")
    return public_reconciliation_evidence(row)


def reconcile_graph_ingest_operation(
    db: Session,
    *,
    organization_id: int,
    actor_id: int,
    operation_id: str,
    expected_attempt_nonce: str,
    action: str,
    entire_operation_present_attested: bool,
    entire_operation_absent_attested: bool,
) -> dict[str, Any]:
    """Apply one exact owner attestation; never invoke a provider directly."""

    row = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.operation_id == str(operation_id),
            GraphIngestDispatch.organization_id == int(organization_id),
        )
        .populate_existing()
        .with_for_update(of=GraphIngestDispatch)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Graph operation not found")
    if row.status != GRAPH_INGEST_RECONCILIATION:
        raise _conflict(
            "This graph operation is no longer awaiting reconciliation. Refresh before acting."
        )
    current_nonce = str(row.worker_attempt_nonce or "")
    if (
        not current_nonce
        or _UUIDISH.fullmatch(current_nonce) is None
        or str(expected_attempt_nonce) != current_nonce
        or row.provider_attempt_started_at is None
    ):
        raise _conflict(
            "The graph operation attempt changed or cannot be fenced. Refresh before reconciling it."
        )
    _source_refs, source_evidence_state = _public_source_refs(row.source_refs)
    if source_evidence_state != "available":
        raise _conflict(
            "Stored graph source evidence requires support review; no evidence was overwritten."
        )
    manifest_state, _manifest = _manifest_evidence(row)
    if manifest_state != "available":
        raise _conflict(
            "The exact graph operation manifest is unavailable or malformed; no evidence was overwritten."
        )

    if action == CONFIRM_ENTIRE_OPERATION_PRESENT:
        if not entire_operation_present_attested or entire_operation_absent_attested:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Confirm that the entire exact graph operation is fully present. "
                    "Partial or uncertain results must remain fenced."
                ),
            )
    elif action == RETRY_AFTER_ENTIRE_OPERATION_ABSENT:
        if not entire_operation_absent_attested or entire_operation_present_attested:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Retry requires confirmation that the entire exact graph "
                    "operation is absent. Partial or uncertain results must remain fenced."
                ),
            )
    else:
        raise HTTPException(status_code=422, detail="Unsupported reconciliation action")

    history = _validated_history(row.reconciliation_history)
    if len(history) >= _MAX_HISTORY_ENTRIES:
        raise _conflict(
            "Graph reconciliation history reached its retained-evidence limit; contact support."
        )
    history.append(_resolution_entry(row, action=action, actor_id=int(actor_id)))
    if (
        len(json.dumps(history, separators=(",", ":")).encode("utf-8"))
        > _MAX_HISTORY_BYTES
    ):
        raise _conflict(
            "Graph reconciliation history reached its retained-evidence limit; contact support."
        )
    row.reconciliation_history = history

    dispatch_required = action == RETRY_AFTER_ENTIRE_OPERATION_ABSENT
    if dispatch_required:
        row.status = GRAPH_INGEST_PENDING
        row.dispatch_nonce = None
        row.worker_attempt_nonce = None
        row.next_attempt_at = _now()
        row.dispatched_at = None
        row.claimed_at = None
        row.provider_attempt_started_at = None
        row.completed_at = None
        row.last_error_code = "owner_attested_entire_operation_absent"
    else:
        row.status = GRAPH_INGEST_COMPLETE
        row.next_attempt_at = None
        row.completed_at = _now()
        row.last_error_code = None
    db.commit()

    return {
        "status": str(row.status),
        "operation_id": str(row.operation_id),
        "dispatch_required": dispatch_required,
        "operation": public_reconciliation_evidence(row),
    }


__all__ = [
    "CONFIRM_ENTIRE_OPERATION_PRESENT",
    "RETRY_AFTER_ENTIRE_OPERATION_ABSENT",
    "get_reconciliation_operation",
    "list_reconciliation_operations",
    "owner_authorized_exact_replay",
    "public_reconciliation_evidence",
    "reconcile_graph_ingest_operation",
]
