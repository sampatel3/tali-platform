"""Metering and hard-admission wrapper around the Anthropic SDK.

Clients come from ``claude_client_resolver``. The wrapper consumes the local
``metering`` kwarg before the wire call, records usage in an independent
transaction, and preserves the SDK streaming context-manager API.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from anthropic import Anthropic

from ..models.claude_call_log import ClaudeCallLog
from ..models.usage_event import UsageEvent
from ..platform.database import SessionLocal
from .anthropic_surface_guard import (
    NONBILLABLE_MESSAGE_OPERATIONS,
    NONBILLABLE_MODEL_OPERATIONS,
    NonbillableAnthropicResource,
    UnsupportedAnthropicSurfaceError,
)
from . import anthropic_metering_identity as identity
from .anthropic_reservation_admission import (
    ProviderAttemptMarkerError,
    ensure_anthropic_provider_reservation,
)
from .anthropic_usage_tokens import (
    extract_cache_creation_1h as _extract_cache_creation_1h,
)
from .claude_model_pricing import (
    require_priceable_claude_model,
)
from .pricing_service import Feature, raw_cost_usd_micro
from .metered_anthropic_stream import MeteredAnthropicStreamContext
from .provider_error_evidence import (
    classify_anthropic_exception,
    safe_provider_error_code,
)
from . import provider_retry_policy as retry_policy
from .provider_usage_admission import (
    mark_provider_usage_succeeded,
    release_provider_usage_if_definitely_nonbillable,
)
from .usage_credit_reservations import (
    release_credit_reservation,
    reservation_from_payload,
)
from .usage_metering_service import record_event

logger = logging.getLogger("taali.metered_anthropic")


class MeteringRequiredError(ValueError):
    """Raised when a caller passes ``metering`` without a ``feature`` key.

    A caller intentionally tagging the call must name its feature; an
    accidentally-missing ``metering`` falls back to ``Feature.OTHER`` with
    a warning, but a *partial* metering dict is almost certainly a bug.
    """


class _MeteredMessages:
    """Wraps ``Anthropic.messages`` to record a ``usage_event`` per call.

    Holds a reference to the org_id captured at client construction so
    callers don't have to repeat it. Each call may pass its own
    ``user_id`` / ``role_id`` / ``entity_id`` for finer attribution.
    """

    def __init__(self, *, inner: Any, organization_id: Optional[int]):
        self._inner = inner
        self._organization_id = organization_id

    # ``messages.batches`` is intercepted below.  Alternative response
    # wrappers (``with_raw_response`` / ``with_streaming_response``) are
    # intentionally blocked: they expose their own ``create`` and would skip
    # the hard-admission and settlement path in this class.
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in NONBILLABLE_MESSAGE_OPERATIONS:
            return getattr(self._inner, name)
        raise UnsupportedAnthropicSurfaceError(
            "Anthropic messages operation is unavailable until metering is implemented"
        )

    @property
    def batches(self) -> Any:
        from .metered_anthropic_batches import MeteredAnthropicBatches

        return MeteredAnthropicBatches(messages=self)

    # ----- public API -----------------------------------------------------

    @staticmethod
    def _retry_context(metering: Any) -> tuple[int, Optional[int], Optional[str]]:
        """B1: pull retry threading hints off the metering dict.

        Callers that orchestrate their own retries (cv_match's
        validation loop, the agent orchestrator's per-round calls)
        pass ``retry_attempt`` / ``parent_call_log_id`` / ``trace_id``
        so claude_call_log rows can be chained. Defaults are
        (0, None, None) — first try, no parent, no trace.
        """
        if not isinstance(metering, dict):
            return (0, None, None)
        try:
            attempt = int(metering.get("retry_attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        parent = metering.get("parent_call_log_id")
        if parent is not None:
            try:
                parent = int(parent)
            except (TypeError, ValueError):
                parent = None
        trace = metering.get("trace_id")
        trace = str(trace) if trace else None
        return (attempt, parent, trace)

    def create(self, **kwargs: Any) -> Any:
        metering = self._extract_metering(kwargs)
        wire_attempt_limit = retry_policy.provider_wire_attempt_limit(metering)
        model = str(kwargs.get("model") or "")
        feature_hint = self._feature_hint_from(metering)
        base_retry_attempt, parent_call_log_id, trace_id = self._retry_context(
            metering
        )
        base_retry_attempt = max(base_retry_attempt, 0)
        attempt_index = 0
        retry_evidence_missing = False
        while True:
            self._ensure_provider_reservation(metering, request=kwargs)
            trace_id = self._retry_context(metering)[2] or trace_id
            retry_attempt = base_retry_attempt + attempt_index
            try:
                response = self._inner.create(**kwargs)
                break
            except Exception as exc:
                # A timeout can arrive after Anthropic accepted and billed the
                # request. Retain ambiguous holds; only explicit rejections are
                # released. A retry always reserves a new single-use hold.
                error_class, http_status = self._classify_exception(exc)
                reservation_payload = metering.get("credit_reservation")
                released = release_provider_usage_if_definitely_nonbillable(
                    reservation_payload,
                    error=exc,
                    reason=f"sdk_error:{error_class or 'other'}",
                )
                logger.error(
                    "Anthropic create failed org=%s model=%s error_type=%s",
                    self._call_org_id(metering),
                    model,
                    type(exc).__name__,
                )
                failure_log_id = self._record_call_log_safe(
                    organization_id=self._call_org_id(metering),
                    model=model,
                    usage=None,
                    feature_hint=feature_hint,
                    status=(
                        "sdk_ambiguous_error"
                        if reservation_payload and not released
                        else "sdk_error"
                    ),
                    error_reason=safe_provider_error_code(
                        exc,
                        operation="anthropic_create",
                    ),
                    anthropic_request_id=None,
                    error_class=error_class,
                    http_status=http_status,
                    retry_attempt=retry_attempt,
                    parent_call_log_id=parent_call_log_id,
                    trace_id=trace_id,
                )
                retryable = retry_policy.provider_error_is_retryable(exc)
                if retryable and failure_log_id is None:
                    logger.error(
                        "Anthropic retry blocked: failure evidence unavailable"
                    )
                    retry_evidence_missing = True
                    break
                if not retry_policy.should_retry_provider_error(
                    exc,
                    attempt_index=attempt_index,
                    max_attempts=wire_attempt_limit,
                ):
                    raise
                parent_call_log_id = failure_log_id
                attempt_index += 1
                retry_policy.sleep_before_retry(
                    next_attempt_index=attempt_index,
                    error=exc,
                )
                metering = retry_policy.metering_for_retry(
                    metering,
                    retry_attempt=base_retry_attempt + attempt_index,
                )

        if retry_evidence_missing:
            # Raise outside the provider exception handler so an error body is
            # not retained as exception context by an outer task/result backend.
            raise retry_policy.ProviderRetryEvidenceUnavailableError(
                "provider retry evidence is unavailable"
            )

        usage = getattr(response, "usage", None)
        request_id = self._extract_request_id(response)
        usage_event: Optional[UsageEvent] = None

        self._mark_provider_success(
            usage=usage,
            model=model,
            metering=metering,
            provider_request_id=request_id,
        )
        usage_event = self._record_from_usage(
            usage=usage,
            model=model,
            metering=metering,
        )

        # Every attempted call lands evidence. A NULL usage_event_id exposes
        # an attribution/metering gap instead of hiding it.
        self._record_call_log_safe(
            organization_id=self._call_org_id(metering),
            model=model,
            usage=usage,
            feature_hint=feature_hint,
            status=(
                "metering_error"
                if (
                    usage is not None
                    and isinstance(metering, dict)
                    and metering.get("credit_reservation")
                    and usage_event is None
                )
                else "ok" if usage is not None else "no_usage_on_response"
            ),
            error_reason=None,
            anthropic_request_id=request_id,
            usage_event_id=int(usage_event.id) if usage_event is not None else None,
            retry_attempt=retry_attempt,
            parent_call_log_id=parent_call_log_id,
            trace_id=trace_id,
        )
        return response

    def stream(self, **kwargs: Any):
        metering = self._extract_metering(kwargs)
        # Anthropic does not start the paid request until the returned context
        # manager is entered. Defer both the hold and attempt marker until
        # ``__enter__`` so constructing-but-never-using a stream cannot strand
        # conservative provider capacity.
        inner_cm = self._inner.stream(**kwargs)
        return MeteredAnthropicStreamContext(
            inner=inner_cm,
            inner_factory=lambda: self._inner.stream(**kwargs),
            messages=self,
            model=str(kwargs.get("model") or ""),
            metering=metering,
            request=kwargs,
        )

    async def acreate(self, **_kwargs: Any) -> Any:
        raise UnsupportedAnthropicSurfaceError(
            "Use MeteredAsyncAnthropic for paid async message calls"
        )

    # ----- internals ------------------------------------------------------

    def _extract_metering(self, kwargs: dict[str, Any]):
        """Pop the metering kwarg from ``kwargs`` and normalise it.

        Returns one of:
        - ``dict`` with a resolved ``feature`` key + optional db/user_id/etc
        A legacy ``skip`` request is rejected because it cannot authorize
        unreserved provider spend.
        """
        require_priceable_claude_model(str(kwargs.get("model") or ""))
        meter = kwargs.pop("metering", None)
        if meter is None:
            # No metering specified → record as Feature.OTHER with a warning
            # so attribution is *visible* but flagged. Better than dropping.
            logger.warning(
                "metered_anthropic: call to %s did not pass `metering=` — "
                "falling back to Feature.OTHER. Add `metering={\"feature\": Feature.X, ...}` "
                "to attribute spend correctly.",
                kwargs.get("model") or "<unknown-model>",
            )
            fallback = {"feature": Feature.OTHER}
            self._call_org_id(fallback)
            return fallback

        if not isinstance(meter, dict):
            raise TypeError(
                f"`metering` must be a dict, got {type(meter).__name__}"
            )

        if meter.get("skip"):
            raise ProviderAttemptMarkerError(
                "metering skip cannot authorize an Anthropic provider call"
            )

        feature = meter.get("feature")
        if feature is None:
            raise MeteringRequiredError(
                "metering={...} must include a `feature` key (use "
                "Feature.OTHER for unclassified calls)"
            )

        # Validate local retry ownership before admission or any provider I/O.
        # Callers may shrink the wrapper's cap when they own a larger outer
        # retry/deadline loop, but cannot expand the global wire-attempt bound.
        retry_policy.provider_wire_attempt_limit(meter)

        self._call_org_id(meter)
        return dict(meter)

    def _ensure_provider_reservation(
        self,
        metering: Any,
        *,
        request: dict[str, Any],
    ) -> None:
        """Install the universal hard-admission fallback for a paid call.

        Feature services can reserve explicitly when they need a custom amount
        or a durable reservation that crosses process boundaries.  This guard
        covers every other organization-attributed SDK attempt, including
        workspace chat, validation retries and multi-call scoring pipelines.
        A role is optional for genuine workspace-level work; the organization
        is never guessed, and role-budget admission is applied only when a
        real role id is supplied.
        """

        if isinstance(metering, dict):
            organization_id = self._call_org_id(metering)
            ensure_anthropic_provider_reservation(
                metering=metering,
                request=request,
                organization_id=organization_id,
            )

    # ----- claude_call_log helpers (P0 — source-of-truth log) ------------

    @staticmethod
    def _release_credit_reservation_safe(
        metering: Any,
        *,
        reason: str,
        allow_started: bool = False,
    ) -> None:
        """Refund a hard hold when the SDK produced no billable response."""
        if not isinstance(metering, dict):
            return
        reservation = metering.get("credit_reservation")
        if not reservation:
            return
        try:
            with SessionLocal() as session:
                release_credit_reservation(
                    session,
                    reservation=reservation,
                    reason=reason,
                    allow_started=allow_started,
                )
                session.commit()
        except Exception as exc:
            # Keep the hold rather than risk a double refund. The ledger row is
            # traceable and can be recovered; the provider call itself failed.
            logger.error(
                "metered_anthropic: failed to release provider-error reservation "
                "error_type=%s",
                type(exc).__name__,
            )

    def _feature_hint_from(self, metering) -> Optional[str]:
        """Get the caller's intended feature label for the call_log row.

        ``metering`` is a dict with a feature key after boundary validation.
        """
        if isinstance(metering, dict):
            f = metering.get("feature")
            if isinstance(f, Feature):
                return f.value
            if f is not None:
                return str(f)
        return None

    def _call_org_id(self, metering) -> Optional[int]:
        """Resolve exact org attribution; a bound client cannot be retargeted."""
        return identity.resolve_organization_id(
            client_organization_id=self._organization_id,
            metering=metering if isinstance(metering, dict) else {},
            require_client_match=True,
        )

    @staticmethod
    def _extract_request_id(response: Any) -> Optional[str]:
        """Pull Anthropic's request_id from the response for cross-ref with
        the Console Logs page during incident response. Best effort — the
        SDK has put it in different places across versions."""
        for path in ("id", "_request_id"):
            val = getattr(response, path, None)
            if val:
                return str(val)
        return None

    _classify_exception = staticmethod(classify_anthropic_exception)

    def _build_call_log_row(
        self,
        *,
        organization_id: Optional[int],
        model: str,
        usage: Any,
        feature_hint: Optional[str],
        status: str,
        error_reason: Optional[str],
        anthropic_request_id: Optional[str],
        usage_event_id: Optional[int] = None,
        error_class: Optional[str] = None,
        http_status: Optional[int] = None,
        retry_attempt: int = 0,
        parent_call_log_id: Optional[int] = None,
        trace_id: Optional[str] = None,
        service_tier: str = "standard",
    ) -> ClaudeCallLog:
        """Build the canonical call-log row without choosing a transaction.

        Ordinary calls persist this row in their usual independent session.
        Batch results use the same builder inside the batch receipt transaction
        so the UsageEvent, call log, live settlement, and per-result receipt are
        either all durable or all rolled back together.
        """
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        cache_read_tokens = (
            int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0
        )
        cache_creation_tokens = (
            int(getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0
        )
        # Anthropic returns the 5m/1h split nested under
        # ``usage.cache_creation`` (CacheCreation object). We persist
        # the 1h slice separately so pricing can apply the 2.00× rate
        # to it (vs 1.25× for 5m). The legacy combined
        # ``cache_creation_tokens`` stays as the source of truth for
        # the total — pricing derives 5m = total - 1h.
        cache_creation_1h_tokens = _extract_cache_creation_1h(usage)
        try:
            cost_micro = raw_cost_usd_micro(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
                cache_creation_1h_tokens=cache_creation_1h_tokens,
                model=model,
                service_tier=service_tier,
            )
        except Exception:
            cost_micro = 0

        return ClaudeCallLog(
            organization_id=organization_id,
            model=model or "(unknown)",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_creation_1h_tokens=cache_creation_1h_tokens,
            cost_usd_micro=int(cost_micro),
            feature_hint=feature_hint,
            status=status,
            error_reason=error_reason,
            anthropic_request_id=anthropic_request_id,
            usage_event_id=usage_event_id,
            error_class=error_class,
            http_status=http_status,
            retry_attempt=int(retry_attempt or 0),
            parent_call_log_id=parent_call_log_id,
            trace_id=trace_id,
        )

    def _record_call_log_safe(
        self,
        *,
        organization_id: Optional[int],
        model: str,
        usage: Any,
        feature_hint: Optional[str],
        status: str,
        error_reason: Optional[str],
        anthropic_request_id: Optional[str],
        usage_event_id: Optional[int] = None,
        error_class: Optional[str] = None,
        http_status: Optional[int] = None,
        retry_attempt: int = 0,
        parent_call_log_id: Optional[int] = None,
        trace_id: Optional[str] = None,
        service_tier: str = "standard",
    ) -> int | None:
        """Write one ``ClaudeCallLog`` row without breaking a Claude call.

        Unconditional by design. This is the structural guarantee that every
        call lands a row, regardless of whether application-layer metering
        succeeded. Batch-result transactions call ``_build_call_log_row``
        directly so their multi-row receipt can remain atomic.
        """
        row = self._build_call_log_row(
            organization_id=organization_id,
            model=model,
            usage=usage,
            feature_hint=feature_hint,
            status=status,
            error_reason=error_reason,
            anthropic_request_id=anthropic_request_id,
            usage_event_id=usage_event_id,
            error_class=error_class,
            http_status=http_status,
            retry_attempt=retry_attempt,
            parent_call_log_id=parent_call_log_id,
            trace_id=trace_id,
            service_tier=service_tier,
        )
        try:
            with SessionLocal() as session:
                session.add(row)
                session.flush()
                row_id = int(row.id)
                session.commit()
            return row_id
        except Exception as exc:
            logger.error(
                "metered_anthropic: claude_call_log write failed (model=%s, "
                "status=%s) — Claude call already succeeded so we don't raise, "
                "but reconciliation against Anthropic billing will undercount. "
                "error_type=%s",
                model,
                status,
                type(exc).__name__,
            )
            return None

    # ----- usage_event recording (existing path) --------------------------

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _usage_event_payload(
        self,
        *,
        usage: Any,
        model: str,
        metering: dict[str, Any],
        service_tier: str = "standard",
    ) -> Optional[dict[str, Any]]:
        """Build one JSON-safe receipt shared by live and deferred writes."""

        org_id = self._call_org_id(metering)
        parsed = reservation_from_payload(metering.get("credit_reservation"))
        if org_id is None and parsed is not None:
            org_id = int(parsed.organization_id)
        if org_id is None or usage is None:
            return None
        feature = metering["feature"]
        feature_value = feature.value if isinstance(feature, Feature) else str(feature)
        metadata = metering.get("metadata")
        safe_metadata = json.loads(
            json.dumps(metadata if isinstance(metadata, dict) else {}, default=str)
        )
        if parsed is not None and parsed.version == 2:
            safe_metadata.update(
                {
                    "candidate_id": parsed.candidate_id,
                    "provider": parsed.provider,
                    "request_sha256": parsed.request_sha256,
                }
            )
        return {
            "organization_id": int(org_id),
            "feature": feature_value,
            "model": str(model),
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_tokens": int(
                getattr(usage, "cache_read_input_tokens", 0) or 0
            ),
            "cache_creation_tokens": int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
            "cache_creation_1h_tokens": _extract_cache_creation_1h(usage),
            "cache_hit": bool(metering.get("cache_hit", False)),
            "service_tier": str(service_tier or "standard"),
            "user_id": self._optional_int(metering.get("user_id")),
            "role_id": self._optional_int(metering.get("role_id")),
            "entity_id": (
                str(metering["entity_id"])
                if metering.get("entity_id") is not None
                else None
            ),
            "metadata": safe_metadata,
        }

    def _mark_provider_success(
        self,
        *,
        usage: Any,
        model: str,
        metering: dict[str, Any],
        provider_request_id: Optional[str],
        service_tier: str = "standard", provider: str = "anthropic",
    ) -> None:
        reservation = metering.get("credit_reservation")
        if not reservation:
            return
        receipt = self._usage_event_payload(
            usage=usage,
            model=model,
            metering=metering,
            service_tier=service_tier,
        )
        parsed = reservation_from_payload(reservation)
        if receipt is not None and parsed is not None and parsed.version == 2:
            receipt.update(
                {
                    "candidate_id": parsed.candidate_id,
                    "provider": parsed.provider,
                    "request_sha256": parsed.request_sha256,
                }
            )
        if not mark_provider_usage_succeeded(
            reservation,
            deferred_usage_event=receipt,
            provider=provider,
            provider_request_id=provider_request_id,
        ):
            # The pre-call attempt marker remains the fail-closed recovery
            # state if this post-provider receipt cannot be persisted.
            logger.error(
                "metered_anthropic: provider succeeded but durable usage "
                "receipt could not be written (model=%s)",
                model,
            )

    def _record_from_usage(
        self,
        *,
        usage: Any,
        model: str,
        metering: dict[str, Any],
    ) -> Optional[UsageEvent]:
        """Pull token counts off ``response.usage`` and write a usage_event.

        Never raises — metering errors are logged but never propagate to
        the caller. A scoring run that succeeded but failed to write its
        meter event is still useful; raising here would be worse.

        Returns the written event (so the call_log can FK to it) or None
        when the org context was missing.
        """
        payload = self._usage_event_payload(
            usage=usage,
            model=model,
            metering=metering,
        )
        if payload is None:
            logger.warning(
                "metered_anthropic: skipping record — no usage/organization_id "
                "(client built without org context). Pass metering={'organization_id': ...} "
                "for admin/shared-key flows that should still be billed."
            )
            return None
        return self._write_event(
            **payload,
            credit_reservation=metering.get("credit_reservation"),
        )

    def _write_event(
        self,
        *,
        organization_id: int,
        feature: Feature | str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_creation_tokens: int,
        cache_creation_1h_tokens: Optional[int],
        cache_hit: bool,
        user_id: Optional[int],
        role_id: Optional[int],
        entity_id: Optional[str],
        metadata: Optional[dict],
        service_tier: str = "standard",
        credit_reservation: Optional[dict] = None,
    ) -> Optional[UsageEvent]:
        """Write a usage_event row in a fresh, independently-committed
        session and return it with a populated id. Always swallows errors
        — metering must never break a Claude call.

        The fresh session is committed here, *before* the caller writes
        the FK-linked claude_call_log row, so that row's
        ``usage_event_id`` references a visible, committed parent. Joining
        the caller's still-open transaction (the old ``metering["db"]``
        path) left the usage_event invisible to call_log's separate
        session and raised a FK violation that silently dropped the row.
        """
        try:
            with SessionLocal() as fresh:
                event = record_event(
                    fresh,
                    organization_id=organization_id,
                    feature=feature,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_creation_tokens=cache_creation_tokens,
                    cache_creation_1h_tokens=cache_creation_1h_tokens,
                    cache_hit=cache_hit,
                    service_tier=service_tier,
                    user_id=user_id,
                    role_id=role_id,
                    entity_id=entity_id,
                    metadata=metadata,
                    credit_reservation=credit_reservation,
                )
                fresh.commit()
                # Pull the id while the session is still open; the
                # returned ORM object will be detached after the
                # ``with`` block exits, but the int id stays valid for
                # the call_log FK.
                fresh.refresh(event)
                return event
        except Exception as exc:
            # Defensive: a metering write must never propagate to the
            # caller. Surfacing here would mean a successful Claude call
            # gets reported as a failure — far worse than missing one row.
            logger.error(
                "metered_anthropic: failed to record usage_event "
                "(org=%s feature=%s model=%s error_type=%s)",
                organization_id,
                feature,
                model,
                type(exc).__name__,
            )
            return None


class MeteredAnthropicClient:
    """Anthropic facade that preserves admission, metering, and settlement."""
    def __init__(self, *, inner: Anthropic, organization_id: Optional[int]):
        retry_policy.require_sdk_retries_disabled(inner, provider="Anthropic")
        self._inner = inner
        self._organization_id = organization_id
        self._messages = _MeteredMessages(
            inner=inner.messages,
            organization_id=organization_id,
        )

    @property
    def messages(self) -> _MeteredMessages:
        return self._messages

    @property
    def organization_id(self) -> Optional[int]:
        return self._organization_id

    def with_options(self, *, timeout: float, max_retries: int = 0):
        """Return a metered client with one deadline-bounded SDK attempt."""
        from .metered_anthropic_options import rewrap_with_bounded_options

        return rewrap_with_bounded_options(
            self, timeout=timeout, max_retries=max_retries
        )

    @property
    def models(self) -> NonbillableAnthropicResource:
        """Narrow facade for provider model metadata/health operations."""

        return NonbillableAnthropicResource(
            inner=self._inner.models,
            allowed_operations=NONBILLABLE_MODEL_OPERATIONS,
        )

    @property
    def inner(self) -> Anthropic:
        raise UnsupportedAnthropicSurfaceError(
            "The bare Anthropic client is unavailable because it bypasses metering"
        )

    def close(self) -> Any:
        """Close transport resources without exposing the provider client."""

        return self._inner.close()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        raise UnsupportedAnthropicSurfaceError(
            "Anthropic SDK surface is unavailable until metering is implemented"
        )


@contextmanager
def temporary_metering_override(
    *,
    client: MeteredAnthropicClient,
    organization_id: int,
) -> Iterator[MeteredAnthropicClient]:
    """Yield a transient metered client bound to a different organization."""
    overridden = MeteredAnthropicClient(
        inner=client._inner,
        organization_id=organization_id,
    )
    yield overridden
