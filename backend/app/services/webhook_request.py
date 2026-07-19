"""Bounded raw-body reads for signed provider webhooks."""

from __future__ import annotations

from fastapi import HTTPException, Request


# Provider webhook envelopes contain identifiers and small event metadata, not
# files or transcripts. One MiB preserves generous headroom while bounding an
# unauthenticated caller's per-request memory allocation.
MAX_WEBHOOK_BODY_BYTES = 1024 * 1024


async def read_bounded_webhook_body(
    request: Request,
    *,
    max_bytes: int = MAX_WEBHOOK_BODY_BYTES,
) -> bytes:
    """Read at most ``max_bytes`` without trusting ``Content-Length`` alone."""

    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            content_length = int(raw_length)
        except ValueError:
            content_length = None
        if content_length is not None and content_length > max_bytes:
            raise HTTPException(status_code=413, detail="Webhook payload is too large")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise HTTPException(status_code=413, detail="Webhook payload is too large")
        body.extend(chunk)
    return bytes(body)


__all__ = ["MAX_WEBHOOK_BODY_BYTES", "read_bounded_webhook_body"]
