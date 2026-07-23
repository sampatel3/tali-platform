"""Metering wrappers must not silently expose the unmetered ``.beta`` surface.

``client.beta.messages.create(...)`` only reaches the raw SDK — it writes
neither a usage_event nor a claude_call_log row, so its spend is invisible to
metering and shows up in reconciliation as untraceable drift. Both wrappers
fail loud on ``.beta``; deliberate unmetered beta calls go through ``.inner``.
"""
from __future__ import annotations

import pytest

from app.services.metered_anthropic_client import MeteredAnthropicClient
from app.services.metered_async_anthropic_client import MeteredAsyncAnthropic


class _FakeBeta:
    class messages:  # noqa: N801 - mirror SDK shape
        @staticmethod
        def create(**_kwargs):
            return "unmetered!"


class _FakeInner:
    def __init__(self):
        self.beta = _FakeBeta()
        self.messages = object()
        self.some_other_attr = "ok"


def test_sync_wrapper_blocks_beta():
    client = MeteredAnthropicClient(inner=_FakeInner(), organization_id=1)
    with pytest.raises(RuntimeError, match="bypass metering"):
        _ = client.beta
    # The documented escape hatch still works for intentional raw access.
    assert client.inner.beta.messages.create() == "unmetered!"
    # Unrelated pass-through attributes are unaffected.
    assert client.some_other_attr == "ok"


def test_async_wrapper_blocks_beta():
    client = MeteredAsyncAnthropic(inner=_FakeInner())
    with pytest.raises(RuntimeError, match="bypass metering"):
        _ = client.beta
    assert client.inner.beta.messages.create() == "unmetered!"
