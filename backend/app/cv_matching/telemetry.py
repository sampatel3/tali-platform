"""Per-call telemetry for the cv_matching pipeline.

Writes one structured row per call (cached and uncached) via a dedicated
Python logger. Infra routes the logger to wherever it needs to go (stdout
JSON, log file, Datadog tail) without this module needing to know.

PII rule: log only hashes (`cv_hash`, `jd_hash`) and metadata. Never log
full CV or JD text.

Persistence model:
- Default: log file at ``settings.cv_match_trace_log_path``.
- Fallback: central JSON logging plus the last ``RING_CAPACITY`` traces in memory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runner import _RunContext
    from .schemas import ScoringStatus

trace_logger = logging.getLogger("taali.cv_match.trace")
# No local handler: fallback traces flow through the application's one central
# JSON handler. A configured trace file suppresses that fallback, so each row
# has one operational sink rather than duplicate file + stdout emission.
trace_logger.propagate = True

RING_CAPACITY = 500
_ring: deque[dict[str, Any]] = deque(maxlen=RING_CAPACITY)
_ring_lock = Lock()
_TRACE_IDENTIFIER = re.compile(r"[A-Za-z0-9._-]+")


def _safe_identifier(value: object, *, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        return "unknown"
    return value if _TRACE_IDENTIFIER.fullmatch(value) else "unknown"


def _safe_count(value: object, *, limit: int = 1_000_000_000) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return max(0, min(value, limit))


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

        latency_ms = max(0, int((time.monotonic() - ctx.started_at) * 1000))
        status = getattr(final_status, "value", None)
        if status not in {"ok", "deferred", "failed"}:
            status = "unknown"
        row = {
            "trace_id": _safe_identifier(ctx.trace_id, limit=64),
            "cv_hash": _safe_identifier(ctx.cv_hash, limit=128),
            "jd_hash": _safe_identifier(ctx.jd_hash, limit=128),
            "prompt_version": _safe_identifier(PROMPT_VERSION, limit=128),
            "model_version": _safe_identifier(MODEL_VERSION, limit=128),
            "input_tokens": _safe_count(ctx.input_tokens),
            "output_tokens": _safe_count(ctx.output_tokens),
            # Anthropic prompt-cache accounting. ``cache_read_tokens`` are
            # billed at ~10% of the standard input rate; high cache_read
            # vs cache_creation ratios indicate the static role block is
            # staying warm across candidates. Roll these up per role to
            # tune the cache_control TTL.
            "cache_read_tokens": _safe_count(ctx.cache_read_tokens),
            "cache_creation_tokens": _safe_count(ctx.cache_creation_tokens),
            "latency_ms": _safe_count(latency_ms),
            "retry_count": _safe_count(ctx.retry_count, limit=100),
            "validation_failures": _safe_count(ctx.validation_failures, limit=100),
            "cache_hit": bool(ctx.cache_hit),
            "final_status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        with _ring_lock:
            _ring.append(row)

        path = _trace_log_path()
        wrote_file = False
        if path:
            try:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
                wrote_file = True
            except OSError as exc:
                trace_logger.warning(
                    "Failed to write trace error_type=%s",
                    type(exc).__name__,
                )

        if not wrote_file:
            trace_logger.info(
                "CV match trace trace_id=%s cv_hash=%s jd_hash=%s prompt_version=%s "
                "model_version=%s input_tokens=%s output_tokens=%s cache_read_tokens=%s "
                "cache_creation_tokens=%s latency_ms=%s retry_count=%s "
                "validation_failures=%s cache_hit=%s final_status=%s created_at=%s",
                row["trace_id"], row["cv_hash"], row["jd_hash"],
                row["prompt_version"], row["model_version"], row["input_tokens"],
                row["output_tokens"], row["cache_read_tokens"],
                row["cache_creation_tokens"], row["latency_ms"], row["retry_count"],
                row["validation_failures"], row["cache_hit"], row["final_status"],
                row["created_at"],
            )
    except Exception as exc:  # pragma: no cover — defensive
        trace_logger.warning(
            "emit_trace failed error_type=%s",
            type(exc).__name__,
        )


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
