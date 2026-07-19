"""Fail-closed Sentry configuration and event privacy boundary.

Sentry receives a newly constructed operational envelope, never the SDK's raw
event.  This deliberately trades rich request payloads and exception values
for stable routing, failure type, status, trace, timing, and bounded app-frame
context.  Those fields are sufficient to detect and locate regressions without
copying candidate, customer, credential, or provider data off-platform.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import StrEnum
from itertools import islice
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _BACKEND_ROOT / "app"
_APP_ROOT_RESOLVED = _APP_ROOT.resolve()

_MAX_APP_FRAMES = 12
_MAX_BREADCRUMBS = 20
_MAX_SPANS = 50
_MAX_MEASUREMENTS = 32
_MAX_FRAME_SCAN = 128
_MAX_BREADCRUMB_SCAN = 40
_MAX_SPAN_SCAN = 100
_IDENTITY_DIGEST_LENGTH = 10

_HEX_16 = re.compile(r"[0-9a-f]{16}", re.IGNORECASE)
_HEX_32 = re.compile(r"[0-9a-f]{32}", re.IGNORECASE)
_RELEASE = re.compile(
    r"(?:[0-9a-f]{7,40}|v?\d+\.\d+\.\d+|backend-\d{4}\.\d{1,2}\.\d{1,2})",
    re.IGNORECASE,
)
_ROUTE_SHAPE = re.compile(r"/[A-Za-z0-9_./{}:~-]{0,199}")
_TASK_SHAPE = re.compile(r"[A-Za-z0-9_.:-]{1,240}")
_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)
_HTTP_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
_LEVELS = frozenset({"debug", "info", "warning", "error", "fatal"})
_BREADCRUMB_TYPES = frozenset(
    {"default", "debug", "error", "http", "log", "navigation", "query", "system"}
)
_ENVIRONMENTS = frozenset(
    {"development", "local", "preview", "production", "staging", "test"}
)
_FIXED_ROUTE_IDENTITIES = frozenset(
    {"/api/docs", "/api/openapi.json", "/health", "/ready"}
)
_ROUTE_GROUPS = frozenset(
    {
        "admin",
        "agent",
        "api",
        "applications",
        "assessments",
        "auth",
        "billing",
        "bullhorn",
        "candidates",
        "healthz",
        "job-pages",
        "mcp",
        "organizations",
        "public",
        "requisitions",
        "roles",
        "tasks",
        "users",
        "workable",
    }
)
_TASK_MODULES = frozenset(
    {
        "app.tasks.agent_chat_tasks",
        "app.tasks.agent_tasks",
        "app.tasks.anthropic_batch_tasks",
        "app.tasks.application_ingest_tasks",
        "app.tasks.assessment_tasks",
        "app.tasks.automation_tasks",
        "app.tasks.brain_feed_tasks",
        "app.tasks.bullhorn_tasks",
        "app.tasks.calibration_tasks",
        "app.tasks.compliance_tasks",
        "app.tasks.corroboration_tasks",
        "app.tasks.decision_policy_tasks",
        "app.tasks.decision_tasks",
        "app.tasks.fireflies_tasks",
        "app.tasks.graph_ingest_tasks",
        "app.tasks.graph_outbox_tasks",
        "app.tasks.health_tasks",
        "app.tasks.outreach_tasks",
        "app.tasks.pool_rescore_tasks",
        "app.tasks.prescreen_tasks",
        "app.tasks.reconciliation_tasks",
        "app.tasks.reevaluation_tasks",
        "app.tasks.rubric_retry_tasks",
        "app.tasks.scoring_batch_recovery_tasks",
        "app.tasks.scoring_tasks",
        "app.tasks.sister_role_tasks",
        "app.tasks.synthetic",
        "app.tasks.threshold_calibration_tasks",
        "app.tasks.wire_log_tasks",
        "app.tasks.workable_provider_tasks",
        "app.tasks.workable_tasks",
    }
)
_EXCEPTION_TYPES = frozenset(
    {
        "APIError",
        "AssertionError",
        "CancelledError",
        "ConnectionError",
        "ConnectError",
        "DatabaseError",
        "FileNotFoundError",
        "HTTPException",
        "HTTPStatusError",
        "ImportError",
        "IntegrityError",
        "KeyError",
        "ModuleNotFoundError",
        "OperationalError",
        "OSError",
        "PermissionError",
        "RateLimitError",
        "ReadTimeout",
        "RuntimeError",
        "SQLAlchemyError",
        "TimeoutError",
        "TimeoutException",
        "TypeError",
        "ValidationError",
        "ValueError",
    }
)
_EXCEPTION_MODULE_PREFIXES = (
    ("app.", "app"),
    ("anthropic.", "anthropic"),
    ("asyncio.", "asyncio"),
    ("celery.", "celery"),
    ("fastapi.", "fastapi"),
    ("httpx.", "httpx"),
    ("pydantic.", "pydantic"),
    ("pydantic_core.", "pydantic_core"),
    ("sqlalchemy.", "sqlalchemy"),
    ("starlette.", "starlette"),
)
_MECHANISM_TYPES = frozenset(
    {"asgi", "asyncio", "celery", "chained", "excepthook", "generic", "logging", "starlette", "threading"}
)
_TRACE_OPS = frozenset(
    {
        "celery.task",
        "db",
        "db.sql.query",
        "function",
        "http.client",
        "http.server",
        "middleware.fastapi",
        "middleware.starlette",
        "queue.process",
        "queue.task.celery",
        "task",
    }
)
_TRACE_STATUSES = frozenset(
    {
        "aborted",
        "already_exists",
        "cancelled",
        "data_loss",
        "deadline_exceeded",
        "failed_precondition",
        "internal_error",
        "invalid_argument",
        "not_found",
        "ok",
        "out_of_range",
        "permission_denied",
        "resource_exhausted",
        "unauthenticated",
        "unavailable",
        "unimplemented",
        "unknown_error",
    }
)
_TRACE_ORIGINS = frozenset(
    {
        "auto.celery",
        "auto.db.sqlalchemy",
        "auto.function",
        "auto.http.fastapi",
        "auto.http.httpx",
        "auto.http.starlette",
        "auto.queue.celery",
        "manual",
    }
)
_BREADCRUMB_CATEGORY_ALIASES = {
    "celery": "celery",
    "console": "console",
    "httplib": "httplib",
    "httpx": "httpx",
    "query": "query",
    "redis": "redis",
    "subprocess": "subprocess",
}
_BREADCRUMB_CATEGORY_PREFIXES = (
    ("app.", "app"),
    ("celery.", "celery"),
    ("httpcore.", "httpcore"),
    ("sentry_sdk.", "sentry_sdk"),
    ("sqlalchemy.", "sqlalchemy"),
    ("urllib3.", "urllib3"),
    ("uvicorn.", "uvicorn"),
)
_MEASUREMENT_NAMES = frozenset(
    {
        "cls",
        "fcp",
        "fid",
        "frames_frozen",
        "frames_slow",
        "frames_total",
        "http.request_content_length",
        "http.response_content_length",
        "inp",
        "lcp",
        "stall_count",
        "stall_percentage",
        "ttfb",
    }
)
_MEASUREMENT_UNITS = frozenset(
    {"byte", "millisecond", "nanosecond", "none", "percent", "ratio", "second"}
)


class OperationalAlert(StrEnum):
    """Stable, non-sensitive alert identities accepted by Sentry."""

    ANTHROPIC_RECONCILIATION_DRIFT = "anthropic_reconciliation_drift_alert"
    ANTHROPIC_RECONCILIATION_SETTLEMENT = (
        "anthropic_reconciliation_settlement_window"
    )
    ASSESSMENT_PROVISIONING_UNHEALTHY = "assessment_provisioning_unhealthy"


_OPERATIONAL_METRIC_KEYS = {
    OperationalAlert.ANTHROPIC_RECONCILIATION_DRIFT.value: frozenset(
        {"affected_rows", "drift_percent", "threshold_percent"}
    ),
    OperationalAlert.ANTHROPIC_RECONCILIATION_SETTLEMENT.value: frozenset(
        {"lookback_days", "material_percent", "max_age_days"}
    ),
    OperationalAlert.ASSESSMENT_PROVISIONING_UNHEALTHY.value: frozenset(
        {"status_code"}
    ),
}
_OPERATIONAL_ALERT_VALUES = frozenset(alert.value for alert in OperationalAlert)

_worker_sentry_lock = threading.Lock()
_worker_sentry_initialized = False


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_match(value: object, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        return None
    return value


def _plain_sequence(value: object) -> list[Any] | tuple[Any, ...] | None:
    return value if type(value) in (list, tuple) else None


def _safe_choice(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _identity_digest(value: str) -> str:
    """Return a stable opaque grouping key for a bounded route/task identity."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_IDENTITY_DIGEST_LENGTH]


