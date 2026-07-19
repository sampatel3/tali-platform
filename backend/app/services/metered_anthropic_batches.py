"""Metered and hard-admitted Anthropic Message Batch SDK surface."""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..models.anthropic_batch_job import AnthropicBatchJob
from ..platform.database import SessionLocal
from .anthropic_batch_admission import prepare_batch_admission, release_batch_reservations
from .anthropic_batch_submission import (
    mark_batch_submission_attempt_started,
    record_batch_submission,
    record_batch_submission_failure_safe,
    submission_claim_from_metering,
)
from .anthropic_metering_identity import resolve_organization_id
from .anthropic_surface_guard import (
    NONBILLABLE_BATCH_OPERATIONS,
    UnsupportedAnthropicSurfaceError,
)
from .pricing_service import Feature
from .provider_error_evidence import safe_provider_error_code
from .provider_usage_admission import (
    mark_provider_attempt_started,
    release_provider_usage_if_definitely_nonbillable,
)
from .usage_credit_reservations import reservation_from_payload

logger = logging.getLogger("taali.metered_anthropic")


class MeteredAnthropicBatches:
    """Bridge batch submission attribution to deferred result settlement."""

    def __init__(self, *, messages: Any):
        self._messages = messages
        self._inner = messages._inner.batches

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in NONBILLABLE_BATCH_OPERATIONS:
            return getattr(self._inner, name)
        raise UnsupportedAnthropicSurfaceError(
            "Anthropic batch operation is unavailable until metering is implemented"
        )

    def create(self, **kwargs: Any) -> Any:
        # Imported lazily to keep the parent wrapper free of a module cycle.
        from .metered_anthropic_client import (
            MeteringRequiredError,
            ProviderAttemptMarkerError,
        )

        metering = kwargs.pop("metering", None)
        if not isinstance(metering, dict):
            raise TypeError(
                f"`metering` must be a dict, got {type(metering).__name__}"
            )

        request_models: dict[str, str] = {}
        reservation_entries: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        by_custom_id: dict[str, dict[str, Any]] = {}
        requests: list[dict[str, Any]] = []
        feature_str = ""
        claim_batch_id: Optional[str] = None
        claim_attempt_id: Optional[str] = None
        provider_invoked = False
        markers_started = False
        try:
            organization_id = resolve_organization_id(
                client_organization_id=self._messages._organization_id,
                metering=metering,
                require_client_match=True,
            )
            if organization_id is None:
                raise ValueError("batch submission requires organization_id")
            feature = metering.get("feature")
            if feature is None:
                raise MeteringRequiredError(
                    "batch metering requires a feature"
                )
            feature_str = (
                feature.value if isinstance(feature, Feature) else str(feature)
            )
            try:
                feature_enum = Feature(feature_str)
            except ValueError as exc:
                raise MeteringRequiredError(
                    f"unknown metering feature {feature_str!r} for batch submit"
                ) from exc

            admission = prepare_batch_admission(
                requests=kwargs.get("requests"),
                metering=metering,
                feature=feature_enum,
                organization_id=organization_id,
            )
            requests = admission.requests
            request_models = admission.request_models
            by_custom_id = admission.by_custom_id
            reservation_entries = admission.reservation_entries
            kwargs["requests"] = requests
            claim_batch_id, claim_attempt_id = submission_claim_from_metering(
                metering
            )
            for _, _, reservation in reservation_entries:
                if not mark_provider_attempt_started(
                    reservation,
                    provider="anthropic_batch",
                ):
                    raise ProviderAttemptMarkerError(
                        "could not durably mark Anthropic batch attempt"
                    )
                markers_started = True
            if not mark_batch_submission_attempt_started(
                claim_batch_id=claim_batch_id,
                claim_attempt_id=claim_attempt_id,
                feature=feature_str,
                organization_id=organization_id,
                by_custom_id=by_custom_id,
                requests=requests,
            ):
                raise ProviderAttemptMarkerError(
                    "could not durably mark Anthropic batch submission claim"
                )
            provider_invoked = True
            batch = self._inner.create(**kwargs)
        except Exception as exc:
            if not provider_invoked:
                release_batch_reservations(
                    reservation_entries,
                    reason=(
                        "anthropic_batch_attempt_marker_failed"
                        if markers_started
                        else "anthropic_batch_local_validation_failed"
                    ),
                    allow_started=markers_started,
                )
            for custom_id, per, reservation in reservation_entries:
                released = (
                    release_provider_usage_if_definitely_nonbillable(
                        reservation,
                        error=exc,
                        reason="anthropic_batch_submit_error",
                    )
                    if provider_invoked
                    else True
                )
                parsed = reservation_from_payload(reservation)
                evidence_org_id = (
                    per.get("organization_id")
                    or organization_id
                    or (parsed.organization_id if parsed is not None else None)
                )
                self._messages._record_call_log_safe(
                    organization_id=(
                        int(evidence_org_id)
                        if evidence_org_id is not None
                        else None
                    ),
                    model=request_models.get(custom_id, ""),
                    usage=None,
                    feature_hint=feature_str,
                    status="sdk_error" if released else "sdk_ambiguous_error",
                    error_reason=safe_provider_error_code(
                        exc,
                        operation="anthropic_batch_create",
                    ),
                    anthropic_request_id=None,
                    service_tier="batch",
                )
            if claim_batch_id and claim_attempt_id:
                record_batch_submission_failure_safe(
                    claim_batch_id=claim_batch_id,
                    claim_attempt_id=claim_attempt_id,
                    error=exc,
                    provider_invoked=provider_invoked,
                )
            else:
                logger.error(
                    "batch admission failed before claim error_code=%s",
                    safe_provider_error_code(
                        exc,
                        operation="anthropic_batch_admission",
                    ),
                )
            raise

        record_batch_submission(
            batch_id=str(getattr(batch, "id", "") or ""),
            feature=feature_str,
            organization_id=organization_id,
            by_custom_id=by_custom_id,
            requests=requests,
            claim_batch_id=str(claim_batch_id),
            claim_attempt_id=str(claim_attempt_id),
        )
        return batch

    def _require_owned_batch_id(self, batch_id: str) -> str:
        normalized = str(batch_id or "").strip()
        organization_id = self._messages._organization_id
        if not normalized or organization_id is None or int(organization_id) <= 0:
            raise UnsupportedAnthropicSurfaceError(
                "Anthropic batch access requires organization ownership"
            )
        with SessionLocal() as session:
            owned = (
                session.query(AnthropicBatchJob.id)
                .filter(
                    AnthropicBatchJob.batch_id == normalized,
                    AnthropicBatchJob.organization_id == int(organization_id),
                )
                .first()
            )
        if owned is None:
            raise UnsupportedAnthropicSurfaceError(
                "Anthropic batch is not owned by this organization"
            )
        return normalized

    def retrieve(self, batch_id: str, **kwargs: Any) -> Any:
        return self._inner.retrieve(
            self._require_owned_batch_id(batch_id),
            **kwargs,
        )

    def results(self, batch_id: str, **kwargs: Any) -> Any:
        owned_batch_id = self._require_owned_batch_id(batch_id)
        entries = list(self._inner.results(owned_batch_id, **kwargs))
        from .anthropic_batch_result_metering import meter_batch_results_safe

        meter_batch_results_safe(
            self._messages,
            batch_id=owned_batch_id,
            entries=entries,
        )
        return iter(entries)


__all__ = ["MeteredAnthropicBatches"]
