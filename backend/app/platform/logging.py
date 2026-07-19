import hashlib
import json
import logging
import math
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID

from ..platform.request_context import get_request_id, normalize_request_id


_MAX_EXCEPTION_FRAMES = 12
_MAX_LOG_VALUE_DEPTH = 8
_MAX_LOG_CONTAINER_ITEMS = 32
_MAX_LOG_SCALAR_LENGTH = 512
_MAX_LOG_MESSAGE_LENGTH = 2048
_MAX_LOG_RECORD_LENGTH = 4096
_MAX_LOGGER_NAME_LENGTH = 160
_MAX_TASK_NAME_LENGTH = 200
_MAX_TASK_ID_LENGTH = 128
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_LOGGER_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]*")
_TASK_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]*")
_TASK_ID_RE = re.compile(r"[A-Za-z0-9_\-]+")
_EXCEPTION_REPR_RE = re.compile(r"(?:^|: )(?P<type>[A-Za-z_][A-Za-z0-9_.]{0,127})\(")
_EXCEPTION_TYPE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_ROUTE_TEMPLATE_RE = re.compile(r"/[A-Za-z0-9_./{}:\-]*")
_VALIDATION_TYPE_RE = re.compile(r"[a-z][a-z0-9_.\-]{0,63}")
_URL_SCHEME_RE = re.compile(r"[a-z][a-z0-9+.\-]{0,31}")
_URL_HOST_RE = re.compile(r"[A-Za-z0-9_.:\-]{1,253}")
_ALEMBIC_REVISION_RE = re.compile(r"[A-Za-z0-9_.\-]{1,160}")
_SAFE_EXCEPTION_TYPES = frozenset(
    {
        "AssertionError",
        "BaseException",
        "ConnectionError",
        "ConnectTimeout",
        "DecodeError",
        "Exception",
        "HTTPException",
        "Ignore",
        "ImportError",
        "IndexError",
        "IntegrityError",
        "InvalidTaskError",
        "KeyError",
        "LookupError",
        "MemoryError",
        "NotImplementedError",
        "NotRegistered",
        "OperationalError",
        "OSError",
        "PermissionError",
        "Reject",
        "Retry",
        "RuntimeError",
        "StopAsyncIteration",
        "StopIteration",
        "SoftTimeLimitExceeded",
        "SystemExit",
        "TimeoutError",
        "TypeError",
        "UnicodeError",
        "ValueError",
        "Warning",
        "ZeroDivisionError",
    }
)
_TRUSTED_EXCEPTION_MODULE_PREFIXES = (
    "anthropic",
    "anyio",
    "app",
    "asyncio",
    "billiard",
    "botocore",
    "builtins",
    "celery",
    "fastapi",
    "httpcore",
    "httpx",
    "kombu",
    "openai",
    "psycopg",
    "psycopg2",
    "pydantic",
    "redis",
    "requests",
    "sqlalchemy",
    "starlette",
    "stripe",
    "voyageai",
)
_VALIDATION_LOCATION_ROOTS = frozenset(
    {"body", "cookie", "header", "path", "query"}
)


def _bounded_text(value: str, limit: int = _MAX_LOG_SCALAR_LENGTH) -> str:
    if len(value) <= limit:
        return value
    suffix = f"...<truncated:{len(value)}>"
    return value[: max(0, limit - len(suffix))] + suffix


def _safe_exception_type_name(value: object) -> str:
    if isinstance(value, str):
        candidate = value.rsplit(".", 1)[-1]
        if _EXCEPTION_TYPE_NAME_RE.fullmatch(candidate) is None:
            return "Exception"
        return candidate if candidate in _SAFE_EXCEPTION_TYPES else "Exception"

    exception_type = value if isinstance(value, type) else type(value)
    try:
        is_exception = issubclass(exception_type, BaseException)
    except TypeError:
        is_exception = False
    candidate = getattr(exception_type, "__name__", None)
    module_name = getattr(exception_type, "__module__", None)
    if (
        not is_exception
        or not isinstance(candidate, str)
        or _EXCEPTION_TYPE_NAME_RE.fullmatch(candidate) is None
        or not isinstance(module_name, str)
    ):
        return "Exception"
    trusted_module = any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in _TRUSTED_EXCEPTION_MODULE_PREFIXES
    )
    defining_module = sys.modules.get(module_name)
    if (
        not trusted_module
        or defining_module is None
        or getattr(defining_module, candidate, None) is not exception_type
    ):
        return "Exception"
    return candidate


