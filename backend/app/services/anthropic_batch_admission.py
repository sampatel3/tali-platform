"""Universal per-request admission for Anthropic Message Batch submission."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from .claude_model_pricing import materialize_priceable_batch_requests
from .anthropic_request_admission import anthropic_request_credit_upper_bound
from .pricing_service import Feature
from .provider_request_identity import provider_request_sha256
from .provider_reservation_contract import (
    reservation_matches as provider_reservation_matches_attribution,
)
from .provider_usage_admission import (
    release_provider_usage,
    replace_provider_usage_reservation,
    reserve_provider_usage,
)
from .usage_credit_reservations import reservation_from_payload


@dataclass(frozen=True)
class PreparedBatchAdmission:
    requests: list[dict[str, Any]]
    request_models: dict[str, str]
    by_custom_id: dict[str, dict[str, Any]]
    reservation_entries: list[tuple[str, dict[str, Any], dict[str, Any]]]


def _authenticated_supplied_entries(
    metering: dict[str, Any],
    *,
    feature: Feature,
    organization_id: int,
    request_by_custom_id: dict[str, dict[str, Any]],
    request_models: dict[str, str],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Find only exact trusted holds that local validation may compensate."""

    supplied = metering.get("by_custom_id")
    if type(supplied) is not dict:
        return []
    entries = []
    for custom_id, raw_context in supplied.items():
        if type(raw_context) is not dict:
            continue
        context = dict(raw_context)
        context_org = context.get("organization_id")
        if context_org is not None and context_org != organization_id:
            continue
        role_id = context.get("role_id")
        if role_id is not None and (type(role_id) is not int or role_id <= 0):
            continue
        user_id = context.get("user_id")
        if user_id is not None and (type(user_id) is not int or user_id <= 0):
            continue
        candidate_id = context.get("candidate_id")
        if candidate_id is not None and (
            type(candidate_id) is not int or candidate_id <= 0
        ):
            continue
        entity_id = context.get("entity_id")
        if entity_id is None:
            entity_id = str(custom_id)
            context["entity_id"] = entity_id
        if entity_id is not None and (
            type(entity_id) is not str or not entity_id.strip()
        ):
            continue
        request = request_by_custom_id.get(str(custom_id))
        model = request_models.get(str(custom_id))
        if request is None or model is None:
            continue
        try:
            request_hash = provider_request_sha256(request)
        except ValueError:
            continue
        payload = context.get("credit_reservation")
        if isinstance(payload, dict) and provider_reservation_matches_attribution(
            payload,
            organization_id=organization_id,
            feature=feature,
            role_id=role_id,
            user_id=user_id,
            entity_id=entity_id,
            candidate_id=candidate_id,
            provider="anthropic_batch",
            model=model,
            request_sha256=request_hash,
        ):
            entries.append((str(custom_id), context, payload))
    return entries


def release_batch_reservations(
    entries: list[tuple[str, dict[str, Any], dict[str, Any]]],
    *,
    reason: str,
    allow_started: bool = False,
) -> None:
    """Release each distinct reservation at most once."""

    seen: set[str] = set()
    for _, _, payload in entries:
        parsed = reservation_from_payload(payload)
        ref = parsed.external_ref if parsed is not None else ""
        if not ref or ref in seen:
            continue
        seen.add(ref)
        release_provider_usage(
            payload,
            reason=reason,
            allow_started=allow_started,
        )


def prepare_batch_admission(
    *,
    requests: Any,
    metering: dict[str, Any],
    feature: Feature,
    organization_id: int | None,
) -> PreparedBatchAdmission:
    """Validate exact attribution and install one hold per request.

    Any failure is provably local. Existing caller holds and holds created by
    this function are therefore compensated before the exception escapes.
    """

    if type(organization_id) is not int or organization_id <= 0:
        raise ValueError("batch submission requires organization attribution")
    effective_org_id = organization_id
    # Only exact ledger-backed or authenticated-shadow holds are refundable.
    all_entries: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    try:
        if type(requests) is list:
            partial_requests: dict[str, dict[str, Any]] = {}
            partial_models: dict[str, str] = {}
            for raw_request in requests:
                try:
                    valid, models = materialize_priceable_batch_requests(
                        [raw_request]
                    )
                except (TypeError, ValueError):
                    continue
                request = valid[0]
                custom_id = str(request["custom_id"])
                partial_requests[custom_id] = request
                partial_models.update(models)
            all_entries = _authenticated_supplied_entries(
                metering,
                feature=feature,
                organization_id=effective_org_id,
                request_by_custom_id=partial_requests,
                request_models=partial_models,
            )
        materialized, request_models = materialize_priceable_batch_requests(requests)
        request_by_custom_id = {
            str(request["custom_id"]): request for request in materialized
        }
        all_entries.extend(
            _authenticated_supplied_entries(
                metering,
                feature=feature,
                organization_id=effective_org_id,
                request_by_custom_id=request_by_custom_id,
                request_models=request_models,
            )
        )
        request_ids = set(request_models)
        supplied_context = metering.get("by_custom_id")
        if supplied_context is None:
            supplied_context = {custom_id: {} for custom_id in request_models}
        if type(supplied_context) is not dict:
            raise ValueError("batch by_custom_id must be an object")
        raw_metadata = metering.get("metadata")
        if raw_metadata is not None and type(raw_metadata) is not dict:
            raise ValueError("batch metadata must be an object")
        if set(supplied_context) != request_ids:
            raise ValueError(
                "batch attribution must exactly match request custom_id values"
            )

        admitted_context: dict[str, dict[str, Any]] = {}
        reservation_entries: list[
            tuple[str, dict[str, Any], dict[str, Any]]
        ] = []
        reservation_refs: set[str] = set()
        supplied_reservation_refs: set[str] = set()
        for custom_id in request_models:
            raw_context = supplied_context[custom_id]
            if type(raw_context) is not dict:
                raise ValueError("each batch attribution context must be an object")
            per = dict(raw_context)
            context_org = per.get("organization_id")
            if context_org is not None and (
                type(context_org) is not int
                or context_org <= 0
                or context_org != effective_org_id
            ):
                raise ValueError(
                    "batch attribution organization does not match client"
                )
            per["organization_id"] = effective_org_id
            role_id = per.get("role_id")
            if role_id is not None and (type(role_id) is not int or role_id <= 0):
                raise ValueError("batch attribution role_id must be a positive integer")
            user_id = per.get("user_id")
            if user_id is not None and (type(user_id) is not int or user_id <= 0):
                raise ValueError("batch attribution user_id must be a positive integer")
            candidate_id = per.get("candidate_id")
            if candidate_id is not None and (
                type(candidate_id) is not int or candidate_id <= 0
            ):
                raise ValueError(
                    "batch attribution candidate_id must be a positive integer"
                )
            entity_id = per.get("entity_id")
            if entity_id is None:
                entity_id = custom_id
                per["entity_id"] = entity_id
            if entity_id is not None and (
                type(entity_id) is not str or not entity_id.strip()
            ):
                raise ValueError(
                    "batch attribution entity_id must be a non-empty string"
                )
            request_hash = provider_request_sha256(
                request_by_custom_id[custom_id]
            )
            model = request_models[custom_id]
            required_amount = anthropic_request_credit_upper_bound(
                dict(request_by_custom_id[custom_id]["params"]),
                feature=feature,
                service_tier="batch",
            )
            payload = per.get("credit_reservation")
            if payload is not None:
                parsed = reservation_from_payload(payload)
                if (
                    parsed is None
                    or not provider_reservation_matches_attribution(
                        payload,
                        organization_id=effective_org_id,
                        feature=feature,
                        role_id=role_id,
                        user_id=user_id,
                        entity_id=entity_id,
                        candidate_id=candidate_id,
                        provider="anthropic_batch",
                        model=model,
                        request_sha256=request_hash,
                    )
                ):
                    raise ValueError("batch credit reservation does not match request")
                if parsed.external_ref in supplied_reservation_refs:
                    raise ValueError("batch requests require distinct credit reservations")
                supplied_reservation_refs.add(parsed.external_ref)
                if int(parsed.amount) < int(required_amount):
                    replacement = replace_provider_usage_reservation(
                        payload,
                        organization_id=effective_org_id,
                        role_id=role_id,
                        feature=feature,
                        trace_id=str(
                            metering.get("trace_id")
                            or f"anthropic-batch:{custom_id}:{uuid.uuid4().hex}"
                        ),
                        amount=required_amount,
                        user_id=user_id,
                        entity_id=entity_id,
                        candidate_id=candidate_id,
                        provider="anthropic_batch",
                        model=model,
                        request_sha256=request_hash,
                        metadata={
                            **dict(raw_metadata or {}),
                            "custom_id": custom_id,
                            "admission_source": "metered_anthropic_batch_bound_upgrade",
                        },
                    )
                    payload = replacement.as_metering_payload()
                    per["credit_reservation"] = payload
                all_entries.append((custom_id, per, payload))
            else:
                reservation = reserve_provider_usage(
                    organization_id=effective_org_id,
                    role_id=role_id,
                    feature=feature,
                    trace_id=str(
                        metering.get("trace_id")
                        or f"anthropic-batch:{custom_id}:{uuid.uuid4().hex}"
                    ),
                    user_id=user_id,
                    entity_id=entity_id,
                    candidate_id=candidate_id,
                    provider="anthropic_batch",
                    model=model,
                    request_sha256=request_hash,
                    sub_feature="anthropic_batch_request",
                    amount=required_amount,
                    metadata={
                        **dict(raw_metadata or {}),
                        "custom_id": custom_id,
                        "admission_source": "metered_anthropic_batch_fallback",
                    },
                )
                payload = reservation.as_metering_payload()
                per["credit_reservation"] = payload
                all_entries.append((custom_id, per, payload))
            parsed_payload = reservation_from_payload(payload)
            if parsed_payload is None:
                raise ValueError("batch credit reservation is invalid")
            if parsed_payload.external_ref in reservation_refs:
                raise ValueError("batch requests require distinct credit reservations")
            reservation_refs.add(parsed_payload.external_ref)
            admitted_context[custom_id] = per
            reservation_entries.append((custom_id, per, payload))

        return PreparedBatchAdmission(
            requests=materialized,
            request_models=request_models,
            by_custom_id=admitted_context,
            reservation_entries=reservation_entries,
        )
    except Exception:
        release_batch_reservations(
            all_entries,
            reason="anthropic_batch_local_validation_failed",
        )
        raise


__all__ = [
    "PreparedBatchAdmission",
    "prepare_batch_admission",
    "release_batch_reservations",
]
