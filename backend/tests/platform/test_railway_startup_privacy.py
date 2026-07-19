"""Railway startup diagnostics must not echo dependency credentials or bodies."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from redis.exceptions import AuthenticationError
from sqlalchemy.exc import OperationalError

from app.scripts import railway_start, railway_worker_start
from app.scripts.startup_error_evidence import startup_error_code

_SECRET = "provider-response password=railway-secret bearer-token"
_STARTUP_SCRIPTS = (
    Path(railway_start.__file__),
    Path(railway_worker_start.__file__),
)


def _one_attempt_then_timeout(monkeypatch, bootstrap) -> None:
    moments = iter((10.0, 10.0, 12.0))
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _seconds: None)


@pytest.mark.parametrize("bootstrap", (railway_start, railway_worker_start))
def test_database_wait_reports_safe_code_host_and_timeout(monkeypatch, bootstrap):
    database_url = "postgresql://worker:railway-secret@postgres.railway.internal:5432/taali"
    failure = OperationalError("connect failed", {"password": _SECRET}, RuntimeError(_SECRET))
    monkeypatch.setattr(bootstrap.settings, "DATABASE_URL", database_url)
    monkeypatch.setattr(bootstrap, "create_engine", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure))
    _one_attempt_then_timeout(monkeypatch, bootstrap)

    with pytest.raises(SystemExit) as caught:
        bootstrap._wait_for_database(timeout_seconds=1, interval_seconds=0)

    message = str(caught.value)
    assert "postgres.railway.internal:5432" in message
    assert "after 1s" in message
    assert "database_connectivity:connection:OperationalError" in message
    assert _SECRET not in message
    assert "railway-secret@" not in message


def test_redis_wait_reports_safe_authentication_code_host_and_timeout(monkeypatch):
    redis_url = "redis://worker:railway-secret@redis.railway.internal:6379/0"
    monkeypatch.setattr(railway_worker_start.settings, "REDIS_URL", redis_url)
    monkeypatch.setattr(
        railway_worker_start.redis,
        "from_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AuthenticationError(_SECRET)),
    )
    _one_attempt_then_timeout(monkeypatch, railway_worker_start)

    with pytest.raises(SystemExit) as caught:
        railway_worker_start._wait_for_redis(timeout_seconds=1, interval_seconds=0)

    message = str(caught.value)
    assert "redis.railway.internal:6379" in message
    assert "after 1s" in message
    assert "redis_connectivity:authentication:AuthenticationError" in message
    assert _SECRET not in message
    assert "railway-secret@" not in message


def test_optional_startup_cleanup_logs_only_safe_error_code(monkeypatch, capsys):
    from app.platform import database
    from app.scripts import invalidate_stale_cv_scores

    class FakeSession:
        def close(self) -> None:
            return None

    failure = OperationalError("cleanup failed", {}, RuntimeError(_SECRET))
    monkeypatch.delenv("DISABLE_STALE_SCORE_INVALIDATION", raising=False)
    monkeypatch.setattr(database, "SessionLocal", FakeSession)
    monkeypatch.setattr(
        invalidate_stale_cv_scores,
        "invalidate_stale_cv_match_scores",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    railway_start._invalidate_stale_cv_match_scores()

    output = capsys.readouterr().out
    assert "stale_cv_score_invalidation:connection:OperationalError" in output
    assert _SECRET not in output


def test_startup_error_code_does_not_trust_error_class_name():
    error_type = type("SafeLookingError", (RuntimeError,), {})
    error_type.__name__ = _SECRET

    assert startup_error_code(error_type(_SECRET), operation="database_connectivity") == (
        "database_connectivity:unexpected:Error"
    )


def test_startup_error_code_contains_hostile_exception_properties():
    class HostileProviderError(RuntimeError):
        @property
        def sqlstate(self):
            raise RuntimeError(_SECRET)

        @property
        def pgcode(self):
            raise RuntimeError(_SECRET)

        @property
        def orig(self):
            raise RuntimeError(_SECRET)

    code = startup_error_code(
        HostileProviderError(_SECRET),
        operation="database_connectivity",
    )

    assert code == "database_connectivity:unexpected:HostileProviderError"
    assert _SECRET not in code


def test_startup_waits_never_format_caught_exception_bodies_directly():
    offenders: list[str] = []
    for path in _STARTUP_SCRIPTS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
            if not handler.name:
                continue
            for node in ast.walk(handler):
                if isinstance(node, ast.FormattedValue) and isinstance(node.value, ast.Name):
                    if node.value.id == handler.name:
                        offenders.append(f"{path.name}:{node.lineno}")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"repr", "str"}
                    and any(
                        isinstance(argument, ast.Name) and argument.id == handler.name
                        for argument in node.args
                    )
                ):
                    offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == []
