from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.platform.sentry_privacy import (
    OperationalAlert,
    capture_operational_alert,
    initialize_sentry,
    sanitize_breadcrumb,
    sanitize_error_event,
    sanitize_transaction_event,
)
import app.platform.sentry_privacy as sentry_privacy

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND_ROOT / "app"


def _encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def _identity_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def _route_group(value: str) -> str:
    group = value.lstrip("/").split("/", 1)[0]
    return f"/{group}/<route-{_identity_digest(value)}>"


def _task_group(value: str) -> str:
    module = ".".join(value.split(".")[:3])
    return f"{module}.<task-{_identity_digest(value)}>"


def test_error_events_are_rebuilt_from_an_allowlist() -> None:
    secret = "candidate-email@example.invalid bearer-private-value"
    provider_secret = "postgres://private:password@tenant-db/records"
    trace_id = "a" * 32
    span_id = "b" * 16
    frames = [
        {
            "filename": "vendor/dependency.py",
            "abs_path": f"/private/vendor/{secret}.py",
            "function": "dispatch",
            "lineno": 10,
            "vars": {"secret": secret},
        }
    ]
    frames.extend(
        {
                "filename": "app/platform/sentry_privacy.py",
            "abs_path": f"/host/path/{provider_secret}/{index}.py",
                "function": "candidate_private_marker",
                "module": "app.candidate_private_marker",
            "lineno": 100 + index,
            "vars": {"body": secret},
            "pre_context": [secret],
            "context_line": provider_secret,
            "post_context": [provider_secret],
        }
        for index in range(20)
    )
    event = {
        "event_id": "c" * 32,
        "timestamp": "2026-07-18T12:30:00Z",
        "platform": "python",
        "level": "error",
        "release": "backend-2026.07.18",
        "environment": "production",
        "transaction": "/assessments/{assessment_id}/submit",
        "transaction_info": {"source": "route"},
        "request": {
            "method": "POST",
            "url": f"https://api.invalid/assessments/{secret}",
            "query_string": f"token={secret}",
            "data": {"candidate": secret},
            "headers": {"authorization": secret, "x-assessment-token": secret},
            "cookies": {"session": secret},
            "env": {"REMOTE_ADDR": secret},
        },
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "module": "builtins",
                    "value": secret,
                    "mechanism": {"type": "chained", "handled": False},
                },
                {
                    "type": "RuntimeError",
                    "module": "builtins",
                    "value": provider_secret,
                    "mechanism": {
                        "type": "starlette",
                        "handled": False,
                        "data": {"detail": secret},
                        "meta": {"errno": provider_secret},
                    },
                    "stacktrace": {"frames": frames},
                },
            ]
        },
        "contexts": {
            "trace": {
                "trace_id": trace_id,
                "span_id": span_id,
                "op": "http.server",
                "status": "internal_error",
                "origin": "auto.http.starlette",
                "description": secret,
                "data": {"secret": provider_secret},
                "dynamic_sampling_context": {"user_segment": secret},
            },
            "response": {"status_code": 500, "body_size": secret},
            "runtime": {"raw_description": secret},
        },
        "breadcrumbs": {
            "values": [
                {
                    "timestamp": 1_721_305_800.25,
                    "type": "http",
                    "category": "httplib",
                    "level": "error",
                    "message": secret,
                    "data": {
                        "method": "POST",
                        "status_code": 503,
                        "url": provider_secret,
                        "request_body": secret,
                    },
                }
            ]
        },
        "message": secret,
        "logentry": {"message": provider_secret},
        "user": {"email": secret, "ip_address": provider_secret},
        "extra": {"payload": secret},
        "modules": {secret: provider_secret},
        "server_name": provider_secret,
        "threads": {"values": [{"stacktrace": {"frames": frames}}]},
    }

    safe = sanitize_error_event(event, {"exc_info": RuntimeError(secret)})
    assert safe is not None
    encoded = _encoded(safe)
    assert secret not in encoded
    assert provider_secret not in encoded
    assert safe["transaction"] == _route_group("/assessments/{assessment_id}/submit")
    assert safe["request"] == {"method": "POST"}
    assert safe["contexts"]["response"] == {"status_code": 500}
    assert safe["contexts"]["trace"] == {
        "trace_id": trace_id,
        "span_id": span_id,
        "op": "http.server",
        "status": "internal_error",
        "origin": "auto.http.starlette",
    }
    exceptions = safe["exception"]["values"]
    assert len(exceptions) == 1
    assert exceptions[0]["type"] == "RuntimeError"
    assert "value" not in exceptions[0]
    safe_frames = exceptions[0]["stacktrace"]["frames"]
    assert len(safe_frames) == 12
    assert all(frame["filename"].startswith("app/") for frame in safe_frames)
    assert all(set(frame) <= {"filename", "module", "lineno", "in_app"} for frame in safe_frames)
    assert all(frame["module"] == "app.platform.sentry_privacy" for frame in safe_frames)
    assert safe["breadcrumbs"]["values"] == [
        {
            "timestamp": 1_721_305_800.25,
            "type": "http",
            "category": "httplib",
            "level": "error",
            "data": {"method": "POST", "status_code": 503},
        }
    ]


