"""Paid Anthropic SDK surfaces cannot bypass the metered adapters."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.models.anthropic_batch_job import AnthropicBatchJob
from app.models.organization import Organization
from app.services.anthropic_request_admission import AnthropicRequestAdmissionError
from app.services.anthropic_surface_guard import UnsupportedAnthropicSurfaceError
from app.services.metered_anthropic_client import (
    MeteredAnthropicClient,
    ProviderAttemptMarkerError,
)
from app.services.metered_async_anthropic_client import MeteredAsyncAnthropic


class _Resource:
    def __init__(self):
        self.accessed: list[str] = []
        self.calls: list[str] = []

    def __getattr__(self, name):
        self.accessed.append(name)

        def operation(*_args, **_kwargs):
            self.calls.append(name)
            return name

        return operation


class _SyncInner:
    def __init__(self):
        self.messages = _Resource()
        self.models = _Resource()
        self.top_level_accessed: list[str] = []
        self.option_calls: list[dict] = []

    def with_options(self, **kwargs):
        self.option_calls.append(kwargs)
        return self

    def __getattr__(self, name):
        self.top_level_accessed.append(name)
        return object()

    def close(self):
        return None


class _AsyncInner(_SyncInner):
    async def close(self):
        return None


def test_sync_wrapper_rejects_hidden_sdk_retries():
    inner = _SyncInner()
    inner.max_retries = 1

    with pytest.raises(RuntimeError, match="retries"):
        MeteredAnthropicClient(inner=inner, organization_id=1)


def test_async_wrapper_rejects_hidden_sdk_retries():
    inner = _AsyncInner()
    inner.max_retries = 1

    with pytest.raises(RuntimeError, match="retries"):
        MeteredAsyncAnthropic(inner=inner)


@pytest.mark.parametrize("name", ["with_raw_response", "with_streaming_response"])
def test_sync_message_response_wrappers_are_blocked_before_inner_access(name):
    inner = _SyncInner()
    client = MeteredAnthropicClient(inner=inner, organization_id=1)

    with pytest.raises(UnsupportedAnthropicSurfaceError):
        getattr(client.messages, name)

    assert inner.messages.accessed == []


@pytest.mark.parametrize(
    "name",
    ["beta", "completions", "post", "request", "inner"],
)
def test_sync_top_level_paid_or_raw_surfaces_are_blocked(name):
    inner = _SyncInner()
    client = MeteredAnthropicClient(inner=inner, organization_id=1)

    with pytest.raises(UnsupportedAnthropicSurfaceError):
        getattr(client, name)

    assert inner.top_level_accessed == []


def test_sync_transport_options_are_bounded_and_remain_metered():
    inner = _SyncInner()
    client = MeteredAnthropicClient(inner=inner, organization_id=17)

    bounded = client.with_options(timeout=2.5, max_retries=0)

    assert isinstance(bounded, MeteredAnthropicClient)
    assert bounded is not client
    assert bounded.organization_id == 17
    assert inner.option_calls == [{"timeout": 2.5, "max_retries": 0}]
    with pytest.raises(UnsupportedAnthropicSurfaceError):
        _ = bounded.inner


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_sync_transport_options_reject_invalid_deadline_timeout(timeout):
    client = MeteredAnthropicClient(inner=_SyncInner(), organization_id=1)

    with pytest.raises(ValueError, match="timeout"):
        client.with_options(timeout=timeout, max_retries=0)


@pytest.mark.parametrize("max_retries", [1, -1, False, 0.0])
def test_sync_transport_options_reject_retries_and_authority_overrides(
    max_retries,
):
    client = MeteredAnthropicClient(inner=_SyncInner(), organization_id=1)

    with pytest.raises(ValueError, match="max_retries"):
        client.with_options(timeout=1, max_retries=max_retries)
    with pytest.raises(TypeError):
        client.with_options(timeout=1, max_retries=0, base_url="https://evil.invalid")


@pytest.mark.parametrize("attempt_limit", [True, 0, 3, "1"])
def test_sync_retry_limit_is_rejected_before_provider_access(attempt_limit):
    inner = _SyncInner()
    client = MeteredAnthropicClient(inner=inner, organization_id=1)

    with pytest.raises(ValueError, match="wire attempt limit"):
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[],
            metering={
                "feature": "other",
                "provider_wire_attempt_limit": attempt_limit,
            },
        )

    assert inner.messages.accessed == []


def test_sync_reviewed_nonbillable_operations_remain_available():
    inner = _SyncInner()
    client = MeteredAnthropicClient(inner=inner, organization_id=1)

    assert client.messages.count_tokens(model="claude-haiku-4-5", messages=[]) == (
        "count_tokens"
    )
    assert client.models.retrieve("claude-haiku-4-5") == "retrieve"
    assert inner.messages.calls == ["count_tokens"]
    assert inner.models.calls == ["retrieve"]


def test_shared_batch_list_and_cancel_are_not_exposed():
    inner = _SyncInner()
    batch_inner = _Resource()
    inner.messages.batches = batch_inner
    batches = MeteredAnthropicClient(inner=inner, organization_id=1).messages.batches

    for name in ("list", "cancel"):
        with pytest.raises(UnsupportedAnthropicSurfaceError):
            getattr(batches, name)

    assert batch_inner.accessed == []


def test_batch_retrieve_and_results_require_local_organization_ownership(db):
    first = Organization(name="Batch owner", slug=f"batch-owner-{id(db)}")
    second = Organization(name="Other owner", slug=f"other-owner-{id(db)}")
    db.add_all([first, second])
    db.flush()
    db.add(
        AnthropicBatchJob(
            batch_id="msgbatch-owned",
            organization_id=int(first.id),
            feature="cv_parse",
            model="claude-haiku-4-5",
            request_count=0,
            status="submitted",
            context={},
        )
    )
    db.commit()

    class _Batches:
        def __init__(self):
            self.calls: list[str] = []

        def retrieve(self, batch_id, **_kwargs):
            self.calls.append(f"retrieve:{batch_id}")
            return batch_id

        def results(self, batch_id, **_kwargs):
            self.calls.append(f"results:{batch_id}")
            return iter(())

    batch_inner = _Batches()
    messages = SimpleNamespace(batches=batch_inner)
    owner = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=messages),
        organization_id=int(first.id),
    )
    other = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=messages),
        organization_id=int(second.id),
    )

    assert owner.messages.batches.retrieve("msgbatch-owned") == "msgbatch-owned"
    with pytest.raises(UnsupportedAnthropicSurfaceError, match="not owned"):
        other.messages.batches.retrieve("msgbatch-owned")
    with pytest.raises(UnsupportedAnthropicSurfaceError, match="not owned"):
        list(other.messages.batches.results("msgbatch-owned"))
    assert batch_inner.calls == ["retrieve:msgbatch-owned"]


@pytest.mark.parametrize("name", ["with_raw_response", "with_streaming_response"])
def test_async_message_response_wrappers_are_blocked_before_inner_access(name):
    inner = _AsyncInner()
    client = MeteredAsyncAnthropic(inner=inner)

    with pytest.raises(UnsupportedAnthropicSurfaceError):
        getattr(client.messages, name)

    assert inner.messages.accessed == []


@pytest.mark.parametrize(
    "name",
    ["beta", "completions", "with_options", "post", "request", "inner"],
)
def test_async_top_level_paid_or_raw_surfaces_are_blocked(name):
    inner = _AsyncInner()
    client = MeteredAsyncAnthropic(inner=inner)

    with pytest.raises(UnsupportedAnthropicSurfaceError):
        getattr(client, name)

    assert inner.top_level_accessed == []


def test_async_stream_is_blocked_without_touching_provider():
    inner = _AsyncInner()
    client = MeteredAsyncAnthropic(inner=inner)

    with pytest.raises(UnsupportedAnthropicSurfaceError, match="streaming"):
        client.messages.stream(model="claude-haiku-4-5", max_tokens=10, messages=[])

    assert inner.messages.accessed == []


def test_sync_create_stream_true_is_blocked_without_touching_provider():
    inner = _SyncInner()
    client = MeteredAnthropicClient(inner=inner, organization_id=1)

    with pytest.raises(AnthropicRequestAdmissionError, match="streaming"):
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[],
            stream=True,
            metering={"feature": "other", "organization_id": 1},
        )

    assert inner.messages.calls == []


def test_async_create_stream_true_is_blocked_without_touching_provider():
    from app.services.metered_async_anthropic_client import (
        GraphMeteringContext,
        graph_metering_ctx,
    )

    inner = _AsyncInner()
    client = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(GraphMeteringContext(organization_id=1))
    try:
        with pytest.raises(AnthropicRequestAdmissionError, match="streaming"):
            asyncio.run(
                client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=10,
                    messages=[],
                    stream=True,
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    assert inner.messages.calls == []


def test_legacy_skip_cannot_authorize_sync_create_or_stream():
    class _Messages:
        def __init__(self):
            self.calls: list[str] = []

        def create(self, **_kwargs):
            self.calls.append("create")
            return SimpleNamespace(usage=None)

        def stream(self, **_kwargs):
            self.calls.append("stream")
            return object()

    messages = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=messages),
        organization_id=1,
    )
    request = {
        "model": "claude-haiku-4-5",
        "max_tokens": 10,
        "messages": [],
        "metering": {"feature": "other", "skip": True},
    }

    with pytest.raises(ProviderAttemptMarkerError, match="skip"):
        client.messages.create(**request)
    with pytest.raises(ProviderAttemptMarkerError, match="skip"):
        client.messages.stream(**request)

    assert messages.calls == []


def test_sync_wrapper_async_create_is_blocked_before_provider_access():
    messages = _Resource()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=messages),
        organization_id=1,
    )

    with pytest.raises(UnsupportedAnthropicSurfaceError, match="MeteredAsyncAnthropic"):
        asyncio.run(
            client.messages.acreate(
                model="claude-haiku-4-5",
                max_tokens=10,
                messages=[],
            )
        )

    assert messages.calls == []


@pytest.mark.parametrize("organization_id", [2, True, False, 0, -1, 1.5, "1"])
@pytest.mark.parametrize("operation", ["create", "stream"])
def test_bound_sync_client_cannot_be_retargeted_before_provider_access(
    organization_id,
    operation,
):
    class _Messages:
        def __init__(self):
            self.calls: list[str] = []

        def create(self, **_kwargs):
            self.calls.append("create")
            return SimpleNamespace(usage=None)

        def stream(self, **_kwargs):
            self.calls.append("stream")
            return object()

    messages = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=messages),
        organization_id=1,
    )
    request = {
        "model": "claude-haiku-4-5",
        "max_tokens": 10,
        "messages": [],
        "metering": {"feature": "other", "organization_id": organization_id},
    }

    with pytest.raises(ValueError, match="organization_id"):
        getattr(client.messages, operation)(**request)

    assert messages.calls == []
