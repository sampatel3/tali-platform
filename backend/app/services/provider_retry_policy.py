"""Bounded, observable retries for paid provider wire attempts."""

from __future__ import annotations

import asyncio
import email.utils
import math
import random
import time
from datetime import timezone
from typing import Any, Callable


MAX_PROVIDER_WIRE_ATTEMPTS = 2
PROVIDER_WIRE_ATTEMPT_LIMIT_KEY = "provider_wire_attempt_limit"
INITIAL_RETRY_BACKOFF_SECONDS = 0.25
MAX_RETRY_BACKOFF_SECONDS = 2.0
RETRY_JITTER_RATIO = 0.25


class ProviderRetryEvidenceUnavailableError(RuntimeError):
    """A transient failure cannot be retried without durable failure evidence."""


def provider_wire_attempt_limit(metering: Any) -> int:
    """Return a caller's bounded local retry cap without permitting expansion."""

    if not isinstance(metering, dict) or PROVIDER_WIRE_ATTEMPT_LIMIT_KEY not in metering:
        return MAX_PROVIDER_WIRE_ATTEMPTS
    value = metering[PROVIDER_WIRE_ATTEMPT_LIMIT_KEY]
    if (
        type(value) is not int
        or value < 1
        or value > MAX_PROVIDER_WIRE_ATTEMPTS
    ):
        raise ValueError(
            "provider wire attempt limit must be an integer between 1 and "
            f"{MAX_PROVIDER_WIRE_ATTEMPTS}"
        )
    return value


def require_sdk_retries_disabled(client: Any, *, provider: str) -> None:
    """Reject a wrapped real SDK client that could retry invisibly."""

    state = getattr(client, "__dict__", None)
    configured = state.get("max_retries") if isinstance(state, dict) else None
    if configured is None:
        return
    if type(configured) is not int or configured != 0:
        raise RuntimeError(
            f"{provider} SDK retries must be disabled for per-attempt metering"
        )