def test_message_events_use_only_tagged_stable_operation_identifiers() -> None:
    dynamic = "tenant/model/cost details are private"
    operation = OperationalAlert.ANTHROPIC_RECONCILIATION_DRIFT.value
    safe = sanitize_error_event(
        {
            "message": dynamic,
            "level": "error",
            "tags": {"operation": operation},
            "extra": {"tenant": "never-serialize-this"},
            "contexts": {
                "operational": {
                    "affected_rows": 7,
                    "drift_percent": 12.5,
                    "threshold_percent": float("inf"),
                    "private_metric": 999,
                    "raw_detail": "never-serialize-this",
                }
            },
        }
    )
    assert safe is not None
    assert safe["message"] == operation
    assert safe["tags"] == {"operation": operation}
    assert safe["fingerprint"] == [operation]
    assert safe["contexts"]["operational"] == {
        "operation": operation,
        "affected_rows": 7,
        "drift_percent": 12.5,
    }
    assert "tenant/model" not in _encoded(safe)

    unknown = sanitize_error_event({"message": "dynamic customer text"})
    assert unknown is not None
    assert unknown["message"] == "application_alert"


def test_transaction_events_keep_bounded_timing_not_payload_descriptions() -> None:
    secret = "SELECT * FROM candidate_private WHERE token='secret-value'"
    url_secret = "https://provider.invalid/candidates/private-id?api_key=secret"
    spans = [
        {
            "trace_id": "1" * 32,
            "span_id": f"{index:016x}",
            "parent_span_id": "2" * 16,
            "op": "db.sql.query",
            "status": "ok",
            "origin": "auto.db.sqlalchemy",
            "start_timestamp": 1_721_305_800 + index / 100,
            "timestamp": 1_721_305_800 + index / 100 + 0.005,
            "same_process_as_parent": True,
            "description": secret,
            "data": {"db.statement": secret, "url": url_secret},
            "tags": {"provider_payload": secret},
        }
        for index in range(70)
    ]
    event = {
        "type": "transaction",
        "transaction": "/candidates/{candidate_id}",
        "transaction_info": {"source": "route"},
        "start_timestamp": 1_721_305_800,
        "timestamp": 1_721_305_801.25,
        "request": {
            "method": "GET",
            "url": url_secret,
            "query_string": secret,
            "headers": {"x-assessment-token": secret},
            "data": secret,
        },
        "contexts": {
            "trace": {
                "trace_id": "1" * 32,
                "span_id": "2" * 16,
                "op": "http.server",
                "status": "ok",
                "origin": "auto.http.starlette",
                "description": url_secret,
            },
            "response": {"status_code": 200, "body": secret},
        },
        "spans": spans,
        "measurements": {
            "http.response_content_length": {"value": 1234, "unit": "byte"},
            "private_measurement": {"value": secret, "unit": url_secret},
        },
        "breadcrumbs": {"values": [{"message": secret, "data": {"url": url_secret}}]},
    }

    safe = sanitize_transaction_event(event)
    assert safe is not None
    encoded = _encoded(safe)
    assert secret not in encoded
    assert url_secret not in encoded
    assert safe["type"] == "transaction"
    assert safe["transaction"] == _route_group("/candidates/{candidate_id}")
    assert safe["request"] == {"method": "GET"}
    assert safe["start_timestamp"] == 1_721_305_800
    assert safe["timestamp"] == 1_721_305_801.25
    assert safe["contexts"]["response"] == {"status_code": 200}
    assert len(safe["spans"]) == 50
    assert all(
        set(span)
        <= {
            "trace_id",
            "span_id",
            "parent_span_id",
            "op",
            "status",
            "origin",
            "start_timestamp",
            "timestamp",
            "same_process_as_parent",
        }
        for span in safe["spans"]
    )
    assert safe["measurements"] == {
        "http.response_content_length": {"value": 1234, "unit": "byte"}
    }


@pytest.mark.parametrize(
    "malformed",
    [None, "text", 123, object(), {"timestamp": float("nan")}, {"data": object()}],
)
def test_breadcrumb_boundary_never_raises_on_malformed_input(malformed: object) -> None:
    assert sanitize_breadcrumb(malformed, {"secret": "ignored"}) is None


def test_untrusted_route_sources_never_preserve_literal_paths() -> None:
    safe = sanitize_transaction_event(
        {
            "type": "transaction",
            "transaction": "/candidate/private-person-id",
            "transaction_info": {"source": "url"},
        }
    )
    assert safe is not None
    assert safe["transaction"] == "<unmatched-route>"
    assert "private-person-id" not in _encoded(safe)


def test_task_names_are_grouped_without_preserving_raw_identity() -> None:
    task_name = "app.tasks.assessment_tasks.score_candidate"
    safe = sanitize_transaction_event(
        {
            "type": "transaction",
            "transaction": task_name,
            "transaction_info": {"source": "task"},
            "start_timestamp": 100.0,
            "timestamp": 101.0,
        }
    )
    assert safe is not None
    assert safe["transaction"] == _task_group(task_name)
    assert safe["transaction_info"] == {"source": "task"}

    unsafe = sanitize_transaction_event(
        {
            "type": "transaction",
            "transaction": "tenant-private-task-name",
            "transaction_info": {"source": "task"},
        }
    )
    assert unsafe is not None
    assert unsafe["transaction"] == f"<task-{_identity_digest('tenant-private-task-name')}>"
    assert "tenant-private" not in _encoded(unsafe)


def test_route_and_task_grouping_is_stable_distinct_and_opaque() -> None:
    def grouped(identity: str, source: str) -> str:
        event = sanitize_transaction_event(
            {
                "type": "transaction",
                "transaction": identity,
                "transaction_info": {"source": source},
            }
        )
        assert event is not None
        return event["transaction"]

    first_route = "/api/applications/{application_id}"
    second_route = "/api/assessments/{assessment_id}"
    first_task = "app.tasks.assessment_tasks.score_candidate"
    second_task = "app.tasks.assessment_tasks.finalize_assessment"

    assert grouped(first_route, "route") == grouped(first_route, "route")
    assert grouped(first_route, "route") != grouped(second_route, "route")
    assert grouped(first_task, "task") == grouped(first_task, "task")
    assert grouped(first_task, "task") != grouped(second_task, "task")
    assert first_route not in grouped(first_route, "route")
    assert first_task not in grouped(first_task, "task")


def test_task_module_allowlist_matches_registered_app_task_modules() -> None:
    registered_modules: set[str] = set()
    for path in sorted((APP_ROOT / "tasks").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                attribute = decorator.func if isinstance(decorator, ast.Call) else decorator
                if not (
                    isinstance(attribute, ast.Attribute)
                    and attribute.attr == "task"
                    and isinstance(attribute.value, ast.Name)
                    and attribute.value.id == "celery_app"
                ):
                    continue
                explicit_name = None
                if isinstance(decorator, ast.Call):
                    explicit_name = next(
                        (
                            keyword.value.value
                            for keyword in decorator.keywords
                            if keyword.arg == "name"
                            and isinstance(keyword.value, ast.Constant)
                            and isinstance(keyword.value.value, str)
                        ),
                        None,
                    )
                if explicit_name is None:
                    registered_modules.add(f"app.tasks.{path.stem}")
                elif explicit_name.startswith("app.tasks."):
                    registered_modules.add(".".join(explicit_name.split(".")[:3]))

    assert sentry_privacy._TASK_MODULES == registered_modules | {  # noqa: SLF001
        "app.tasks.synthetic"
    }


def test_syntax_valid_private_markers_never_survive_identity_fields() -> None:
    marker = "candidate_private_marker"
    error = sanitize_error_event(
        {
            "release": marker,
            "environment": marker,
            "transaction": f"/assessments/{marker}",
            "transaction_info": {"source": "route"},
            "exception": {
                "values": [
                    {
                        "type": marker,
                        "module": f"app.{marker}",
                        "mechanism": {"type": marker, "handled": False},
                        "stacktrace": {
                            "frames": [
                                {
                                    "filename": "app/platform/sentry_privacy.py",
                                    "function": marker,
                                    "module": f"app.{marker}",
                                    "lineno": 1,
                                }
                            ]
                        },
                    }
                ]
            },
            "contexts": {
                "trace": {"op": marker, "status": marker, "origin": marker}
            },
            "breadcrumbs": {"values": [{"type": "log", "category": marker}]},
        }
    )
    transaction = sanitize_transaction_event(
        {
            "type": "transaction",
            "transaction": f"app.tasks.synthetic.{marker}",
            "transaction_info": {"source": "task"},
            "spans": [{"op": marker, "status": marker, "origin": marker}],
            "measurements": {
                marker: {"value": 1, "unit": "byte"},
                "http.response_content_length": {"value": 2, "unit": marker},
            },
        }
    )

    assert error is not None and transaction is not None
    assert marker not in _encoded({"error": error, "transaction": transaction})
    assert error["transaction"] == _route_group(f"/assessments/{marker}")
    assert error["exception"]["values"][0]["type"] == "Exception"
    assert error["exception"]["values"][0]["module"] == "app"
    assert error["exception"]["values"][0]["stacktrace"]["frames"][0] == {
        "filename": "app/platform/sentry_privacy.py",
        "in_app": True,
        "module": "app.platform.sentry_privacy",
        "lineno": 1,
    }
    assert transaction["transaction"] == _task_group(f"app.tasks.synthetic.{marker}")
    assert transaction["measurements"] == {
        "http.response_content_length": {"value": 2}
    }


class _HostileSequence(Sequence[object]):
    def __init__(self, value: object) -> None:
        self.value = value
        self.reads = 0

    def __len__(self) -> int:
        self.reads += 1
        return 5_000

    def __getitem__(self, index: object) -> object:
        self.reads += 1
        if isinstance(index, slice):
            return [self.value] * 5_000
        if isinstance(index, int) and 0 <= index < 5_000:
            return self.value
        raise IndexError


class _HostileMeasurements(Mapping[str, object]):
    def __init__(self) -> None:
        self.reads = 0

    def __getitem__(self, key: str) -> object:
        self.reads += 1
        return {"value": 1, "unit": "byte"}

    def __iter__(self):
        self.reads += 1
        return iter(["http.response_content_length"] * 5_000)

    def __len__(self) -> int:
        self.reads += 1
        return 5_000


def test_hostile_sequence_and_mapping_objects_are_rejected_without_iteration() -> None:
    frame = {"filename": "app/platform/sentry_privacy.py", "lineno": 1}
    frames = _HostileSequence(frame)
    breadcrumbs = _HostileSequence({"type": "log", "category": "app.safe"})
    spans = _HostileSequence({"op": "function"})
    measurements = _HostileMeasurements()

    error = sanitize_error_event(
        {
            "exception": {"values": [{"type": "RuntimeError", "stacktrace": {"frames": frames}}]},
            "breadcrumbs": {"values": breadcrumbs},
        }
    )
    transaction = sanitize_transaction_event(
        {
            "type": "transaction",
            "spans": spans,
            "measurements": measurements,
        }
    )

    assert error is not None and transaction is not None
    assert "stacktrace" not in error["exception"]["values"][0]
    assert "breadcrumbs" not in error
    assert "spans" not in transaction
    assert "measurements" not in transaction
    assert frames.reads == breadcrumbs.reads == spans.reads == measurements.reads == 0


def test_operational_alert_wrapper_accepts_no_dynamic_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    import sentry_sdk

    captured: dict[str, object] = {}

    class FakeScope:
        def set_tag(self, key: str, value: str) -> None:
            captured["tag"] = (key, value)

        def set_context(self, key: str, value: object) -> None:
            captured["context"] = (key, value)

    @contextmanager
    def fake_scope():
        yield FakeScope()

    def fake_capture(message: str, *, level: str) -> str:
        captured["message"] = message
        captured["level"] = level
        return "event-id"

    monkeypatch.setattr(sentry_sdk, "new_scope", fake_scope)
    monkeypatch.setattr(sentry_sdk, "capture_message", fake_capture)

    operation = OperationalAlert.ASSESSMENT_PROVISIONING_UNHEALTHY
    assert capture_operational_alert(
        operation,
        level="warning",
        metrics={"status_code": 503, "private_metric": 42, "other": float("nan")},
    ) == "event-id"
    assert captured == {
        "tag": ("operation", operation.value),
        "context": (
            "operational",
            {"operation": operation.value, "status_code": 503},
        ),
        "message": operation.value,
        "level": "warning",
    }

    monkeypatch.setattr(
        sentry_sdk,
        "capture_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("SDK failure")),
    )
    assert capture_operational_alert(operation) is None


def test_initializer_sets_explicit_privacy_cost_and_integration_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))

    assert initialize_sentry(
        "https://public-key@sentry.example.invalid/prefix/123",
        traces_sample_rate=0.25,
    )
    assert len(calls) == 1
    options = calls[0]
    assert options["default_integrations"] is True
    assert options["auto_enabling_integrations"] is False
    assert options["traces_sample_rate"] == 0.25
    assert options["profiles_sample_rate"] == 0.0
    assert options["stream_gen_ai_spans"] is False
    assert options["send_default_pii"] is False
    assert options["include_local_variables"] is False
    assert options["include_source_context"] is False
    assert options["max_request_body_size"] == "never"
    assert options["attach_stacktrace"] is False
    assert options["max_breadcrumbs"] == 20
    assert options["max_value_length"] == 256
    assert options["enable_db_query_source"] is False
    assert options["trace_propagation_targets"] == []
    assert options["server_name"] == "tali-backend"
    assert options["before_send"] is sanitize_error_event
    assert options["before_send_transaction"] is sanitize_transaction_event
    assert options["before_breadcrumb"] is sanitize_breadcrumb
    assert options["event_scrubber"].recursive is True
    assert {integration.__name__ for integration in options["disabled_integrations"]} == {
        "ArgvIntegration",
        "ModulesIntegration",
    }

    integrations = options["integrations"]
    assert [type(integration) for integration in integrations] == [
        FastApiIntegration,
        StarletteIntegration,
        SqlalchemyIntegration,
    ]
    for integration in integrations[:2]:
        assert integration.middleware_spans is False
        assert integration.transaction_style == "url"
        assert integration.failed_request_status_codes == set(range(500, 600))
        assert integration.http_methods_to_capture == (
            "DELETE",
            "GET",
            "HEAD",
            "OPTIONS",
            "PATCH",
            "POST",
            "PUT",
        )

    assert initialize_sentry("http://public@sentry.invalid/123") is False
    assert initialize_sentry("https://missing-project@sentry.invalid/not-a-number") is False
    assert initialize_sentry("https://[invalid/123") is False
    assert len(calls) == 1


