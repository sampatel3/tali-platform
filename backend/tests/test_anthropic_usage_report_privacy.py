"""Anthropic Admin failures never retain provider response bodies."""

from datetime import datetime, timedelta, timezone

import pytest

from app.components.integrations.anthropic_admin import usage_reports


def test_usage_report_http_failure_does_not_read_or_return_body(monkeypatch):
    secret_marker = "anthropic-admin-provider-secret-must-not-escape"

    class _Response:
        status_code = 429

        @property
        def text(self):
            raise AssertionError(f"provider body must not be read: {secret_marker}")

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(usage_reports.httpx, "Client", _Client)
    monkeypatch.setattr(
        usage_reports.settings,
        "ANTHROPIC_ADMIN_API_KEY",
        "admin-secret",
        raising=False,
    )
    end = datetime.now(timezone.utc)

    with pytest.raises(usage_reports.AnthropicUsageError) as exc_info:
        list(
            usage_reports.fetch_usage_buckets(
                starting_at=end - timedelta(days=1),
                ending_at=end,
            )
        )

    assert str(exc_info.value).endswith("failed: HTTP 429")
    assert secret_marker not in str(exc_info.value)
