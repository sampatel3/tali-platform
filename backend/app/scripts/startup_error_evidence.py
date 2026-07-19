"""Secret-safe diagnostic evidence for startup dependency failures."""

from __future__ import annotations

import re

from redis import exceptions as redis_exceptions
from sqlalchemy import exc as sqlalchemy_exceptions

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


def _sqlstate(error: BaseException) -> str | None:
    current: object | None = error
    for _ in range(3):
        if current is None:
            break
        for attribute in ("sqlstate", "pgcode"):
            try:
                value = getattr(current, attribute, None)
            except Exception:
                value = None
            if isinstance(value, str) and len(value) >= 2:
                return value
        try:
            current = getattr(current, "orig", None)
        except Exception:
            break
    return None


def startup_error_category(error: BaseException) -> str:
    """Classify an error without reading its potentially sensitive message."""

    sqlstate = _sqlstate(error)
    if sqlstate:
        if sqlstate.startswith("28"):
            return "authentication"
        if sqlstate.startswith("08"):
            return "connection"
        if sqlstate.startswith("3D"):
            return "configuration"
    if isinstance(
        error,
        (redis_exceptions.AuthenticationError, PermissionError),
    ):
        return "authentication"
    if isinstance(
        error,
        (
            redis_exceptions.TimeoutError,
            sqlalchemy_exceptions.TimeoutError,
            TimeoutError,
        ),
    ):
        return "timeout"
    if isinstance(
        error,
        (
            redis_exceptions.ConnectionError,
            sqlalchemy_exceptions.InterfaceError,
            sqlalchemy_exceptions.OperationalError,
            ConnectionError,
            OSError,
        ),
    ):
        return "connection"
    if isinstance(
        error,
        (
            ImportError,
            sqlalchemy_exceptions.ArgumentError,
        ),
    ):
        return "configuration"
    if isinstance(error, (redis_exceptions.RedisError, sqlalchemy_exceptions.DBAPIError)):
        return "provider"
    return "unexpected"


def startup_error_code(error: BaseException, *, operation: str) -> str:
    """Return controlled operation/category/type evidence for operator output."""

    safe_operation = operation if _TOKEN_RE.fullmatch(operation) else "startup_dependency"
    error_type = type(error).__name__
    safe_type = error_type if _TOKEN_RE.fullmatch(error_type) else "Error"
    return f"{safe_operation}:{startup_error_category(error)}:{safe_type}"


__all__ = ["startup_error_category", "startup_error_code"]
