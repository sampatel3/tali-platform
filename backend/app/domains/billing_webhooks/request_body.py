"""Bounded raw-body handling for signed provider webhooks."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request


MAX_SIGNED_WEBHOOK_BODY_BYTES = 1024 * 1024
_OVERSIZED_DETAIL = "Webhook payload exceeds the 1 MiB limit"


def _declared_content_length(
    request: Request,
    *,
    max_bytes: int,
) -> int | None:
    """Return a bounded Content-Length, rejecting ambiguous declarations."""
    raw_values = request.headers.getlist("content-length")
    if not raw_values:
        return None

    # ASGI servers may preserve duplicate fields or combine them with commas.
    # Accept repeated identical values, but never choose between conflicting
    # declarations because that creates request-smuggling ambiguity.
    tokens = [token.strip() for value in raw_values for token in value.split(",")]
    if not tokens or any(
        not token.isascii() or not token.isdecimal() for token in tokens
    ):
        raise HTTPException(status_code=400, detail="Invalid Content-Length header")

    # Compare normalized decimal strings before calling ``int``.  Python caps
    # decimal-to-int conversions, so a syntactically numeric header containing
    # thousands of digits must become a bounded 413 rather than an uncaught 500.
    normalized = {token.lstrip("0") or "0" for token in tokens}
    if len(normalized) != 1:
        raise HTTPException(status_code=400, detail="Conflicting Content-Length headers")
    declared = normalized.pop()
    maximum = str(max_bytes)
    if len(declared) > len(maximum) or (
        len(declared) == len(maximum) and declared > maximum
    ):
        raise HTTPException(status_code=413, detail=_OVERSIZED_DETAIL)
    return int(declared)


async def read_signed_webhook_body(
    request: Request,
    *,
    max_bytes: int = MAX_SIGNED_WEBHOOK_BODY_BYTES,
) -> bytes:
    """Read the exact signed bytes without buffering more than ``max_bytes``."""
    _declared_content_length(request, max_bytes=max_bytes)

    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > max_bytes - len(body):
            raise HTTPException(status_code=413, detail=_OVERSIZED_DETAIL)
        body.extend(chunk)
    return bytes(body)


def parse_signed_webhook_json(body: bytes) -> dict[str, Any]:
    """Parse a provider object directly from the bytes used for verification."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    return payload
