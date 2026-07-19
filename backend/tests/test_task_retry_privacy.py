"""Celery retries must not persist the active exception through Redis."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from billiard.einfo import ExceptionInfo
from celery import Celery, states
from celery.exceptions import Retry

from app.tasks.retry_safety import (
    SecretSafeTaskRetryError,
    raise_secret_safe_task_retry,
    raise_secret_safe_task_retry_code,
)


_SECRET = "provider-response bearer-secret candidate-payload"
_BROKER_SECRET = "redis://worker:credential@broker/provider-response"
_APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def test_secret_safe_retry_omits_raw_error_from_retry_traceback_and_backend():
    app = Celery("retry-privacy", broker="memory://", backend="cache+memory://")

    @app.task(bind=True, max_retries=3)
    def retrying_task(self):
        try:
            raise RuntimeError(_SECRET)
        except RuntimeError as exc:
            raise_secret_safe_task_retry(
                self,
                exc,
                operation="retry_privacy",
                countdown=1,
            )

    retrying_task.push_request(called_directly=False, is_eager=True, retries=0)
    caught: Retry | None = None
    try:
        try:
            retrying_task.run()
        except Retry as exc:
            caught = exc
            traceback_text = ExceptionInfo(sys.exc_info()).traceback
        else:  # pragma: no cover - the helper is typed and tested as NoReturn
            raise AssertionError("retry helper returned")
    finally:
        retrying_task.pop_request()

    assert caught is not None
    retry = caught
    assert isinstance(retry.exc, SecretSafeTaskRetryError)
    assert str(retry.exc) == "retry_privacy:RuntimeError"
    backend_payload = retrying_task.backend.prepare_exception(
        retry.exc,
        serializer="json",
    )
    retrying_task.backend.mark_as_retry(
        "retry-privacy-task",
        retry.exc,
        traceback=traceback_text,
    )
    backend_meta = retrying_task.backend.get_task_meta("retry-privacy-task")
    evidence = repr((retry, traceback_text, backend_payload, backend_meta))
    assert _SECRET not in evidence
    assert backend_payload["exc_message"] == ("retry_privacy:RuntimeError",)
    assert backend_meta["status"] == states.RETRY
    assert isinstance(backend_meta["result"], SecretSafeTaskRetryError)
    assert str(backend_meta["result"]) == "retry_privacy:RuntimeError"


def test_secret_safe_retry_redacts_broker_publication_failure():
    class FailedBrokerTask:
        def retry(self, **_options):
            raise ConnectionError(_BROKER_SECRET)

    caught: SecretSafeTaskRetryError | None = None
    try:
        try:
            raise RuntimeError(_SECRET)
        except RuntimeError as exc:
            raise_secret_safe_task_retry(
                FailedBrokerTask(),
                exc,
                operation="retry_privacy",
            )
    except SecretSafeTaskRetryError as exc:
        caught = exc
        traceback_text = ExceptionInfo(sys.exc_info()).traceback

    assert caught is not None
    assert str(caught) == "retry_privacy_retry_publish:ConnectionError"
    assert _SECRET not in traceback_text
    assert _BROKER_SECRET not in traceback_text


def test_retry_code_boundary_rejects_uncontrolled_text():
    class CapturingRetryTask:
        def retry(self, *, exc, throw, **_options):
            assert throw is False
            return Retry(exc=exc)

    caught: Retry | None = None
    try:
        raise_secret_safe_task_retry_code(
            CapturingRetryTask(),
            f"uncontrolled:{_SECRET}",
        )
    except Retry as exc:
        caught = exc

    assert caught is not None
    assert isinstance(caught.exc, SecretSafeTaskRetryError)
    assert str(caught.exc) == "task_retry:invalid_error_code"
    assert _SECRET not in repr(caught)


def test_caught_exceptions_never_call_celery_retry_directly():
    """Direct ``self.retry`` raises before an outer ``from None`` can run."""

    offenders: list[str] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
                and node.func.attr == "retry"
            ):
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent,
                (ast.ExceptHandler, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                parent = parents.get(parent)
            if isinstance(parent, ast.ExceptHandler):
                offenders.append(f"{path.relative_to(_APP_ROOT)}:{node.lineno}")

    assert offenders == []


def test_rubric_retry_boundary_never_stringifies_caught_exception():
    path = _APP_ROOT / "tasks" / "rubric_retry_tasks.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "retry_incomplete_rubric_scoring"
    )
    offenders = []
    for handler in (
        node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler)
    ):
        if not handler.name:
            continue
        for node in ast.walk(handler):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"repr", "str"}
                and any(
                    isinstance(child, ast.Name) and child.id == handler.name
                    for child in ast.walk(node)
                )
            ):
                offenders.append(node.lineno)

    assert offenders == []
