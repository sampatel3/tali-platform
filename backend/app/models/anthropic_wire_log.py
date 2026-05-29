"""Ground-truth log of every Anthropic ``/v1/messages`` HTTP request.

Written by ``services.anthropic_wire_tap``, which patches
``httpx.Client.send`` / ``httpx.AsyncClient.send`` at the transport
layer — *below* every Anthropic client (wrapped, bare, Graphiti, the
llm gateway) and below the SDK's internal retry loop. One row per
actual outbound HTTP request.

This is the measurement that ``claude_call_log`` (application-layer)
is checked against. Diff on ``anthropic_request_id``:

    wire rows with no matching claude_call_log row  →  metering bypass

Deliberately minimal: no token columns. The hook only reads response
headers + status (never the body), so it's safe for streaming
responses and adds negligible latency.
"""
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
)
from sqlalchemy.sql import func

from ..platform.database import Base


class AnthropicWireLog(Base):
    __tablename__ = "anthropic_wire_log"

    id = Column(BigInteger, primary_key=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Parsed from the request body (safe — request side, not response).
    model = Column(String, nullable=True)
    # From the response ``request-id`` header — joins to
    # ``claude_call_log.anthropic_request_id``.
    anthropic_request_id = Column(String, nullable=True)
    path = Column(String, nullable=True)        # /v1/messages, /v1/messages/batches, ...
    http_status = Column(Integer, nullable=True)
    method = Column(String, nullable=True)
    is_stream = Column(Boolean, default=False, nullable=False)
