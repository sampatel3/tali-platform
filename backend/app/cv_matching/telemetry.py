"""Per-call telemetry for the cv_matching pipeline.

Writes one structured row per call (cached and uncached) via a dedicated
Python logger. Infra routes the logger to wherever it needs to go (stdout
JSON, log file, Datadog tail) without this module needing to know.

PII rule: log only hashes (`cv_hash`, `jd_hash`) and metadata. Never log
full CV or JD text.

Persistence model:
- Default: log file at ``settings.cv_match_trace_log_path``.
- Fallback: in-process ring buffer of the last ``RING_CAPACITY`` traces.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runner import _RunContext
    from .schemas import ScoringStatus

trace_logger = logging.getLogger("taali.cv_match.trace")
trace_logger.propagate = False  # don't pollute the root logger with trace JSON

RING_CAPACITY = 500
_ring: deque[dict[str, Any]] = deque(maxlen=RING_CAPACITY)
_ring_lock = Lock()


def _trace_log_path() -> str:
    try:
        from ..platform.config import settings

        return getattr(settings, "CV_MATCH_TRACE_LOG_PATH", "") or os.environ.get(
            "CV_MATCH_TRACE_LOG_PATH", ""
        )
    except Exception:
        return os.environ.get("CV_MATCH_TRACE_LOG_PATH", "")


def emit_trace(
    ctx: "_RunContext",
    *,
    final_status: "ScoringStatus",
) -> None:
    """Append one structured trace row. Never raises."""
    try:
        from . import MODEL_VERSION, PROMPT_VERSION

        latency_ms = int((time.monotonic() - ctx.started_at) * 1000)
        row = {
            "trace_id": ctx.trace_id,
            "cv_hash": ctx.cv_hash,
            "jd_hash": ctx.jd_hash,
            "prompt_version": PROMPT_VERSION,
            "model_version": MODEL_VERSION,
            "input_tokens": ctx.input_tokens,
            "output_tokens": ctx.output_tokens,
            "latency_ms": latency_ms,
            "retry_count": ctx.retry_count,
            "validation_failures": ctx.validation_failures,
            "cache_hit": ctx.cache_hit,
            "final_status": getattr(final_status, "value", str(final_status)),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        with _ring_lock:
            _ring.append(row)

        path = _trace_log_path()
        if path:
            try:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
            except OSError as exc:
                trace_logger.warning("Failed to write trace to %s: %s", path, exc)

        trace_logger.info(json.dumps(row))
    except Exception as exc:  # pragma: no cover — defensive
        trace_logger.warning("emit_trace failed: %s", exc)


def recent_traces(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent traces for the admin endpoint. Newest first."""
    limit = max(1, min(int(limit), RING_CAPACITY))
    path = _trace_log_path()

    file_rows: list[dict[str, Any]] = []
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                tail = fh.readlines()[-limit:]
            for line in tail:
                line = line.strip()
                if not line:
                    continue
                try:
                    file_rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            file_rows = []

    if file_rows:
        file_rows.reverse()
        return file_rows[:limit]

    with _ring_lock:
        rows = list(_ring)
    rows.reverse()
    return rows[:limit]
