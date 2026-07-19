"""Recover known-accepted Anthropic batches after local anchor failure.

Recovery is deliberately separate from submission: it never calls the provider.
It validates the durable tenant/request identity, locks the immutable anchor row,
and atomically replaces only that row's synthetic id with the real provider id.
Unknown-outcome submissions and distinct-id collisions always fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Optional

from sqlalchemy import String, cast, literal, or_, text
from sqlalchemy.exc import IntegrityError

from ..models.anthropic_batch_job import (
    ANTHROPIC_BATCH_KNOWN_ACCEPTED_RECOVERY_PREDICATE,
    AnthropicBatchJob,
)
from ..platform.database import SessionLocal

logger = logging.getLogger("taali.anthropic_batch_recovery")

_KNOWN_ACCEPTED_FAILURE_STATE = "provider_accepted_anchor_finalize_failed"
_RECOVERY_EVIDENCE_KEY = "_submission_recovery"
_INVALID_RECOVERY_EVIDENCE_STATE = "invalid_known_accepted_claim"
_PROVIDER_ID_COLLISION_EVIDENCE_STATE = "provider_id_collision"
_AUTOMATIC_RECOVERY_QUARANTINE_STATES = (
    _INVALID_RECOVERY_EVIDENCE_STATE,
    _PROVIDER_ID_COLLISION_EVIDENCE_STATE,
)
_RECOVERY_SCAN_MULTIPLIER = 4
_MAX_RECOVERY_SCAN_ROWS = 4_000


@dataclass(frozen=True)
class _KnownAcceptedClaim:
    row_id: int
    feature: str
    claim_batch_id: str
    claim_attempt_id: str
    provider_batch_id: str


def _attribution_context(context: object) -> dict[str, dict]:
    if not isinstance(context, dict):
        return {}
    return {
        str(custom_id): per
        for custom_id, per in context.items()
        if not str(custom_id).startswith("_") and isinstance(per, dict)
    }


def _claim_context_is_owned(row: AnthropicBatchJob, claim: dict) -> bool:
    """Validate durable tenant/request ownership without trusting a caller."""
    try:
        request_count = int(claim.get("request_count"))
    except (TypeError, ValueError):
        return False
    if request_count != int(row.request_count or 0):
        return False

    by_custom_id = _attribution_context(row.context)
    if len(by_custom_id) != request_count:
        return False
    if row.organization_id is None:
        return False
    organization_id = int(row.organization_id)
    for custom_id, per in by_custom_id.items():
        try:
            context_organization_id = int(per.get("organization_id"))
        except (TypeError, ValueError):
            return False
        if context_organization_id != organization_id:
            return False
        if row.feature == "cv_parse":
            prefix = "cvparse-"
            if not custom_id.startswith(prefix):
                return False
            application_id = custom_id[len(prefix):]
            if not application_id.isdigit():
                return False
            if per.get("entity_id") != f"application:{application_id}":
                return False

    if row.feature == "cv_parse":
        request_sha256 = str(claim.get("request_sha256") or "").strip()
        if not request_sha256:
            return False
        if claim.get("claim_batch_id") != f"claim:cv_parse:{request_sha256}":
            return False
    return True


def _known_accepted_claim(row: AnthropicBatchJob) -> Optional[_KnownAcceptedClaim]:
    """Return a strict recovery identity for one known-accepted claim.

    Outcome-ambiguous submissions intentionally do not qualify. The synthetic
    id must still belong to this exact row and the provider id must be distinct;
    malformed or partially written evidence fails closed.
    """
    if row.status != "submission_ambiguous":
        return None
    context = row.context if isinstance(row.context, dict) else {}
    claim = context.get("_submission_claim")
    if not isinstance(claim, dict):
        return None
    claim_batch_id = str(claim.get("claim_batch_id") or "").strip()
    claim_attempt_id = str(claim.get("attempt_id") or "").strip()
    provider_batch_id = str(claim.get("provider_batch_id") or "").strip()
    if (
        claim.get("version") != 2
        or claim.get("state") != _KNOWN_ACCEPTED_FAILURE_STATE
        or not claim_batch_id
        or claim_batch_id != str(row.batch_id)
        or not claim_attempt_id
        or not provider_batch_id
        or provider_batch_id == claim_batch_id
        or not _claim_context_is_owned(row, claim)
    ):
        return None
    return _KnownAcceptedClaim(
        row_id=int(row.id),
        feature=str(row.feature),
        claim_batch_id=claim_batch_id,
        claim_attempt_id=claim_attempt_id,
        provider_batch_id=provider_batch_id,
    )


def _same_recovered_row(
    row: AnthropicBatchJob, candidate: _KnownAcceptedClaim
) -> bool:
    """Whether a racing worker already re-keyed this immutable row."""
    if int(row.id) != candidate.row_id:
        return False
    context = row.context if isinstance(row.context, dict) else {}
    claim = context.get("_submission_claim")
    return bool(
        isinstance(claim, dict)
        and str(row.feature) == candidate.feature
        and row.status in {"submitted", "ended", "results_applied"}
        and str(row.batch_id) == candidate.provider_batch_id
        and claim.get("state") == "submitted"
        and claim.get("claim_batch_id") == candidate.claim_batch_id
        and claim.get("attempt_id") == candidate.claim_attempt_id
        and claim.get("provider_batch_id") == candidate.provider_batch_id
        and _claim_context_is_owned(row, claim)
    )


def _recover_known_accepted_claim(candidate: _KnownAcceptedClaim) -> str:
    """Atomically promote one exact synthetic anchor to its provider id."""
    try:
        with SessionLocal() as session:
            row = (
                session.query(AnthropicBatchJob)
                .filter(AnthropicBatchJob.id == candidate.row_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                logger.error(
                    "known-accepted batch recovery lost claim row id=%s",
                    candidate.row_id,
                )
                return "error"
            if _same_recovered_row(row, candidate):
                session.rollback()
                return "already_owned"
            current = _known_accepted_claim(row)
            if current != candidate:
                logger.warning(
                    "known-accepted batch recovery ignored changed claim row=%s",
                    candidate.row_id,
                )
                session.rollback()
                return "error"

            collision = (
                session.query(AnthropicBatchJob)
                .filter(
                    AnthropicBatchJob.batch_id == candidate.provider_batch_id,
                    AnthropicBatchJob.id != candidate.row_id,
                )
                .with_for_update()
                .one_or_none()
            )
            if collision is not None:
                # Never merge based on caller-controlled JSON. A distinct row
                # can carry another tenant's metering/apply context even when
                # its claim fields look identical.
                session.rollback()
                logger.error(
                    "known-accepted batch recovery refused provider-id collision "
                    "claim=%s provider_batch_id=%s existing_row=%s",
                    candidate.claim_batch_id,
                    candidate.provider_batch_id,
                    collision.id,
                )
                return "collision"

            context = dict(row.context) if isinstance(row.context, dict) else {}
            claim = dict(context.get("_submission_claim") or {})
            claim.update(
                {
                    "state": "submitted",
                    "anchor_recovered_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            context["_submission_claim"] = claim
            row.batch_id = candidate.provider_batch_id
            row.status = "submitted"
            row.context = context
            session.commit()
            return "recovered"
    except IntegrityError:
        # A provider-id row may have raced the non-existent-row check. The
        # synthetic claim remains blocked and will be classified next pass.
        logger.warning(
            "known-accepted batch recovery hit provider-id uniqueness race "
            "claim=%s provider_batch_id=%s",
            candidate.claim_batch_id,
            candidate.provider_batch_id,
        )
        return "collision"
    except Exception:
        logger.exception(
            "known-accepted batch recovery failed claim=%s provider_batch_id=%s",
            candidate.claim_batch_id,
            candidate.provider_batch_id,
        )
        return "error"


def _scan_row_budget(candidate_limit: int) -> int:
    """Bound ORM materialization even when stored claim evidence is corrupt."""

    return min(
        _MAX_RECOVERY_SCAN_ROWS,
        max(int(candidate_limit), int(candidate_limit) * _RECOVERY_SCAN_MULTIPLIER),
    )


def _known_accepted_scan_query(session, *, feature: Optional[str]):
    """Filter permanent unknown-outcome rows at the database boundary."""

    query = session.query(AnthropicBatchJob)
    if session.get_bind().dialect.name == "postgresql":
        # Use exactly the trusted literal predicate managed by revision 190.
        # Combining it with the portable CAST/OR expression tree prevents
        # PostgreSQL from proving partial-index eligibility under realistic data
        # skew, even though the literal predicate is also present.
        query = query.filter(
            text(ANTHROPIC_BATCH_KNOWN_ACCEPTED_RECOVERY_PREDICATE)
        )
    else:
        claim = AnthropicBatchJob.context["_submission_claim"]
        claim_version = cast(claim["version"].as_string(), String)
        claim_batch_id = cast(claim["claim_batch_id"].as_string(), String)
        claim_attempt_id = cast(claim["attempt_id"].as_string(), String)
        provider_batch_id = cast(claim["provider_batch_id"].as_string(), String)
        claim_request_count = cast(claim["request_count"].as_string(), String)
        request_sha256 = cast(claim["request_sha256"].as_string(), String)
        recovery_state = cast(
            AnthropicBatchJob.context[_RECOVERY_EVIDENCE_KEY]["state"].as_string(),
            String,
        )
        query = query.filter(
            AnthropicBatchJob.status == "submission_ambiguous",
            AnthropicBatchJob.organization_id.isnot(None),
            claim_version == "2",
            claim["state"].as_string() == _KNOWN_ACCEPTED_FAILURE_STATE,
            claim_batch_id == AnthropicBatchJob.batch_id,
            claim_attempt_id.isnot(None),
            claim_attempt_id != "",
            provider_batch_id.isnot(None),
            provider_batch_id != "",
            provider_batch_id != AnthropicBatchJob.batch_id,
            claim_request_count == cast(AnthropicBatchJob.request_count, String),
            or_(
                AnthropicBatchJob.feature != "cv_parse",
                claim_batch_id == literal("claim:cv_parse:") + request_sha256,
            ),
            or_(
                recovery_state.is_(None),
                recovery_state.notin_(_AUTOMATIC_RECOVERY_QUARANTINE_STATES),
            ),
        )
    if feature is not None:
        query = query.filter(AnthropicBatchJob.feature == str(feature))
    return query


def _revalidate_or_quarantine_scan_row(
    session,
    *,
    row_id: int,
    feature: Optional[str],
) -> tuple[Optional[_KnownAcceptedClaim], bool]:
    """Recheck a scan row under lock or durably remove its poison potential.

    Quarantine never changes ``submission_ambiguous``. The application remains
    protected from a second paid submission while operators retain the exact
    original claim evidence for repair; only future automatic scans skip it.
    """

    row = (
        session.query(AnthropicBatchJob)
        .filter(AnthropicBatchJob.id == int(row_id))
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        return None, False
    if feature is not None and str(row.feature) != str(feature):
        return None, False
    candidate = _known_accepted_claim(row)
    if candidate is not None:
        return candidate, False
    context = dict(row.context) if isinstance(row.context, dict) else {}
    claim = context.get("_submission_claim")
    if (
        row.status != "submission_ambiguous"
        or not isinstance(claim, dict)
        or claim.get("state") != _KNOWN_ACCEPTED_FAILURE_STATE
    ):
        return None, False
    existing = context.get(_RECOVERY_EVIDENCE_KEY)
    if (
        isinstance(existing, dict)
        and existing.get("state") in _AUTOMATIC_RECOVERY_QUARANTINE_STATES
    ):
        return None, False
    context[_RECOVERY_EVIDENCE_KEY] = {
        "version": 1,
        "state": _INVALID_RECOVERY_EVIDENCE_STATE,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    row.context = context
    return None, True


def _quarantine_collision(candidate: _KnownAcceptedClaim) -> str:
    """Keep a permanent provider-id collision blocked without scan starvation."""

    try:
        with SessionLocal() as session:
            row = (
                session.query(AnthropicBatchJob)
                .filter(AnthropicBatchJob.id == candidate.row_id)
                .populate_existing()
                .with_for_update()
                .one_or_none()
            )
            if row is None or _known_accepted_claim(row) != candidate:
                session.rollback()
                return "changed"
            collision = (
                session.query(AnthropicBatchJob)
                .filter(
                    AnthropicBatchJob.batch_id == candidate.provider_batch_id,
                    AnthropicBatchJob.id != candidate.row_id,
                )
                .with_for_update()
                .one_or_none()
            )
            if collision is None:
                session.rollback()
                return "changed"
            context = dict(row.context) if isinstance(row.context, dict) else {}
            context[_RECOVERY_EVIDENCE_KEY] = {
                "version": 1,
                "state": _PROVIDER_ID_COLLISION_EVIDENCE_STATE,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            row.context = context
            session.commit()
            return "quarantined"
    except Exception:
        logger.exception(
            "known-accepted batch collision quarantine failed row=%s",
            candidate.row_id,
        )
        return "error"


def recover_known_accepted_batch_submissions(
    *, feature: Optional[str] = None, limit: int = 100
) -> dict[str, int]:
    """Recover provider-accepted claims without another provider submission.

    Only the explicit known-accepted finalization-failure receipt qualifies.
    Each candidate is revalidated under a lock on its immutable row id before
    the synthetic id, status and recovery audit receipt commit together.
    """
    summary = {
        "recovered": 0,
        "already_owned": 0,
        "collisions": 0,
        "errors": 0,
    }
    try:
        bounded_limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        bounded_limit = 100
    try:
        with SessionLocal() as session:
            query = _known_accepted_scan_query(session, feature=feature)
            rows = (
                query.order_by(AnthropicBatchJob.id.asc())
                .limit(_scan_row_budget(bounded_limit))
                .all()
            )
            candidates = []
            quarantined = 0
            for row in rows:
                candidate = _known_accepted_claim(row)
                if candidate is None:
                    candidate, was_quarantined = _revalidate_or_quarantine_scan_row(
                        session,
                        row_id=int(row.id),
                        feature=feature,
                    )
                    quarantined += int(was_quarantined)
                if candidate is not None:
                    candidates.append(candidate)
                if len(candidates) >= bounded_limit:
                    break
            if quarantined:
                session.commit()
                summary["errors"] += quarantined
            else:
                session.rollback()
    except Exception:
        logger.exception("known-accepted batch recovery scan failed")
        summary["errors"] += 1
        return summary

    for candidate in candidates:
        outcome = _recover_known_accepted_claim(candidate)
        if outcome == "recovered":
            summary["recovered"] += 1
        elif outcome == "already_owned":
            summary["already_owned"] += 1
        elif outcome == "collision":
            summary["collisions"] += 1
            if _quarantine_collision(candidate) == "error":
                summary["errors"] += 1
        else:
            summary["errors"] += 1
    return summary


__all__ = ["recover_known_accepted_batch_submissions"]
