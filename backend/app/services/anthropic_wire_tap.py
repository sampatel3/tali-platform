"""Transport-level wire-tap for Anthropic ``/v1/messages`` requests.

**Why this exists.** Reconciliation against Anthropic billing kept
showing residual Haiku drift we couldn't fully attribute. Every prior
investigation reasoned about *which functions should* route through
``MeteredAnthropicClient`` — but that's inference, not measurement. A
call that constructs a bare client, or a library that wraps the SDK, or
the SDK's own internal retry, can all bill Anthropic without our
application-layer wrapper ever running.

This module measures the truth. It patches ``httpx.Client.send`` and
``httpx.AsyncClient.send`` — the single chokepoint *every* Anthropic
SDK request (sync, async, streaming, batches, retries) passes through —
and writes one ``AnthropicWireLog`` row per outbound request to
``api.anthropic.com/v1/messages*``.

Diff ``anthropic_wire_log`` against ``claude_call_log`` on
``anthropic_request_id``: wire rows with no matching call_log row are
metering bypasses, located exactly. Any *future* bypass shows up
immediately.

**Safety.** The hook:
- filters by host so non-Anthropic traffic (Workable, Voyage, Resend)
  pays only one cheap string check;
- reads the *request* body to extract the model (safe — already built)
  and the *response headers/status* (available before the body), but
  NEVER reads the response body — so streaming responses are untouched;
- records best-effort in its own short-lived DB session and never
  raises, so a logging failure can't break a Claude call;
- is idempotent and patches each ``send`` exactly once.
"""
from __future__ import annotations

import functools
import json
import logging
import atexit
import queue
import threading
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger("taali.anthropic_wire_tap")

_installed = False
_lock = threading.Lock()
_PATCH_FLAG = "_anthropic_wiretap_patched"
_WRITE_QUEUE: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=10_000)
_WRITER_THREAD: threading.Thread | None = None
_WRITER_STOP = threading.Event()
_WRITER_LOCK = threading.Lock()
_BATCH_SIZE = 100
_BATCH_WAIT_SECONDS = 0.25


def _is_anthropic_messages(request: httpx.Request) -> bool:
    try:
        host = request.url.host or ""
        path = request.url.path or ""
        return host.endswith("anthropic.com") and path.startswith("/v1/messages")
    except Exception:
        return False


def _model_from_request(request: httpx.Request) -> Optional[str]:
    """Extract ``model`` from the request body. Safe — the request is
    fully built by the time ``send`` runs, and reading ``request.content``
    doesn't consume anything the SDK still needs."""
    try:
        body = request.content
        if not body:
            return None
        data = json.loads(body)
        if isinstance(data, dict):
            m = data.get("model")
            return str(m) if m is not None else None
    except Exception:
        return None
    return None


def _is_stream_request(request: httpx.Request) -> bool:
    try:
        body = request.content
        if not body:
            return False
        data = json.loads(body)
        return bool(isinstance(data, dict) and data.get("stream"))
    except Exception:
        return False


def _write_batch(records: list[dict[str, Any]]) -> None:
    """Persist a batch in one short transaction.

    Wire logging is diagnostic and best-effort, but it must not add a database
    checkout and commit to the latency of every Anthropic attempt.
    """
    if not records:
        return
    try:
        from ..models.anthropic_wire_log import AnthropicWireLog
        from ..platform.database import SessionLocal

        with SessionLocal() as session:
            session.add_all(AnthropicWireLog(**record) for record in records)
            session.commit()
    except Exception:
        logger.debug(
            "anthropic_wire_tap: batch write failed rows=%d",
            len(records),
            exc_info=True,
        )


def _writer_loop() -> None:
    while not _WRITER_STOP.is_set() or _WRITE_QUEUE.unfinished_tasks:
        try:
            first = _WRITE_QUEUE.get(timeout=_BATCH_WAIT_SECONDS)
        except queue.Empty:
            continue
        batch = [first]
        while len(batch) < _BATCH_SIZE:
            try:
                batch.append(_WRITE_QUEUE.get_nowait())
            except queue.Empty:
                break
        try:
            _write_batch(batch)
        finally:
            for _ in batch:
                _WRITE_QUEUE.task_done()


