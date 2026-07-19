from __future__ import annotations

import importlib
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
from celery.app.trace import LOG_IGNORED, LOG_REJECTED, LOG_RETRY
from celery.exceptions import InvalidTaskError, NotRegistered
from celery.signals import after_setup_logger, after_setup_task_logger
from celery.worker.consumer.consumer import (
    INVALID_TASK_ERROR,
    MESSAGE_DECODE_ERROR,
    UNKNOWN_FORMAT,
    UNKNOWN_TASK_ERROR,
)
from kombu.exceptions import DecodeError

from app.platform.logging import CeleryJsonFormatter, configure_celery_logger


def _trace_record(
    *,
    message: str,
    context: dict,
    exc_info=None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="celery.app.trace",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(context,),
        exc_info=exc_info,
    )
    record.data = context
    return record


def _raise_nested(depth: int, secret: str) -> None:
    if depth:
        _raise_nested(depth - 1, secret)
    else:
        raise RuntimeError(secret)


def test_celery_trace_failure_keeps_type_and_bounded_frames_not_raw_payloads():
    secret = "Bearer sk-worker-secret provider body candidate@example.com"
    context = {
        "hostname": "worker-1",
        "id": "task-123",
        "name": "app.tasks.example",
        "exc": f"RuntimeError({secret!r})",
        "traceback": f"Traceback with {secret}",
        "args": f"({secret!r},)",
        "kwargs": f"{{'token': {secret!r}}}",
        "description": "raised unexpected",
        "internal": False,
    }
    try:
        _raise_nested(20, secret)
    except RuntimeError:
        record = _trace_record(
            message="Task %(name)s[%(id)s] %(description)s: %(exc)s",
            context=context,
            exc_info=sys.exc_info(),
        )
        payload = json.loads(CeleryJsonFormatter().format(record))

    encoded = json.dumps(payload)
    assert payload["message"] == "Task app.tasks.example[task-123] failed"
    assert payload["task_name"] == "app.tasks.example"
    assert payload["task_id"] == "task-123"
    assert payload["task_state"] == "failed"
    assert payload["exception"] == "RuntimeError"
    assert len(payload["exception_frames"]) == 12
    assert all(
        set(frame) == {"path", "line", "function"}
        for frame in payload["exception_frames"]
    )
    assert secret not in encoded
    assert "candidate@example.com" not in encoded


def test_exception_frames_fail_closed_for_hostile_dynamic_filenames():
    secret_filename = "tests/candidate@example.com-provider-secret.py"
    try:
        exec(compile("raise RuntimeError('opaque')", secret_filename, "exec"))
    except RuntimeError:
        record = logging.LogRecord(
            name="app.tasks.example",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="dynamic code failed",
            args=(),
            exc_info=sys.exc_info(),
        )
        payload = json.loads(CeleryJsonFormatter().format(record))

    encoded = json.dumps(payload)
    assert payload["exception_frames"][-1] == {
        "path": "<external>",
        "line": 1,
        "function": "<external>",
    }
    assert secret_filename not in encoded
    assert "candidate@example.com" not in encoded


def test_celery_trace_rejects_email_shaped_task_identity():
    secret = "candidate@example.com"
    context = {
        "id": secret,
        "name": f"app.tasks.{secret}",
        "runtime": 0.25,
        "return_value": "safe",
    }

    payload = json.loads(
        CeleryJsonFormatter().format(
            _trace_record(
                message=(
                    "Task %(name)s[%(id)s] succeeded in %(runtime)ss: %(return_value)s"
                ),
                context=context,
            )
        )
    )

    assert payload["task_name"] == "unknown"
    assert payload["task_id"] == "unknown"
    assert secret not in json.dumps(payload)


def test_celery_expected_failure_without_trace_keeps_only_class_shaped_type():
    secret = "postgres://user:password@private-host/tenant"
    context = {
        "id": "task-expected",
        "name": "app.tasks.expected",
        "description": "raised expected",
        "exc": f"OperationalError({secret!r})",
        "args": f"({secret!r},)",
        "kwargs": "{}",
    }
    payload = json.loads(
        CeleryJsonFormatter().format(
            _trace_record(
                message="Task %(name)s[%(id)s] %(description)s: %(exc)s",
                context=context,
            )
        )
    )

    assert payload["exception"] == "OperationalError"
    assert "exception_frames" not in payload
    assert secret not in json.dumps(payload)


def test_celery_success_keeps_timing_and_identity_not_raw_return_value():
    secret = "candidate-answer-and-provider-token"
    context = {
        "id": "task-success",
        "name": "app.tasks.success",
        "runtime": 1.23456789,
        "return_value": {"answer": secret},
    }
    payload = json.loads(
        CeleryJsonFormatter().format(
            _trace_record(
                message=(
                    "Task %(name)s[%(id)s] succeeded in %(runtime)ss: %(return_value)s"
                ),
                context=context,
            )
        )
    )

    assert payload["message"] == "Task app.tasks.success[task-success] succeeded"
    assert payload["task_state"] == "succeeded"
    assert payload["task_runtime_seconds"] == 1.234568
    assert secret not in json.dumps(payload)


def test_task_log_keeps_useful_fields_and_sanitizes_nested_exception_objects():
    secret = "raw-provider-response-with-credential"
    record = logging.LogRecord(
        name="app.tasks.example",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="processed rows=%d cost_usd=%.2f failure=%s",
        args=(17, 2.5, {"nested": [{"error": RuntimeError(secret)}]}),
        exc_info=None,
    )
    record.task_name = "app.tasks.example"
    record.task_id = "task-structured"
    record.request_id = "request-42"

    payload = json.loads(CeleryJsonFormatter().format(record))

    assert payload["logger"] == "app.tasks.example"
    assert payload["level"] == "WARNING"
    assert payload["request_id"] == "request-42"
    assert payload["task_name"] == "app.tasks.example"
    assert payload["task_id"] == "task-structured"
    assert payload["message"] == (
        "processed rows=17 cost_usd=2.50 failure={'nested': [{'error': 'RuntimeError'}]}"
    )
    assert secret not in json.dumps(payload)


def test_exception_object_used_as_entire_message_keeps_only_its_type():
    secret = "provider-body-used-as-log-message"
    record = logging.LogRecord(
        name="app.tasks.example",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=RuntimeError(secret),
        args=(),
        exc_info=None,
    )

    payload = json.loads(CeleryJsonFormatter().format(record))

    assert payload["message"] == "RuntimeError"
    assert secret not in json.dumps(payload)


@pytest.mark.parametrize(
    ("message", "args", "category", "exception_type"),
    [
        (
            UNKNOWN_FORMAT,
            ("body: candidate-private-marker headers=Bearer-secret",),
            "unknown_format",
            None,
        ),
        (
            UNKNOWN_TASK_ERROR,
            (
                NotRegistered("candidate-private-marker"),
                "candidate-private-marker body",
                {"authorization": "Bearer candidate-private-marker"},
                {"routing_key": "candidate-private-marker"},
            ),
            "unregistered_task",
            "NotRegistered",
        ),
        (
            INVALID_TASK_ERROR,
            (
                InvalidTaskError("candidate-private-marker"),
                "candidate-private-marker body",
            ),
            "invalid_task",
            "InvalidTaskError",
        ),
        (
            MESSAGE_DECODE_ERROR,
            (
                DecodeError("candidate-private-marker"),
                "application/json-candidate-private-marker",
                "utf-8-candidate-private-marker",
                {"candidate": "candidate-private-marker"},
                "candidate-private-marker body",
            ),
            "decode_error",
            "DecodeError",
        ),
    ],
)
def test_actual_celery_consumer_templates_never_log_message_payloads(
    message,
    args,
    category,
    exception_type,
):
    record = logging.LogRecord(
        name="celery.worker.consumer.consumer",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )

    payload = json.loads(CeleryJsonFormatter().format(record))
    encoded = json.dumps(payload)

    assert payload["message"] == f"Celery message rejected category={category}"
    assert payload["celery_event"] == "message_rejected"
    assert payload["celery_category"] == category
    if exception_type is None:
        assert "exception" not in payload
    else:
        assert payload["exception"] == exception_type
    assert "candidate-private-marker" not in encoded
    assert "Bearer-secret" not in encoded


@pytest.mark.parametrize(
    ("message", "description", "expected_state"),
    [
        (LOG_REJECTED, "rejected", "rejected"),
        (LOG_RETRY, "retrying", "retrying"),
        (LOG_IGNORED, "ignored", "ignored"),
    ],
)
def test_actual_celery_trace_templates_reconstruct_rejected_retry_and_ignored(
    message,
    description,
    expected_state,
):
    marker = "candidate-private-marker"
    context = {
        "id": "016da5b1-652f-4f1c-8d5a-f943d85fc0eb",
        "name": "app.tasks.example",
        "description": description,
        "exc": f"Reject({marker!r})",
        "args": marker,
        "kwargs": {"candidate": marker},
    }

    payload = json.loads(
        CeleryJsonFormatter().format(
            _trace_record(message=message, context=context)
        )
    )

    assert payload["task_state"] == expected_state
    assert payload["message"].endswith(expected_state)
    assert marker not in json.dumps(payload)