class _DirectSentryCallVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.function_names: list[str] = []
        self.calls: list[tuple[str, str, str]] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.function_names.append(node.name)
        self.generic_visit(node)
        self.function_names.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "add_attachment",
            "capture_checkin",
            "capture_envelope",
            "capture_event",
            "capture_exception",
            "capture_message",
            "capture_session",
        }:
            function = self.function_names[-1] if self.function_names else "<module>"
            self.calls.append((self.relative_path, function, node.func.attr))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if any(alias.name == "sentry_sdk" or alias.name.startswith("sentry_sdk.") for alias in node.names):
            function = self.function_names[-1] if self.function_names else "<module>"
            self.calls.append((self.relative_path, function, "import:sentry_sdk"))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "sentry_sdk" or (node.module or "").startswith("sentry_sdk."):
            function = self.function_names[-1] if self.function_names else "<module>"
            self.calls.append((self.relative_path, function, "import:sentry_sdk"))


def test_direct_sentry_payload_calls_are_prohibited_outside_the_boundary() -> None:
    calls: Counter[tuple[str, str, str]] = Counter()
    helper = "app/platform/sentry_privacy.py"
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if relative == helper:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _DirectSentryCallVisitor(relative)
        visitor.visit(tree)
        calls.update(visitor.calls)

    assert calls == Counter()


def test_main_has_one_central_sentry_initializer() -> None:
    source = (APP_ROOT / "main.py").read_text(encoding="utf-8")
    assert source.count("initialize_sentry(") == 1
    assert "sentry_sdk.init(" not in source


def test_main_starts_with_a_valid_sentry_dsn_in_the_pinned_environment() -> None:
    script = r"""
import importlib.metadata
import importlib.util
import os
import warnings

warnings.simplefilter("error")

os.environ["SENTRY_DSN"] = "https://public-key@sentry.example.invalid/123"

assert importlib.metadata.version("sentry-sdk") == "2.66.0"
assert importlib.util.find_spec("jinja2") is None
assert importlib.util.find_spec("markupsafe") is not None

import app.platform.startup_validation as startup_validation
startup_validation.collect_startup_failures = lambda _settings: []
startup_validation.is_production_like = lambda _settings: False

import app.main
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

client = sentry_sdk.get_client()
assert client.options["server_name"] == "tali-backend"
assert client.get_integration(FastApiIntegration) is not None
assert client.get_integration(StarletteIntegration) is not None
print("ok")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "ok"


def test_pinned_sdk_does_not_rewrap_sync_fastapi_handlers_per_request() -> None:
    script = r"""
import importlib.metadata

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import app.platform.sentry_privacy as privacy

def major_minor(distribution):
    return tuple(
        int(part)
        for part in importlib.metadata.version(distribution).split(".")[:2]
    )

assert major_minor("sentry-sdk") >= (2, 63)
assert major_minor("fastapi") >= (0, 137)
assert privacy.initialize_sentry(
    "https://public-key@sentry.example.invalid/123",
    traces_sample_rate=0.0,
)

router = APIRouter()

@router.get("/sync")
def sync_endpoint():
    return {"ok": True}

app = FastAPI()
app.include_router(router)
included = next(route for route in app.routes if type(route).__name__ == "_IncludedRouter")

def wrapper_depth(call):
    depth = 0
    while hasattr(call, "__wrapped__"):
        depth += 1
        call = call.__wrapped__
    return depth, call

with TestClient(app) as client:
    assert client.get("/sync").status_code == 200
    first = included._effective_candidates[0].dependant.call
    first_depth, original = wrapper_depth(first)
    assert getattr(first, "_sentry_is_patched", False) is True
    assert first_depth == 1
    assert original is sync_endpoint

    for _ in range(7):
        assert client.get("/sync").status_code == 200
        current = included._effective_candidates[0].dependant.call
        assert current is first
        assert wrapper_depth(current) == (first_depth, sync_endpoint)