def _ensure_writer() -> None:
    global _WRITER_THREAD
    with _WRITER_LOCK:
        if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
            return
        _WRITER_STOP.clear()
        _WRITER_THREAD = threading.Thread(
            target=_writer_loop,
            name="anthropic-wire-log-writer",
            daemon=True,
        )
        _WRITER_THREAD.start()


def flush(timeout: float = 5.0) -> bool:
    """Wait for queued diagnostic rows. Returns False on timeout."""
    deadline = time.monotonic() + max(float(timeout), 0.0)
    while _WRITE_QUEUE.unfinished_tasks:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


def shutdown(timeout: float = 5.0) -> None:
    """Flush queued rows during graceful API/worker process shutdown."""
    _WRITER_STOP.set()
    flush(timeout=timeout)
    thread = _WRITER_THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=max(float(timeout), 0.0))


def _record(
    *,
    model: Optional[str],
    request_id: Optional[str],
    path: Optional[str],
    status: Optional[int],
    method: Optional[str],
    is_stream: bool,
) -> None:
    """Queue one wire row without blocking the paid provider request."""
    record = {
        "model": model or "(unknown)",
        "anthropic_request_id": request_id,
        "path": path,
        "http_status": status,
        "method": method,
        "is_stream": bool(is_stream),
    }
    _ensure_writer()
    try:
        _WRITE_QUEUE.put_nowait(record)
    except queue.Full:
        # Preserve the ground-truth measurement under an exceptional burst.
        # A synchronous fallback is preferable to silently losing the exact
        # rows used to detect unmetered provider traffic.
        _write_batch([record])


def _response_request_id(response: Any) -> Optional[str]:
    try:
        headers = getattr(response, "headers", {}) or {}
        return headers.get("request-id") or headers.get("anthropic-request-id")
    except Exception:
        return None


def _patch_sync() -> None:
    orig = httpx.Client.send
    if getattr(orig, _PATCH_FLAG, False):
        return

    @functools.wraps(orig)
    def send(self: httpx.Client, request: httpx.Request, **kwargs: Any):
        if not _is_anthropic_messages(request):
            return orig(self, request, **kwargs)
        model = _model_from_request(request)
        is_stream = _is_stream_request(request)
        path = request.url.path
        method = request.method
        try:
            response = orig(self, request, **kwargs)
        except Exception:
            # Connection-level failure — no response, but the attempt
            # still left the process. Record status=None so the count
            # reflects every attempt (incl. ones Anthropic may bill).
            _record(model=model, request_id=None, path=path, status=None,
                    method=method, is_stream=is_stream)
            raise
        _record(
            model=model,
            request_id=_response_request_id(response),
            path=path,
            status=getattr(response, "status_code", None),
            method=method,
            is_stream=is_stream,
        )
        return response

    setattr(send, _PATCH_FLAG, True)
    httpx.Client.send = send  # type: ignore[method-assign]


def _patch_async() -> None:
    orig = httpx.AsyncClient.send
    if getattr(orig, _PATCH_FLAG, False):
        return

    @functools.wraps(orig)
    async def send(self: httpx.AsyncClient, request: httpx.Request, **kwargs: Any):
        if not _is_anthropic_messages(request):
            return await orig(self, request, **kwargs)
        model = _model_from_request(request)
        is_stream = _is_stream_request(request)
        path = request.url.path
        method = request.method
        try:
            response = await orig(self, request, **kwargs)
        except Exception:
            _record(model=model, request_id=None, path=path, status=None,
                    method=method, is_stream=is_stream)
            raise
        _record(
            model=model,
            request_id=_response_request_id(response),
            path=path,
            status=getattr(response, "status_code", None),
            method=method,
            is_stream=is_stream,
        )
        return response

    setattr(send, _PATCH_FLAG, True)
    httpx.AsyncClient.send = send  # type: ignore[method-assign]


def install() -> None:
    """Idempotently patch httpx so every Anthropic /v1/messages request
    writes a wire-log row. Call once at process startup (API lifespan +
    Celery worker init)."""
    global _installed
    with _lock:
        if _installed:
            return
        try:
            _patch_sync()
            _patch_async()
            _ensure_writer()
            _installed = True
            logger.info("anthropic wire-tap installed (httpx sync+async)")
        except Exception:
            # Never let instrumentation break boot.
            logger.exception("anthropic_wire_tap: install failed")


atexit.register(shutdown)
