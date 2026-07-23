"""Metering wrapper around the Anthropic SDK client.

Every Claude call site in the platform should go through a wrapped client
returned by ``claude_client_resolver`` so that ``usage_events`` rows are
written for every billable call. Without this wrapper, attribution is
per-call-site and easy to forget — historically only 2 of 14 sites
self-reported, leaving ~80% of spend invisible to the settings → usage
tab.

Usage::

    from ..services.claude_client_resolver import get_client_for_org
    from ..services.pricing_service import Feature

    client = get_client_for_org(org)
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=4096,
        messages=[...],
        metering={
            "feature": Feature.SCORE,
            "user_id": user.id,
            "role_id": role.id,
            "entity_id": str(application.id),
            "db": db,                   # optional — see below
            "metadata": {"trace_id": trace_id},
        },
    )

The ``metering`` kwarg is consumed by the wrapper and **stripped before
the call reaches Anthropic**. It is the only thing the wrapper adds on
top of the underlying SDK; everything else passes through unchanged.

DB session policy
-----------------

The wrapper always writes its rows in fresh, independently-committed
``SessionLocal()`` sessions — first the usage_event, then the FK-linked
claude_call_log row. It deliberately does NOT join the caller's open
transaction: doing so left the usage_event uncommitted and invisible to
the (separate) call_log session, which raised a FK violation and
silently dropped every call_log row for the score + pre-screen paths.
Independent commit is also the right meter semantic — a call we actually
made and paid for must be recorded even if the caller later rolls back.
A ``metering["db"]`` key, if present, is ignored.

Default-feature policy
----------------------

If ``metering`` is missing entirely, the wrapper records the call as
``Feature.OTHER`` and logs a warning naming the model. Forgotten
attribution still shows up in the dashboard rather than vanishing — but
under "Other / unattributed" so it's visibly wrong.

Streaming
---------

``messages.stream()`` returns a context manager. The wrapper proxies the
context manager and reads ``stream.get_final_message().usage`` after the
``with`` block exits. Callers iterate the stream exactly like before.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from anthropic import Anthropic

from ..components.ai_routing.pricing import (
    RoutedPricing,
    RoutedPricingContractError,
    RoutedPricingOutcomeError,
    resolve_routed_pricing,
    resolve_routed_pricing_receipt,
)
from ..models.claude_call_log import ClaudeCallLog
from ..models.usage_event import UsageEvent
from ..platform.database import SessionLocal
from .pricing_service import Feature, raw_cost_usd_micro
from .provider_usage_admission import (
    mark_provider_attempt_started,
    mark_provider_usage_succeeded,
    release_provider_usage,
    release_provider_usage_if_definitely_nonbillable,
    reserve_provider_usage,
)
from .usage_credit_reservations import (
    release_credit_reservation,
    reservation_from_payload,
)
from .usage_metering_service import record_event

logger = logging.getLogger("taali.metered_anthropic")


def _extract_cache_creation_1h(usage: Any) -> Optional[int]:
    """Pull the 1-hour cache_creation token count off ``response.usage``.

    Anthropic exposes the breakdown at
    ``usage.cache_creation.ephemeral_1h_input_tokens`` (and
    ``ephemeral_5m_input_tokens``). When the field is absent (older SDK,
    no cache_creation on this call, etc.) we return None so pricing
    falls back to the conservative 1.25×-on-total default — matches
    pre-#387 behaviour.
    """
    if usage is None:
        return None
    cache_creation = getattr(usage, "cache_creation", None)
    if cache_creation is None:
        return None
    val = getattr(cache_creation, "ephemeral_1h_input_tokens", None)
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _safe_call_log_usage_int(usage: Any, field: str) -> int:
    """Return exact non-negative provider evidence, or a non-billable zero.

    Receipt-error rows are intentionally excluded from settlement. Their call
    log must still survive malformed SDK fields so operators retain the trace
    and provider request ID that explain the protected hold.
    """

    if usage is None:
        return 0
    value = getattr(usage, field, 0)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


# Sentinel returned by ``MeteredMessages._extract_metering`` when a caller
# explicitly opts out (``metering={"skip": True}``). We still strip the
# kwarg from the SDK call but skip recording. Used by tests and by the
# rare cases where the same call is metered upstream.
_SKIP = object()


class MeteringRequiredError(ValueError):
    """Raised when a caller passes ``metering`` without a ``feature`` key.

    A caller intentionally tagging the call must name its feature; an
    accidentally-missing ``metering`` falls back to ``Feature.OTHER`` with
    a warning, but a *partial* metering dict is almost certainly a bug.
    """


class ProviderAttemptMarkerError(RuntimeError):
    """Raised before the SDK when a paid attempt cannot be durably marked."""

    provider_not_called = True


class _MeteredMessages:
    """Wraps ``Anthropic.messages`` to record a ``usage_event`` per call.

    Holds a reference to the org_id captured at client construction so
    callers don't have to repeat it. Each call may pass its own
    ``user_id`` / ``role_id`` / ``entity_id`` for finer attribution.
    """

    def __init__(self, *, inner: Any, organization_id: Optional[int]):
        self._inner = inner
        self._organization_id = organization_id

    # ``messages.batches`` is intercepted (see ``batches`` property below)
    # so batch spend is metered like everything else. Any other nested
    # resource passes through unwrapped.
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    @property
    def batches(self) -> "_MeteredBatches":
        return _MeteredBatches(messages=self)

    # ----- public API -----------------------------------------------------

    def _retry_context(self, metering: Any) -> tuple[int, Optional[int], Optional[str]]:
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
        if parent is None and attempt > 0:
            parent = self._routed_retry_parent(metering)
        return (attempt, parent, trace)

    @staticmethod
    def _routed_retry_parent(metering: dict[str, Any]) -> Optional[int]:
        """Link an adapter-owned retry to the preceding physical call log.

        Routed callers cannot know the independently committed call-log ID when
        they render the next attempt. The immutable invocation/ordinal pair is
        enough to recover that link without trusting feature code.
        """

        metadata = metering.get("metadata")
        route = metadata.get("ai_routing") if isinstance(metadata, dict) else None
        if not isinstance(route, dict):
            return None
        invocation_id = str(route.get("invocation_id") or "").strip()
        try:
            ordinal = int(route.get("attempt_ordinal"))
        except (TypeError, ValueError):
            return None
        if not invocation_id or ordinal <= 1:
            return None
        previous_trace = f"ai-route:{invocation_id}:{ordinal - 1}"
        try:
            with SessionLocal() as session:
                row = (
                    session.query(ClaudeCallLog.id)
                    .filter(ClaudeCallLog.trace_id == previous_trace)
                    .order_by(ClaudeCallLog.id.desc())
                    .first()
                )
                return int(row[0]) if row is not None else None
        except Exception:
            logger.exception(
                "metered_anthropic: could not link routed retry parent trace=%s",
                previous_trace,
            )
            return None

    def create(self, **kwargs: Any) -> Any:
        metering = self._extract_metering(kwargs)
        model = str(kwargs.get("model") or "")
        routed_pricing = resolve_routed_pricing(
            metering,
            model=model,
            inference_geo=kwargs.get("inference_geo"),
        )
        self._reject_routed_service_tier_override(routed_pricing, kwargs)
        self._ensure_provider_reservation(metering)
        feature_hint = self._feature_hint_from(metering)
        retry_attempt, parent_call_log_id, trace_id = self._retry_context(metering)
        try:
            response = self._inner.create(**kwargs)
        except Exception as exc:
            # A client exception is not proof of zero spend: a timeout can
            # arrive after Anthropic accepted/billed the request. Release only
            # an explicit allowlisted rejection; otherwise retain the attempt
            # hold and log the reconciliation gap. Never suppress the error.
            error_class, http_status = self._classify_exception(exc)
            reservation_payload = (
                metering.get("credit_reservation")
                if isinstance(metering, dict)
                else None
            )
            released = release_provider_usage_if_definitely_nonbillable(
                reservation_payload,
                error=exc,
                reason=f"sdk_error:{error_class or 'other'}",
            )
            self._record_call_log_safe(
                organization_id=self._call_org_id(metering),
                model=model,
                usage=None,
                feature_hint=feature_hint,
                status=(
                    "sdk_ambiguous_error"
                    if reservation_payload and not released
                    else "sdk_error"
                ),
                error_reason=str(exc)[:500],
                anthropic_request_id=self._extract_error_request_id(exc),
                error_class=error_class,
                http_status=http_status,
                retry_attempt=retry_attempt,
                parent_call_log_id=parent_call_log_id,
                trace_id=trace_id,
            )
            raise

        usage = getattr(response, "usage", None)
        request_id = self._extract_request_id(response)
        try:
            routed_receipt = (
                resolve_routed_pricing_receipt(
                    metering,
                    routed_pricing=routed_pricing,
                    response=response,
                )
                if routed_pricing is not None
                else None
            )
            provider_cost_usd_micro = (
                routed_receipt.pricing.cost_usd_micro(usage)
                if routed_receipt is not None and usage is not None
                else None
            )
        except Exception as exc:
            if routed_pricing is None:
                raise
            self._record_routed_receipt_error(
                metering=metering,
                requested_model=model,
                response=response,
                usage=usage,
                feature_hint=feature_hint,
                request_id=request_id,
                retry_attempt=retry_attempt,
                parent_call_log_id=parent_call_log_id,
                trace_id=trace_id,
                error=exc,
            )
            raise
        if routed_receipt is not None:
            model = routed_receipt.model_id
        usage_event: Optional[UsageEvent] = None

        if metering is not _SKIP:
            self._mark_provider_success(
                usage=usage,
                model=model,
                metering=metering,
                provider_request_id=request_id,
                provider_cost_usd_micro=provider_cost_usd_micro,
            )
            usage_event = self._record_from_usage(
                usage=usage,
                model=model,
                metering=metering,
                provider_cost_usd_micro=provider_cost_usd_micro,
            )

        # Unconditional call_log write — the structural guarantee that
        # every Claude call lands a row, even when the application-layer
        # metering opted out (skip=True) or fell through to its own
        # ``record_event`` path. ``usage_event_id`` is NULL when no
        # UsageEvent was attached; that's the "metering attribution gap"
        # signal we now surface.
        self._record_call_log_safe(
            organization_id=self._call_org_id(metering),
            model=model,
            usage=usage,
            feature_hint=feature_hint,
            status=(
                (
                    "routed_contract_mismatch"
                    if usage is not None
                    else "routed_contract_mismatch_no_usage"
                )
                if routed_receipt is not None and routed_receipt.contract_mismatch
                else (
                    "metering_error_completed"
                    if (
                        usage is not None
                        and isinstance(metering, dict)
                        and metering.get("credit_reservation")
                        and usage_event is None
                    )
                    else "ok" if usage is not None else "no_usage_on_response"
                )
            ),
            error_reason=None,
            anthropic_request_id=request_id,
            usage_event_id=int(usage_event.id) if usage_event is not None else None,
            retry_attempt=retry_attempt,
            parent_call_log_id=parent_call_log_id,
            trace_id=trace_id,
            provider_cost_usd_micro=provider_cost_usd_micro,
        )
        if routed_receipt is not None and routed_receipt.contract_mismatch:
            raise RoutedPricingOutcomeError(
                "billed Anthropic response model or region differs from its route"
            )
        return response

    def _record_routed_receipt_error(
        self,
        *,
        metering: dict[str, Any],
        requested_model: str,
        response: Any,
        usage: Any,
        feature_hint: Optional[str],
        request_id: Optional[str],
        retry_attempt: int,
        parent_call_log_id: Optional[int],
        trace_id: Optional[str],
        error: BaseException,
    ) -> None:
        """Persist post-provider, price-unknown evidence before propagating.

        Once transport returned, a malformed or unpriceable receipt is never a
        pre-call contract failure. The hold remains protected as known-billable
        with unknown usage cost, and the traced call log gives reconciliation a
        durable incident record without inventing a fallback price.
        """

        actual_model = str(getattr(response, "model", None) or requested_model)
        self._mark_provider_success(
            usage=None,
            model=actual_model,
            metering=metering,
            provider_request_id=request_id,
        )
        self._record_call_log_safe(
            organization_id=self._call_org_id(metering),
            model=actual_model,
            usage=usage,
            feature_hint=feature_hint,
            status="routed_pricing_receipt_error",
            error_reason=str(error)[:500],
            anthropic_request_id=request_id,
            error_class="routed_pricing_receipt",
            retry_attempt=retry_attempt,
            parent_call_log_id=parent_call_log_id,
            trace_id=trace_id,
            cost_unknown=True,
        )

    def stream(self, **kwargs: Any):
        metering = self._extract_metering(kwargs)
        if metering is _SKIP:
            return self._inner.stream(**kwargs)
        model = str(kwargs.get("model") or "")
        routed_pricing = resolve_routed_pricing(
            metering,
            model=model,
            inference_geo=kwargs.get("inference_geo"),
        )
        self._reject_routed_service_tier_override(routed_pricing, kwargs)
        # Anthropic does not start the paid request until the returned context
        # manager is entered. Defer both the hold and attempt marker until
        # ``__enter__`` so constructing-but-never-using a stream cannot strand
        # conservative provider capacity.
        inner_cm = self._inner.stream(**kwargs)
        return _MeteredStreamCtx(
            inner=inner_cm,
            messages=self,
            model=model,
            metering=metering,
            routed_pricing=routed_pricing,
        )

    # Async surface — kept thin and currently unused. Added so anyone
    # reaching for ``AsyncAnthropic`` later doesn't silently bypass the
    # meter. Mirror the sync ``create`` exactly.
    async def acreate(self, **kwargs: Any) -> Any:  # pragma: no cover
        metering = self._extract_metering(kwargs)
        if metering is _SKIP:
            return await self._inner.create(**kwargs)
        model = str(kwargs.get("model") or "")
        routed_pricing = resolve_routed_pricing(
            metering,
            model=model,
            inference_geo=kwargs.get("inference_geo"),
        )
        self._reject_routed_service_tier_override(routed_pricing, kwargs)
        self._ensure_provider_reservation(metering)
        feature_hint = self._feature_hint_from(metering)
        retry_attempt, parent_call_log_id, trace_id = self._retry_context(metering)
        try:
            response = await self._inner.create(**kwargs)
        except Exception as exc:
            reservation_payload = metering.get("credit_reservation")
            released = release_provider_usage_if_definitely_nonbillable(
                reservation_payload,
                error=exc,
                reason="async_sdk_error",
            )
            error_class, http_status = self._classify_exception(exc)
            self._record_call_log_safe(
                organization_id=self._call_org_id(metering),
                model=model,
                usage=None,
                feature_hint=feature_hint,
                status=(
                    "sdk_ambiguous_error"
                    if reservation_payload and not released
                    else "sdk_error"
                ),
                error_reason=str(exc)[:500],
                anthropic_request_id=self._extract_error_request_id(exc),
                error_class=error_class,
                http_status=http_status,
                retry_attempt=retry_attempt,
                parent_call_log_id=parent_call_log_id,
                trace_id=trace_id,
            )
            raise
        usage = getattr(response, "usage", None)
        request_id = self._extract_request_id(response)
        try:
            routed_receipt = (
                resolve_routed_pricing_receipt(
                    metering,
                    routed_pricing=routed_pricing,
                    response=response,
                )
                if routed_pricing is not None
                else None
            )
            provider_cost_usd_micro = (
                routed_receipt.pricing.cost_usd_micro(usage)
                if routed_receipt is not None and usage is not None
                else None
            )
        except Exception as exc:
            if routed_pricing is None:
                raise
            self._record_routed_receipt_error(
                metering=metering,
                requested_model=model,
                response=response,
                usage=usage,
                feature_hint=feature_hint,
                request_id=request_id,
                retry_attempt=retry_attempt,
                parent_call_log_id=parent_call_log_id,
                trace_id=trace_id,
                error=exc,
            )
            raise
        if routed_receipt is not None:
            model = routed_receipt.model_id
        self._mark_provider_success(
            usage=usage,
            model=model,
            metering=metering,
            provider_request_id=request_id,
            provider_cost_usd_micro=provider_cost_usd_micro,
        )
        usage_event = self._record_from_usage(
            usage=usage,
            model=model,
            metering=metering,
            provider_cost_usd_micro=provider_cost_usd_micro,
        )
        self._record_call_log_safe(
            organization_id=self._call_org_id(metering),
            model=model,
            usage=usage,
            feature_hint=feature_hint,
            status=(
                (
                    "routed_contract_mismatch"
                    if usage is not None
                    else "routed_contract_mismatch_no_usage"
                )
                if routed_receipt is not None and routed_receipt.contract_mismatch
                else (
                    "metering_error_completed"
                    if usage is not None
                    and metering.get("credit_reservation")
                    and usage_event is None
                    else "ok" if usage is not None else "no_usage_on_response"
                )
            ),
            error_reason=None,
            anthropic_request_id=request_id,
            usage_event_id=int(usage_event.id) if usage_event is not None else None,
            retry_attempt=retry_attempt,
            parent_call_log_id=parent_call_log_id,
            trace_id=trace_id,
            provider_cost_usd_micro=provider_cost_usd_micro,
        )
        if routed_receipt is not None and routed_receipt.contract_mismatch:
            raise RoutedPricingOutcomeError(
                "billed Anthropic response model or region differs from its route"
            )
        return response

    # ----- internals ------------------------------------------------------

    def _extract_metering(self, kwargs: dict[str, Any]):
        """Pop the metering kwarg from ``kwargs`` and normalise it.

        Returns one of:
        - ``dict`` with a resolved ``feature`` key + optional db/user_id/etc
        - ``_SKIP`` sentinel when ``{"skip": True}`` is set
        """
        meter = kwargs.pop("metering", None)
        if meter is None:
            # No metering specified → record as Feature.OTHER with a warning
            # so attribution is *visible* but flagged. Better than dropping.
            logger.warning(
                "metered_anthropic: call to %s did not pass `metering=` — "
                'falling back to Feature.OTHER. Add `metering={"feature": Feature.X, ...}` '
                "to attribute spend correctly.",
                kwargs.get("model") or "<unknown-model>",
            )
            return {"feature": Feature.OTHER}

        if not isinstance(meter, dict):
            raise TypeError(f"`metering` must be a dict, got {type(meter).__name__}")

        if meter.get("skip"):
            return _SKIP

        feature = meter.get("feature")
        if feature is None:
            raise MeteringRequiredError(
                "metering={...} must include a `feature` key (use "
                "Feature.OTHER for unclassified calls)"
            )

        return meter

    def _ensure_provider_reservation(self, metering: Any) -> None:
        """Install the universal hard-admission fallback for a paid call.

        Feature services can reserve explicitly when they need a custom amount
        or a durable reservation that crosses process boundaries.  This guard
        covers every other role-attributed SDK attempt, including validation
        retries and multi-call scoring pipelines.  A missing org/role context
        is deliberately not guessed; those calls remain visible as attribution
        gaps in ``claude_call_log`` and cannot be charged to an arbitrary job.
        """

        if not isinstance(metering, dict):
            return
        reservation_payload = metering.get("credit_reservation")
        if not reservation_payload:
            organization_id = self._call_org_id(metering)
            role_id = metering.get("role_id")
            if organization_id is None or role_id is None:
                return

            trace_id = str(metering.get("trace_id") or uuid.uuid4().hex)
            metering["trace_id"] = trace_id
            reservation = reserve_provider_usage(
                organization_id=int(organization_id),
                role_id=int(role_id),
                feature=metering["feature"],
                trace_id=trace_id,
                entity_id=(
                    str(metering["entity_id"])
                    if metering.get("entity_id") is not None
                    else None
                ),
                metadata={
                    **dict(metering.get("metadata") or {}),
                    "admission_source": "metered_anthropic_fallback",
                },
                require_role_authority=bool(
                    metering.get("require_role_authority", False)
                ),
            )
            reservation_payload = reservation.as_metering_payload()
            metering["credit_reservation"] = reservation_payload
        elif reservation_from_payload(reservation_payload) is None:
            raise ProviderAttemptMarkerError(
                "invalid provider credit reservation payload"
            )

        attempt_ref = self._routed_attempt_ref(metering)
        if not mark_provider_attempt_started(
            reservation_payload,
            provider="anthropic",
            attempt_ref=attempt_ref,
        ):
            release_provider_usage(
                reservation_payload,
                reason="anthropic_attempt_marker_failed",
            )
            raise ProviderAttemptMarkerError(
                "could not durably mark Anthropic provider attempt"
            )

    @staticmethod
    def _routed_attempt_ref(metering: dict[str, Any]) -> Optional[str]:
        """Return the adapter's exact marker identity for idempotent re-marking."""

        metadata = metering.get("metadata")
        route = metadata.get("ai_routing") if isinstance(metadata, dict) else None
        if not isinstance(route, dict):
            return None
        invocation_id = str(route.get("invocation_id") or "").strip()
        try:
            ordinal = int(route.get("attempt_ordinal"))
        except (TypeError, ValueError) as exc:
            raise ProviderAttemptMarkerError(
                "routed metering requires an integer attempt ordinal"
            ) from exc
        if not invocation_id or ordinal <= 0:
            raise ProviderAttemptMarkerError(
                "routed metering requires an invocation and positive attempt ordinal"
            )
        return f"{invocation_id}:{ordinal}"

    @staticmethod
    def _reject_routed_service_tier_override(
        routed_pricing: RoutedPricing | None,
        kwargs: dict[str, Any],
    ) -> None:
        if routed_pricing is not None and kwargs.get("service_tier") is not None:
            raise RoutedPricingContractError(
                "routed Anthropic Messages service tier is control-plane-owned"
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
        except Exception:
            # Keep the hold rather than risk a double refund. The ledger row is
            # traceable and can be recovered; the provider call itself failed.
            logger.exception(
                "metered_anthropic: failed to release provider-error reservation"
            )

    def _feature_hint_from(self, metering) -> Optional[str]:
        """Get the caller's intended feature label for the call_log row.

        ``metering`` may be:
        - a dict with ``feature`` key → record the string value
        - ``_SKIP`` sentinel → record the ``metered_by`` hint if present
        - None / no feature → NULL (surfaces as an attribution-gap signal)
        """
        if metering is _SKIP:
            # _SKIP comes from ``{"skip": True, "metered_by": "..."}`` — we
            # lost the ``metered_by`` hint when we collapsed to the sentinel.
            # Best effort: record "skip" so analytics can group these.
            return "skip"
        if isinstance(metering, dict):
            f = metering.get("feature")
            if isinstance(f, Feature):
                return f.value
            if f is not None:
                return str(f)
        return None

    def _call_org_id(self, metering) -> Optional[int]:
        """Effective organization_id for this call. ``metering.organization_id``
        overrides the client-bound org (for admin/shared flows that thread
        the customer's org context per-call)."""
        if isinstance(metering, dict):
            override = metering.get("organization_id")
            if override is not None:
                return int(override)
        return self._organization_id

    @staticmethod
    def _extract_request_id(response: Any) -> Optional[str]:
        """Pull Anthropic's request_id from the response for cross-ref with
        the Console Logs page during incident response. Best effort — the
        SDK has put it in different places across versions."""
        # Prefer the HTTP request identity when the SDK exposes it; fall back
        # to the message ID for older response/stream objects.
        for path in ("_request_id", "id"):
            val = getattr(response, path, None)
            if val:
                return str(val)
        return None

    @staticmethod
    def _extract_error_request_id(error: Any) -> Optional[str]:
        """Best-effort provider request identity from an SDK exception."""

        for source in (error, getattr(error, "response", None)):
            if source is None:
                continue
            for attribute in ("request_id", "_request_id", "id"):
                value = getattr(source, attribute, None)
                if value:
                    return str(value)
        return None

    @staticmethod
    def _classify_exception(exc: BaseException) -> tuple[Optional[str], Optional[int]]:
        """B1: bucket SDK exceptions into a small set of machine-readable
        categories.

        Returns ``(error_class, http_status)``. ``error_class`` ∈
          {rate_limit, overloaded, context_length, credit_exhausted,
           bad_request, server_error, timeout, network, validation, other}
        ``http_status`` is the numeric code when the SDK exposes one,
        else None. Used by dashboards to distinguish "Anthropic is
        slow / rate-limiting us" from "we sent garbage" without
        scraping error_reason text.

        ``credit_exhausted`` is broken out separately because real
        production data (2026-05-20 through 2026-05-21) showed it as
        the dominant failure mode — 122 of 172 failed agent_runs hit
        "Your credit balance is too low to access the Anthropic API".
        Switching models doesn't help (Haiku 400s the same way); the
        only fix is to detect and stop firing wasted calls until the
        org's Anthropic balance is topped up.

        Pure dispatch — no imports of anthropic at module load (so
        tests that stub the SDK don't need the real package).
        """
        try:
            import anthropic  # type: ignore[import-not-found]
        except Exception:
            return (None, None)
        status_code: Optional[int] = None
        for attr in ("status_code", "http_status", "code"):
            value = getattr(exc, attr, None)
            if isinstance(value, int):
                status_code = value
                break
        if isinstance(exc, getattr(anthropic, "RateLimitError", ())):
            return ("rate_limit", status_code or 429)
        if isinstance(exc, getattr(anthropic, "APITimeoutError", ())):
            return ("timeout", status_code)
        if isinstance(exc, getattr(anthropic, "APIConnectionError", ())):
            return ("network", status_code)
        if isinstance(exc, getattr(anthropic, "InternalServerError", ())):
            return ("server_error", status_code or 500)
        if isinstance(exc, getattr(anthropic, "BadRequestError", ())):
            message = str(exc).lower()
            # Anthropic returns 400 with this exact wording when the
            # org's Anthropic billing balance is exhausted. Detect it
            # specifically so the orchestrator can short-circuit
            # instead of letting cohort ticks keep producing failed
            # agent_runs indefinitely.
            if "credit balance is too low" in message:
                return ("credit_exhausted", status_code or 400)
            if "context" in message and ("length" in message or "window" in message):
                return ("context_length", status_code or 400)
            return ("bad_request", status_code or 400)
        if isinstance(exc, getattr(anthropic, "APIStatusError", ())):
            if status_code == 529:
                return ("overloaded", 529)
            if status_code and status_code >= 500:
                return ("server_error", status_code)
            if status_code and status_code >= 400:
                return ("bad_request", status_code)
        # Last-resort string match — non-anthropic exception wrappers
        # (e.g. tests that raise generic RuntimeError) can still carry
        # the credit-balance message; we want the dashboard to count
        # them correctly.
        if "credit balance is too low" in str(exc).lower():
            return ("credit_exhausted", 400)
        return ("other", status_code)

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
        provider_cost_usd_micro: Optional[int] = None,
        cost_unknown: bool = False,
    ) -> bool:
        """Write one ``ClaudeCallLog`` row. Never raises — call_log failures
        must not break Claude calls. Logs at WARNING so ops sees them.
        Returns True when the row committed, False when the failure was
        swallowed (the batch path uses this to skip its idempotency latch
        so a failed write is retried on the next results() call).

        Unconditional by design. This is the structural guarantee that
        every call lands a row, regardless of whether the application
        layer's metering succeeded.
        """
        input_tokens = _safe_call_log_usage_int(usage, "input_tokens")
        output_tokens = _safe_call_log_usage_int(usage, "output_tokens")
        cache_read_tokens = _safe_call_log_usage_int(usage, "cache_read_input_tokens")
        cache_creation_tokens = _safe_call_log_usage_int(
            usage, "cache_creation_input_tokens"
        )
        # Anthropic returns the 5m/1h split nested under
        # ``usage.cache_creation`` (CacheCreation object). We persist
        # the 1h slice separately so pricing can apply the 2.00× rate
        # to it (vs 1.25× for 5m). The legacy combined
        # ``cache_creation_tokens`` stays as the source of truth for
        # the total — pricing derives 5m = total - 1h.
        cache_creation_1h_tokens = _extract_cache_creation_1h(usage)
        if cost_unknown:
            # The provider returned, but the routed registry could not price
            # its receipt exactly. Zero is a sentinel on this non-billable
            # evidence status, not an invented cost used for settlement.
            cost_micro = 0
        elif provider_cost_usd_micro is not None:
            cost_micro = max(0, int(provider_cost_usd_micro))
        else:
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

        row = ClaudeCallLog(
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
        try:
            with SessionLocal() as session:
                session.add(row)
                session.commit()
            return True
        except Exception:
            logger.exception(
                "metered_anthropic: claude_call_log write failed (model=%s, "
                "status=%s) — Claude call already succeeded so we don't raise, "
                "but reconciliation against Anthropic billing will undercount.",
                model,
                status,
            )
            return False

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
        provider_cost_usd_micro: Optional[int] = None,
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
        return {
            "organization_id": int(org_id),
            "feature": feature_value,
            "model": str(model),
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
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
            "provider_cost_usd_micro": provider_cost_usd_micro,
        }

    def _mark_provider_success(
        self,
        *,
        usage: Any,
        model: str,
        metering: dict[str, Any],
        provider_request_id: Optional[str],
        service_tier: str = "standard",
        provider_cost_usd_micro: Optional[int] = None,
    ) -> None:
        reservation = metering.get("credit_reservation")
        if not reservation:
            return
        receipt = self._usage_event_payload(
            usage=usage,
            model=model,
            metering=metering,
            service_tier=service_tier,
            provider_cost_usd_micro=provider_cost_usd_micro,
        )
        if not mark_provider_usage_succeeded(
            reservation,
            deferred_usage_event=receipt,
            provider="anthropic",
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
        provider_cost_usd_micro: Optional[int] = None,
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
            provider_cost_usd_micro=provider_cost_usd_micro,
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
        provider_cost_usd_micro: Optional[int] = None,
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
                    provider_cost_usd_micro=provider_cost_usd_micro,
                    credit_reservation=credit_reservation,
                )
                fresh.commit()
                # Pull the id while the session is still open; the
                # returned ORM object will be detached after the
                # ``with`` block exits, but the int id stays valid for
                # the call_log FK.
                fresh.refresh(event)
                return event
        except Exception:
            # Defensive: a metering write must never propagate to the
            # caller. Surfacing here would mean a successful Claude call
            # gets reported as a failure — far worse than missing one row.
            logger.exception(
                "metered_anthropic: failed to record usage_event "
                "(org=%s feature=%s model=%s)",
                organization_id,
                feature,
                model,
            )
            return None


class _MeteredStreamCtx:
    """Wraps the Anthropic ``messages.stream`` context manager so token
    usage from ``stream.get_final_message().usage`` is recorded after the
    block exits. The yielded stream object is the underlying SDK stream;
    callers iterate it exactly as before."""

    def __init__(
        self,
        *,
        inner,
        messages: _MeteredMessages,
        model: str,
        metering: dict[str, Any],
        routed_pricing: RoutedPricing | None,
    ):
        self._inner = inner
        self._messages = messages
        self._model = model
        self._metering = metering
        self._routed_pricing = routed_pricing
        self._stream = None

    def __enter__(self):
        self._messages._ensure_provider_reservation(self._metering)
        try:
            self._stream = self._inner.__enter__()
        except Exception as exc:
            reservation = self._metering.get("credit_reservation")
            released = release_provider_usage_if_definitely_nonbillable(
                reservation,
                error=exc,
                reason="stream_enter_error",
            )
            error_class, http_status = self._messages._classify_exception(exc)
            retry_attempt, parent_call_log_id, trace_id = self._messages._retry_context(
                self._metering
            )
            self._messages._record_call_log_safe(
                organization_id=self._call_org_id(self._metering),
                model=self._model,
                usage=None,
                feature_hint=self._messages._feature_hint_from(self._metering),
                status=(
                    "sdk_ambiguous_error"
                    if reservation and not released
                    else "sdk_error"
                ),
                error_reason=str(exc)[:500],
                anthropic_request_id=self._messages._extract_error_request_id(exc),
                error_class=error_class,
                http_status=http_status,
                retry_attempt=retry_attempt,
                parent_call_log_id=parent_call_log_id,
                trace_id=trace_id,
            )
            raise
        return self._stream

    def __exit__(self, exc_type, exc, tb):
        # Snapshot final usage *before* closing the stream — the SDK
        # exposes it on the live stream object and on the final message.
        usage = None
        final_message = None
        final_message_error: BaseException | None = None
        if self._stream is not None:
            try:
                final_message = self._stream.get_final_message()
                usage = getattr(final_message, "usage", None)
            except BaseException as final_exc:
                final_message_error = final_exc
                logger.debug(
                    "metered_anthropic: get_final_message() failed; "
                    "skipping meter for this stream",
                    exc_info=True,
                )
        inner_exit_error: BaseException | None = None
        try:
            result = self._inner.__exit__(exc_type, exc, tb)
        except BaseException as inner_exc:
            # Closing the SDK stream can itself fail after provider acceptance.
            # Persist whatever receipt evidence we captured before propagating.
            inner_exit_error = inner_exc
            result = False

        retry_attempt, parent_call_log_id, trace_id = self._messages._retry_context(
            self._metering
        )
        request_id = (
            self._messages._extract_request_id(final_message)
            if final_message is not None
            else self._messages._extract_error_request_id(
                inner_exit_error or exc or final_message_error
            )
        )
        routed_receipt = None
        receipt_error: BaseException | None = None
        provider_cost_usd_micro = None
        try:
            routed_receipt = (
                resolve_routed_pricing_receipt(
                    self._metering,
                    routed_pricing=self._routed_pricing,
                    response=final_message,
                )
                if self._routed_pricing is not None and final_message is not None
                else None
            )
            provider_cost_usd_micro = (
                routed_receipt.pricing.cost_usd_micro(usage)
                if routed_receipt is not None and usage is not None
                else None
            )
        except BaseException as pricing_exc:
            receipt_error = pricing_exc
        if routed_receipt is not None:
            self._model = routed_receipt.model_id

        interrupted = exc_type is not None or inner_exit_error is not None
        evidence_error = inner_exit_error or exc or final_message_error

        # A client disconnect / generator cancellation can interrupt a stream
        # after Anthropic has already billed tokens.  If the SDK exposes a
        # usage snapshot, meter it even on exceptional exit; otherwise those
        # are exactly the paid calls reconciliation can never attribute.
        self._messages._mark_provider_success(
            # An invalid routed receipt is known provider success, but its cost
            # is not safe to reconstruct until the receipt contract is fixed.
            usage=None if receipt_error is not None else usage,
            model=self._model,
            metering=self._metering,
            provider_request_id=request_id,
            provider_cost_usd_micro=provider_cost_usd_micro,
        )
        usage_event: Optional[UsageEvent] = None
        if usage is not None and receipt_error is None:
            try:
                usage_event = self._messages._record_from_usage(
                    usage=usage,
                    model=self._model,
                    metering=self._metering,
                    provider_cost_usd_micro=provider_cost_usd_micro,
                )
            except Exception:
                logger.exception("metered_anthropic: stream meter write failed")

        if receipt_error is not None:
            status = "routed_pricing_receipt_error"
            error_reason = str(receipt_error)[:500]
            error_class = "routed_pricing_receipt"
        elif routed_receipt is not None and routed_receipt.contract_mismatch:
            status = (
                "routed_contract_mismatch"
                if usage is not None
                else "routed_contract_mismatch_no_usage"
            )
            error_reason = "provider response violated the routed model/region contract"
            error_class = "routed_contract_mismatch"
        elif usage is None:
            status = "interrupted_no_usage" if interrupted else "no_usage_on_response"
            error_reason = (
                type(evidence_error).__name__ if evidence_error is not None else None
            )
            error_class = None
        elif self._metering.get("credit_reservation") and usage_event is None:
            status = (
                "metering_error_interrupted"
                if interrupted
                else "metering_error_completed"
            )
            error_reason = (
                type(evidence_error).__name__
                if evidence_error is not None
                else "usage_event_write_failed"
            )
            error_class = None
        else:
            status = "interrupted" if interrupted else "ok"
            error_reason = (
                type(evidence_error).__name__ if evidence_error is not None else None
            )
            error_class = None

        # Every entered stream writes evidence, including completed or
        # interrupted streams with no usage snapshot. Routed retry/trace
        # threading mirrors the non-streaming path exactly.
        self._messages._record_call_log_safe(
            organization_id=self._call_org_id(self._metering),
            model=self._model,
            usage=usage,
            feature_hint=self._messages._feature_hint_from(self._metering),
            status=status,
            error_reason=error_reason,
            anthropic_request_id=request_id,
            usage_event_id=int(usage_event.id) if usage_event is not None else None,
            error_class=error_class,
            retry_attempt=retry_attempt,
            parent_call_log_id=parent_call_log_id,
            trace_id=trace_id,
            provider_cost_usd_micro=provider_cost_usd_micro,
            cost_unknown=receipt_error is not None,
        )

        if usage is None:
            # A completed stream with no usage is outcome-ambiguous: Anthropic
            # may still have accepted/billed it. The pre-call marker (or the
            # success/usage-unknown marker above) deliberately retains the
            # hold. Only an explicit SDK initialization/enter error releases.
            logger.error(
                "metered_anthropic: stream ended without usage; retaining "
                "provider hold for reconciliation (model=%s error=%s)",
                self._model,
                getattr(exc_type, "__name__", None),
            )

        if inner_exit_error is not None:
            raise inner_exit_error
        if not interrupted and receipt_error is not None:
            raise receipt_error
        if (
            not interrupted
            and routed_receipt is not None
            and routed_receipt.contract_mismatch
        ):
            raise RoutedPricingOutcomeError(
                "billed Anthropic response model or region differs from its route"
            )
        return result

    def _call_org_id(self, metering: dict[str, Any]) -> Optional[int]:
        # Delegate to the messages helper so org-resolution stays in
        # one place (handles both client-bound and per-call org_id).
        return self._messages._call_org_id(metering)


class _MeteredBatches:
    """Wraps ``messages.batches`` so Message Batches API spend is metered.

    A batch splits one logical operation across processes and time: the
    submitter knows the attribution but has no usage; the poller sees the
    usage but (natively) no attribution. The bridge is an
    ``anthropic_batch_jobs`` row written at ``create()`` and read back at
    ``results()``:

    * ``create(requests=[...], metering={...})`` — strips the ``metering``
      kwarg (same policy as ``messages.create``: missing → ``Feature.OTHER``
      with a warning), submits, then records an ``AnthropicBatchJob`` row
      carrying feature / org / per-custom_id attribution. ``metering`` may
      include ``by_custom_id`` — ``{custom_id: {"entity_id": ..., "role_id":
      ..., "user_id": ...}}`` — for per-request usage_event attribution.
    * ``results(batch_id)`` — materialises the result stream, then writes
      one claude_call_log row (always) and one usage_events row (when org
      context exists) per succeeded entry, both priced at
      ``service_tier="batch"`` (50% of standard). Idempotent: the batch
      row's ``metered_at`` is a latch, so polling / re-reading an ended
      batch never double-bills. A batch unknown to the table (submitted
      outside the wrapper) is still captured — as ``Feature.OTHER`` with
      no org — so reconciliation against Anthropic billing stays tight.

    A batch runs on ONE API key, so callers must only submit single-org
    batches when per-org workspace keys are enabled (multi-org batches
    would need splitting; deliberately unsupported).

    ``retrieve`` / ``cancel`` / ``list`` pass through unwrapped — they
    carry no token usage.
    """

    def __init__(self, *, messages: _MeteredMessages):
        self._messages = messages
        self._inner = messages._inner.batches

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    # ----- submit ---------------------------------------------------------

    def create(self, **kwargs: Any) -> Any:
        metering = kwargs.pop("metering", None)
        if metering is None:
            logger.warning(
                "metered_anthropic: messages.batches.create called without "
                "`metering=` — batch spend will be recorded as Feature.OTHER "
                "with no org attribution."
            )
            metering = {"feature": Feature.OTHER}
        if not isinstance(metering, dict):
            raise TypeError(f"`metering` must be a dict, got {type(metering).__name__}")
        feature = metering.get("feature")
        if feature is None:
            raise MeteringRequiredError(
                "batch metering={...} must include a `feature` key (use "
                "Feature.OTHER for unclassified batches)"
            )
        feature_str = feature.value if isinstance(feature, Feature) else str(feature)
        try:
            Feature(feature_str)
        except ValueError:
            # Fail fast at submit: an unknown feature would make every
            # results()-time usage_event write fail deterministically,
            # leaving the batch permanently unlatched and re-metered.
            raise MeteringRequiredError(
                f"unknown metering feature {feature_str!r} for batch submit"
            )

        requests = kwargs.get("requests") or []
        by_custom_id = metering.get("by_custom_id")
        reservation_entries = [
            (str(custom_id), per, per.get("credit_reservation"))
            for custom_id, per in (
                by_custom_id.items() if isinstance(by_custom_id, dict) else ()
            )
            if isinstance(per, dict) and per.get("credit_reservation")
        ]
        try:
            for _, _, reservation in reservation_entries:
                if not mark_provider_attempt_started(
                    reservation,
                    provider="anthropic_batch",
                ):
                    raise ProviderAttemptMarkerError(
                        "could not durably mark Anthropic batch attempt"
                    )
            batch = self._inner.create(**kwargs)
        except Exception as exc:
            request_models = {
                str(request.get("custom_id") or ""): str(
                    (request.get("params") or {}).get("model") or ""
                )
                for request in requests
                if isinstance(request, dict)
            }
            for custom_id, per, reservation in reservation_entries:
                if isinstance(exc, ProviderAttemptMarkerError):
                    # The provider was never invoked; unwind any earlier
                    # per-request markers from this all-or-nothing batch.
                    release_provider_usage(
                        reservation,
                        reason="anthropic_batch_attempt_marker_failed",
                        allow_started=True,
                    )
                    released = True
                else:
                    released = release_provider_usage_if_definitely_nonbillable(
                        reservation,
                        error=exc,
                        reason="anthropic_batch_submit_error",
                    )
                parsed = reservation_from_payload(reservation)
                evidence_org_id = (
                    per.get("organization_id")
                    or self._messages._call_org_id(metering)
                    or (parsed.organization_id if parsed is not None else None)
                )
                self._messages._record_call_log_safe(
                    organization_id=(
                        int(evidence_org_id) if evidence_org_id is not None else None
                    ),
                    model=request_models.get(custom_id, ""),
                    usage=None,
                    feature_hint=feature_str,
                    status=("sdk_error" if released else "sdk_ambiguous_error"),
                    error_reason=str(exc)[:500],
                    anthropic_request_id=None,
                    service_tier="batch",
                )
            raise
        self._record_submission_safe(
            batch_id=str(getattr(batch, "id", "") or ""),
            feature=feature_str,
            organization_id=self._messages._call_org_id(metering),
            by_custom_id=by_custom_id,
            requests=requests,
        )
        return batch

    def _record_submission_safe(
        self,
        *,
        batch_id: str,
        feature: str,
        organization_id: Optional[int],
        by_custom_id: Optional[dict],
        requests: list,
    ) -> None:
        """Write the AnthropicBatchJob anchor row. Never raises — the batch
        is already submitted, so failing the caller would strand it; a
        missing row degrades to Feature.OTHER capture at results() time."""
        from ..models.anthropic_batch_job import AnthropicBatchJob

        model = None
        try:
            model = str(requests[0]["params"]["model"])
        except (IndexError, KeyError, TypeError):
            pass
        try:
            with SessionLocal() as session:
                session.add(
                    AnthropicBatchJob(
                        batch_id=batch_id,
                        organization_id=organization_id,
                        feature=feature,
                        model=model,
                        request_count=len(requests),
                        status="submitted",
                        context=(
                            by_custom_id if isinstance(by_custom_id, dict) else None
                        ),
                    )
                )
                session.commit()
        except Exception:
            logger.exception(
                "metered_anthropic: anthropic_batch_jobs write failed "
                "(batch_id=%s feature=%s) — batch submitted OK; its results "
                "will be metered as Feature.OTHER without attribution.",
                batch_id,
                feature,
            )

    # ----- retrieve + meter -------------------------------------------------

    def results(self, batch_id: str, **kwargs: Any) -> Any:
        """Fetch batch results and meter every succeeded entry exactly once.

        Materialises the SDK's result stream first so metering is all-or-
        nothing under the batch row's lock, then returns an iterator over
        the entries — same consumption shape as the bare SDK.
        """
        entries = list(self._inner.results(batch_id, **kwargs))
        self._meter_results_safe(batch_id=str(batch_id), entries=entries)
        return iter(entries)

    def _meter_results_safe(self, *, batch_id: str, entries: list) -> None:
        """Write call_log + usage_event rows for one batch's results.

        Never raises. Idempotency: the batch row is locked (FOR UPDATE),
        ``metered_at`` checked, rows written, latch set, one commit. The
        latch is only set when EVERY entry's writes landed — a swallowed
        write failure (or a crash mid-way) leaves the batch unlatched so
        the next results() call re-meters it. Per-request live reservations
        are also durable idempotency keys, so admitted batch work reuses an
        already-settled event on that retry rather than double-counting it.
        """
        from datetime import datetime, timezone

        from ..models.anthropic_batch_job import AnthropicBatchJob

        try:
            with SessionLocal() as session:
                row = (
                    session.query(AnthropicBatchJob)
                    .filter_by(batch_id=batch_id)
                    .with_for_update()
                    .first()
                )
                if row is None:
                    logger.warning(
                        "metered_anthropic: results for unknown batch_id=%s "
                        "(submitted outside the wrapper?) — metering as "
                        "Feature.OTHER with no org attribution.",
                        batch_id,
                    )
                    row = AnthropicBatchJob(
                        batch_id=batch_id,
                        feature=Feature.OTHER.value,
                        request_count=len(entries),
                        status="submitted",
                        context=None,
                    )
                    session.add(row)
                    session.flush()
                if row.metered_at is not None:
                    return

                context = row.context if isinstance(row.context, dict) else {}
                metered = 0
                failed = 0
                for entry in entries:
                    outcome = self._meter_one_result(
                        entry, batch_row=row, context=context
                    )
                    if outcome == "metered":
                        metered += 1
                    elif outcome == "failed":
                        failed += 1

                if failed:
                    logger.error(
                        "metered_anthropic: %d of %d batch result(s) failed "
                        "their metering writes (batch_id=%s) — NOT latching; "
                        "the next results() call retries the whole batch "
                        "(settled request holds prevent duplicate events).",
                        failed,
                        len(entries),
                        batch_id,
                    )
                    session.rollback()
                    return

                row.metered_at = datetime.now(timezone.utc)
                row.metered_count = metered
                row.status = "ended"
                session.commit()
        except Exception:
            logger.exception(
                "metered_anthropic: batch results metering failed "
                "(batch_id=%s) — results were still returned to the caller, "
                "but reconciliation against Anthropic billing will "
                "undercount until results() is called again.",
                batch_id,
            )

    def _meter_one_result(self, entry: Any, *, batch_row: Any, context: dict) -> str:
        """Meter one result entry. Returns ``"metered"``, ``"skipped"``
        (nothing billable) or ``"failed"`` (a metering write was swallowed
        — the caller must not latch, so the next results() call retries).

        Non-succeeded entries (errored / canceled / expired) carry no
        usage and are not billed by Anthropic — skipped.
        """
        result = getattr(entry, "result", None)
        custom_id = str(getattr(entry, "custom_id", "") or "")
        per = context.get(custom_id) or {}
        reservation = per.get("credit_reservation")
        if getattr(result, "type", None) != "succeeded":
            self._messages._release_credit_reservation_safe(
                {"credit_reservation": reservation},
                reason=f"batch_result:{getattr(result, 'type', None) or 'not_succeeded'}",
                allow_started=True,
            )
            return "skipped"
        message = getattr(result, "message", None)
        usage = getattr(message, "usage", None)
        model = str(getattr(message, "model", None) or batch_row.model or "")
        org_id = per.get("organization_id")
        if org_id is None:
            org_id = batch_row.organization_id
        parsed_reservation = reservation_from_payload(reservation)
        if org_id is None and parsed_reservation is not None:
            org_id = int(parsed_reservation.organization_id)
        result_metering = {
            "feature": batch_row.feature,
            "organization_id": org_id,
            "user_id": per.get("user_id"),
            "role_id": per.get("role_id"),
            "entity_id": per.get("entity_id") or custom_id,
            "metadata": {"batch_id": batch_row.batch_id},
            "credit_reservation": reservation,
        }
        if usage is None:
            self._messages._mark_provider_success(
                usage=None,
                model=model,
                metering=result_metering,
                provider_request_id=(
                    str(getattr(message, "id", None))
                    if getattr(message, "id", None)
                    else None
                ),
                service_tier="batch",
            )
            # The provider explicitly reported success but omitted the usage
            # needed for settlement. Keep the hold and leave the batch
            # unlatched so a later poll can recover a complete result.
            return "failed" if reservation else "skipped"

        self._messages._mark_provider_success(
            usage=usage,
            model=model,
            metering=result_metering,
            provider_request_id=(
                str(getattr(message, "id", None))
                if getattr(message, "id", None)
                else None
            ),
            service_tier="batch",
        )

        # The batch latch is deliberately all-or-nothing, while usage events
        # commit independently. If one later entry's write fails, a poll retry
        # reaches the earlier entries again. Their per-request reservation is
        # the durable idempotency key: reuse the already-settled event instead
        # of inserting a second event (which would inflate role spend even
        # though the ledger correctly ignores a second debit).
        existing_event_id = self._settled_reservation_event_id(reservation)
        if existing_event_id is not None and self._call_log_exists_for_event(
            existing_event_id
        ):
            return "metered"

        usage_event: Optional[UsageEvent] = None
        if org_id is not None:
            if existing_event_id is None:
                payload = self._messages._usage_event_payload(
                    usage=usage,
                    model=model,
                    metering=result_metering,
                    service_tier="batch",
                )
                usage_event = (
                    self._messages._write_event(
                        **payload,
                        credit_reservation=reservation,
                    )
                    if payload is not None
                    else None
                )
            if usage_event is None and existing_event_id is None:
                # _write_event swallowed a failure — don't latch, retry
                # on the next results() call.
                return "failed"
        usage_event_id = (
            existing_event_id
            if existing_event_id is not None
            else int(usage_event.id) if usage_event is not None else None
        )
        wrote = self._messages._record_call_log_safe(
            organization_id=int(org_id) if org_id is not None else None,
            model=model,
            usage=usage,
            feature_hint=str(batch_row.feature),
            status="ok",
            error_reason=None,
            anthropic_request_id=(
                str(getattr(message, "id", None))
                if getattr(message, "id", None)
                else None
            ),
            usage_event_id=usage_event_id,
            service_tier="batch",
        )
        return "metered" if wrote else "failed"

    @staticmethod
    def _settled_reservation_event_id(reservation: Any) -> Optional[int]:
        """Return the event already attached to this request's live hold."""

        parsed = reservation_from_payload(reservation)
        if parsed is None or not parsed.live:
            return None
        from ..models.billing_credit_ledger import BillingCreditLedger

        refs = (
            f"{parsed.external_ref}:settled",
            f"{parsed.external_ref}:late-settled",
        )
        try:
            with SessionLocal() as session:
                rows = (
                    session.query(BillingCreditLedger)
                    .filter(BillingCreditLedger.external_ref.in_(refs))
                    .all()
                )
                for row in rows:
                    metadata = (
                        row.entry_metadata
                        if isinstance(row.entry_metadata, dict)
                        else {}
                    )
                    try:
                        event_id = int(metadata.get("event_id"))
                    except (TypeError, ValueError):
                        continue
                    if session.get(UsageEvent, event_id) is not None:
                        return event_id
        except Exception:
            logger.exception(
                "metered_anthropic: failed to inspect batch reservation settlement"
            )
        return None

    @staticmethod
    def _call_log_exists_for_event(event_id: int) -> bool:
        try:
            with SessionLocal() as session:
                return (
                    session.query(ClaudeCallLog.id)
                    .filter(ClaudeCallLog.usage_event_id == int(event_id))
                    .first()
                    is not None
                )
        except Exception:
            logger.exception(
                "metered_anthropic: failed to inspect batch call-log idempotency"
            )
            return False


class MeteredAnthropicClient:
    """Drop-in replacement for ``anthropic.Anthropic`` that auto-meters.

    Constructed by ``claude_client_resolver``; the rest of the codebase
    treats it identically to the bare SDK client. Only adds the
    ``metering=`` kwarg on ``messages.create`` / ``messages.stream``.
    """

    ai_routing_metered_transport = True

    def __init__(
        self,
        *,
        inner: Anthropic,
        organization_id: Optional[int],
        sdk_max_retries: Optional[int] = None,
    ):
        self._inner = inner
        self._organization_id = organization_id
        self.ai_routing_sdk_max_retries = sdk_max_retries
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

    @property
    def inner(self) -> Anthropic:
        """Escape hatch for callers that need the bare SDK client (e.g.
        admin tooling, client.beta resources). Use sparingly — anything
        that calls the underlying ``inner`` directly will not be metered."""
        return self._inner

    # Pass-through for any attribute we don't override (rare).
    def __getattr__(self, name: str) -> Any:
        if name == "beta":
            # ``client.beta.messages.create(...)`` would slip past the meter
            # entirely — no usage_event AND no claude_call_log row — because
            # only ``.messages`` is wrapped. That is the exact signature of
            # untraceable spend in reconciliation, so fail loud rather than
            # silently bill an org nothing. Intentional unmetered beta calls
            # must reach for ``.inner.beta`` explicitly.
            raise RuntimeError(
                "MeteredAnthropicClient does not expose `.beta`: beta calls "
                "bypass metering (no usage_event / claude_call_log row). Use "
                "`.messages` for metered calls, or `.inner.beta` if an "
                "unmetered beta call is genuinely intended."
            )
        return getattr(self._inner, name)


@contextmanager
def temporary_metering_override(
    *,
    client: MeteredAnthropicClient,
    organization_id: int,
) -> Iterator[MeteredAnthropicClient]:
    """Yield a transient metered client bound to a *different* org.

    Useful when a shared-key client (no org bound) is used inside a
    flow that does have an org context (e.g. archetype synthesis run
    from a route handler). Avoids carrying the org through every helper.
    """
    overridden = MeteredAnthropicClient(
        inner=client._inner,
        organization_id=organization_id,
        sdk_max_retries=client.ai_routing_sdk_max_retries,
    )
    yield overridden