print("ok")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "ok"


def test_web_task_import_is_inert_and_worker_signal_initializes_once() -> None:
    script = r"""
import importlib
import os

os.environ["SENTRY_DSN"] = "https://public-key@sentry.example.invalid/123"

import sentry_sdk
import app.platform.sentry_privacy as privacy

calls = []
sentry_sdk.init = lambda **kwargs: calls.append(kwargs)

# Simulate the API client being initialized before app.main imports task modules.
assert privacy.initialize_sentry(os.environ["SENTRY_DSN"])
assert len(calls) == 1
assert calls[0]["server_name"] == "tali-backend"

celery_module = importlib.import_module("app.tasks.celery_app")
assert len(calls) == 1, "task imports must not replace the web Sentry client"

from celery.signals import worker_init
worker_init.send(sender=celery_module.celery_app)
worker_init.send(sender=celery_module.celery_app)
assert len(calls) == 2, "the worker lifecycle may initialize its client only once"
worker_options = calls[1]
assert worker_options["server_name"] == "tali-worker"
assert worker_options["auto_enabling_integrations"] is False
assert worker_options["stream_gen_ai_spans"] is False
assert worker_options["trace_propagation_targets"] == []
assert worker_options["before_send"] is privacy.sanitize_error_event
assert worker_options["before_send_transaction"] is privacy.sanitize_transaction_event
assert [type(item).__name__ for item in worker_options["integrations"]] == [
    "CeleryIntegration",
    "SqlalchemyIntegration",
]
assert worker_options["integrations"][0].propagate_traces is False
print("ok")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "ok"


def test_pinned_celery_sdk_drops_task_arguments_results_and_exception_text() -> None:
    script = r"""
import json

from celery import Celery

import app.platform.sentry_privacy as privacy

errors = []
transactions = []
sanitize_error = privacy.sanitize_error_event
sanitize_transaction = privacy.sanitize_transaction_event

def collect_error(event, hint):
    errors.append(sanitize_error(event, hint))
    return None

def collect_transaction(event, hint):
    transactions.append(sanitize_transaction(event, hint))
    return None

privacy.sanitize_error_event = collect_error
privacy.sanitize_transaction_event = collect_transaction
assert privacy.initialize_worker_sentry(
    "https://public-key@sentry.example.invalid/123",
    traces_sample_rate=1.0,
)

worker = Celery("privacy-test", broker="memory://", backend="cache+memory://")
worker.conf.update(task_always_eager=True, task_eager_propagates=False)

@worker.task(name="app.tasks.synthetic.private_worker_task")
def private_worker_task(candidate_value, metadata=None):
    local_private_value = candidate_value
    raise RuntimeError(
        f"worker-private-exception {local_private_value} {metadata['private']}"
    )

result = private_worker_task.apply(
    args=["worker-private-argument"],
    kwargs={"metadata": {"private": "worker-private-kwarg"}},
)
assert result.failed()
print(json.dumps({"errors": errors, "transactions": transactions}, sort_keys=True))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    encoded = _encoded(payload)
    for marker in (
        "worker-private-argument",
        "worker-private-kwarg",
        "worker-private-exception",
    ):
        assert marker not in encoded

    errors = [event for event in payload["errors"] if event is not None]
    assert len(errors) == 1
    exception = errors[0]["exception"]["values"][0]
    assert exception["type"] == "RuntimeError"
    assert "value" not in exception

    transaction = next(
        event
        for event in payload["transactions"]
        if event is not None
        and event.get("transaction")
        == _task_group("app.tasks.synthetic.private_worker_task")
    )
    assert transaction["transaction_info"] == {"source": "task"}
    assert transaction["start_timestamp"]
    assert transaction["timestamp"]
    assert transaction["contexts"]["trace"]["trace_id"]
    assert transaction["contexts"]["trace"]["op"] == "queue.task.celery"
    assert transaction["contexts"]["trace"]["origin"] == "auto.queue.celery"
    assert transaction["contexts"]["trace"]["status"] == "internal_error"


