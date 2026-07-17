"""Durable, detached material-change assessment for Workable role sync."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.orm import Session

from ....models.role import Role
from ....services import material_change
from ....services.claude_client_resolver import get_client_for_org
from ....services.decision_staleness import rebaseline_pending_criteria_fingerprint
from ....services.pricing_service import Feature
from ....services.role_criteria_service import sync_derived_criteria
from ....services.spec_normalizer import DerivedCriterion, derive_criteria, normalize_spec
from .sync_provider_boundaries import assert_provider_ready, finish_db_phase

logger = logging.getLogger(__name__)

MATERIAL_CHANGE_MARKER_KEY = "_taali_material_change_assessment"


@dataclass(frozen=True, slots=True, repr=False)
class MaterialChangeClaim:
    organization_id: int
    role_id: int
    expected_role_version: int
    workable_job_id: str
    role_name: str
    spec_sha256: str
    proposed_fingerprint: str
    operation_id: str
    current: tuple[DerivedCriterion, ...]
    proposed: tuple[DerivedCriterion, ...]
    provider_required: bool


def _spec_hash(role: Role) -> str:
    return hashlib.sha256((role.job_spec_text or "").encode("utf-8")).hexdigest()


def preserve_material_change_marker(previous: dict | None, current: dict) -> dict:
    """Keep an unfinished internal receipt when refreshing provider job JSON."""

    result = dict(current)
    marker = (previous or {}).get(MATERIAL_CHANGE_MARKER_KEY)
    if isinstance(marker, dict):
        result[MATERIAL_CHANGE_MARKER_KEY] = dict(marker)
    return result


def _write_marker(role: Role, claim: MaterialChangeClaim, *, status: str) -> None:
    data = dict(role.workable_job_data or {})
    data[MATERIAL_CHANGE_MARKER_KEY] = {
        "operation_id": claim.operation_id,
        "status": status,
        "proposed_fingerprint": claim.proposed_fingerprint,
        "spec_sha256": claim.spec_sha256,
        "expected_role_version": claim.expected_role_version,
    }
    role.workable_job_data = data


def _clear_marker(role: Role) -> None:
    data = dict(role.workable_job_data or {})
    data.pop(MATERIAL_CHANGE_MARKER_KEY, None)
    role.workable_job_data = data


def clear_material_change_marker(role: Role) -> None:
    _clear_marker(role)


def has_material_change_marker(role: Role) -> bool:
    return isinstance(
        (role.workable_job_data or {}).get(MATERIAL_CHANGE_MARKER_KEY),
        dict,
    )


def prepare_material_change_claim(
    db: Session,
    role: Role,
) -> MaterialChangeClaim | None:
    """Prepare a durable claim; return ``None`` when no provider call is needed."""

    proposed = derive_criteria(normalize_spec(role.job_spec_text).requirements)
    current = material_change._current_derived(db, role)
    proposed_fingerprint = material_change._fingerprint(proposed)
    if proposed_fingerprint == material_change._fingerprint(current):
        _clear_marker(role)
        return None
    if material_change._already_handled(
        db,
        role=role,
        proposed_fp=proposed_fingerprint,
    ):
        _clear_marker(role)
        return None
    operation_id = f"workable-material:{int(role.id)}:{proposed_fingerprint}"
    marker = (role.workable_job_data or {}).get(MATERIAL_CHANGE_MARKER_KEY)
    provider_required = not (
        isinstance(marker, dict)
        and marker.get("operation_id") == operation_id
        and marker.get("status") == "provider_call_started"
        and marker.get("spec_sha256") == _spec_hash(role)
    )
    claim = MaterialChangeClaim(
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        expected_role_version=int(role.version or 1),
        workable_job_id=str(role.workable_job_id or ""),
        role_name=str(role.name or "(unnamed)"),
        spec_sha256=_spec_hash(role),
        proposed_fingerprint=proposed_fingerprint,
        operation_id=operation_id,
        current=tuple(current),
        proposed=tuple(proposed),
        provider_required=provider_required,
    )
    _write_marker(
        role,
        claim,
        status="authorized" if provider_required else "provider_call_started",
    )
    return claim


def stamp_material_change_version(
    role: Role,
    claim: MaterialChangeClaim | None,
) -> MaterialChangeClaim | None:
    if claim is None:
        return None
    stamped = replace(claim, expected_role_version=int(role.version or 1))
    marker = (role.workable_job_data or {}).get(MATERIAL_CHANGE_MARKER_KEY)
    status = str((marker or {}).get("status") or "authorized")
    _write_marker(role, stamped, status=status)
    return stamped


def claim_material_change_provider_call(db: Session, claim: MaterialChangeClaim) -> bool:
    """Durably mark an authorized call as started before ambiguous network I/O."""

    role = (
        db.query(Role)
        .filter(
            Role.id == claim.role_id,
            Role.organization_id == claim.organization_id,
            Role.workable_job_id == claim.workable_job_id,
        )
        .with_for_update(of=Role)
        .first()
    )
    marker = (role.workable_job_data or {}).get(MATERIAL_CHANGE_MARKER_KEY) if role else None
    if (
        role is None
        or int(role.version or 1) != claim.expected_role_version
        or _spec_hash(role) != claim.spec_sha256
        or not isinstance(marker, dict)
        or marker.get("operation_id") != claim.operation_id
        or marker.get("status") != "authorized"
        or marker.get("proposed_fingerprint") != claim.proposed_fingerprint
        or marker.get("spec_sha256") != claim.spec_sha256
        or marker.get("expected_role_version") != claim.expected_role_version
    ):
        db.rollback()
        return False
    _write_marker(role, claim, status="provider_call_started")
    finish_db_phase(db)
    return True


def build_material_change_client(org: Any) -> Any:
    """Build auth locally while the organization snapshot is still available."""

    return get_client_for_org(org)


def assess_material_change(
    client: Any,
    claim: MaterialChangeClaim,
) -> material_change.MaterialityVerdict:
    """Call Anthropic using only immutable primitives and independent metering."""

    if not claim.provider_required:
        return material_change.MaterialityVerdict(
            material=True,
            summary="Job spec changed — please review the new requirements.",
        )

    def render(items: tuple[DerivedCriterion, ...]) -> str:
        return "\n".join(f"- [{item.bucket}] {item.text}" for item in items) or "(none)"

    user_message = (
        f"Role: {claim.role_name}\n\nCURRENT_CRITERIA:\n{render(claim.current)}\n\n"
        f"PROPOSED_CRITERIA (from the new spec):\n{render(claim.proposed)}\n\n"
        f"{material_change._OUTPUT_INSTRUCTIONS}"
    )
    try:
        response = client.messages.create(
            model=material_change.MATERIAL_CHANGE_MODEL,
            max_tokens=300,
            temperature=0,
            system=material_change._SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            metering={
                "feature": Feature.MATERIAL_CHANGE,
                "organization_id": claim.organization_id,
                "role_id": claim.role_id,
                "metadata": {"sub_agent": "material_change_assessor"},
            },
        )
        payload = material_change._extract_json(response.content[0].text)
    except Exception:
        logger.warning("Detached material-change call failed role_id=%s", claim.role_id)
        payload = None
    if not isinstance(payload, dict):
        return material_change.MaterialityVerdict(
            material=True,
            summary="Job spec changed — please review the new requirements.",
        )
    return material_change.MaterialityVerdict(
        material=bool(payload.get("material", True)),
        summary=str(payload.get("summary") or "Job spec changed — please review.").strip()[:240],
    )


def finalize_material_change(
    db: Session,
    claim: MaterialChangeClaim,
    verdict: material_change.MaterialityVerdict,
) -> bool:
    """Apply a result only to the exact role/spec/receipt generation claimed."""

    role = (
        db.query(Role)
        .filter(
            Role.id == claim.role_id,
            Role.organization_id == claim.organization_id,
            Role.workable_job_id == claim.workable_job_id,
        )
        .with_for_update(of=Role)
        .first()
    )
    marker = (role.workable_job_data or {}).get(MATERIAL_CHANGE_MARKER_KEY) if role else None
    if (
        role is None
        or int(role.version or 1) != claim.expected_role_version
        or _spec_hash(role) != claim.spec_sha256
        or not isinstance(marker, dict)
        or marker.get("operation_id") != claim.operation_id
        or marker.get("status") != "provider_call_started"
        or marker.get("proposed_fingerprint") != claim.proposed_fingerprint
        or marker.get("spec_sha256") != claim.spec_sha256
        or marker.get("expected_role_version") != claim.expected_role_version
    ):
        db.rollback()
        return False
    if verdict.material:
        material_change._raise_confirm(
            db,
            role=role,
            proposed=list(claim.proposed),
            proposed_fp=claim.proposed_fingerprint,
            verdict=verdict,
        )
    else:
        sync_derived_criteria(db, role)
        db.flush()
        rebaseline_pending_criteria_fingerprint(db, role_id=claim.role_id)
    _clear_marker(role)
    finish_db_phase(db)
    return True


def execute_material_change(
    db: Session,
    claim: MaterialChangeClaim,
    client: Any,
) -> bool:
    """Run the durable start -> detached provider -> exact finalize sequence."""

    if claim.provider_required and not claim_material_change_provider_call(db, claim):
        return False
    assert_provider_ready(db)
    verdict = assess_material_change(client, claim)
    return finalize_material_change(db, claim, verdict)


__all__ = [
    "MaterialChangeClaim",
    "assess_material_change",
    "build_material_change_client",
    "clear_material_change_marker",
    "execute_material_change",
    "finalize_material_change",
    "has_material_change_marker",
    "prepare_material_change_claim",
    "preserve_material_change_marker",
    "stamp_material_change_version",
]