def _safe_logger_name(value: object) -> str:
    if not isinstance(value, str) or len(value) > _MAX_LOGGER_NAME_LENGTH:
        return "unknown"
    return value if _LOGGER_NAME_RE.fullmatch(value) else "unknown"


def safe_http_route(request: object) -> str:
    """Return a trusted route template, never the caller-supplied literal path."""

    scope = getattr(request, "scope", None)
    if type(scope) is not dict:
        return "<unmatched-route>"
    route = scope.get("route")
    route_path = getattr(route, "path", None)
    root_path = scope.get("root_path", "")
    if not isinstance(route_path, str) or not isinstance(root_path, str):
        return "<unmatched-route>"
    combined = f"{root_path.rstrip('/')}/{route_path.lstrip('/')}"
    if len(combined) > _MAX_LOG_SCALAR_LENGTH:
        return "<unmatched-route>"
    return combined if _ROUTE_TEMPLATE_RE.fullmatch(combined) else "<unmatched-route>"


def _validation_location(value: object, *, opaque: bool) -> object:
    if isinstance(value, int) and not isinstance(value, bool):
        return "<index>" if opaque else value
    if not isinstance(value, str):
        return "<field>" if opaque else _safe_log_value(value)
    if not opaque:
        return _bounded_text(value, 128)
    if value in _VALIDATION_LOCATION_ROOTS:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"field-{digest}"


def sanitize_validation_errors(
    errors: object, *, for_log: bool
) -> list[dict[str, object]]:
    """Build bounded response or telemetry projections of validation failures."""

    if type(errors) not in {list, tuple}:
        return []
    safe: list[dict[str, object]] = []
    for error in errors[:20]:
        if type(error) is not dict:
            continue
        raw_type = error.get("type")
        error_type = (
            raw_type
            if isinstance(raw_type, str) and _VALIDATION_TYPE_RE.fullmatch(raw_type)
            else "validation_error"
        )
        raw_location = error.get("loc")
        location_items = (
            raw_location[:8] if type(raw_location) in {list, tuple} else ()
        )
        projected: dict[str, object] = {
            "type": error_type,
            "loc": [
                _validation_location(item, opaque=for_log) for item in location_items
            ],
        }
        if not for_log and isinstance(error.get("msg"), str):
            projected["msg"] = _bounded_text(error["msg"], 512)
        safe.append(projected)
    return safe


def _container_tail(length: int) -> str:
    return f"<truncated:{length - _MAX_LOG_CONTAINER_ITEMS}>"


def _safe_frame_path(filename: str) -> str:
    try:
        resolved = Path(filename).resolve()
        if not resolved.is_file():
            return "<external>"
        relative = resolved.relative_to(_BACKEND_ROOT)
    except (OSError, RuntimeError, ValueError):
        return "<external>"
    if not relative.parts or relative.parts[0] not in {
        "app",
        "alembic",
        "scripts",
        "tests",
    }:
        return "<external>"
    return relative.as_posix()


def _safe_exception_frames(exc_info) -> list[dict[str, object]]:
    """Retain bounded code locations without exception text or local values."""

    import traceback

    try:
        frames = traceback.extract_tb(exc_info[2])[-_MAX_EXCEPTION_FRAMES:]
    except Exception:
        return []
    safe_frames = []
    for frame in frames:
        path = _safe_frame_path(frame.filename)
        safe_frames.append(
            {
                "path": path[:160],
                "line": int(frame.lineno),
                "function": (
                    _bounded_text(str(frame.name), 64)
                    if path != "<external>"
                    else "<external>"
                ),
            }
        )
    return safe_frames