def test_pinned_celery_sdk_never_forwards_inbound_trace_headers_to_tasks() -> None:
    script = r"""
import json

import sentry_sdk
from celery import Celery
from celery.signals import before_task_publish

import app.platform.sentry_privacy as privacy

assert privacy.initialize_worker_sentry(
    "https://public-key@sentry.example.invalid/123",
    traces_sample_rate=1.0,
)

worker = Celery("publish-privacy-test", broker="memory://", backend="cache+memory://")

@worker.task(name="app.tasks.synthetic.publish_probe")
def publish_probe():
    return None

published = []

@before_task_publish.connect(weak=False)
def collect_publish(headers=None, body=None, **_kwargs):
    published.append({"headers": headers, "body": body})

marker = "candidate_private_marker"
incoming_headers = {
    "sentry-trace": f"{'1' * 32}-{'2' * 16}-1",
    "baggage": (
        f"sentry-transaction={marker},"
        f"sentry-public_key={marker},third-party={marker}"
    ),
}
transaction = sentry_sdk.continue_trace(
    incoming_headers,
    op="http.server",
    name="/assessments/{assessment_id}",
    source="route",
)
with sentry_sdk.start_transaction(transaction):
    publish_probe.apply_async()

assert len(published) == 1
print(json.dumps(published[0], sort_keys=True))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    published = json.loads(result.stdout.strip().splitlines()[-1])
    encoded = _encoded(published)
    assert "candidate_private_marker" not in encoded
    assert "sentry-trace" not in encoded
    assert "sentry-task-enqueued-time" not in encoded
    assert "baggage" not in encoded


def test_pinned_sdk_never_serializes_scope_or_hint_attachments() -> None:
    script = r"""
import json

import sentry_sdk
from sentry_sdk.attachments import Attachment
from sentry_sdk.transport import Transport

import app.platform.sentry_privacy as privacy

records = []

class RecordingTransport(Transport):
    def capture_envelope(self, envelope):
        records.append(
            {
                "item_types": [item.headers.get("type") for item in envelope.items],
                "serialized": envelope.serialize().decode("utf-8", "replace"),
            }
        )

sentry_sdk.init(
    dsn="https://public-key@sentry.example.invalid/123",
    transport=RecordingTransport,
    default_integrations=False,
    traces_sample_rate=1.0,
    send_default_pii=False,
    before_send=privacy.sanitize_error_event,
    before_send_transaction=privacy.sanitize_transaction_event,
    before_breadcrumb=privacy.sanitize_breadcrumb,
)

with sentry_sdk.new_scope() as scope:
    scope.add_attachment(
        bytes=b"scope-error-private-marker",
        filename="scope-error.txt",
        add_to_transactions=True,
    )
    sentry_sdk.capture_event(
        {"message": "error-controlled-private-marker"},
        hint={
            "attachments": [
                Attachment(
                    bytes=b"hint-error-private-marker",
                    filename="hint-error.txt",
                    add_to_transactions=True,
                )
            ]
        },
    )

with sentry_sdk.new_scope() as scope:
    scope.add_attachment(
        bytes=b"scope-transaction-private-marker",
        filename="scope-transaction.txt",
        add_to_transactions=True,
    )
    sentry_sdk.capture_event(
        {
            "type": "transaction",
            "transaction": "/assessments/{assessment_id}",
            "transaction_info": {"source": "route"},
            "start_timestamp": 100.0,
            "timestamp": 101.0,
        },
        hint={
            "attachments": [
                Attachment(
                    bytes=b"hint-transaction-private-marker",
                    filename="hint-transaction.txt",
                    add_to_transactions=True,
                )
            ]
        },
    )

print(json.dumps(records, sort_keys=True))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    records = json.loads(result.stdout.strip().splitlines()[-1])
    assert [record["item_types"] for record in records] == [["event"], ["transaction"]]
    encoded = _encoded(records)
    for marker in (
        "error-controlled-private-marker",
        "scope-error-private-marker",
        "hint-error-private-marker",
        "scope-transaction-private-marker",
        "hint-transaction-private-marker",
    ):
        assert marker not in encoded
    assert "attachment" not in encoded