def test_actual_celery_logging_setup_and_fork_keep_safe_json_contract():
    backend_root = Path(__file__).resolve().parents[2]
    script = textwrap.dedent(
        """
        import json
        import logging
        import os

        from celery.exceptions import NotRegistered
        from celery.utils.log import get_multiprocessing_logger
        from celery.worker.consumer.consumer import MESSAGE_DECODE_ERROR, UNKNOWN_TASK_ERROR
        from kombu.exceptions import DecodeError

        from app.platform.logging import CeleryJsonFormatter
        from app.tasks.celery_app import celery_app

        celery_app.log.setup_logging_subsystem(loglevel=logging.INFO, colorize=False)
        roots = {
            "root": logging.getLogger(),
            "task": logging.getLogger("celery.task"),
            "multiprocessing": get_multiprocessing_logger(),
        }
        summary = {
            name: [isinstance(handler.formatter, CeleryJsonFormatter) for handler in logger.handlers]
            for name, logger in roots.items()
        }
        print("SUMMARY " + json.dumps(summary), flush=True)

        marker = "candidate-private-marker"
        consumer = logging.getLogger("celery.worker.consumer.consumer")
        consumer.error(
            UNKNOWN_TASK_ERROR,
            NotRegistered(marker), marker, {"header": marker}, {"route": marker},
        )
        consumer.critical(
            MESSAGE_DECODE_ERROR,
            DecodeError(marker), marker, marker, {"header": marker}, marker,
        )
        roots["task"].warning("task subsystem ready rows=%d", 3)
        roots["multiprocessing"].warning("multiprocessing subsystem ready rows=%d", 4)

        if hasattr(os, "fork"):
            child = os.fork()
            if child == 0:
                logging.getLogger("celery.worker.child").warning("fork child ready rows=%d", 5)
                os._exit(0)
            os.waitpid(child, 0)
        """
    )

    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(backend_root)},
    )
    output = completed.stdout + completed.stderr
    summary_line = next(
        line.removeprefix("SUMMARY ")
        for line in completed.stdout.splitlines()
        if line.startswith("SUMMARY ")
    )
    summary = json.loads(summary_line)

    assert all(values and all(values) for values in summary.values())
    assert "candidate-private-marker" not in output
    payloads = [
        json.loads(line)
        for line in output.splitlines()
        if line.startswith("{")
    ]
    assert {payload.get("celery_category") for payload in payloads} >= {
        "unregistered_task",
        "decode_error",
    }
    assert any("fork child ready rows=5" in payload["message"] for payload in payloads)


def test_celery_formatter_retains_current_task_identity(monkeypatch):
    from celery import _state

    monkeypatch.setattr(
        _state,
        "get_current_task",
        lambda: SimpleNamespace(
            name="app.tasks.current",
            request=SimpleNamespace(id="task-current"),
        ),
    )
    record = logging.LogRecord(
        name="app.tasks.current",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="processed rows=%d",
        args=(3,),
        exc_info=None,
    )

    payload = json.loads(CeleryJsonFormatter().format(record))

    assert payload["message"] == "processed rows=3"
    assert payload["task_name"] == "app.tasks.current"
    assert payload["task_id"] == "task-current"


def test_configure_celery_logger_preserves_handler_topology_and_is_idempotent(
    tmp_path,
):
    logger = logging.Logger("isolated.celery")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    stream = io.StringIO()
    stream_handler = logging.StreamHandler(stream)
    stream_handler.setLevel(logging.WARNING)
    marker_filter = logging.Filter("allowed")
    stream_handler.addFilter(marker_filter)
    file_handler = logging.FileHandler(tmp_path / "worker.log")
    file_handler.setLevel(logging.ERROR)
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    original_handlers = tuple(logger.handlers)
    original_streams = tuple(
        getattr(handler, "stream", None) for handler in logger.handlers
    )
    original_levels = tuple(handler.level for handler in logger.handlers)

    try:
        configure_celery_logger(logger)
        formatter_ids = tuple(id(handler.formatter) for handler in logger.handlers)
        configure_celery_logger(logger)

        assert tuple(logger.handlers) == original_handlers
        assert (
            tuple(getattr(handler, "stream", None) for handler in logger.handlers)
            == original_streams
        )
        assert tuple(handler.level for handler in logger.handlers) == original_levels
        assert logger.level == logging.DEBUG
        assert logger.propagate is False
        assert stream_handler.filters == [marker_filter]
        assert all(
            isinstance(handler.formatter, CeleryJsonFormatter)
            for handler in logger.handlers
        )
        assert (
            tuple(id(handler.formatter) for handler in logger.handlers) == formatter_ids
        )
    finally:
        file_handler.close()


def test_celery_signals_cover_worker_task_multiprocessing_and_beat(monkeypatch):
    celery_module = importlib.import_module("app.tasks.celery_app")
    worker_logger = logging.Logger("isolated.worker")
    task_logger = logging.Logger("isolated.task")
    multiprocessing_logger = logging.Logger("isolated.multiprocessing")
    for logger in (worker_logger, task_logger, multiprocessing_logger):
        logger.addHandler(logging.StreamHandler(io.StringIO()))

    monkeypatch.setattr(
        celery_module,
        "get_multiprocessing_logger",
        lambda: multiprocessing_logger,
    )

    # Both a standalone Beat command and a worker call Celery's logging setup,
    # which emits after_setup_logger. Embedded Beat shares the worker root.
    after_setup_logger.send(
        sender=celery_module.celery_app,
        logger=worker_logger,
        loglevel=logging.INFO,
        logfile=None,
        format="ignored",
        colorize=False,
    )
    after_setup_task_logger.send(
        sender=celery_module.celery_app,
        logger=task_logger,
        loglevel=logging.INFO,
        logfile=None,
        format="ignored",
        colorize=False,
    )

    assert isinstance(worker_logger.handlers[0].formatter, CeleryJsonFormatter)
    assert isinstance(task_logger.handlers[0].formatter, CeleryJsonFormatter)
    assert isinstance(multiprocessing_logger.handlers[0].formatter, CeleryJsonFormatter)
