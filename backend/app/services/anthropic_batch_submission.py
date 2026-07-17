"""Durable local anchors for Anthropic Message Batch submission.

The provider does not accept an application idempotency key for batch create.
Callers that cannot safely replay an accepted-but-unacknowledged request first
persist a synthetic ``AnthropicBatchJob.batch_id`` and pass it through the
metering wrapper. This module atomically replaces that claim with Anthropic's
real id on success or leaves it visibly blocked when acceptance is uncertain.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..models.anthropic_batch_job import AnthropicBatchJob
from ..platform.database import SessionLocal
from .anthropic_batch_recovery import (
    _attribution_context,
    _claim_context_is_owned,
    recover_known_accepted_batch_submissions,
)
from .provider_usage_admission import provider_error_is_definitely_nonbillable

logger = logging.getLogger("taali.anthropic_batch_submission")

__all__ = ["recover_known_accepted_batch_submissions"]


class BatchSubmissionAnchorError(RuntimeError):
    """Provider accepted a batch whose durable claim could not be finalized."""


class BatchSubmissionOwnershipError(BatchSubmissionAnchorError):
    """Provider response cannot safely be attached to the persisted claim."""


def submission_claim_from_metering(
    metering: dict,
) -> tuple[Optional[str], Optional[str]]:
    """Validate and normalize the optional exact claim/attempt identity."""
    claim_value = metering.get("submission_claim_batch_id")
    attempt_value = metering.get("submission_claim_attempt_id")
    if claim_value is None:
        if attempt_value is not None:
            raise ValueError("batch submission attempt id requires a claim id")
        return None, None
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
    except Exception:
        logger.exception(
            "submission claim status write failed (claim=%s status=%s)",
            claim_batch_id,
            status,
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
    _update_claim_safe(
        claim_batch_id=claim_batch_id,
        status="submission_failed" if safe_to_retry else "submission_ambiguous",
        state=(
            "provider_rejected"
            if safe_to_retry
            else "provider_outcome_ambiguous"
        ),
        error_reason=str(error)[:500],
        expected_attempt_id=claim_attempt_id,
    )


def mark_batch_submission_attempt_started(
    *, claim_batch_id: str, claim_attempt_id: str
) -> bool:
    """Durably close the final local-crash window before the provider call."""
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
                or claim.get("state") != "claimed"
                or claim.get("attempt_id") != claim_attempt_id
            ):
                return False
            claim["state"] = "provider_attempt_started"
            context["_submission_claim"] = claim
            row.context = context
            session.commit()
            return True
    except Exception:
        logger.exception(
            "failed to mark batch submission attempt claim=%s attempt=%s",
            claim_batch_id,
            claim_attempt_id,
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
            context = dict(by_custom_id) if isinstance(by_custom_id, dict) else {}
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
            if context != persisted_context:
                raise BatchSubmissionOwnershipError(
                    "batch submission attribution does not match claim: "
                    f"{claim_batch_id}"
                )
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
    claim_batch_id: Optional[str],
    claim_attempt_id: Optional[str],
) -> None:
    """Finalize a strict pre-call claim or write the legacy best-effort row."""
    if claim_batch_id:
        if not claim_attempt_id:
            raise ValueError("batch submission claim attempt id is required")
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
            # The provider returned an id, but the caller no longer owns the
            # exact persisted claim. Preserve the evidence while keeping it
            # permanently outside automatic recovery.
            _update_claim_safe(
                claim_batch_id=claim_batch_id,
                status="submission_ambiguous",
                state="provider_accepted_anchor_ownership_mismatch",
                error_reason=str(exc)[:500],
                expected_attempt_id=claim_attempt_id,
                provider_batch_id=batch_id,
            )
            raise
        except Exception as exc:
            # Provider acceptance is known. Keep the exact synthetic claim
            # blocked even when renaming it to the provider id failed locally.
            _update_claim_safe(
                claim_batch_id=claim_batch_id,
                status="submission_ambiguous",
                state="provider_accepted_anchor_finalize_failed",
                error_reason=str(exc)[:500],
                expected_attempt_id=claim_attempt_id,
                provider_batch_id=batch_id,
            )
            raise
        return

    # Compatibility path for callers that do not require a pre-provider claim.
    # The batch already exists remotely, so this remains best effort exactly as
    # before: unknown results are still captured later as Feature.OTHER.
    try:
        with SessionLocal() as session:
            session.add(
                AnthropicBatchJob(
                    batch_id=batch_id,
                    organization_id=organization_id,
                    feature=feature,
                    model=_submission_model(requests),
                    request_count=len(requests),
                    status="submitted",
                    context=by_custom_id if isinstance(by_custom_id, dict) else None,
                )
            )
            session.commit()
    except Exception:
        logger.exception(
            "anthropic_batch_jobs write failed (batch_id=%s feature=%s) — "
            "batch submitted OK; results will be metered as Feature.OTHER "
            "without attribution",
            batch_id,
            feature,
        )
