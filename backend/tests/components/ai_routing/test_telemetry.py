from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select

from app.components.ai_routing.telemetry import (
    AIRoutingIdempotencyConflict,
    AIRoutingStatusTransitionError,
    AIRoutingTelemetryError,
    create_attempt,
    create_invocation,
    finish_attempt,
    finish_invocation,
    json_safe_snapshot,
    start_attempt,
    start_invocation,
)
from app.models.ai_routing import AIRoutingAttempt, AIRoutingInvocation


class _SnapshotValue(str, Enum):
    ROUTE = "primary"


def _ids() -> tuple[str, str]:
    return str(uuid4()), str(uuid4())


def _create(db, *, invocation_id: str | None = None, route_id: str | None = None):
    generated_invocation, generated_route = _ids()
    return create_invocation(
        db,
        invocation_id=invocation_id or generated_invocation,
        route_id=route_id or generated_route,
        operation="chat",
        workflow="general_chat",
        task="general_chat.orchestration",
        profile_version="general-chat.v1",
        policy_version="parity.v1",
        registry_version="registry.v1",
        request_snapshot={"estimated_input_tokens": 200},
        decision_snapshot={"reason_codes": ["route.selected.primary.v1"]},
        selected_deployment_id="anthropic.messages.sonnet",
        organization_id=11,
        user_id=12,
        role_id=13,
        agent_run_id=14,
        entity_id="turn-15",
    )


def _create_primary_attempt(db, invocation_id: str, *, ordinal: int = 1):
    return create_attempt(
        db,
        invocation_id=invocation_id,
        ordinal=ordinal,
        iteration_ordinal=ordinal,
        attempt_in_iteration=1,
        provider="anthropic",
        runtime="messages",
        deployment_id="anthropic.messages.sonnet",
        model="claude-sonnet-snapshot",
        credit_reservation_ref=f"test-reservation:{invocation_id}:{ordinal}",
        estimated_input_tokens=100,
        estimated_output_tokens=20,
        estimated_input_cost_basis="standard",
        admitted_cost_usd_micro=1_000,
    )


def _finish_known(db, invocation_id: str, ordinal: int):
    return finish_attempt(
        db,
        invocation_id,
        ordinal,
        status="succeeded",
        latency_ms=37,
        usage_unknown=False,
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=10,
        cache_creation_tokens=0,
        cost_usd_micro=630,
        provider_request_id=f"request-{ordinal}",
    )


def test_invocation_and_attempt_lifecycle_is_idempotent_and_flush_only(db):
    invocation_id, route_id = _ids()
    row = _create(db, invocation_id=invocation_id, route_id=route_id)
    duplicate = _create(db, invocation_id=invocation_id, route_id=route_id)

    assert duplicate is row
    assert row.root_invocation_id == invocation_id
    assert (
        db.scalar(
            select(AIRoutingInvocation).where(
                AIRoutingInvocation.invocation_id == invocation_id
            )
        )
        is row
    )

    started_at = datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc)
    assert start_invocation(db, invocation_id, started_at=started_at) is row
    assert start_invocation(db, invocation_id) is row
    attempt = _create_primary_attempt(db, invocation_id)
    assert _create_primary_attempt(db, invocation_id) is attempt
    assert start_attempt(db, invocation_id, 1, started_at=started_at) is attempt
    with pytest.raises(AIRoutingStatusTransitionError, match="cannot be replayed"):
        start_attempt(db, invocation_id, 1)

    finished = _finish_known(db, invocation_id, 1)
    assert _finish_known(db, invocation_id, 1) is finished
    terminal = finish_invocation(
        db,
        invocation_id,
        status="succeeded",
        selected_deployment_id="anthropic.messages.sonnet",
    )
    assert finish_invocation(db, invocation_id, status="succeeded") is terminal
    assert terminal.status == "succeeded"
    assert terminal.finished_at is not None
    assert finished.status == "succeeded"
    assert finished.usage_unknown is False
    assert finished.input_tokens == 100
    assert len(terminal.attempts) == 1

    # The repository owns no transaction boundary: its flushed rows disappear
    # when the calling workflow rolls back.
    db.rollback()
    assert db.get(AIRoutingInvocation, invocation_id) is None


