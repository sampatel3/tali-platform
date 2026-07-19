"""Secret-safe evidence for Celery retry/result persistence."""

from __future__ import annotations

import re
from typing import NoReturn

from ..services.provider_error_evidence import safe_provider_error_code

_OPERATION_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_STABLE_CODE_RE = re.compile(
    r"[a-z][a-z0-9_]{0,63}(?::[A-Za-z_][A-Za-z0-9_]{0,63}){1,3}\Z"
)


class SecretSafeTaskRetryError(RuntimeError):
    """Stable task failure whose arguments are safe for a result backend."""


def secret_safe_task_retry_error(
    error: BaseException,
    *,
    operation: str,
) -> SecretSafeTaskRetryError:
    """Replace an uncontrolled exception body with operation + class only."""

    safe_operation = operation if _OPERATION_RE.fullmatch(operation) else "task_retry"
    code = safe_provider_error_code(error, operation=safe_operation)
    if not _STABLE_CODE_RE.fullmatch(code):
        code = f"{safe_operation}:task_error"
    return SecretSafeTaskRetryError(code)


def _raise_retry(
    task,
    retry_error: SecretSafeTaskRetryError,
    *,
    operation: str,
    **options,
) -> NoReturn:
    """Publish a retry without letting Celery format the active exception."""

    try:
        retry = task.retry(exc=retry_error, throw=False, **options)
    except Exception as retry_failure:
        # Direct calls and exhausted retry budgets re-raise ``exc`` internally;
        # broker publication can fail here too. Never let the latter persist a
        # broker URL/body; retain only its class and the controlled operation.
        safe_failure = (
            retry_error
            if retry_failure is retry_error
            else secret_safe_task_retry_error(
                retry_failure,
                operation=f"{operation}_retry_publish",
            )
        )
        raise safe_failure from None
    raise retry from None


def raise_secret_safe_task_retry(
    task,
    error: BaseException,
    *,
    operation: str,
    **options,
) -> NoReturn:
    safe_error = secret_safe_task_retry_error(error, operation=operation)
    safe_operation = operation if _OPERATION_RE.fullmatch(operation) else "task_retry"
    _raise_retry(task, safe_error, operation=safe_operation, **options)


def raise_secret_safe_task_retry_code(task, code: str, **options) -> NoReturn:
    if isinstance(code, str) and _STABLE_CODE_RE.fullmatch(code):
        safe_code = code
        operation = code.partition(":")[0]
    else:
        safe_code = "task_retry:invalid_error_code"
        operation = "task_retry"
    _raise_retry(
        task,
        SecretSafeTaskRetryError(safe_code),
        operation=operation,
        **options,
    )


__all__ = [
    "SecretSafeTaskRetryError",
    "raise_secret_safe_task_retry",
    "raise_secret_safe_task_retry_code",
    "secret_safe_task_retry_error",
]
