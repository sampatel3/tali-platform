from __future__ import annotations

import ast
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from billiard.exceptions import SoftTimeLimitExceeded

from app.platform.logging import JsonFormatter


def _record_with_current_exception() -> logging.LogRecord:
    return logging.LogRecord(
        name="taali.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="provider operation failed",
        args=(),
        exc_info=sys.exc_info(),
    )


def test_json_formatter_keeps_safe_stack_evidence_without_exception_text():
    provider_secret = "Bearer sk-secret provider response and tenant payload"
    try:
        raise RuntimeError(provider_secret)
    except RuntimeError:
        payload = json.loads(JsonFormatter().format(_record_with_current_exception()))

    encoded = json.dumps(payload)
    assert payload["exception"] == "RuntimeError"
    assert payload["exception_frames"]
    assert payload["exception_frames"][-1]["path"].endswith(
        "tests/platform/test_logging.py"
    )
    assert provider_secret not in encoded
    assert "sk-secret" not in encoded


def test_json_formatter_never_serializes_chained_exception_messages():
    inner_secret = "postgres://user:password@private-host/tenant"
    outer_secret = "provider-body-with-candidate-data"
    try:
        try:
            raise ValueError(inner_secret)
        except ValueError as exc:
            raise RuntimeError(outer_secret) from exc
    except RuntimeError:
        payload = json.loads(JsonFormatter().format(_record_with_current_exception()))

    encoded = json.dumps(payload)
    assert payload["exception"] == "RuntimeError"
    assert inner_secret not in encoded
    assert outer_secret not in encoded


def test_json_formatter_handles_exception_log_outside_an_exception_block():
    record = logging.LogRecord(
        name="taali.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="defensive boundary",
        args=(),
        exc_info=(None, None, None),
    )

    payload = json.loads(JsonFormatter().format(record))
    assert payload["exception"] == "Exception"
    assert payload["exception_frames"] == []


def test_json_formatter_keeps_only_validated_alembic_revision_ids():
    from alembic.runtime.migration import RevisionStep

    step = object.__new__(RevisionStep)
    step.is_upgrade = True
    step.revision_map = None
    step.revision = SimpleNamespace(
        revision="190_fireflies_org_index",
        _normalized_down_revisions=("189_shared_family_reject_repair",),
    )
    record = logging.LogRecord(
        name="alembic.runtime.migration",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Running %s",
        args=(step,),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == (
        "Running migration operation=upgrade "
        "from=189_shared_family_reject_repair to=190_fireflies_org_index"
    )

    step.revision.revision = "invalid/revision migration-secret-sentinel"
    rejected = json.loads(JsonFormatter().format(record))
    assert rejected["message"] == "Running <object>"
    assert "migration-secret-sentinel" not in json.dumps(rejected)


def test_json_formatter_sanitizes_exception_objects_used_as_message_arguments():
    provider_secret = "raw provider response bearer-secret"
    record = logging.LogRecord(
        name="taali.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="provider failed: %s nested=%s",
        args=(RuntimeError(provider_secret), {"failure": ValueError(provider_secret)}),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == (
        "provider failed: RuntimeError nested={'failure': 'ValueError'}"
    )
    assert provider_secret not in json.dumps(payload)


def test_json_formatter_bounds_messages_containers_and_unusual_record_fields():
    marker = "candidate@example.com-provider-private-marker"
    record = logging.LogRecord(
        name=marker * 100,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="processed rows=%d payload=%s suffix=%s",
        args=(17, list(range(10_000)), "x" * 100_000),
        exc_info=None,
    )
    record.task_name = marker * 100
    record.task_id = marker * 100

    encoded = JsonFormatter().format(record)
    payload = json.loads(encoded)

    assert payload["message"].startswith("processed rows=17 payload=[0, 1, 2")
    assert len(payload["message"]) <= 2048
    assert len(encoded) <= 4096
    assert payload["logger"] == "unknown"
    assert "task_name" not in payload
    assert "task_id" not in payload
    assert marker not in encoded


def test_json_formatter_normalizes_invalid_request_ids_stably():
    marker = "candidate@example.com bearer-private-marker/" + ("x" * 10_000)

    def _format() -> dict:
        record = logging.LogRecord(
            name="taali.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="request complete",
            args=(),
            exc_info=None,
        )
        record.request_id = marker
        return json.loads(JsonFormatter().format(record))

    first = _format()
    second = _format()

    assert first["request_id"] == second["request_id"]
    assert first["request_id"].startswith("opaque-")
    assert len(first["request_id"]) <= 128
    assert marker not in json.dumps(first)


def test_json_formatter_rejects_hostile_exception_class_names():
    marker = "candidate_private_marker"
    hostile_type = type(f"{marker * 1000}Error", (RuntimeError,), {})

    try:
        raise hostile_type("provider body must stay private")
    except RuntimeError:
        payload = json.loads(JsonFormatter().format(_record_with_current_exception()))

    assert payload["exception"] == "Exception"
    assert marker not in json.dumps(payload)


@pytest.mark.parametrize(
    ("exception_type", "expected_name"),
    (
        (httpx.ConnectTimeout, "ConnectTimeout"),
        (SoftTimeLimitExceeded, "SoftTimeLimitExceeded"),
    ),
)
def test_json_formatter_keeps_trusted_exception_types_without_messages(
    exception_type,
    expected_name,
):
    marker = "provider-private-exception-message"
    try:
        raise exception_type(marker)
    except BaseException:
        payload = json.loads(JsonFormatter().format(_record_with_current_exception()))

    assert payload["exception"] == expected_name
    assert marker not in json.dumps(payload)


def test_json_formatter_rejects_trusted_module_name_spoofing():
    marker = "candidate_private_marker"
    spoofed_type = type(
        f"{marker}Error",
        (RuntimeError,),
        {"__module__": "httpx"},
    )

    try:
        raise spoofed_type("provider body must stay private")
    except RuntimeError:
        payload = json.loads(JsonFormatter().format(_record_with_current_exception()))

    assert payload["exception"] == "Exception"
    assert marker not in json.dumps(payload)


def test_json_formatter_keeps_http_origin_without_private_url_components():
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="HTTP Request: %s %s",
        args=(
            "POST",
            httpx.URL(
                "https://private-user:private-password@example.invalid:8443/"
                "candidate/private-path?token=private-query"
            ),
        ),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "HTTP Request: POST https://example.invalid:8443"
    assert "private" not in json.dumps(payload)


def test_exception_logs_never_interpolate_raw_exception_objects():
    app_root = Path(__file__).resolve().parents[2] / "app"
    common_exception_names = {"e", "err", "error", "exc", "exception", "_exc"}
    offenders = []
    for path in sorted(app_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"log", "logger"}
                and node.func.attr
                in {"critical", "debug", "error", "exception", "info", "warning"}
            ):
                continue
            for arg in node.args:
                direct = isinstance(arg, ast.Name) and arg.id in common_exception_names
                formatted = isinstance(arg, ast.JoinedStr) and any(
                    isinstance(child, ast.Name)
                    and child.id in common_exception_names
                    for child in ast.walk(arg)
                )
                stringified = (
                    isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Name)
                    and arg.func.id in {"repr", "str"}
                    and any(
                        isinstance(child, ast.Name)
                        and child.id in common_exception_names
                        for child in ast.walk(arg)
                    )
                )
                raw_exception_arg = node.func.attr == "exception" and direct
                if raw_exception_arg or formatted or stringified:
                    offenders.append(f"{path.relative_to(app_root)}:{node.lineno}")

    assert offenders == []
