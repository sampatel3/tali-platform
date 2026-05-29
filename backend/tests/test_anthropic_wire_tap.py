"""The transport-level wire-tap records every Anthropic /v1/messages
request — including ones that bypass MeteredAnthropicClient entirely.

This is the instrument that answers "is every Claude call logged?" by
measurement instead of inference. The key test is
``test_bare_client_bypass_is_still_caught``: a request made through a
RAW httpx client (simulating any metering bypass) still lands an
anthropic_wire_log row, because the tap sits at the transport layer.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.models.anthropic_wire_log import AnthropicWireLog
from app.platform.database import SessionLocal
from app.services import anthropic_wire_tap


@pytest.fixture
def wire_tap_installed(db):
    # Depend on ``db`` so the schema (incl. anthropic_wire_log) is created.
    anthropic_wire_tap.install()
    # Clean slate so row counts are deterministic.
    with SessionLocal() as s:
        s.query(AnthropicWireLog).delete()
        s.commit()
    yield
    with SessionLocal() as s:
        s.query(AnthropicWireLog).delete()
        s.commit()


def _mock_transport(captured: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"request-id": "req_test_123"},
            json={"id": "msg_1", "usage": {"input_tokens": 10, "output_tokens": 5}},
        )

    return httpx.MockTransport(handler)


def test_bare_client_bypass_is_still_caught(wire_tap_installed):
    """A request through a raw httpx client (no MeteredAnthropicClient,
    no claude_client_resolver) still writes a wire-log row. This is the
    whole point — the tap can't be bypassed by how the client is built."""
    captured: list[httpx.Request] = []
    client = httpx.Client(transport=_mock_transport(captured))
    resp = client.post(
        "https://api.anthropic.com/v1/messages",
        content=json.dumps({"model": "claude-haiku-4-5-20251001", "messages": []}),
    )
    assert resp.status_code == 200

    with SessionLocal() as s:
        rows = s.query(AnthropicWireLog).all()
        assert len(rows) == 1, f"expected 1 wire row, got {len(rows)}"
        row = rows[0]
        assert row.model == "claude-haiku-4-5-20251001"
        assert row.anthropic_request_id == "req_test_123"
        assert row.http_status == 200
        assert row.path == "/v1/messages"
        assert row.method == "POST"


def test_non_anthropic_requests_are_ignored(wire_tap_installed):
    """Traffic to other hosts (Workable, Voyage, Resend) must NOT write
    wire rows — the tap filters by host."""
    captured: list[httpx.Request] = []
    client = httpx.Client(transport=_mock_transport(captured))
    client.post("https://api.workable.com/spi/v3/candidates", content=b"{}")
    client.get("https://api.voyageai.com/v1/embeddings")

    with SessionLocal() as s:
        assert s.query(AnthropicWireLog).count() == 0


def test_stream_request_flagged(wire_tap_installed):
    """A streaming request (taali_chat) is recorded with is_stream=True,
    and the tap does NOT read the response body (no consumption)."""
    captured: list[httpx.Request] = []
    client = httpx.Client(transport=_mock_transport(captured))
    client.post(
        "https://api.anthropic.com/v1/messages",
        content=json.dumps(
            {"model": "claude-haiku-4-5-20251001", "messages": [], "stream": True}
        ),
    )
    with SessionLocal() as s:
        row = s.query(AnthropicWireLog).one()
        assert row.is_stream is True


@pytest.mark.asyncio
async def test_async_client_bypass_is_caught(wire_tap_installed):
    """The async transport hook (Graphiti's path) records too."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"request-id": "req_async_9"}, json={"id": "m"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await client.post(
            "https://api.anthropic.com/v1/messages",
            content=json.dumps({"model": "claude-haiku-4-5-20251001", "messages": []}),
        )

    with SessionLocal() as s:
        row = s.query(AnthropicWireLog).one()
        assert row.anthropic_request_id == "req_async_9"
        assert row.model == "claude-haiku-4-5-20251001"