def _drop_hint_attachments(hint: object) -> None:
    # Client.capture_event always supplies a plain dict. Scope.apply_to_event
    # appends scope attachments to this same list before either callback runs.
    if type(hint) is dict:
        hint["attachments"] = []


def _safe_level(value: object) -> str | None:
    return value if isinstance(value, str) and value in _LEVELS else None


def _safe_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _safe_timestamp(value: object) -> int | float | str | None:
    if isinstance(value, datetime):
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return _safe_number(normalized.timestamp())
    number = _safe_number(value)
    if number is not None:
        return number
    return _safe_match(value, _ISO_TIMESTAMP)


def _safe_status_code(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 100 <= value <= 599 else None


def _safe_method(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    method = value.upper()
    return method if method in _HTTP_METHODS else None


def _safe_transaction_identity(
    event: Mapping[str, Any],
) -> tuple[str | None, dict[str, str] | None]:
    source = _mapping(event.get("transaction_info")).get("source")
    transaction = event.get("transaction")
    if source == "route" and isinstance(transaction, str):
        if transaction in _FIXED_ROUTE_IDENTITIES:
            return transaction, {"source": "route"}
        if _ROUTE_SHAPE.fullmatch(transaction):
            digest = _identity_digest(transaction)
            group = transaction.lstrip("/").split("/", 1)[0]
            if group in _ROUTE_GROUPS:
                return f"/{group}/<route-{digest}>", {"source": "route"}
            return f"<route-{digest}>", {"source": "route"}
    if source == "task" and isinstance(transaction, str) and _TASK_SHAPE.fullmatch(transaction):
        digest = _identity_digest(transaction)
        parts = transaction.split(".")
        module = ".".join(parts[:3]) if len(parts) >= 4 else ""
        if module in _TASK_MODULES:
            return f"{module}.<task-{digest}>", {"source": "task"}
        return f"<task-{digest}>", {"source": "task"}
    if "transaction" in event:
        unmatched = "<unmatched-task>" if source == "task" else "<unmatched-route>"
        return unmatched, {"source": "sanitized"}
    return None, None


def _safe_app_filename(frame: Mapping[str, Any]) -> str | None:
    for raw in (frame.get("filename"), frame.get("abs_path")):
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            continue
        try:
            candidate = _BACKEND_ROOT / raw if raw.replace("\\", "/").startswith("app/") else Path(raw)
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(_APP_ROOT_RESOLVED).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        safe = f"app/{relative}"
        if len(safe) <= 240 and safe.endswith(".py") and resolved.is_file():
            return safe
    return None


def _safe_frame(frame: object) -> dict[str, Any] | None:
    source = _mapping(frame)
    filename = _safe_app_filename(source)
    if filename is None:
        return None

    result: dict[str, Any] = {
        "filename": filename,
        "in_app": True,
        "module": filename.removesuffix(".py").replace("/", "."),
    }
    lineno = source.get("lineno")
    if isinstance(lineno, int) and not isinstance(lineno, bool) and 0 < lineno < 10_000_000:
        result["lineno"] = lineno
    return result


def _safe_stacktrace(value: object) -> dict[str, list[dict[str, Any]]] | None:
    frames = _plain_sequence(_mapping(value).get("frames"))
    if frames is None:
        return None
    safe_frames = [
        safe
        for frame in frames[-_MAX_FRAME_SCAN:]
        if (safe := _safe_frame(frame)) is not None
    ]
    if not safe_frames:
        return None
    return {"frames": safe_frames[-_MAX_APP_FRAMES:]}


def _safe_trace_context(value: object) -> dict[str, Any] | None:
    source = _mapping(value)
    result: dict[str, Any] = {}
    for key, pattern in (
        ("trace_id", _HEX_32),
        ("span_id", _HEX_16),
        ("parent_span_id", _HEX_16),
    ):
        safe = _safe_match(source.get(key), pattern)
        if safe is not None:
            result[key] = safe.lower()
    for key, allowed in (
        ("op", _TRACE_OPS),
        ("status", _TRACE_STATUSES),
        ("origin", _TRACE_ORIGINS),
    ):
        safe = _safe_choice(source.get(key), allowed)
        if safe is not None:
            result[key] = safe
    return result or None


def _safe_contexts(value: object) -> dict[str, Any] | None:
    source = _mapping(value)
    result: dict[str, Any] = {}
    trace = _safe_trace_context(source.get("trace"))
    if trace is not None:
        result["trace"] = trace
    status_code = _safe_status_code(_mapping(source.get("response")).get("status_code"))
    if status_code is not None:
        result["response"] = {"status_code": status_code}
    return result or None


def _safe_request(value: object) -> dict[str, str] | None:
    method = _safe_method(_mapping(value).get("method"))
    return {"method": method} if method is not None else None


def _stable_operation(event: Mapping[str, Any]) -> str:
    tags = _mapping(event.get("tags"))
    tagged = tags.get("operation")
    if tagged in _OPERATIONAL_ALERT_VALUES:
        return tagged
    return "application_alert"


def _safe_operational_metrics(value: object, operation: str) -> dict[str, int | float]:
    allowed = _OPERATIONAL_METRIC_KEYS.get(operation, frozenset())
    source = _mapping(_mapping(value).get("operational"))
    result: dict[str, int | float] = {}
    for key in allowed:
        number = _safe_number(source.get(key))
        if number is not None:
            result[key] = number
    return result


def _safe_exception(value: object) -> dict[str, list[dict[str, Any]]] | None:
    values = _plain_sequence(_mapping(value).get("values"))
    if values is None:
        return None
    outer = _mapping(values[-1]) if values else {}
    if not outer:
        return None

    raw_type = outer.get("type")
    result: dict[str, Any] = {
        "type": raw_type if raw_type in _EXCEPTION_TYPES else "Exception"
    }
    raw_module = outer.get("module")
    if raw_module == "builtins":
        result["module"] = "builtins"
    elif isinstance(raw_module, str):
        for prefix, identity in _EXCEPTION_MODULE_PREFIXES:
            if raw_module.startswith(prefix):
                result["module"] = identity
                break

    mechanism_source = _mapping(outer.get("mechanism"))
    mechanism: dict[str, Any] = {}
    mechanism_type = _safe_choice(mechanism_source.get("type"), _MECHANISM_TYPES)
    if mechanism_type is not None:
        mechanism["type"] = mechanism_type
    if isinstance(mechanism_source.get("handled"), bool):
        mechanism["handled"] = mechanism_source["handled"]
    if mechanism:
        result["mechanism"] = mechanism

    stacktrace = _safe_stacktrace(outer.get("stacktrace"))
    if stacktrace is not None:
        result["stacktrace"] = stacktrace
    return {"values": [result]} if result else None


def sanitize_breadcrumb(crumb: object, _hint: object = None) -> dict[str, Any] | None:
    """Return only bounded breadcrumb metadata; malformed inputs are discarded."""

    try:
        source = _mapping(crumb)
        if not source:
            return None
        result: dict[str, Any] = {}
        timestamp = _safe_timestamp(source.get("timestamp"))
        crumb_type = source.get("type")
        raw_category = source.get("category")
        category = _BREADCRUMB_CATEGORY_ALIASES.get(raw_category)
        if category is None and isinstance(raw_category, str):
            for prefix, identity in _BREADCRUMB_CATEGORY_PREFIXES:
                if raw_category.startswith(prefix):
                    category = identity
                    break
        level = _safe_level(source.get("level"))
        if timestamp is not None:
            result["timestamp"] = timestamp
        if isinstance(crumb_type, str) and crumb_type in _BREADCRUMB_TYPES:
            result["type"] = crumb_type
        if category is not None:
            result["category"] = category
        if level is not None:
            result["level"] = level

        data_source = _mapping(source.get("data"))
        data: dict[str, Any] = {}
        method = _safe_method(data_source.get("method"))
        status_code = _safe_status_code(data_source.get("status_code"))
        if method is not None:
            data["method"] = method
        if status_code is not None:
            data["status_code"] = status_code
        if data:
            result["data"] = data
        return result or None
    except Exception:
        return None


def _safe_breadcrumbs(value: object) -> dict[str, list[dict[str, Any]]] | None:
    values = _plain_sequence(_mapping(value).get("values"))
    if values is None:
        return None
    safe = [
        item
        for value in values[-_MAX_BREADCRUMB_SCAN:]
        if (item := sanitize_breadcrumb(value)) is not None
    ]
    return {"values": safe[-_MAX_BREADCRUMBS:]} if safe else None


def _base_event(event: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"platform": "python"}
    event_id = _safe_match(event.get("event_id"), _HEX_32)
    timestamp = _safe_timestamp(event.get("timestamp"))
    level = _safe_level(event.get("level"))
    if event_id is not None:
        result["event_id"] = event_id.lower()
    if timestamp is not None:
        result["timestamp"] = timestamp
    if level is not None:
        result["level"] = level
    release = _safe_match(event.get("release"), _RELEASE)
    environment = _safe_choice(event.get("environment"), _ENVIRONMENTS)
    if release is not None:
        result["release"] = release
    if environment is not None:
        result["environment"] = environment
    return result


def _add_common_fields(result: dict[str, Any], event: Mapping[str, Any]) -> None:
    transaction, transaction_info = _safe_transaction_identity(event)
    if transaction is not None:
        result["transaction"] = transaction
    if transaction_info is not None:
        result["transaction_info"] = transaction_info
    for key, sanitizer in (
        ("contexts", _safe_contexts),
        ("request", _safe_request),
        ("breadcrumbs", _safe_breadcrumbs),
    ):
        safe = sanitizer(event.get(key))
        if safe is not None:
            result[key] = safe


def _privacy_fallback(kind: str) -> dict[str, Any]:
    return {
        "platform": "python",
        "level": "error",
        "message": f"sentry_privacy_{kind}_fallback",
        "tags": {"operation": "sentry_privacy_fallback"},
        "fingerprint": ["sentry_privacy_fallback", kind],
    }


def sanitize_error_event(event: object, _hint: object = None) -> dict[str, Any] | None:
    """Rebuild an error/message event from safe operational metadata only."""

    _drop_hint_attachments(_hint)
    try:
        source = _mapping(event)
        if not source:
            return None
        result = _base_event(source)
        _add_common_fields(result, source)
        exception = _safe_exception(source.get("exception"))
        if exception is not None:
            result["exception"] = exception
        else:
            operation = _stable_operation(source)
            result["message"] = operation
            result["tags"] = {"operation": operation}
            result["fingerprint"] = [operation]
            metrics = _safe_operational_metrics(source.get("contexts"), operation)
            if metrics:
                contexts = result.setdefault("contexts", {})
                contexts["operational"] = {"operation": operation, **metrics}
        return result
    except Exception:
        return _privacy_fallback("error")


def _safe_span(value: object) -> dict[str, Any] | None:
    source = _mapping(value)
    result: dict[str, Any] = {}
    for key, pattern in (
        ("trace_id", _HEX_32),
        ("span_id", _HEX_16),
        ("parent_span_id", _HEX_16),
    ):
        safe = _safe_match(source.get(key), pattern)
        if safe is not None:
            result[key] = safe.lower()
    for key, allowed in (
        ("op", _TRACE_OPS),
        ("status", _TRACE_STATUSES),
        ("origin", _TRACE_ORIGINS),
    ):
        safe = _safe_choice(source.get(key), allowed)
        if safe is not None:
            result[key] = safe
    for key in ("start_timestamp", "timestamp"):
        safe = _safe_timestamp(source.get(key))
        if safe is not None:
            result[key] = safe
    if isinstance(source.get("same_process_as_parent"), bool):
        result["same_process_as_parent"] = source["same_process_as_parent"]
    return result or None


def _safe_measurements(value: object) -> dict[str, dict[str, Any]] | None:
    if type(value) is not dict:
        return None
    source = value
    result: dict[str, dict[str, Any]] = {}
    for raw_name, raw_measurement in islice(source.items(), _MAX_MEASUREMENTS):
        name = _safe_choice(raw_name, _MEASUREMENT_NAMES)
        measurement = _mapping(raw_measurement)
        number = _safe_number(measurement.get("value"))
        if name is None or number is None:
            continue
        safe: dict[str, Any] = {"value": number}
        unit = _safe_choice(measurement.get("unit"), _MEASUREMENT_UNITS)
        if unit is not None:
            safe["unit"] = unit
        result[name] = safe
    return result or None


def sanitize_transaction_event(event: object, _hint: object = None) -> dict[str, Any] | None:
    """Rebuild a transaction with bounded timings and no payload descriptions."""

    _drop_hint_attachments(_hint)
    try:
        source = _mapping(event)
        if not source:
            return None
        result = _base_event(source)
        result["type"] = "transaction"
        _add_common_fields(result, source)
        for key in ("start_timestamp", "timestamp"):
            safe = _safe_timestamp(source.get(key))
            if safe is not None:
                result[key] = safe

        raw_spans = source.get("spans")
        if (raw_spans := _plain_sequence(raw_spans)) is not None:
            spans = [
                safe
                for span in raw_spans[-_MAX_SPAN_SCAN:]
                if (safe := _safe_span(span)) is not None
            ]
            if spans:
                result["spans"] = spans[-_MAX_SPANS:]
        measurements = _safe_measurements(source.get("measurements"))
        if measurements is not None:
            result["measurements"] = measurements
        return result
    except Exception:
        return _privacy_fallback("transaction")


def capture_operational_alert(
    operation: OperationalAlert,
    *,
    level: Literal["debug", "info", "warning", "error", "fatal"] = "error",
    metrics: Mapping[str, int | float] | None = None,
) -> str | None:
    """Capture a stable alert identity and its allowlisted finite metrics."""

    try:
        import sentry_sdk

        safe_metrics = {
            key: value
            for key in _OPERATIONAL_METRIC_KEYS[operation.value]
            if (value := _safe_number(_mapping(metrics).get(key))) is not None
        }
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("operation", operation.value)
            if safe_metrics:
                scope.set_context(
                    "operational",
                    {"operation": operation.value, **safe_metrics},
                )
            return sentry_sdk.capture_message(operation.value, level=level)
    except Exception:
        return None


def _valid_dsn(dsn: str | None) -> bool:
    if not isinstance(dsn, str) or len(dsn) > 2048:
        return False
    try:
        parsed = urlsplit(dsn)
        project = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.username
            and project.isdigit()
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _initialize_sentry_client(
    dsn: str,
    *,
    integrations: list[object],
    traces_sample_rate: float,
    server_name: str,
) -> None:
    import sentry_sdk
    from sentry_sdk.integrations.argv import ArgvIntegration
    from sentry_sdk.integrations.modules import ModulesIntegration
    from sentry_sdk.scrubber import EventScrubber

    sample_rate = _safe_number(traces_sample_rate)
    if sample_rate is None or not 0 <= sample_rate <= 1:
        sample_rate = 0.0
    sentry_sdk.init(
        dsn=dsn,
        integrations=integrations,
        default_integrations=True,
        auto_enabling_integrations=False,
        disabled_integrations=[ArgvIntegration, ModulesIntegration],
        traces_sample_rate=sample_rate,
        profiles_sample_rate=0.0,
        stream_gen_ai_spans=False,
        send_default_pii=False,
        include_local_variables=False,
        include_source_context=False,
        max_request_body_size="never",
        attach_stacktrace=False,
        event_scrubber=EventScrubber(recursive=True, send_default_pii=False),
        before_send=sanitize_error_event,
        before_send_transaction=sanitize_transaction_event,
        before_breadcrumb=sanitize_breadcrumb,
        max_breadcrumbs=_MAX_BREADCRUMBS,
        max_value_length=256,
        server_name=server_name,
        project_root=str(_BACKEND_ROOT),
        in_app_include=["app"],
        enable_db_query_source=False,
        trace_propagation_targets=[],
    )


def initialize_sentry(
    dsn: str | None,
    *,
    traces_sample_rate: float = 0.1,
) -> bool:
    """Initialize the web SDK with privacy- and cost-bounded integrations."""

    if not _valid_dsn(dsn):
        return False

    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    failed_server_responses = set(range(500, 600))
    captured_http_methods = tuple(sorted(_HTTP_METHODS))
    _initialize_sentry_client(
        dsn,
        integrations=[
            FastApiIntegration(
                transaction_style="url",
                failed_request_status_codes=failed_server_responses,
                middleware_spans=False,
                http_methods_to_capture=captured_http_methods,
            ),
            StarletteIntegration(
                transaction_style="url",
                failed_request_status_codes=failed_server_responses,
                middleware_spans=False,
                http_methods_to_capture=captured_http_methods,
            ),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=traces_sample_rate,
        server_name="tali-backend",
    )
    return True


def initialize_worker_sentry(
    dsn: str | None,
    *,
    traces_sample_rate: float = 0.1,
) -> bool:
    """Initialize Sentry once from a Celery worker lifecycle signal."""

    if not _valid_dsn(dsn):
        return False

    global _worker_sentry_initialized
    with _worker_sentry_lock:
        if _worker_sentry_initialized:
            return True

        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        _initialize_sentry_client(
            dsn,
            integrations=[
                CeleryIntegration(propagate_traces=False, monitor_beat_tasks=False),
                SqlalchemyIntegration(),
            ],
            traces_sample_rate=traces_sample_rate,
            server_name="tali-worker",
        )
        _worker_sentry_initialized = True
    return True


__all__ = [
    "OperationalAlert",
    "capture_operational_alert",
    "initialize_sentry",
    "initialize_worker_sentry",
    "sanitize_breadcrumb",
    "sanitize_error_event",
    "sanitize_transaction_event",
]