def test_child_invocation_inherits_root_without_domain_foreign_keys(db):
    root = _create(db)
    child_id, child_route = _ids()
    child = create_invocation(
        db,
        invocation_id=child_id,
        route_id=child_route,
        parent_invocation_id=root.invocation_id,
        operation="structured_output",
        workflow="candidate_search",
        task="candidate_search.parse",
        profile_version="search-parse.v1",
        policy_version="parity.v1",
        registry_version="registry.v1",
        request_snapshot={},
        decision_snapshot={},
    )

    assert child.root_invocation_id == root.invocation_id
    assert child.parent_invocation_id == root.invocation_id
    assert not AIRoutingInvocation.__table__.foreign_keys
    attempt_fks = list(AIRoutingAttempt.__table__.foreign_keys)
    assert len(attempt_fks) == 1
    assert attempt_fks[0].target_fullname == "ai_routing_invocations.invocation_id"
    assert AIRoutingAttempt.__table__.c.usage_event_id.foreign_keys == set()
    assert AIRoutingAttempt.__table__.c.claude_call_log_id.foreign_keys == set()


def test_repeated_calls_on_pinned_deployment_are_not_marked_as_fallback(db):
    invocation = _create(db)
    start_invocation(db, invocation.invocation_id)
    first = _create_primary_attempt(db, invocation.invocation_id)
    start_attempt(db, invocation.invocation_id, first.ordinal)
    _finish_known(db, invocation.invocation_id, first.ordinal)

    second = _create_primary_attempt(db, invocation.invocation_id, ordinal=2)
    assert second.deployment_id == first.deployment_id
    assert second.fallback_from_deployment_id is None
    assert second.fallback_reason is None
    start_attempt(db, invocation.invocation_id, second.ordinal)
    _finish_known(db, invocation.invocation_id, second.ordinal)

    with pytest.raises(AIRoutingTelemetryError, match="deployment change"):
        create_attempt(
            db,
            invocation_id=invocation.invocation_id,
            ordinal=3,
            iteration_ordinal=3,
            attempt_in_iteration=1,
            provider="anthropic",
            runtime="messages",
            deployment_id="anthropic.messages.haiku",
            model="claude-haiku-snapshot",
            credit_reservation_ref=(
                f"test-reservation:{invocation.invocation_id}:3-invalid"
            ),
            estimated_input_tokens=100,
            estimated_output_tokens=20,
            estimated_input_cost_basis="standard",
            admitted_cost_usd_micro=1_000,
        )
    fallback = create_attempt(
        db,
        invocation_id=invocation.invocation_id,
        ordinal=3,
        iteration_ordinal=3,
        attempt_in_iteration=1,
        provider="anthropic",
        runtime="messages",
        deployment_id="anthropic.messages.haiku",
        model="claude-haiku-snapshot",
        credit_reservation_ref=f"test-reservation:{invocation.invocation_id}:3",
        estimated_input_tokens=100,
        estimated_output_tokens=20,
        estimated_input_cost_basis="standard",
        admitted_cost_usd_micro=1_000,
        fallback_from_deployment_id=second.deployment_id,
        fallback_reason="retryable_transport.v1",
    )
    assert fallback.fallback_from_deployment_id == second.deployment_id
    assert fallback.fallback_reason == "retryable_transport.v1"


def test_unknown_usage_and_illegal_transitions_fail_closed(db):
    invocation = _create(db)
    with pytest.raises(AIRoutingStatusTransitionError):
        finish_invocation(db, invocation.invocation_id, status="succeeded")
    with pytest.raises(AIRoutingStatusTransitionError):
        _create_primary_attempt(db, invocation.invocation_id)

    start_invocation(db, invocation.invocation_id)
    attempt = _create_primary_attempt(db, invocation.invocation_id)
    start_attempt(db, invocation.invocation_id, attempt.ordinal)
    with pytest.raises(AIRoutingStatusTransitionError):
        finish_invocation(db, invocation.invocation_id, status="failed")
    with pytest.raises(AIRoutingTelemetryError, match="Unknown usage"):
        finish_attempt(
            db,
            invocation.invocation_id,
            attempt.ordinal,
            status="failed",
            latency_ms=10,
            usage_unknown=True,
            input_tokens=1,
            error_class="transport.timeout.v1",
        )

    terminal = finish_attempt(
        db,
        invocation.invocation_id,
        attempt.ordinal,
        status="ambiguous",
        latency_ms=10,
        usage_unknown=True,
        error_class="transport.timeout.v1",
        error_reason="provider_acceptance_unknown.v1",
        provider_request_id="request-ambiguous",
    )
    assert terminal.usage_unknown is True
    assert terminal.input_tokens is None
    assert terminal.cost_usd_micro is None

    finish_invocation(db, invocation.invocation_id, status="failed")
    with pytest.raises(AIRoutingStatusTransitionError):
        start_invocation(db, invocation.invocation_id)


