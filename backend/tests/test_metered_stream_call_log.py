"""Stream path must write a claude_call_log row.

Pre-#387 the wrapper's ``_MeteredStreamCtx.__exit__`` wrote a
usage_event but silently skipped the claude_call_log row — breaking
the #237 "every call writes a call_log row" invariant for the stream
path. Only ``taali_chat`` streams in prod today (small volume) but
the gap was real and any future streaming caller would have widened it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.services.metered_anthropic_client import (
    MeteredAnthropicClient,
    _MeteredStreamCtx,
)
from app.services.pricing_service import Feature


@dataclass
class _FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _FakeFinalMessage:
    usage: _FakeUsage


class _FakeStream:
    """The object yielded by ``with client.messages.stream(...) as stream``.
    Only needs ``get_final_message`` for the wrapper's metering hook."""

    def __init__(self, *, usage: _FakeUsage):
        self._usage = usage

    def get_final_message(self) -> _FakeFinalMessage:
        return _FakeFinalMessage(usage=self._usage)


class _FakeStreamCM:
    def __init__(self, *, usage: _FakeUsage):
        self._usage = usage

    def __enter__(self):
        return _FakeStream(usage=self._usage)

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeMessages:
    def __init__(self, *, usage: _FakeUsage):
        self._usage = usage

    def stream(self, **_: Any) -> _FakeStreamCM:
        return _FakeStreamCM(usage=self._usage)


class _FakeAnthropic:
    def __init__(self, *, usage: _FakeUsage):
        self.messages = _FakeMessages(usage=usage)


def test_stream_exit_writes_call_log_row(db):
    """Driving a streaming call through MeteredAnthropicClient writes a
    claude_call_log row on the way out — with real tokens, FK-linked to
    the usage_event we already wrote."""
    org = Organization(name="O", slug=f"o-{id(db)}-stream")
    db.add(org); db.commit()

    inner = _FakeAnthropic(
        usage=_FakeUsage(input_tokens=512, output_tokens=128)
    )
    client = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        messages=[],
        metering={"feature": Feature.TAALI_CHAT},
    ) as _stream:
        # Caller drains the stream — we don't iterate in this test, the
        # wrapper's __exit__ is what matters.
        pass

    from app.platform.database import SessionLocal
    with SessionLocal() as s:
        rows = s.query(ClaudeCallLog).filter(
            ClaudeCallLog.organization_id == int(org.id),
            ClaudeCallLog.model == "claude-haiku-4-5-20251001",
        ).all()
        # ONE row per stream call.
        assert len(rows) == 1, f"expected 1 call_log row, got {len(rows)}"
        row = rows[0]
        assert row.input_tokens == 512
        assert row.output_tokens == 128
        assert row.feature_hint == "taali_chat"
        # FK-linked to the usage_event written by the same exit hook.
        assert row.usage_event_id is not None
        # Clean up so other tests have an empty table.
        s.query(ClaudeCallLog).delete()
        s.commit()