def _safe_httpx_origin(value: object) -> str | None:
    """Keep outbound request grouping without URL credentials/path/query data."""

    if type(value).__module__ != "httpx" or type(value).__name__ != "URL":
        return None
    try:
        from httpx import URL

        if type(value) is not URL:
            return None
        scheme = value.scheme
        host = value.host
        port = value.port
    except Exception:
        return "<http-url>"
    if not isinstance(scheme, str) or _URL_SCHEME_RE.fullmatch(scheme) is None:
        return "<http-url>"
    if not isinstance(host, str) or _URL_HOST_RE.fullmatch(host) is None:
        return "<http-url>"
    rendered_host = f"[{host}]" if ":" in host else host
    rendered_port = f":{port}" if isinstance(port, int) and 0 < port <= 65535 else ""
    return _bounded_text(f"{scheme}://{rendered_host}{rendered_port}")


def _safe_log_value(value, *, depth: int = 0):
    if isinstance(value, BaseException):
        return _safe_exception_type_name(value)
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    container_type = type(value)
    if depth >= _MAX_LOG_VALUE_DEPTH and container_type in {
        dict,
        list,
        set,
        frozenset,
        tuple,
    }:
        # Returning a deep container unchanged can expose an exception object's
        # repr from below the recursion boundary. Keep the boundary explicit.
        return f"<{container_type.__name__}>"
    if container_type is tuple:
        items = tuple(
            _safe_log_value(item, depth=depth + 1)
            for item in value[:_MAX_LOG_CONTAINER_ITEMS]
        )
        return items + ((_container_tail(len(value)),) if len(value) > len(items) else ())
    if container_type is list:
        items = [
            _safe_log_value(item, depth=depth + 1)
            for item in value[:_MAX_LOG_CONTAINER_ITEMS]
        ]
        if len(value) > len(items):
            items.append(_container_tail(len(value)))
        return items
    if container_type in {set, frozenset}:
        items = list(value)[:_MAX_LOG_CONTAINER_ITEMS]
        safe_items = [_safe_log_value(item, depth=depth + 1) for item in items]
        if len(value) > len(items):
            safe_items.append(_container_tail(len(value)))
        return safe_items
    if container_type is dict:
        items = list(value.items())[:_MAX_LOG_CONTAINER_ITEMS]
        safe = {
            _safe_log_value(key, depth=depth + 1): _safe_log_value(
                item, depth=depth + 1
            )
            for key, item in items
        }
        if len(value) > len(items):
            safe["<truncated>"] = len(value) - len(items)
        return safe
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Enum):
        return _safe_log_value(value.value, depth=depth + 1)
    if isinstance(value, (date, Decimal, Path, UUID)):
        return _bounded_text(str(value))
    if safe_origin := _safe_httpx_origin(value):
        return safe_origin
    return "<object>"


def _bounded_task_identifier(
    value: object, *, limit: int, pattern: re.Pattern[str]
) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    rendered = str(value)
    if not rendered or len(rendered) > limit:
        return None
    if pattern.fullmatch(rendered) is None:
        return None
    return rendered


def _celery_consumer_event(record: logging.LogRecord) -> dict[str, str] | None:
    """Classify Celery's payload-bearing rejection templates without rendering them."""

    if record.name != "celery.worker.consumer.consumer" or not isinstance(
        record.msg, str
    ):
        return None
    try:
        from celery.worker.consumer.consumer import (
            INVALID_TASK_ERROR,
            MESSAGE_DECODE_ERROR,
            UNKNOWN_FORMAT,
            UNKNOWN_TASK_ERROR,
        )
    except Exception:
        return None
    categories = {
        UNKNOWN_FORMAT: "unknown_format",
        UNKNOWN_TASK_ERROR: "unregistered_task",
        INVALID_TASK_ERROR: "invalid_task",
        MESSAGE_DECODE_ERROR: "decode_error",
    }
    category = categories.get(record.msg)
    if category is None:
        return None
    result = {
        "celery_event": "message_rejected",
        "celery_category": category,
    }
    args = record.args if type(record.args) is tuple else ()
    if category != "unknown_format" and args:
        result["exception"] = _safe_exception_type_name(args[0])
    return result


