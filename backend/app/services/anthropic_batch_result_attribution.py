"""Fail-closed result-set attribution for strict Anthropic batch anchors."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from typing import Mapping, Optional

from ..models.anthropic_batch_job import AnthropicBatchJob
from .anthropic_batch_recovery import (
    _attribution_context,
    _claim_context_is_owned,
)

RESULT_ATTRIBUTION_KEY = "_result_attribution_validation"
_EVIDENCE_SAMPLE_LIMIT = 20
_EVIDENCE_TEXT_LIMIT = 200
_ISSUE_LIMIT = 50


def _result_type(entry: object) -> str:
    return str(getattr(getattr(entry, "result", None), "type", None) or "not_succeeded")


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _expected_ids_sha256(expected_ids: frozenset[str]) -> str:
    return _sha256_json(sorted(expected_ids))


def _result_outcomes(entries: list) -> list[tuple[str, str]]:
    return sorted(
        (str(getattr(entry, "custom_id", "") or ""), _result_type(entry))
        for entry in entries
    )


def _succeeded_provider_identities(entries: list) -> list[tuple[str, str]]:
    identities = []
    for entry in entries:
        if _result_type(entry) != "succeeded":
            continue
        message = getattr(getattr(entry, "result", None), "message", None)
        provider_message_id = str(getattr(message, "id", None) or "").strip()
        identities.append(
            (str(getattr(entry, "custom_id", "") or ""), provider_message_id)
        )
    return sorted(identities)


def result_identity_issues(entries: list) -> list[str]:
    """Validate identities that are unsafe even for pre-claim legacy rows."""

    observed_values = [getattr(entry, "custom_id", "") for entry in entries]
    observed_ids = [str(value or "") for value in observed_values]
    counts = Counter(observed_ids)
    issues: list[str] = []
    if any(not isinstance(value, str) for value in observed_values):
        issues.append("invalid_result_custom_id_type")
    if any(not custom_id.strip() for custom_id in observed_ids):
        issues.append("empty_result_custom_id")
    if any(count > 1 for count in counts.values()):
        issues.append("duplicate_result_custom_ids")
    succeeded_provider_ids = [
        provider_message_id
        for _, provider_message_id in _succeeded_provider_identities(entries)
    ]
    if any(not provider_message_id for provider_message_id in succeeded_provider_ids):
        issues.append("empty_succeeded_provider_message_id")
    if len(set(succeeded_provider_ids)) != len(succeeded_provider_ids):
        issues.append("duplicate_succeeded_provider_message_ids")
    return issues


def _receipt_outcome_mismatch(
    context: dict,
    entries: list,
    receipts: Optional[Mapping[str, dict]] = None,
) -> bool:
    if receipts is None:
        receipts = context.get("_metered_results")
    if not isinstance(receipts, dict):
        return False
    for entry in entries:
        custom_id = str(getattr(entry, "custom_id", "") or "")
        receipt = receipts.get(custom_id)
        if not isinstance(receipt, dict):
            continue
        result_type = _result_type(entry)
        state = str(receipt.get("state") or "")
        receipt_type = str(receipt.get("result_type") or "")
        if result_type == "succeeded":
            message = getattr(getattr(entry, "result", None), "message", None)
            provider_message_id = str(getattr(message, "id", None) or "").strip()
            receipt_provider_id = str(receipt.get("provider_message_id") or "").strip()
            if state == "metered" and (
                not receipt_provider_id or receipt_provider_id == provider_message_id
            ):
                continue
            if state == "skipped" and receipt_type == "missing_usage":
                continue
        elif state == "skipped" and receipt_type == result_type:
            continue
        return True
    return False


def _bounded_issues(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return sorted({str(issue)[:_EVIDENCE_TEXT_LIMIT] for issue in value})[:_ISSUE_LIMIT]


def _bounded_ids(values: set[str] | frozenset[str]) -> list[str]:
    return [
        value[:_EVIDENCE_TEXT_LIMIT]
        for value in sorted(values)[:_EVIDENCE_SAMPLE_LIMIT]
    ]


def strict_anchor_contract(
    row: AnthropicBatchJob,
) -> tuple[Optional[frozenset[str]], list[str]]:
    """Validate the immutable persisted attribution for a strict anchor."""
    context = row.context if isinstance(row.context, dict) else {}
    if "_submission_claim" not in context:
        return None, []

    issues: list[str] = []
    pending = context.get(RESULT_ATTRIBUTION_KEY)
    if isinstance(pending, dict) and pending.get("state") == "reconciliation_pending":
        issues.append("prior_reconciliation_pending")
    claim = context.get("_submission_claim")
    attribution = _attribution_context(context)
    expected_ids = frozenset(
        str(custom_id) for custom_id in context if not str(custom_id).startswith("_")
    )
    try:
        request_count = int(row.request_count)
    except (TypeError, ValueError):
        request_count = -1
    valid_status = row.status in {"submitted", "ended"} or (
        row.status == "results_applied" and row.metered_at is not None
    )
    if (
        not isinstance(claim, dict)
        or not valid_status
        or claim.get("version") != 2
        or claim.get("state") != "submitted"
        or str(claim.get("provider_batch_id") or "") != str(row.batch_id)
        or not _claim_context_is_owned(row, claim)
    ):
        issues.append("invalid_submitted_claim")
    if set(attribution) != set(expected_ids):
        issues.append("invalid_submitted_attribution")
    if request_count != len(expected_ids):
        issues.append("submitted_attribution_count_mismatch")
    if not expected_ids or any(not custom_id.strip() for custom_id in expected_ids):
        issues.append("empty_submitted_attribution")
    if (
        isinstance(pending, dict)
        and pending.get("state") == "validated"
        and pending.get("expected_custom_ids_sha256")
        != _expected_ids_sha256(expected_ids)
    ):
        issues.append("validated_attribution_changed")
    return expected_ids, issues


def strict_result_contract(
    row: AnthropicBatchJob,
    entries: list,
    *,
    receipts: Optional[Mapping[str, dict]] = None,
) -> tuple[Optional[frozenset[str]], list[str]]:
    """Validate a complete result identity set before any per-result work.

    Claim absence is the only legacy signal. A present but malformed claim is
    not allowed to silently downgrade to best-effort attribution.
    """
    expected_ids, issues = strict_anchor_contract(row)
    if expected_ids is None:
        return None, issues
    try:
        request_count = int(row.request_count)
    except (TypeError, ValueError):
        request_count = -1

    observed_values = [getattr(entry, "custom_id", "") for entry in entries]
    observed_ids = [str(value or "") for value in observed_values]
    observed_set = set(observed_ids)
    issues.extend(result_identity_issues(entries))
    if len(entries) != request_count:
        issues.append("result_count_mismatch")
    if expected_ids.difference(observed_set):
        issues.append("missing_result_custom_ids")
    if observed_set.difference(expected_ids):
        issues.append("extra_result_custom_ids")
    context = row.context if isinstance(row.context, dict) else {}
    validation = context.get(RESULT_ATTRIBUTION_KEY)
    if (
        isinstance(validation, dict)
        and validation.get("state") == "validated"
        and validation.get("result_outcomes_sha256")
        != _sha256_json(_result_outcomes(entries))
    ):
        issues.append("result_outcome_mismatch")
    if (
        isinstance(validation, dict)
        and validation.get("state") == "validated"
        and "succeeded_provider_ids_sha256" in validation
        and validation.get("succeeded_provider_ids_sha256")
        != _sha256_json(_succeeded_provider_identities(entries))
    ):
        issues.append("provider_message_id_mismatch")
    if _receipt_outcome_mismatch(context, entries, receipts):
        issues.append("result_outcome_receipt_mismatch")
    return expected_ids, issues


def store_attribution_validated(
    row: AnthropicBatchJob,
    *,
    entries: list,
    expected_ids: frozenset[str],
) -> bool:
    """Persist the first exact result-type contract before any entry work."""
    context = dict(row.context) if isinstance(row.context, dict) else {}
    prior = context.get(RESULT_ATTRIBUTION_KEY)
    receipt = {
        "version": 2,
        "state": "validated",
        "expected_request_count": int(row.request_count or 0),
        "expected_custom_ids_sha256": _expected_ids_sha256(expected_ids),
        "observed_result_count": len(entries),
        "result_outcomes_sha256": _sha256_json(_result_outcomes(entries)),
        "succeeded_provider_ids_sha256": _sha256_json(
            _succeeded_provider_identities(entries)
        ),
    }
    if isinstance(prior, dict) and all(
        prior.get(key) == value for key, value in receipt.items()
    ):
        return False
    receipt["validated_at"] = datetime.now(timezone.utc).isoformat()
    context[RESULT_ATTRIBUTION_KEY] = receipt
    row.context = context
    return True


def store_attribution_pending(
    row: AnthropicBatchJob,
    *,
    entries: list,
    expected_ids: frozenset[str],
    issues: list[str],
) -> None:
    """Preserve the observed provider identity evidence for reconciliation."""
    context = dict(row.context) if isinstance(row.context, dict) else {}
    prior = context.get(RESULT_ATTRIBUTION_KEY)
    prior = prior if isinstance(prior, dict) else {}
    now = datetime.now(timezone.utc).isoformat()
    observed_results = _result_outcomes(entries)
    provider_identities = _succeeded_provider_identities(entries)
    provider_ids = [provider_id for _, provider_id in provider_identities]
    provider_id_counts = Counter(provider_ids)
    observed_ids = [custom_id for custom_id, _ in observed_results]
    counts = Counter(observed_ids)
    observed_set = set(observed_ids)
    try:
        observation_count = max(int(prior.get("observation_count") or 0), 0)
    except (TypeError, ValueError):
        observation_count = 0
    current_issues = _bounded_issues(issues)
    prior_issues = _bounded_issues(prior.get("issues"))
    all_issues = _bounded_issues([*prior_issues, *current_issues])
    evidence = {
        "version": 1,
        "state": "reconciliation_pending",
        "first_observed_at": prior.get("first_observed_at")
        or prior.get("validated_at")
        or now,
        "last_observed_at": now,
        "observation_count": observation_count + 1,
        "expected_request_count": int(row.request_count or 0),
        "expected_custom_ids_sha256": _expected_ids_sha256(expected_ids),
        "observed_result_count": len(entries),
        "observed_results_sha256": _sha256_json(observed_results),
        "succeeded_provider_ids_sha256": _sha256_json(provider_identities),
        "first_succeeded_provider_ids_sha256": prior.get(
            "first_succeeded_provider_ids_sha256"
        )
        or prior.get("succeeded_provider_ids_sha256")
        or _sha256_json(provider_identities),
        "first_observed_results_sha256": prior.get("first_observed_results_sha256")
        or prior.get("result_outcomes_sha256")
        or _sha256_json(observed_results),
        "missing_custom_id_sample": _bounded_ids(expected_ids.difference(observed_set)),
        "extra_custom_id_sample": _bounded_ids(observed_set.difference(expected_ids)),
        "duplicate_custom_id_sample": _bounded_ids(
            {custom_id for custom_id, count in counts.items() if count > 1}
        ),
        "duplicate_provider_message_id_sample": _bounded_ids(
            {
                provider_id
                for provider_id, count in provider_id_counts.items()
                if provider_id and count > 1
            }
        ),
        "observed_result_sample": [
            {
                "custom_id": custom_id[:_EVIDENCE_TEXT_LIMIT],
                "result_type": result_type[:_EVIDENCE_TEXT_LIMIT],
            }
            for custom_id, result_type in observed_results[:_EVIDENCE_SAMPLE_LIMIT]
        ],
        "first_issues": _bounded_issues(prior.get("first_issues")) or current_issues,
        "latest_issues": current_issues,
        "issues": all_issues,
    }
    if row.metered_at is not None:
        evidence["prior_metered_at"] = row.metered_at.isoformat()
        evidence["prior_metered_count"] = int(row.metered_count or 0)
    elif prior.get("prior_metered_at"):
        evidence["prior_metered_at"] = str(prior["prior_metered_at"])[
            :_EVIDENCE_TEXT_LIMIT
        ]
        try:
            evidence["prior_metered_count"] = max(
                int(prior.get("prior_metered_count") or 0),
                0,
            )
        except (TypeError, ValueError):
            evidence["prior_metered_count"] = 0
    context[RESULT_ATTRIBUTION_KEY] = evidence
    row.context = context


__all__ = [
    "RESULT_ATTRIBUTION_KEY",
    "result_identity_issues",
    "store_attribution_pending",
    "store_attribution_validated",
    "strict_anchor_contract",
    "strict_result_contract",
]