def test_pinned_sdk_captures_safe_5xx_and_transaction_envelopes() -> None:
    script = r"""
import json
from pathlib import Path

import httpx
import sentry_sdk
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import app.platform.sentry_privacy as privacy

errors = []
transactions = []
sanitize_error = privacy.sanitize_error_event
sanitize_transaction = privacy.sanitize_transaction_event

def collect_error(event, hint):
    errors.append(sanitize_error(event, hint))
    return None

def collect_transaction(event, hint):
    transactions.append(sanitize_transaction(event, hint))
    return None

privacy.sanitize_error_event = collect_error
privacy.sanitize_transaction_event = collect_transaction

app = FastAPI()

@app.exception_handler(HTTPException)
async def http_exception_handler(_request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

assert privacy.initialize_sentry(
    "https://public-key@sentry.example.invalid/123",
    traces_sample_rate=1.0,
)

from sentry_sdk.tracing_utils import should_propagate_trace
assert not should_propagate_trace(
    sentry_sdk.get_client(),
    "https://provider.invalid/private-path",
)
outbound_headers = {}
def outbound_response(request):
    outbound_headers.update(request.headers)
    return httpx.Response(200, json={"ok": True})
with sentry_sdk.start_transaction(name="outbound-check", op="task"):
    with httpx.Client(transport=httpx.MockTransport(outbound_response)) as outbound:
        assert outbound.get("https://provider.invalid/private-path").status_code == 200
assert "baggage" not in outbound_headers
assert "sentry-trace" not in outbound_headers

source = r'''
async def handled_503(item_id: str, request: Request):
    body = await request.json()
    local_candidate_value = body["candidate"]
    raise HTTPException(503, detail=f"private handled detail {item_id} {local_candidate_value}")

async def chained_500(item_id: str, request: Request):
    body = await request.json()
    local_candidate_value = body["candidate"]
    try:
        raise ValueError(f"private inner detail {item_id} {local_candidate_value}")
    except ValueError as exc:
        raise RuntimeError(f"private outer detail {item_id} {local_candidate_value}") from exc

async def probe(item_id: str, request: Request):
    body = await request.json()
    local_candidate_value = body["candidate"]
    with sentry_sdk.start_span(
        op="db.sql.query",
        description=f"SELECT private candidate {item_id} {local_candidate_value}",
    ) as span:
        span.set_data("db.statement", f"SELECT token={local_candidate_value}")
    return {"ok": True}
'''

namespace = {
    "HTTPException": HTTPException,
    "Request": Request,
    "sentry_sdk": sentry_sdk,
}
synthetic_path = Path(privacy.__file__).resolve().parents[1] / "synthetic_sentry_endpoint.py"
exec(compile(source, str(synthetic_path), "exec"), namespace)
app.post("/handled/{item_id}")(namespace["handled_503"])
app.post("/chained/{item_id}")(namespace["chained_500"])
app.post("/probe/{item_id}")(namespace["probe"])

headers = {"X-Assessment-Token": "header-private-marker"}
body = {"candidate": "body-private-marker"}
with TestClient(app, raise_server_exceptions=False) as client:
    assert client.post(
        "/handled/path-private-marker?token=query-private-marker",
        headers=headers,
        json=body,
    ).status_code == 503
    assert client.post(
        "/missing/missing-private-marker?token=query-private-marker",
        headers=headers,
        json=body,
    ).status_code == 404
    assert client.post(
        "/chained/path-private-marker?token=query-private-marker",
        headers=headers,
        json=body,
    ).status_code == 500
    assert client.post(
        "/probe/path-private-marker?token=query-private-marker",
        headers=headers,
        json=body,
    ).status_code == 200

print(json.dumps({"errors": errors, "transactions": transactions}, sort_keys=True))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    encoded = _encoded(payload)
    for marker in (
        "header-private-marker",
        "body-private-marker",
        "path-private-marker",
        "missing-private-marker",
        "query-private-marker",
        "private handled detail",
        "private inner detail",
        "private outer detail",
        "SELECT private candidate",
        "SELECT token=",
    ):
        assert marker not in encoded

    errors = [event for event in payload["errors"] if event is not None]
    assert len(errors) == 2
    by_type = {event["exception"]["values"][0]["type"]: event for event in errors}
    assert set(by_type) == {"HTTPException", "RuntimeError"}
    assert by_type["HTTPException"]["exception"]["values"][0]["mechanism"]["handled"] is True
    assert by_type["RuntimeError"]["exception"]["values"][0]["mechanism"]["handled"] is False
    assert all(
        len(event["exception"]["values"]) == 1
        and "value" not in event["exception"]["values"][0]
        for event in errors
    )

    probe = next(
        event
        for event in payload["transactions"]
        if event is not None
        and any(span.get("op") == "db.sql.query" for span in event.get("spans", []))
    )
    assert probe["transaction"] == f"<route-{_identity_digest('/probe/{item_id}')}>"
    assert probe["request"] == {"method": "POST"}
    assert probe["start_timestamp"]
    assert probe["timestamp"]
    assert probe["contexts"]["trace"]["trace_id"]
    assert probe["contexts"]["trace"]["op"] == "http.server"
    sql_span = next(span for span in probe["spans"] if span.get("op") == "db.sql.query")
    assert sql_span["timestamp"] >= sql_span["start_timestamp"]
    assert set(sql_span).isdisjoint({"data", "description", "tags"})


def test_datetime_timestamps_are_normalized_without_losing_timing() -> None:
    start = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    finish = datetime(2026, 7, 18, 12, 0, 1, 250_000, tzinfo=timezone.utc)
    safe = sanitize_transaction_event(
        {
            "type": "transaction",
            "transaction": "/health",
            "transaction_info": {"source": "route"},
            "start_timestamp": start,
            "timestamp": finish,
            "spans": [
                {
                    "op": "function",
                    "start_timestamp": start,
                    "timestamp": finish,
                }
            ],
        }
    )
    assert safe is not None
    assert safe["timestamp"] - safe["start_timestamp"] == pytest.approx(1.25)
    assert safe["spans"][0]["timestamp"] - safe["spans"][0]["start_timestamp"] == pytest.approx(1.25)
