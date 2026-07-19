"""Hard admission and settlement for one CV-score cache fee."""

from __future__ import annotations

from typing import Any, Callable

from .pricing_service import Feature
from .provider_usage_admission import release_provider_usage, reserve_provider_usage


def record_cv_score_cache_fee(
    db: Any,
    *,
    organization_id: int,
    role_id: int,
    application_id: int,
    candidate_id: int | None,
    score_job_id: int,
    trace_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    record_event: Callable[..., Any],
) -> None:
    metadata = {
        "source": "cv_score_cache_fee",
        "score_job_id": score_job_id,
        "candidate_id": candidate_id,
        "provider": None,
        "request_sha256": None,
    }
    reservation = reserve_provider_usage(
        organization_id=organization_id,
        role_id=role_id,
        feature=Feature.SCORE,
        trace_id=f"{trace_id}:cache-hit",
        entity_id=f"application:{application_id}",
        candidate_id=candidate_id,
        provider=None,
        model=model,
        request_sha256=None,
        metadata=metadata,
    )
    try:
        record_event(
            db,
            organization_id=organization_id,
            role_id=role_id,
            feature=Feature.SCORE,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_hit=True,
            entity_id=f"application:{application_id}",
            metadata=metadata,
            credit_reservation=reservation.as_metering_payload(),
        )
    except Exception:
        release_provider_usage(
            reservation,
            reason="cv_score_cache_fee_record_failed",
        )
        raise


__all__ = ["record_cv_score_cache_fee"]