def test_idempotency_keys_cannot_be_reused_for_different_metadata(db):
    invocation_id, route_id = _ids()
    _create(db, invocation_id=invocation_id, route_id=route_id)
    with pytest.raises(AIRoutingIdempotencyConflict, match="task"):
        create_invocation(
            db,
            invocation_id=invocation_id,
            route_id=route_id,
            operation="chat",
            workflow="general_chat",
            task="general_chat.different",
            profile_version="general-chat.v1",
            policy_version="parity.v1",
            registry_version="registry.v1",
            request_snapshot={"estimated_input_tokens": 200},
            decision_snapshot={"reason_codes": ["route.selected.primary.v1"]},
            selected_deployment_id="anthropic.messages.sonnet",
            organization_id=11,
            user_id=12,
            role_id=13,
            agent_run_id=14,
            entity_id="turn-15",
        )


def test_snapshots_are_json_safe_bounded_and_content_free():
    stamp = datetime(2026, 7, 22, 10, 30, tzinfo=timezone.utc)
    identifier = uuid4()
    safe = json_safe_snapshot(
        {
            "created_at": stamp,
            "route_kind": _SnapshotValue.ROUTE,
            "price": Decimal("1.25"),
            "identifier": identifier,
            "capabilities": {"tools", "streaming"},
        }
    )
    assert safe == {
        "created_at": stamp.isoformat(),
        "route_kind": "primary",
        "price": "1.25",
        "identifier": str(identifier),
        "capabilities": ["streaming", "tools"],
    }
    UUID(safe["identifier"])

    for forbidden in ("prompt", "messages", "content", "cv-text"):
        with pytest.raises(AIRoutingTelemetryError, match="forbidden"):
            json_safe_snapshot({"nested": {forbidden: "must not persist"}})
    with pytest.raises(AIRoutingTelemetryError, match="NaN"):
        json_safe_snapshot({"latency": float("nan")})
    with pytest.raises(AIRoutingTelemetryError, match="128 KiB"):
        json_safe_snapshot({"reason_codes": ["x" * (129 * 1024)]})


def _load_migration():
    path = (
        Path(__file__).parents[3]
        / "alembic"
        / "versions"
        / "184_add_ai_routing_telemetry.py"
    )
    spec = importlib.util.spec_from_file_location(
        "ai_routing_telemetry_migration", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_upgrade_and_downgrade_contract(monkeypatch):
    migration = _load_migration()
    assert migration.revision == "184_ai_routing_telemetry"
    assert migration.down_revision == "183_agent_run_event_retry"

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()
        inspector = inspect(connection)
        assert {"ai_routing_invocations", "ai_routing_attempts"}.issubset(
            inspector.get_table_names()
        )
        invocation_fks = inspector.get_foreign_keys("ai_routing_invocations")
        attempt_fks = inspector.get_foreign_keys("ai_routing_attempts")
        assert invocation_fks == []
        assert len(attempt_fks) == 1
        assert attempt_fks[0]["referred_table"] == "ai_routing_invocations"
        assert attempt_fks[0]["options"].get("ondelete") == "CASCADE"
        unique_names = {
            item["name"]
            for item in inspector.get_unique_constraints("ai_routing_attempts")
        }
        assert {
            "uq_ai_routing_attempt_invocation_ordinal",
            "uq_ai_routing_attempt_credit_reservation",
        }.issubset(unique_names)
        attempt_columns = {
            item["name"]: item for item in inspector.get_columns("ai_routing_attempts")
        }
        for required in (
            "credit_reservation_ref",
            "estimated_input_tokens",
            "estimated_output_tokens",
            "estimated_input_cost_basis",
            "admitted_cost_usd_micro",
        ):
            assert attempt_columns[required]["nullable"] is False
        check_names = {
            item["name"]
            for item in inspector.get_check_constraints("ai_routing_attempts")
        }
        assert {
            "ck_ai_routing_attempt_status",
            "ck_ai_routing_attempt_started",
            "ck_ai_routing_attempt_finished",
            "ck_ai_routing_attempt_usage_complete",
            "ck_ai_routing_attempt_fallback_complete",
            "ck_ai_routing_attempt_error_complete",
            "ck_ai_routing_attempt_estimated_input_cost_basis",
            "ck_ai_routing_attempt_admitted_cost",
        }.issubset(check_names)

        migration.downgrade()
        assert "ai_routing_invocations" not in inspect(connection).get_table_names()
        assert "ai_routing_attempts" not in inspect(connection).get_table_names()
