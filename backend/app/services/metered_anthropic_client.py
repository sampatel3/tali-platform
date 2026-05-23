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

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from anthropic import Anthropic

from ..models.claude_call_log import ClaudeCallLog
from ..models.usage_event import UsageEvent
from ..platform.database import SessionLocal
from .pricing_service import Feature, raw_cost_usd_micro
from .usage_metering_service import record_event

logger = logging.getLogger("taali.metered_anthropic")


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


class _MeteredMessages:
    """Wraps ``Anthropic.messages`` to record a ``usage_event`` per call.

    Holds a reference to the org_id captured at client construction so
    callers don't have to repeat it. Each call may pass its own
    ``user_id`` / ``role_id`` / ``entity_id`` for finer attribution.
    """

    def __init__(self, *, inner: Any, organization_id: Optional[int]):
        self._inner = inner
        self._organization_id = organization_id

    # Pass-through for nested resources we don't intercept yet — most
    # importantly ``messages.batches.*``. Without this, accessing
    # ``client.messages.batches`` would fail because ``_MeteredMessages``
    # is not the SDK's real Messages resource. Batch usage is rare and
    # not yet metered through the wrapper; surfaced un-metered for now
    # with a TODO to instrument it once the batch metering shape lands.
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

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
        model = str(kwargs.get("model") or "")
        feature_hint = self._feature_hint_from(metering)
        retry_attempt, parent_call_log_id, trace_id = self._retry_context(metering)
        try:
            response = self._inner.create(**kwargs)
        except Exception as exc:
            # Anthropic call failed (network, 4xx, 5xx). Tokens are zero
            # so no $ charge — but we still log the attempt so the user
            # can see the failure rate. NEVER suppress the exception.
            error_class, http_status = self._classify_exception(exc)
            self._record_call_log_safe(
                organization_id=self._call_org_id(metering),
                model=model,
                usage=None,
                feature_hint=feature_hint,
                status="sdk_error",
                error_reason=str(exc)[:500],
                anthropic_request_id=None,
                error_class=error_class,
                http_status=http_status,
                retry_attempt=retry_attempt,
                parent_call_log_id=parent_call_log_id,
                trace_id=trace_id,
            )
            raise

        usage = getattr(response, "usage", None)
        request_id = self._extract_request_id(response)
        usage_event: Optional[UsageEvent] = None

        if metering is not _SKIP:
            usage_event = self._record_from_usage(
                usage=usage,
                model=model,
                metering=metering,
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
            status="ok" if usage is not None else "no_usage_on_response",
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
        inner_cm = self._inner.stream(**kwargs)
        if metering is _SKIP:
            return inner_cm
        return _MeteredStreamCtx(
            inner=inner_cm,
            messages=self,
            model=str(kwargs.get("model") or ""),
            metering=metering,
        )

    # Async surface — kept thin and currently unused. Added so anyone
    # reaching for ``AsyncAnthropic`` later doesn't silently bypass the
    # meter. Mirror the sync ``create`` exactly.
    async def acreate(self, **kwargs: Any) -> Any:  # pragma: no cover
        metering = self._extract_metering(kwargs)
        response = await self._inner.create(**kwargs)
        if metering is _SKIP:
            return response
        usage = getattr(response, "usage", None)
        self._record_from_usage(
            usage=usage,
            model=str(kwargs.get("model") or ""),
            metering=metering,
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
                "falling back to Feature.OTHER. Add `metering={\"feature\": Feature.X, ...}` "
                "to attribute spend correctly.",
                kwargs.get("model") or "<unknown-model>",
            )
            return {"feature": Feature.OTHER}

        if not isinstance(meter, dict):
            raise TypeError(
                f"`metering` must be a dict, got {type(meter).__name__}"
            )

        if meter.get("skip"):
            return _SKIP

        feature = meter.get("feature")
        if feature is None:
            raise MeteringRequiredError(
                "metering={...} must include a `feature` key (use "
                "Feature.OTHER for unclassified calls)"
            )

        return meter

    # ----- claude_call_log helpers (P0 — source-of-truth log) ------------

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
        for path in ("id", "_request_id"):
            val = getattr(response, path, None)
            if val:
                return str(val)
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
    ) -> None:
        """Write one ``ClaudeCallLog`` row. Never raises — call_log failures
        must not break Claude calls. Logs at WARNING so ops sees them.

        Unconditional by design. This is the structural guarantee that
        every call lands a row, regardless of whether the application
        layer's metering succeeded.
        """
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        cache_read_tokens = (
            int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0
        )
        cache_creation_tokens = (
            int(getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0
        )
        try:
            cost_micro = raw_cost_usd_micro(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
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
        except Exception:
            logger.exception(
                "metered_anthropic: claude_call_log write failed (model=%s, "
                "status=%s) — Claude call already succeeded so we don't raise, "
                "but reconciliation against Anthropic billing will undercount.",
                model,
                status,
            )

    # ----- usage_event recording (existing path) --------------------------

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
        if self._organization_id is None and metering.get("organization_id") is None:
            logger.warning(
                "metered_anthropic: skipping record — no organization_id "
                "(client built without org context). Pass metering={'organization_id': ...} "
                "for admin/shared-key flows that should still be billed."
            )
            return None

        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read_tokens = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_creation_tokens = int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        )
        cache_hit = bool(metering.get("cache_hit", False))

        org_id = int(
            metering.get("organization_id")
            if metering.get("organization_id") is not None
            else self._organization_id
        )

        return self._write_event(
            organization_id=org_id,
            feature=metering["feature"],
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_hit=cache_hit,
            user_id=metering.get("user_id"),
            role_id=metering.get("role_id"),
            entity_id=metering.get("entity_id"),
            metadata=metering.get("metadata"),
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
        cache_hit: bool,
        user_id: Optional[int],
        role_id: Optional[int],
        entity_id: Optional[str],
        metadata: Optional[dict],
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
                    cache_hit=cache_hit,
                    user_id=user_id,
                    role_id=role_id,
                    entity_id=entity_id,
                    metadata=metadata,
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
                organization_id, feature, model,
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
    ):
        self._inner = inner
        self._messages = messages
        self._model = model
        self._metering = metering
        self._stream = None

    def __enter__(self):
        self._stream = self._inner.__enter__()
        return self._stream

    def __exit__(self, exc_type, exc, tb):
        # Snapshot final usage *before* closing the stream — the SDK
        # exposes it on the live stream object and on the final message.
        usage = None
        if self._stream is not None:
            try:
                final_message = self._stream.get_final_message()
                usage = getattr(final_message, "usage", None)
            except Exception:
                logger.debug(
                    "metered_anthropic: get_final_message() failed; "
                    "skipping meter for this stream",
                    exc_info=True,
                )
        result = self._inner.__exit__(exc_type, exc, tb)

        if exc_type is None and usage is not None:
            try:
                self._messages._record_from_usage(
                    usage=usage,
                    model=self._model,
                    metering=self._metering,
                )
            except Exception:
                logger.exception(
                    "metered_anthropic: stream meter write failed"
                )
        return result


class MeteredAnthropicClient:
    """Drop-in replacement for ``anthropic.Anthropic`` that auto-meters.

    Constructed by ``claude_client_resolver``; the rest of the codebase
    treats it identically to the bare SDK client. Only adds the
    ``metering=`` kwarg on ``messages.create`` / ``messages.stream``.
    """

    def __init__(self, *, inner: Anthropic, organization_id: Optional[int]):
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

    @property
    def inner(self) -> Anthropic:
        """Escape hatch for callers that need the bare SDK client (e.g.
        admin tooling, client.beta resources). Use sparingly — anything
        that calls the underlying ``inner`` directly will not be metered."""
        return self._inner

    # Pass-through for any attribute we don't override (rare).
    def __getattr__(self, name: str) -> Any:
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
    )
    yield overridden
