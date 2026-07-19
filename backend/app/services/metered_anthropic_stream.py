"""Durable evidence for the synchronous Anthropic streaming surface."""

from __future__ import annotations

import logging
from typing import Any

from . import provider_retry_policy as retry_policy
from .provider_error_evidence import safe_provider_error_code
from .provider_usage_admission import release_provider_usage_if_definitely_nonbillable

logger = logging.getLogger("taali.metered_anthropic")


class _IncompleteStreamError(RuntimeError):
    """Internal evidence marker for a stream closed before its final delta."""


class MeteredAnthropicStreamContext:
    """Defer admission until enter and persist evidence on every exit path."""

    def __init__(
        self, *, inner, inner_factory, messages, model: str, metering, request
    ):
        self._inner = inner
        self._inner_factory = inner_factory
        self._messages = messages
        self._model = model
        self._metering = metering
        self._request = request
        self._stream = None
        self._retry_attempt = 0

    def __enter__(self):
        wire_attempt_limit = retry_policy.provider_wire_attempt_limit(self._metering)
        base_attempt, parent_call_log_id, trace_id = self._messages._retry_context(
            self._metering
        )
        base_attempt = max(base_attempt, 0)
        self._parent_call_log_id = parent_call_log_id
        attempt_index = 0
        retry_evidence_missing = False
        while True:
            self._messages._ensure_provider_reservation(
                self._metering,
                request=self._request,
            )
            trace_id = self._messages._retry_context(self._metering)[2] or trace_id
            self._retry_attempt = base_attempt + attempt_index
            try:
                self._stream = self._inner.__enter__()
                break
            except Exception as exc:
                reservation = self._metering.get("credit_reservation")
                released = release_provider_usage_if_definitely_nonbillable(
                    reservation,
                    error=exc,
                    reason="stream_enter_error",
                )
                error_class, http_status = self._messages._classify_exception(exc)
                logger.error(
                    "Anthropic stream enter failed model=%s error_type=%s",
                    self._model,
                    type(exc).__name__,
                )
                failure_log_id = self._messages._record_call_log_safe(
                    organization_id=self._messages._call_org_id(self._metering),
                    model=self._model,
                    usage=None,
                    feature_hint=self._messages._feature_hint_from(self._metering),
                    status=(
                        "sdk_ambiguous_error"
                        if reservation and not released
                        else "sdk_error"
                    ),
                    error_reason=safe_provider_error_code(
                        exc,
                        operation="anthropic_stream_enter",
                    ),
                    anthropic_request_id=None,
                    error_class=error_class,
                    http_status=http_status,
                    retry_attempt=self._retry_attempt,
                    parent_call_log_id=parent_call_log_id,
                    trace_id=trace_id,
                )
                retryable = retry_policy.provider_error_is_retryable(exc)
                if retryable and failure_log_id is None:
                    logger.error(
                        "Anthropic stream retry blocked: failure evidence unavailable"
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
                self._parent_call_log_id = failure_log_id
                attempt_index += 1
                retry_policy.sleep_before_retry(
                    next_attempt_index=attempt_index,
                    error=exc,
                )
                self._metering = retry_policy.metering_for_retry(
                    self._metering,
                    retry_attempt=base_attempt + attempt_index,
                )
                self._inner = self._inner_factory()
        if retry_evidence_missing:
            # Avoid attaching an uncontrolled provider body as exception context.
            raise retry_policy.ProviderRetryEvidenceUnavailableError(
                "provider retry evidence is unavailable"
            )
        return self._stream

    def __exit__(self, exc_type, exc, tb):
        usage, stream_completed = self._completed_usage_without_draining()
        exit_error: BaseException | None = None
        try:
            result = self._inner.__exit__(exc_type, exc, tb)
        except BaseException as caught:
            exit_error = caught
            raise
        finally:
            effective_exc_type = (
                exc_type
                or (type(exit_error) if exit_error is not None else None)
                or (None if stream_completed else _IncompleteStreamError)
            )
            try:
                self._persist_usage(usage, effective_exc_type)
            except Exception as evidence_error:
                logger.error(
                    "metered_anthropic: stream evidence write failed error_code=%s",
                    safe_provider_error_code(
                        evidence_error,
                        operation="anthropic_stream_evidence",
                    ),
                )
        return result

    def _completed_usage_without_draining(self) -> tuple[Any, bool]:
        """Read an already-final snapshot without consuming provider output.

        Anthropic's ``get_final_message()`` calls ``until_done()``. Calling it
        from ``__exit__`` would therefore keep generating after a consumer
        disconnect or early exit. A non-null stop reason is written by the
        final message delta and proves the public snapshot is complete.
        """

        if self._stream is None:
            return None, False
        try:
            final_message = self._stream.current_message_snapshot
        except Exception as caught:
            logger.debug(
                "metered_anthropic: final snapshot unavailable error_code=%s",
                safe_provider_error_code(
                    caught,
                    operation="anthropic_stream_final_snapshot",
                ),
            )
            return None, False
        if getattr(final_message, "stop_reason", None) is None:
            return None, False
        return getattr(final_message, "usage", None), True

    def _persist_usage(self, usage: Any, exc_type: Any) -> None:
        self._messages._mark_provider_success(
            usage=usage,
            model=self._model,
            metering=self._metering,
            provider_request_id=None,
        )
        usage_event = None
        if usage is None:
            logger.error(
                "metered_anthropic: stream ended without usage; retaining "
                "provider hold for reconciliation (model=%s error=%s)",
                self._model,
                getattr(exc_type, "__name__", None),
            )
        else:
            usage_event = self._messages._record_from_usage(
                usage=usage,
                model=self._model,
                metering=self._metering,
            )
        self._messages._record_call_log_safe(
            organization_id=self._messages._call_org_id(self._metering),
            model=self._model,
            usage=usage,
            feature_hint=self._messages._feature_hint_from(self._metering),
            status=(
                "interrupted"
                if exc_type is not None
                else "no_usage_on_response"
                if usage is None
                else "metering_error"
                if self._metering.get("credit_reservation") and usage_event is None
                else "ok"
            ),
            error_reason=(
                None
                if exc_type is None
                else getattr(exc_type, "__name__", str(exc_type))
            ),
            anthropic_request_id=None,
            usage_event_id=int(usage_event.id) if usage_event is not None else None,
            retry_attempt=self._retry_attempt,
            parent_call_log_id=self._parent_call_log_id,
            trace_id=(
                str(self._metering.get("trace_id"))
                if self._metering.get("trace_id")
                else None
            ),
        )


__all__ = ["MeteredAnthropicStreamContext"]
