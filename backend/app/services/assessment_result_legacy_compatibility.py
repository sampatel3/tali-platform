"""Fence rolling-deploy assessment-result tasks that lack durable identity."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from ..components.assessments.result_delivery_legacy_inventory import (
    classify_legacy_assessment_result_delivery,
)
from ..models.assessment import Assessment
from ..models.organization import Organization

_LEGACY_RESULTS_PATH = re.compile(r"(?:^|/)assessments/(\d+)(?:/|$)")
_LEGACY_ASSESSMENT_DATA_KEYS = frozenset(
    {"results_url", "score", "tests_passed", "tests_total", "time_taken"}
)
_MAX_LEGACY_PAYLOAD_BYTES = 4096


def _legacy_assessment_id(assessment_data: dict[str, Any] | None) -> int | None:
    if not isinstance(assessment_data, dict):
        return None
    parsed = urlparse(str(assessment_data.get("results_url") or ""))
    match = _LEGACY_RESULTS_PATH.search(parsed.path)
    return int(match.group(1)) if match else None


def _legacy_payload_evidence(
    *,
    subdomain: str | None,
    candidate_id: str | None,
    assessment_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(assessment_data, dict) or not set(assessment_data).issubset(
        _LEGACY_ASSESSMENT_DATA_KEYS
    ):
        return None
    if not all(
        value is None or type(value) in {str, int, float, bool}
        for value in assessment_data.values()
    ):
        return None
    candidate = str(candidate_id or "").strip()
    domain = str(subdomain or "").strip()
    if not candidate or len(candidate) > 200 or not domain or len(domain) > 200:
        return None
    snapshot = {
        "subdomain": domain,
        "candidate_id": candidate,
        "assessment_data": deepcopy(assessment_data),
    }
    try:
        encoded = json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if len(encoded) > _MAX_LEGACY_PAYLOAD_BYTES:
        return None
    return {
        **snapshot,
        "payload_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def fence_legacy_assessment_result_payload(
    *,
    subdomain: str | None,
    candidate_id: str | None,
    assessment_data: dict[str, Any] | None,
    session_factory: Callable[[], Session],
) -> dict[str, Any]:
    """Bind exact legacy identity, inventory it, and never call the provider."""

    assessment_id = _legacy_assessment_id(assessment_data)
    if assessment_id is None:
        return {"status": "legacy_payload_unbound", "success": False}
    payload_evidence = _legacy_payload_evidence(
        subdomain=subdomain,
        candidate_id=candidate_id,
        assessment_data=assessment_data,
    )
    if payload_evidence is None:
        return {"status": "legacy_payload_unsafe", "success": False}
    with session_factory() as db:
        row = (
            db.query(Assessment)
            .filter(Assessment.id == int(assessment_id))
            .with_for_update(of=Assessment)
            .one_or_none()
        )
        org = (
            db.query(Organization)
            .filter(Organization.id == int(row.organization_id))
            .one_or_none()
            if row is not None and row.organization_id is not None
            else None
        )
        if (
            row is None
            or org is None
            or str(row.workable_candidate_id or "").strip()
            != str(candidate_id or "").strip()
            or str(org.workable_subdomain or "").strip().lower()
            != str(subdomain or "").strip().lower()
        ):
            db.rollback()
            return {"status": "legacy_payload_mismatch", "success": False}
        if bool(row.is_voided):
            db.rollback()
            return {"status": "legacy_payload_assessment_voided", "success": False}
        if bool(row.posted_to_workable):
            db.rollback()
            return {"status": "legacy_payload_already_delivered", "success": True}
        if classify_legacy_assessment_result_delivery(
            row,
            legacy_payload_evidence=payload_evidence,
        ):
            db.commit()
            return {
                "status": "legacy_reconciliation_required",
                "success": False,
            }
        db.rollback()
        return {
            "status": "legacy_payload_superseded_by_durable_receipt",
            "success": False,
        }


__all__ = ["fence_legacy_assessment_result_payload"]
