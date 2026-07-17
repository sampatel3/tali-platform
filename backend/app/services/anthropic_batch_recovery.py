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

from sqlalchemy.exc import IntegrityError

from ..models.anthropic_batch_job import AnthropicBatchJob
from ..platform.database import SessionLocal

logger = logging.getLogger("taali.anthropic_batch_recovery")

_KNOWN_ACCEPTED_FAILURE_STATE = "provider_accepted_anchor_finalize_failed"


@dataclass(frozen=True)
class _KnownAcceptedClaim:
    row_id: int
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
            query = session.query(AnthropicBatchJob).filter(
                AnthropicBatchJob.status == "submission_ambiguous"
            )
            if feature is not None:
                query = query.filter(AnthropicBatchJob.feature == str(feature))
            candidates = []
            for row in query.order_by(AnthropicBatchJob.id.asc()).all():
                candidate = _known_accepted_claim(row)
                if candidate is not None:
                    candidates.append(candidate)
                if len(candidates) >= bounded_limit:
                    break
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
        else:
            summary["errors"] += 1
    return summary


__all__ = ["recover_known_accepted_batch_submissions"]