def _celery_trace_context(record: logging.LogRecord) -> dict | None:
    """Recognize Celery's internal task lifecycle records fail-closed."""

    if record.name != "celery.app.trace" or not isinstance(record.msg, str):
        return None
    if not record.msg.startswith("Task %("):
        return None
    context = getattr(record, "data", None)
    if not isinstance(context, dict):
        return None
    if "name" not in context or "id" not in context:
        return None
    return context


def _alembic_migration_message(record: logging.LogRecord) -> str | None:
    """Expose only validated revision IDs from Alembic's object argument."""

    if (
        record.name != "alembic.runtime.migration"
        or record.msg != "Running %s"
        or type(record.args) is not tuple
        or len(record.args) != 1
    ):
        return None
    step = record.args[0]
    try:
        from alembic.runtime.migration import RevisionStep, StampStep

        if type(step) not in {RevisionStep, StampStep}:
            return None
        info = step.info
        source_ids = info.source_revision_ids
        destination_ids = info.destination_revision_ids
        is_stamp = info.is_stamp
        is_upgrade = info.is_upgrade
    except Exception:
        return None

    def _validated_ids(values: object) -> str | None:
        if type(values) is not tuple or len(values) > 8:
            return None
        rendered = []
        for value in values:
            if not isinstance(value, str) or _ALEMBIC_REVISION_RE.fullmatch(value) is None:
                return None
            rendered.append(value)
        return ",".join(rendered) if rendered else "base"

    source = _validated_ids(source_ids)
    destination = _validated_ids(destination_ids)
    if source is None or destination is None:
        return None
    operation = "stamp" if is_stamp else "upgrade" if is_upgrade else "downgrade"
    return f"Running migration operation={operation} from={source} to={destination}"


def _celery_task_state(record: logging.LogRecord, context: dict) -> str:
    if "return_value" in context and "runtime" in context:
        return "succeeded"
    if " retry:" in record.msg:
        return "retrying"
    description = context.get("description")
    if description == "ignored":
        return "ignored"
    if description == "rejected":
        return "rejected"
    if "exc" in context:
        return "failed"
    return "event"


def _celery_exception_type(record: logging.LogRecord, context: dict) -> str:
    if record.exc_info:
        return _safe_exception_type_name(record.exc_info[0])

    # Expected failures and retries do not carry exc_info. Celery supplies only
    # a rendered exception in those cases. Extract a class-shaped prefix while
    # refusing arbitrary prose; the uncontrolled message is never retained.
    rendered = context.get("exc")
    if not isinstance(rendered, str):
        return "Exception"
    match = _EXCEPTION_REPR_RE.search(rendered)
    if match is None:
        return "Exception"
    candidate = match.group("type").rsplit(".", 1)[-1]
    return _safe_exception_type_name(candidate)


def _celery_trace_fields(record: logging.LogRecord, context: dict) -> dict[str, object]:
    task_name = (
        _bounded_task_identifier(
            context.get("name"),
            limit=_MAX_TASK_NAME_LENGTH,
            pattern=_TASK_NAME_RE,
        )
        or "unknown"
    )
    task_id = (
        _bounded_task_identifier(
            context.get("id"), limit=_MAX_TASK_ID_LENGTH, pattern=_TASK_ID_RE
        )
        or "unknown"
    )
    state = _celery_task_state(record, context)
    fields: dict[str, object] = {
        "task_name": task_name,
        "task_id": task_id,
        "task_state": state,
    }
    runtime = context.get("runtime")
    runtime_value = None
    if isinstance(runtime, (int, float)) and not isinstance(runtime, bool):
        try:
            runtime_value = float(runtime)
        except (OverflowError, TypeError, ValueError):
            pass
    if (
        runtime_value is not None
        and math.isfinite(runtime_value)
        and 0 <= runtime_value <= 31 * 24 * 60 * 60
    ):
        fields["task_runtime_seconds"] = round(runtime_value, 6)
    if "exc" in context:
        fields["exception"] = _celery_exception_type(record, context)
    return fields


