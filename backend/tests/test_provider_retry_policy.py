from __future__ import annotations

from types import SimpleNamespace

import pytest
from voyageai import error as voyage_error

from app.services import provider_retry_policy as retry_policy
from app.services.provider_usage_admission import (
    provider_error_is_definitely_nonbillable,
)


class _StatusError(RuntimeError):
    def __init__(self, status_code):
        super().__init__("provider failure")
        self.status_code = status_code


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504, 529])
def test_transient_provider_statuses_are_retryable(status):
    assert retry_policy.provider_error_is_retryable(_StatusError(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 413, 415, 422])
def test_validation_and_auth_statuses_are_never_retryable(status):
    assert retry_policy.provider_error_is_retryable(_StatusError(status)) is False


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        ConnectionError("connection dropped"),
    ],
)
def test_transport_failures_are_retryable(error):
    assert retry_policy.provider_error_is_retryable(error) is True


@pytest.mark.parametrize(
    "error",
    [
        voyage_error.Timeout("timed out"),
        voyage_error.APIConnectionError("connection dropped"),
        voyage_error.RateLimitError("rate limited"),
        voyage_error.ServiceUnavailableError("unavailable"),
        voyage_error.ServerError("server failed"),
    ],
)
def test_voyage_transient_errors_are_retryable(error):
    assert retry_policy.provider_error_is_retryable(error) is True


@pytest.mark.parametrize(
    "error",
    [
        voyage_error.AuthenticationError("bad key"),
        voyage_error.InvalidRequestError("invalid"),
        voyage_error.MalformedRequestError("malformed"),
    ],
)
def test_voyage_auth_and_validation_errors_are_not_retryable(error):
    assert retry_policy.provider_error_is_retryable(error) is False


def test_voyage_rate_limit_status_is_safe_to_release_before_retry():
    error = voyage_error.RateLimitError("rate limited", http_status=429)

    assert provider_error_is_definitely_nonbillable(error) is True


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("unknown failure"),
        ValueError("local validation failed"),
        _StatusError(True),
    ],
)
def test_unknown_local_and_malformed_failures_are_not_retryable(error):
    assert retry_policy.provider_error_is_retryable(error) is False


def test_retry_is_bounded_to_one_explicit_retry():
    error = TimeoutError("timed out")

    assert retry_policy.should_retry_provider_error(error, attempt_index=0) is True
    assert retry_policy.should_retry_provider_error(error, attempt_index=1) is False


def test_wire_attempt_limit_can_only_shrink_the_global_bound():
    assert retry_policy.provider_wire_attempt_limit({}) == 2
    assert (
        retry_policy.provider_wire_attempt_limit(
            {retry_policy.PROVIDER_WIRE_ATTEMPT_LIMIT_KEY: 1}
        )
        == 1
    )
    assert (
        retry_policy.should_retry_provider_error(
            TimeoutError("timed out"),
            attempt_index=0,
            max_attempts=1,
        )
        is False
    )


@pytest.mark.parametrize("value", [True, False, 0, -1, 3, 1.0, "1", None])
def test_wire_attempt_limit_rejects_invalid_or_expanding_values(value):
    with pytest.raises(ValueError, match="wire attempt limit"):
        retry_policy.provider_wire_attempt_limit(
            {retry_policy.PROVIDER_WIRE_ATTEMPT_LIMIT_KEY: value}
        )


def test_retry_metering_gets_fresh_attempt_identity_without_mutating_caller():
    original = {
        "feature": "score",
        "trace_id": "trace-1",
        "retry_attempt": 4,
        "credit_reservation": {"external_ref": "first-hold"},
    }

    retried = retry_policy.metering_for_retry(original, retry_attempt=5)

    assert retried == {
        "feature": "score",
        "trace_id": "trace-1",
        "retry_attempt": 5,
    }
    assert original["credit_reservation"] == {"external_ref": "first-hold"}


def test_backoff_is_positive_bounded_and_increases():
    first = retry_policy.retry_backoff_seconds(
        next_attempt_index=1,
        jitter_unit=lambda: 0.0,
    )
    second = retry_policy.retry_backoff_seconds(
        next_attempt_index=2,
        jitter_unit=lambda: 0.0,
    )

    assert 0 < first < second <= retry_policy.MAX_RETRY_BACKOFF_SECONDS


def test_retry_after_is_honored_but_strictly_bounded():
    hinted = _StatusError(429)
    hinted.response = SimpleNamespace(headers={"retry-after": "1.5"})
    too_long = _StatusError(429)
    too_long.response = SimpleNamespace(headers={"Retry-After": "90"})
    millisecond_hint = _StatusError(429)
    millisecond_hint.response = SimpleNamespace(headers={"retry-after-ms": "750"})

    assert retry_policy.retry_backoff_seconds(
        next_attempt_index=1,
        error=hinted,
        jitter_unit=lambda: 0.0,
    ) == 1.5
    assert retry_policy.retry_backoff_seconds(
        next_attempt_index=1,
        error=too_long,
        jitter_unit=lambda: 1.0,
    ) == retry_policy.MAX_RETRY_BACKOFF_SECONDS
    assert retry_policy.retry_backoff_seconds(
        next_attempt_index=1,
        error=millisecond_hint,
        jitter_unit=lambda: 0.0,
    ) == 0.75


def test_retry_jitter_is_injectable_deterministic_and_bounded():
    no_jitter = retry_policy.retry_backoff_seconds(
        next_attempt_index=1,
        jitter_unit=lambda: 0.0,
    )
    maximum_jitter = retry_policy.retry_backoff_seconds(
        next_attempt_index=1,
        jitter_unit=lambda: 1.0,
    )

    assert no_jitter == retry_policy.INITIAL_RETRY_BACKOFF_SECONDS
    assert no_jitter < maximum_jitter
    assert maximum_jitter <= retry_policy.MAX_RETRY_BACKOFF_SECONDS
