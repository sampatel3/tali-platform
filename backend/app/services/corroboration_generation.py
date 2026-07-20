"""Exact generation and delivery lease for async corroboration work."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..components.scoring.candidate_inputs import candidate_input_fingerprint
from ..components.scoring.freshness import (
    ScoreGenerationToken,
    latest_score_attempts,
    score_generation_from_fingerprint,
    score_generation_is_current,
)
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.cv_score_job import SCORE_JOB_DONE
from ..models.role import Role
from .role_execution_guard import lock_live_role


MARKER_KEY = "_corroboration_enrichment"
LEASE_TTL = timedelta(minutes=30)
MAX_ATTEMPTS = 2
RETRY_DELAY = timedelta(minutes=1)

_SLOW_SIGNAL_KEYS = (
    "graph_corroboration",
    "github",
    "graph_outcome_prior",
    # These two are deterministic derivatives of all integrity axes. Excluding
    # them keeps a completed enrichment equivalent to its original score input.
    "triangulation",
    "warnings",
)


@dataclass(frozen=True)
class CorroborationLocator:
    application_id: int
    organization_id: int
    role_id: int
    candidate_id: int


@dataclass(frozen=True)
class CorroborationGeneration:
    locator: CorroborationLocator
    score_generation: ScoreGenerationToken
    candidate_input_fingerprint: str
    evidence_fingerprint: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "application_id": self.locator.application_id,
            "organization_id": self.locator.organization_id,
            "role_id": self.locator.role_id,
            "candidate_id": self.locator.candidate_id,
            "score_generation": self.score_generation.as_fingerprint(),
            "candidate_input_fingerprint": self.candidate_input_fingerprint,
            "evidence_fingerprint": self.evidence_fingerprint,
        }

    @classmethod
    def from_payload(cls, payload: Any) -> CorroborationGeneration | None:
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        score = payload.get("score_generation")
        if not isinstance(score, dict):
            return None
        token = score_generation_from_fingerprint({"score_generation": score})
        if token is None:
            return None
        try:
            locator = CorroborationLocator(
                application_id=int(payload["application_id"]),
                organization_id=int(payload["organization_id"]),
                role_id=int(payload["role_id"]),
                candidate_id=int(payload["candidate_id"]),
            )
            candidate_fingerprint = str(payload["candidate_input_fingerprint"])
            evidence_fingerprint = str(payload["evidence_fingerprint"])
        except (KeyError, TypeError, ValueError):
            return None
        if (
            token.application_id != locator.application_id
            or token.role_id != locator.role_id
            or len(candidate_fingerprint) != 64
            or len(evidence_fingerprint) != 64
        ):
            return None
        return cls(
            locator=locator,
            score_generation=token,
            candidate_input_fingerprint=candidate_fingerprint,
            evidence_fingerprint=evidence_fingerprint,
        )

    def digest(self) -> str:
        return _json_hash(self.as_payload())


@dataclass(frozen=True)
class LockedCorroborationRows:
    role: Role
    candidate: Candidate
    application: CandidateApplication


@dataclass(frozen=True)
class CorroborationInputs:
    locator: CorroborationLocator
    cv_sections: dict[str, Any]
    social_profiles: Any


@dataclass(frozen=True)
class CorroborationLease:
    generation_digest: str
    lease_id: str
    attempt: int
    claimed_at: datetime


@dataclass(frozen=True)
class LeaseClaim:
    status: str
    lease: CorroborationLease | None = None
    retry_after_seconds: int | None = None


def _json_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _base_score_details(value: Any) -> dict[str, Any]:
    details = copy.deepcopy(value) if isinstance(value, dict) else {}
    details.pop(MARKER_KEY, None)
    signals = details.get("integrity_signals")
    if isinstance(signals, dict):
        base_signals = copy.deepcopy(signals)
        for key in _SLOW_SIGNAL_KEYS:
            base_signals.pop(key, None)
        details["integrity_signals"] = base_signals
    return details


def _effective_cv_sections(
    application: CandidateApplication, candidate: Candidate
) -> dict[str, Any]:
    value = application.cv_sections or candidate.cv_sections or {}
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def capture_corroboration_generation(
    *,
    application: CandidateApplication,
    candidate: Candidate,
    score_generation: ScoreGenerationToken,
    candidate_fingerprint: str | None = None,
) -> CorroborationGeneration:
    """Capture every mutable DB input consumed by the enrichment worker."""

    locator = CorroborationLocator(
        application_id=int(application.id),
        organization_id=int(application.organization_id),
        role_id=int(application.role_id),
        candidate_id=int(application.candidate_id),
    )
    evidence = {
        "cv_match_score": getattr(application, "cv_match_score", None),
        "cv_match_scored_at": (
            application.cv_match_scored_at.isoformat()
            if getattr(application, "cv_match_scored_at", None) is not None
            else None
        ),
        "cv_sections": _effective_cv_sections(application, candidate),
        "social_profiles": copy.deepcopy(
            getattr(candidate, "social_profiles", None)
        ),
        "score_details": _base_score_details(
            getattr(application, "cv_match_details", None)
        ),
    }
    return CorroborationGeneration(
        locator=locator,
        score_generation=score_generation,
        candidate_input_fingerprint=(
            str(candidate_fingerprint)
            if candidate_fingerprint is not None
            else candidate_input_fingerprint(application, candidate)
        ),
        evidence_fingerprint=_json_hash(evidence),
    )


def capture_corroboration_inputs(
    rows: LockedCorroborationRows,
) -> CorroborationInputs:
    application = rows.application
    return CorroborationInputs(
        locator=CorroborationLocator(
            application_id=int(application.id),
            organization_id=int(application.organization_id),
            role_id=int(application.role_id),
            candidate_id=int(application.candidate_id),
        ),
        cv_sections=_effective_cv_sections(application, rows.candidate),
        social_profiles=copy.deepcopy(rows.candidate.social_profiles),
    )


def locate_corroboration(
    db: Session, *, application_id: int
) -> CorroborationLocator | None:
    row = (
        db.query(
            CandidateApplication.organization_id,
            CandidateApplication.role_id,
            CandidateApplication.candidate_id,
        )
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if row is None or any(value is None for value in row):
        return None
    return CorroborationLocator(
        application_id=int(application_id),
        organization_id=int(row[0]),
        role_id=int(row[1]),
        candidate_id=int(row[2]),
    )


def lock_corroboration_rows(
    db: Session, *, locator: CorroborationLocator
) -> LockedCorroborationRows | None:
    """Lock Organization -> Role -> Candidate -> Application."""

    role = lock_live_role(
        db,
        role_id=locator.role_id,
        organization_id=locator.organization_id,
    )
    if role is None:
        return None
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == locator.candidate_id,
            Candidate.organization_id == locator.organization_id,
            Candidate.deleted_at.is_(None),
        )
        .with_for_update(of=Candidate)
        .populate_existing()
        .one_or_none()
    )
    if candidate is None:
        return None
    application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == locator.application_id,
            CandidateApplication.organization_id == locator.organization_id,
            CandidateApplication.role_id == locator.role_id,
            CandidateApplication.candidate_id == locator.candidate_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .with_for_update(of=CandidateApplication)
        .populate_existing()
        .one_or_none()
    )
    if application is None:
        return None
    return LockedCorroborationRows(
        role=role,
        candidate=candidate,
        application=application,
    )


def generation_is_current(
    db: Session,
    *,
    rows: LockedCorroborationRows,
    expected: CorroborationGeneration,
) -> bool:
    application = rows.application
    if (
        int(application.id) != expected.locator.application_id
        or int(application.organization_id) != expected.locator.organization_id
        or int(application.role_id) != expected.locator.role_id
        or int(application.candidate_id) != expected.locator.candidate_id
        or not score_generation_is_current(
            db,
            expected=expected.score_generation,
            locked_role=rows.role,
            application=application,
        )
    ):
        return False
    return (
        capture_corroboration_generation(
            application=application,
            candidate=rows.candidate,
            score_generation=expected.score_generation,
        )
        == expected
    )


def expected_score_attempt_is_latest(
    db: Session, *, expected: CorroborationGeneration
) -> bool:
    attempt = latest_score_attempts(
        db, [expected.locator.application_id]
    ).get(expected.locator.application_id)
    if expected.score_generation.job_id is None:
        return attempt is None
    return bool(
        attempt is not None
        and attempt.job_id == expected.score_generation.job_id
        and attempt.status == SCORE_JOB_DONE
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_lease_attempt(value: Any) -> int | None:
    """Parse persisted lease state without accepting lossy JSON coercions."""

    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdigit():
            return int(normalized)
    return None


def claim_generation_lease(
    application: CandidateApplication,
    *,
    generation: CorroborationGeneration,
    now: datetime | None = None,
) -> LeaseClaim:
    current_time = now or datetime.now(timezone.utc)
    digest = generation.digest()
    details = copy.deepcopy(application.cv_match_details or {})
    marker = details.get(MARKER_KEY)
    marker = dict(marker) if isinstance(marker, dict) else {}
    same_generation = marker.get("generation") == digest
    status = str(marker.get("status") or "") if same_generation else ""
    if status in {"done", "no_signal"}:
        return LeaseClaim(status="already_complete")

    parsed_attempt = (
        _parse_lease_attempt(marker.get("attempt")) if same_generation else 0
    )
    # Corrupt state must not grant another external-provider attempt. Marking
    # it exhausted is recoverable through a genuinely new score generation.
    attempt = MAX_ATTEMPTS if parsed_attempt is None else parsed_attempt
    claimed_at = _parse_datetime(marker.get("claimed_at"))
    next_attempt_at = _parse_datetime(marker.get("next_attempt_at"))
    if status == "running" and claimed_at is not None:
        expires_at = claimed_at + LEASE_TTL
        if expires_at > current_time:
            return LeaseClaim(
                status="leased",
                retry_after_seconds=max(
                    1, int((expires_at - current_time).total_seconds())
                ),
            )
    if status == "retry_wait" and next_attempt_at is not None:
        if next_attempt_at > current_time:
            return LeaseClaim(
                status="retry_wait",
                retry_after_seconds=max(
                    1, int((next_attempt_at - current_time).total_seconds())
                ),
            )
    if attempt >= MAX_ATTEMPTS:
        marker.update(
            {
                "generation": digest,
                "status": "failed",
                "claimed_at": None,
                "next_attempt_at": None,
            }
        )
        details[MARKER_KEY] = marker
        application.cv_match_details = details
        return LeaseClaim(status="retry_exhausted")

    lease = CorroborationLease(
        generation_digest=digest,
        lease_id=uuid.uuid4().hex,
        attempt=attempt + 1,
        claimed_at=current_time,
    )
    details[MARKER_KEY] = {
        "generation": digest,
        "status": "running",
        "lease_id": lease.lease_id,
        "attempt": lease.attempt,
        "claimed_at": current_time.isoformat(),
        "next_attempt_at": None,
    }
    application.cv_match_details = details
    return LeaseClaim(status="claimed", lease=lease)


def lease_is_current(
    application: CandidateApplication, *, lease: CorroborationLease
) -> bool:
    details = application.cv_match_details
    marker = details.get(MARKER_KEY) if isinstance(details, dict) else None
    attempt = (
        _parse_lease_attempt(marker.get("attempt"))
        if isinstance(marker, dict)
        else None
    )
    return bool(
        isinstance(marker, dict)
        and marker.get("generation") == lease.generation_digest
        and marker.get("status") == "running"
        and marker.get("lease_id") == lease.lease_id
        and attempt == lease.attempt
    )


def update_lease_marker(
    application: CandidateApplication,
    *,
    lease: CorroborationLease,
    status: str,
    now: datetime | None = None,
    error_code: str | None = None,
) -> bool:
    if not lease_is_current(application, lease=lease):
        return False
    current_time = now or datetime.now(timezone.utc)
    details = copy.deepcopy(application.cv_match_details or {})
    marker = dict(details[MARKER_KEY])
    marker.update(
        {
            "status": str(status),
            "claimed_at": None,
            "completed_at": current_time.isoformat(),
            "next_attempt_at": (
                (current_time + RETRY_DELAY).isoformat()
                if status == "retry_wait"
                else None
            ),
        }
    )
    if error_code:
        marker["error_code"] = str(error_code)[:100]
    details[MARKER_KEY] = marker
    application.cv_match_details = details
    return True


__all__ = [
    "CorroborationGeneration",
    "CorroborationInputs",
    "CorroborationLease",
    "CorroborationLocator",
    "LeaseClaim",
    "LockedCorroborationRows",
    "MARKER_KEY",
    "capture_corroboration_generation",
    "capture_corroboration_inputs",
    "claim_generation_lease",
    "expected_score_attempt_is_latest",
    "generation_is_current",
    "lease_is_current",
    "locate_corroboration",
    "lock_corroboration_rows",
    "update_lease_marker",
]