def _safe_log_message(record: logging.LogRecord) -> str:
    consumer_event = _celery_consumer_event(record)
    if consumer_event is not None:
        return f"Celery message rejected category={consumer_event['celery_category']}"
    alembic_message = _alembic_migration_message(record)
    if alembic_message is not None:
        return alembic_message
    celery_context = _celery_trace_context(record)
    if celery_context is not None:
        fields = _celery_trace_fields(record, celery_context)
        return f"Task {fields['task_name']}[{fields['task_id']}] {fields['task_state']}"

    try:
        message = str(_safe_log_value(record.msg))
        if record.args:
            message = message % _safe_log_value(record.args)
    except Exception:
        message = "log_format_error"
    return _bounded_text(message, _MAX_LOG_MESSAGE_LENGTH)


def _encode_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True)
    frames = payload.get("exception_frames")
    while len(encoded) > _MAX_LOG_RECORD_LENGTH and isinstance(frames, list) and frames:
        frames.pop(0)
        encoded = json.dumps(payload, ensure_ascii=True)
    if len(encoded) > _MAX_LOG_RECORD_LENGTH:
        payload["message"] = _bounded_text(str(payload.get("message", "")), 512)
        encoded = json.dumps(payload, ensure_ascii=True)
    if len(encoded) > _MAX_LOG_RECORD_LENGTH:
        payload.pop("exception_frames", None)
        encoded = json.dumps(payload, ensure_ascii=True)
    return encoded


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        request_id = normalize_request_id(
            getattr(record, "request_id", None) or get_request_id()
        )
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": _safe_logger_name(record.name),
            "message": _safe_log_message(record),
        }
        if request_id:
            payload["request_id"] = request_id
        celery_context = _celery_trace_context(record)
        consumer_event = _celery_consumer_event(record)
        if consumer_event is not None:
            payload.update(consumer_event)
        elif celery_context is not None:
            payload.update(_celery_trace_fields(record, celery_context))
        else:
            task_name = _bounded_task_identifier(
                getattr(record, "task_name", None),
                limit=_MAX_TASK_NAME_LENGTH,
                pattern=_TASK_NAME_RE,
            )
            task_id = _bounded_task_identifier(
                getattr(record, "task_id", None),
                limit=_MAX_TASK_ID_LENGTH,
                pattern=_TASK_ID_RE,
            )
            if task_name:
                payload["task_name"] = task_name
            if task_id:
                payload["task_id"] = task_id
        if record.exc_info:
            # Provider/SQL/broker exception messages can contain response
            # bodies, credentials, URLs, prompts, or tenant data. Keep the
            # exception class and bounded stack locations for diagnosis, never
            # the uncontrolled message, chained message, source, or locals.
            payload["exception"] = _safe_exception_type_name(record.exc_info[0])
            payload["exception_frames"] = _safe_exception_frames(record.exc_info)
        return _encode_payload(payload)


class CeleryJsonFormatter(JsonFormatter):
    """JSON formatter that retains Celery task identity, never task payloads."""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "task_name") or not hasattr(record, "task_id"):
            try:
                from celery._state import get_current_task

                task = get_current_task()
                request = getattr(task, "request", None) if task else None
                if task and request:
                    record.__dict__.setdefault("task_name", task.name)
                    record.__dict__.setdefault("task_id", request.id)
            except Exception:
                # Logging must remain available while Celery is booting or
                # tearing down, even if task-local state is unavailable.
                pass
        return super().format(record)


_CELERY_JSON_FORMATTER = CeleryJsonFormatter()


def configure_celery_logger(logger: logging.Logger | None) -> logging.Logger | None:
    """Replace only handler formatters, preserving Celery's logging topology."""

    if logger is None:
        return None
    for handler in tuple(logger.handlers):
        if not isinstance(handler.formatter, CeleryJsonFormatter):
            handler.setFormatter(_CELERY_JSON_FORMATTER)
    return logger


def setup_logging():
    """Configure structured logging for the application."""
    log_level = logging.INFO

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler with structured format
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    formatter = JsonFormatter()
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Silence noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    return root_logger
