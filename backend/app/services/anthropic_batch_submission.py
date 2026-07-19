"""Durable local anchors for Anthropic Message Batch submission.

The provider does not accept an application idempotency key for batch create.
Callers that cannot safely replay an accepted-but-unacknowledged request first
persist a synthetic ``AnthropicBatchJob.batch_id`` and pass it through the
metering wrapper. This module atomically replaces that claim with Anthropic's
real id on success or leaves it visibly blocked when acceptance is uncertain.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

from ..models.anthropic_batch_job import AnthropicBatchJob
from ..platform.database import SessionLocal
from .anthropic_batch_recovery import (
    _attribution_context,
    _claim_context_is_owned,
    recover_known_accepted_batch_submissions,
)
from .provider_error_evidence import safe_provider_error_code
from .provider_usage_admission import provider_error_is_definitely_nonbillable

logger = logging.getLogger("taali.anthropic_batch_submission")

__all__ = [
    "recover_known_accepted_batch_submissions",
    "submission_request_sha256",
]


class BatchSubmissionAnchorError(RuntimeError):
    """Provider accepted a batch whose durable claim could not be finalized."""


class BatchSubmissionOwnershipError(BatchSubmissionAnchorError):
    """Provider response cannot safely be attached to the persisted claim."""


def submission_claim_from_metering(
    metering: dict,
) -> tuple[str, str]:
    """Require and normalize the exact durable claim/attempt identity."""
    claim_value = metering.get("submission_claim_batch_id")
    attempt_value = metering.get("submission_claim_attempt_id")
    if claim_value is None:
        raise ValueError("batch submission requires a durable claim and attempt id")
    claim_batch_id = str(claim_value).strip()
    claim_attempt_id = str(attempt_value or "").strip()
    if not claim_batch_id or not claim_attempt_id:
        raise ValueError("batch submission claim and attempt ids must be non-empty")
    return claim_batch_id, claim_attempt_id


def _submission_model(requests: list) -> Optional[str]:
    try:
        return str(requests[0]["params"]["model"])
    except (IndexError, KeyError, TypeError):
        return None


def submission_request_sha256(
    *,
    organization_id: int,
    requests: list,
    context: dict,
) -> str:
    """Hash the exact paid payload and durable attribution identity.

    Per-attempt reservation ids are excluded deliberately so a definitely
    rejected submission can retry the same logical work with fresh holds.
    """

    if type(organization_id) is not int or organization_id <= 0:
        raise ValueError("batch claim requires a positive organization id")
    if type(requests) is not list or type(context) is not dict:
        raise ValueError("batch claim payload and context must be materialized")
    logical_context: dict[str, dict] = {}
    for custom_id, per in context.items():
        normalized_custom_id = str(custom_id)
        if normalized_custom_id.startswith("_"):
            continue
        if type(per) is not dict:
            raise ValueError("batch claim attribution entries must be objects")
        logical_context[normalized_custom_id] = {
            key: value
            for key, value in per.items()
            if key != "credit_reservation"
        }
    canonical = json.dumps(
        {
            "organization_id": organization_id,
            "requests": requests,
            "context": logical_context,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _claim_matches_submission(
    row: AnthropicBatchJob,
    claim: dict,
    *,
    expected_state: str,
    claim_batch_id: str,
    claim_attempt_id: str,
    feature: str,
    organization_id: Optional[int],
    by_custom_id: Optional[dict],
    requests: list,
) -> bool:
    """Validate the immutable owner and request while the claim row is locked."""

    if (
        claim.get("version") != 2
        or claim.get("state") != expected_state
        or claim.get("claim_batch_id") != claim_batch_id
        or claim.get("attempt_id") != claim_attempt_id
        or not _claim_context_is_owned(row, claim)
        or row.organization_id != organization_id
        or row.feature != feature
        or type(by_custom_id) is not dict
        or type(requests) is not list
    ):
        return False
    persisted_context = _attribution_context(row.context)
    if by_custom_id != persisted_context:
        return False
    request_custom_ids = [
        str(request.get("custom_id") or "")
        for request in requests
        if isinstance(request, dict)
    ]
    if (
        len(request_custom_ids) != len(requests)
        or len(set(request_custom_ids)) != len(request_custom_ids)
        or set(request_custom_ids) != set(persisted_context)
        or len(requests) != int(row.request_count or 0)
        or _submission_model(requests) != row.model
    ):
        return False
    try:
        request_sha256 = submission_request_sha256(
            organization_id=int(organization_id),
            requests=requests,
            context=persisted_context,
        )
    except (TypeError, ValueError):
        return False
    return claim.get("request_sha256") == request_sha256


def _update_claim_safe(
    *,
    claim_batch_id: str,
    status: str,
    state: str,
    error_reason: Optional[str],
    expected_attempt_id: str,
    provider_batch_id: Optional[str] = None,
) -> bool:
    """Best-effort status receipt for a failed/ambiguous submit attempt."""
    try:
        with SessionLocal() as session:
            row = (
                session.query(AnthropicBatchJob)
                .filter(AnthropicBatchJob.batch_id == claim_batch_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                logger.error(
                    "submission claim missing while recording status=%s claim=%s",
                    status,
                    claim_batch_id,
                )
                return False
            context = dict(row.context) if isinstance(row.context, dict) else {}
            claim = dict(context.get("_submission_claim") or {})
            if claim.get("attempt_id") != expected_attempt_id:
                logger.warning(
                    "submission claim attempt changed; ignoring stale outcome "
                    "claim=%s expected_attempt=%s",
                    claim_batch_id,
                    expected_attempt_id,
                )
                return False
            claim.update({"state": state, "error_reason": error_reason})
            if provider_batch_id:
                claim["provider_batch_id"] = provider_batch_id
            context["_submission_claim"] = claim
            row.status = status
            row.context = context
            session.commit()
            return True
    except Exception as exc:
        logger.error(
            "submission claim status write failed claim=%s status=%s error_code=%s",
            claim_batch_id,
            status,
            safe_provider_error_code(exc, operation="anthropic_batch_claim_status"),
        )
        return False


def record_batch_submission_failure_safe(
    *,
    claim_batch_id: str,
    claim_attempt_id: str,
    error: BaseException,
    provider_invoked: bool,
) -> None:
    """Record whether a failed create is retryable or outcome-ambiguous."""
    safe_to_retry = (
        not provider_invoked or provider_error_is_definitely_nonbillable(error)
    )
    logger.error(
        "batch submission failed claim=%s provider_invoked=%s error_type=%s",
        claim_batch_id,
        provider_invoked,
        type(error).__name__,
    )
    _update_claim_safe(
        claim_batch_id=claim_batch_id,
        status="submission_failed" if safe_to_retry else "submission_ambiguous",
        state=(
            "provider_rejected"
            if safe_to_retry
            else "provider_outcome_ambiguous"
        ),
        error_reason=safe_provider_error_code(
            error,
            operation="anthropic_batch_create",
        ),
        expected_attempt_id=claim_attempt_id,
    )


def mark_batch_submission_attempt_started(
    *,
    claim_batch_id: str,
    claim_attempt_id: str,
    feature: str,
    organization_id: Optional[int],
    by_custom_id: Optional[dict],
    requests: list,
) -> bool:
    """Lock and validate the exact owner/request at the last safe boundary."""
    try:
        with SessionLocal() as session:
            row = (
                session.query(AnthropicBatchJob)
                .filter(AnthropicBatchJob.batch_id == claim_batch_id)
                .with_for_update()
                .one_or_none()
            )
            context = (
                dict(row.context)
                if row is not None and isinstance(row.context, dict)
                else {}
            )
            claim = dict(context.get("_submission_claim") or {})
            if (
                row is None
                or row.status != "submitting"
                or not _claim_matches_submission(
                    row,
                    claim,
                    expected_state="claimed",
                    claim_batch_id=claim_batch_id,
                    claim_attempt_id=claim_attempt_id,
                    feature=feature,
                    organization_id=organization_id,
                    by_custom_id=by_custom_id,
                    requests=requests,
                )
            ):
                return False
            claim["state"] = "provider_attempt_started"
            context["_submission_claim"] = claim
            row.context = context
            session.commit()
            return True
    except Exception as exc:
        logger.error(
            "failed to mark batch submission attempt claim=%s attempt=%s error_code=%s",
            claim_batch_id,
            claim_attempt_id,
            safe_provider_error_code(exc, operation="anthropic_batch_attempt_marker"),
        )
        return False


def _finalize_submission_claim(
    *,
    claim_batch_id: str,
    claim_attempt_id: str,
    batch_id: str,
    feature: str,
    organization_id: Optional[int],
    by_custom_id: Optional[dict],
    requests: list,
) -> None:
    if not batch_id:
        raise BatchSubmissionAnchorError(
            "Anthropic batch response did not include a batch id"
        )
    try:
        with SessionLocal() as session:
            row = (
                session.query(AnthropicBatchJob)
                .filter(AnthropicBatchJob.batch_id == claim_batch_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise BatchSubmissionOwnershipError(
                    f"batch submission claim not found: {claim_batch_id}"
                )
            if row.status != "submitting":
                raise BatchSubmissionOwnershipError(
                    "batch submission claim is not active: "
                    f"{claim_batch_id} status={row.status}"
                )
            prior_context = (
                dict(row.context) if isinstance(row.context, dict) else {}
            )
            claim = dict(prior_context.get("_submission_claim") or {})
            if (
                claim.get("version") != 2
                or claim.get("state") != "provider_attempt_started"
                or claim.get("claim_batch_id") != claim_batch_id
                or claim.get("attempt_id") != claim_attempt_id
                or not _claim_context_is_owned(row, claim)
            ):
                raise BatchSubmissionOwnershipError(
                    "batch submission claim ownership changed: "
                    f"{claim_batch_id}"
                )
            if row.organization_id != organization_id or row.feature != feature:
                raise BatchSubmissionOwnershipError(
                    "batch submission caller ownership does not match claim: "
                    f"{claim_batch_id}"
                )
            persisted_context = _attribution_context(prior_context)
            if by_custom_id != persisted_context:
                raise BatchSubmissionOwnershipError(
                    "batch submission attribution does not match claim: "
                    f"{claim_batch_id}"
                )
            request_custom_ids = [
                str(request.get("custom_id") or "")
                for request in requests
                if isinstance(request, dict)
            ]
            request_sha256 = submission_request_sha256(
                organization_id=int(organization_id),
                requests=requests,
                context=persisted_context,
            )
            if (
                len(request_custom_ids) != len(requests)
                or len(set(request_custom_ids)) != len(request_custom_ids)
                or set(request_custom_ids) != set(persisted_context)
                or len(requests) != int(row.request_count or 0)
                or _submission_model(requests) != row.model
                or claim.get("request_sha256") != request_sha256
            ):
                raise BatchSubmissionOwnershipError(
                    "batch submission requests do not match claim: "
                    f"{claim_batch_id}"
                )
            claim.update({"state": "submitted", "provider_batch_id": batch_id})
            prior_context["_submission_claim"] = claim
            row.batch_id = batch_id
            row.status = "submitted"
            row.context = prior_context
            session.commit()
    except BatchSubmissionAnchorError:
        raise
    except Exception as exc:
        raise BatchSubmissionAnchorError(
            f"failed to finalize batch submission claim {claim_batch_id}"
        ) from exc


def record_batch_submission(
    *,
    batch_id: str,
    feature: str,
    organization_id: Optional[int],
    by_custom_id: Optional[dict],
    requests: list,
    claim_batch_id: str,
    claim_attempt_id: str,
) -> None:
    """Finalize the required strict pre-provider claim."""
    try:
        _finalize_submission_claim(
            claim_batch_id=claim_batch_id,
            claim_attempt_id=claim_attempt_id,
            batch_id=batch_id,
            feature=feature,
            organization_id=organization_id,
            by_custom_id=by_custom_id,
            requests=requests,
        )
    except BatchSubmissionOwnershipError as exc:
        logger.error(
            "accepted batch claim ownership mismatch claim=%s error_type=%s",
            claim_batch_id,
            type(exc).__name__,
        )
        _update_claim_safe(
            claim_batch_id=claim_batch_id,
            status="submission_ambiguous",
            state="provider_accepted_anchor_ownership_mismatch",
            error_reason=safe_provider_error_code(
                exc,
                operation="anthropic_batch_finalize",
            ),
            expected_attempt_id=claim_attempt_id,
            provider_batch_id=batch_id,
        )
        raise
    except Exception as exc:
        logger.error(
            "accepted batch claim finalization failed claim=%s error_type=%s",
            claim_batch_id,
            type(exc).__name__,
        )
        _update_claim_safe(
            claim_batch_id=claim_batch_id,
            status="submission_ambiguous",
            state="provider_accepted_anchor_finalize_failed",
            error_reason=safe_provider_error_code(
                exc,
                operation="anthropic_batch_finalize",
            ),
            expected_attempt_id=claim_attempt_id,
            provider_batch_id=batch_id,
        )
        raise
