"""Pre-screen provider/cache paths cannot bypass org or role admission."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.cv_matching.runner_pre_screen import run_pre_screen
from app.services.pre_screening_service import execute_pre_screen_only
from app.services.usage_credit_reservations import (
    CreditReservation,
    InsufficientRoleBudgetError,
)
from tests.sub_agents.conftest import make_full_application


def _application(db):
    org, role, _candidate, app = make_full_application(
        db,
        cv_text="Eight years building Python payment services.",
        jd_text="Seeking a backend engineer for distributed ledgers.",
    )
    role.job_spec_text = "Seeking a backend engineer for distributed ledgers."
    db.flush()
    return org, role, app


def _reservation(org_id: int) -> CreditReservation:
    return CreditReservation(
        organization_id=int(org_id),
        feature="prescreen",
        amount=1_500,
        external_ref="usage-hold:prescreen:test",
        live=False,
    )


def test_execute_pre_screen_admission_failure_skips_runner_without_leaking_details(
    db, caplog
):
    _org, role, app = _application(db)
    blocked = InsufficientRoleBudgetError(
        role_id=int(role.id),
        required=1_500,
        available=100,
    )
    with (
        patch(
            "app.services.pre_screen_usage_admission.run_with_pre_screen_admission",
            side_effect=blocked,
        ),
        patch("app.cv_matching.runner_pre_screen.run_pre_screen") as runner,
    ):
        result = execute_pre_screen_only(app, db=db, client=MagicMock())

    assert result["status"] == "error"
    assert result["reason"] == (
        "budget_admission_failed:InsufficientRoleBudgetError"
    )
    assert app.pre_screen_error_reason == result["reason"]
    assert app.pre_screen_evidence["summary"] == result["reason"]
    assert str(blocked) not in str(result)
    assert str(blocked) not in str(app.pre_screen_evidence)
    assert str(blocked) not in caplog.text
    runner.assert_not_called()


def test_execute_pre_screen_unexpected_failure_is_secret_safe(db, caplog):
    _org, _role, app = _application(db)
    secret = "postgresql://tenant:password@private.internal/provider-body"

    with patch(
        "app.services.pre_screen_usage_admission.run_with_pre_screen_admission",
        side_effect=RuntimeError(secret),
    ):
        result = execute_pre_screen_only(app, db=db, client=MagicMock())

    assert result == {
        "status": "error",
        "reason": "pre_screen_failed:RuntimeError",
    }
    assert app.pre_screen_error_reason == "pre_screen_failed:RuntimeError"
    assert app.pre_screen_evidence["summary"] == "pre_screen_failed:RuntimeError"
    assert secret not in str(result)
    assert secret not in str(app.pre_screen_evidence)
    assert secret not in caplog.text


def test_billed_cache_hit_settles_the_same_hard_hold(db):
    org, _role, app = _application(db)
    reservation = _reservation(int(org.id))
    cached = SimpleNamespace(
        decision="yes",
        reason="cached match",
        score=80.0,
        unverified_claim=False,
        cache_hit=True,
        prompt_version="cv_pre_screen_v2.2",
        trace_id="cache-trace",
        input_tokens=200,
        output_tokens=20,
        cache_read_tokens=0,
        cache_creation_tokens=0,
    )
    with (
        patch(
            "app.services.pre_screen_usage_admission.run_with_pre_screen_admission",
            return_value=(cached, reservation),
        ),
        patch("app.services.pre_screening_service._meter_record_event") as meter,
    ):
        result = execute_pre_screen_only(app, db=db, client=MagicMock())

    assert result["status"] == "ok"
    assert meter.call_args.kwargs["cache_hit"] is True
    assert meter.call_args.kwargs["credit_reservation"] == (
        reservation.as_metering_payload()
    )


def test_direct_runner_role_admission_failure_skips_provider():
    client = MagicMock()
    with patch(
        "app.services.pre_screen_usage_admission.reserve_pre_screen_usage",
        side_effect=InsufficientRoleBudgetError(
            role_id=17,
            required=1_500,
            available=0,
        ),
    ):
        result = run_pre_screen(
            "Python engineer",
            "Backend role",
            client=client,
            skip_cache=True,
            metering_context={
                "organization_id": 42,
                "role_id": 17,
                "entity_id": "application:99",
            },
        )

    assert result.decision == "error"
    assert "budget_admission_failed" in result.reason
    client.messages.create.assert_not_called()


def test_direct_runner_threads_hold_into_gateway_metering():
    reservation = _reservation(42)
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text='{"score": 75, "reason": "match"}')],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )
    with patch(
        "app.services.pre_screen_usage_admission.reserve_pre_screen_usage",
        return_value=reservation,
    ):
        result = run_pre_screen(
            "Python engineer",
            "Backend role",
            client=client,
            skip_cache=True,
            metering_context={
                "organization_id": 42,
                "role_id": 17,
                "entity_id": "application:99",
            },
        )

    assert result.decision == "yes"
    metering = client.messages.create.call_args.kwargs["metering"]
    assert metering["role_id"] == 17
    assert metering["credit_reservation"] == reservation.as_metering_payload()
