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

If ``metering["db"]`` is supplied, the usage_event is written using that
session and committed when the caller commits. This preserves
transactional coupling — if the scoring write rolls back, the meter event
also rolls back.

If ``metering["db"]`` is absent, the wrapper opens a fresh ``SessionLocal()``
just for the metering write and commits it independently. This means
metering is recorded even if the caller never holds a request session
(e.g. background workers calling Anthropic ad-hoc).

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

from ..models.usage_event import UsageEvent
from ..platform.database import SessionLocal
from .pricing_service import Feature
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

    def create(self, **kwargs: Any) -> Any:
        metering = self._extract_metering(kwargs)
        response = self._inner.create(**kwargs)

        if metering is _SKIP:
            return response

        usage = getattr(response, "usage", None)
        self._record_from_usage(
            usage=usage,
            model=str(kwargs.get("model") or ""),
            metering=metering,
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

    def _record_from_usage(
        self,
        *,
        usage: Any,
        model: str,
        metering: dict[str, Any],
    ) -> None:
        """Pull token counts off ``response.usage`` and write a usage_event.

        Never raises — metering errors are logged but never propagate to
        the caller. A scoring run that succeeded but failed to write its
        meter event is still useful; raising here would be worse.
        """
        if self._organization_id is None and metering.get("organization_id") is None:
            logger.warning(
                "metered_anthropic: skipping record — no organization_id "
                "(client built without org context). Pass metering={'organization_id': ...} "
                "for admin/shared-key flows that should still be billed."
            )
            return

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

        self._write_event(
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
            db=metering.get("db"),
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
        db: Any,
    ) -> Optional[UsageEvent]:
        """Write a usage_event row using the caller's session (transactional
        coupling) or a fresh session (independent commit). Always swallows
        errors — metering must never break a Claude call."""
        try:
            if db is not None:
                return record_event(
                    db,
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