def _provider_status_code(error: BaseException) -> int | None:
    for value in (
        getattr(error, "status_code", None),
        getattr(error, "http_status", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        if type(value) is int and 100 <= value <= 599:
            return value
    return None


def provider_error_is_retryable(error: BaseException) -> bool:
    """Retry only transport faults and provider-declared transient responses.

    This deliberately mirrors the recoverable subset of the Anthropic SDK's
    hidden retry policy while leaving validation, authentication, permission,
    and local credit/admission failures terminal.
    """

    status = _provider_status_code(error)
    if status is not None:
        return status in {408, 409, 429} or 500 <= status <= 599
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    try:
        import anthropic  # type: ignore[import-not-found]

        if isinstance(
            error,
            (
                anthropic.APITimeoutError,
                anthropic.APIConnectionError,
            ),
        ):
            return True
    except Exception:
        pass
    try:
        from voyageai import error as voyage_error  # type: ignore[import-not-found]

        return isinstance(
            error,
            (
                voyage_error.Timeout,
                voyage_error.APIConnectionError,
                voyage_error.RateLimitError,
                voyage_error.ServiceUnavailableError,
                voyage_error.ServerError,
            ),
        )
    except Exception:
        return False


def should_retry_provider_error(
    error: BaseException,
    *,
    attempt_index: int,
    max_attempts: int = MAX_PROVIDER_WIRE_ATTEMPTS,
) -> bool:
    if type(attempt_index) is not int or attempt_index < 0:
        return False
    if (
        type(max_attempts) is not int
        or max_attempts < 1
        or max_attempts > MAX_PROVIDER_WIRE_ATTEMPTS
    ):
        raise ValueError("invalid provider wire attempt limit")
    return (
        attempt_index + 1 < max_attempts
        and provider_error_is_retryable(error)
    )


def metering_for_retry(
    metering: dict[str, Any],
    *,
    retry_attempt: int,
) -> dict[str, Any]:
    """Copy attribution but remove the single-use reservation identity."""

    if type(metering) is not dict:
        raise TypeError("provider retry metering must be an object")
    if type(retry_attempt) is not int or retry_attempt < 1:
        raise ValueError("provider retry attempt must be a positive integer")
    retried = dict(metering)
    retried.pop("credit_reservation", None)
    retried["retry_attempt"] = retry_attempt
    return retried


def _header_value(headers: Any, name: str) -> Any:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except (AttributeError, TypeError):
        value = None
    if value is not None:
        return value
    try:
        for key, candidate in headers.items():
            if str(key).lower() == name.lower():
                return candidate
    except (AttributeError, TypeError, ValueError):
        return None
    return None


def _provider_response_headers(error: BaseException | None) -> Any:
    if error is None:
        return None
    response = getattr(error, "response", None)
    for candidate in (response, error):
        headers = getattr(candidate, "headers", None)
        if headers is not None:
            return headers
    return None


def _retry_after_seconds(
    error: BaseException | None,
    *,
    now_seconds: Callable[[], float],
) -> float | None:
    """Read provider delay hints without exposing response/header contents."""

    headers = _provider_response_headers(error)
    milliseconds = _header_value(headers, "retry-after-ms")
    try:
        delay = float(milliseconds) / 1_000.0
    except (TypeError, ValueError, OverflowError):
        delay = 0.0
    if math.isfinite(delay) and delay > 0:
        return delay

    retry_after = _header_value(headers, "retry-after")
    try:
        delay = float(retry_after)
    except (TypeError, ValueError, OverflowError):
        delay = 0.0
    if math.isfinite(delay) and delay > 0:
        return delay

    try:
        parsed = email.utils.parsedate_to_datetime(str(retry_after))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delay = parsed.timestamp() - float(now_seconds())
    return delay if math.isfinite(delay) and delay > 0 else None


def retry_backoff_seconds(
    *,
    next_attempt_index: int,
    error: BaseException | None = None,
    jitter_unit: Callable[[], float] | None = None,
    now_seconds: Callable[[], float] = time.time,
) -> float:
    if type(next_attempt_index) is not int or next_attempt_index < 1:
        raise ValueError("next provider attempt index must be positive")
    exponential_delay = min(
        INITIAL_RETRY_BACKOFF_SECONDS * (2 ** (next_attempt_index - 1)),
        MAX_RETRY_BACKOFF_SECONDS,
    )
    hinted_delay = _retry_after_seconds(error, now_seconds=now_seconds)
    base_delay = min(
        hinted_delay if hinted_delay is not None else exponential_delay,
        MAX_RETRY_BACKOFF_SECONDS,
    )
    unit = float((jitter_unit or random.random)())
    if not math.isfinite(unit) or not 0.0 <= unit <= 1.0:
        raise ValueError("provider retry jitter must be between zero and one")
    # Jitter only later, never earlier than Retry-After. This spreads retry
    # storms while respecting a provider's bounded minimum delay.
    jitter = min(
        base_delay * RETRY_JITTER_RATIO * unit,
        MAX_RETRY_BACKOFF_SECONDS - base_delay,
    )
    return base_delay + jitter


def sleep_before_retry(
    *,
    next_attempt_index: int,
    error: BaseException | None = None,
) -> None:
    time.sleep(
        retry_backoff_seconds(
            next_attempt_index=next_attempt_index,
            error=error,
        )
    )


async def async_sleep_before_retry(
    *,
    next_attempt_index: int,
    error: BaseException | None = None,
) -> None:
    await asyncio.sleep(
        retry_backoff_seconds(
            next_attempt_index=next_attempt_index,
            error=error,
        )
    )


__all__ = [
    "MAX_PROVIDER_WIRE_ATTEMPTS",
    "MAX_RETRY_BACKOFF_SECONDS",
    "PROVIDER_WIRE_ATTEMPT_LIMIT_KEY",
    "ProviderRetryEvidenceUnavailableError",
    "RETRY_JITTER_RATIO",
    "async_sleep_before_retry",
    "metering_for_retry",
    "provider_error_is_retryable",
    "provider_wire_attempt_limit",
    "require_sdk_retries_disabled",
    "retry_backoff_seconds",
    "should_retry_provider_error",
    "sleep_before_retry",
]
