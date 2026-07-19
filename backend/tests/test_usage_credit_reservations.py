"""Ledger-backed provider-call reservations hold, settle, and release safely."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Query

from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob, SCORE_JOB_RUNNING
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.agent_runtime.budget_guard import remaining_role_admission_microcredits
from app.services.pricing_service import Feature
from app.services.provider_usage_admission import (
    PROVIDER_ATTEMPT_STARTED_STATE,
    PROVIDER_SUCCEEDED_PENDING_STATE,
    PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
    mark_provider_attempt_started,
    mark_provider_usage_succeeded,
)
from app.services.usage_credit_reservations import (
    CreditReservation,
    InsufficientRoleBudgetError,
    ensure_role_capacity,
    release_credit_reservation,
    reservation_from_payload,
    reserve_credits,
)
from app.services.usage_credit_reservation_recovery import (
    release_stale_credit_reservations,
)
from app.services.usage_metering_service import record_event


def _capture_postgres_lock_sql(monkeypatch) -> list[str]:
    """Compile production ORM lock queries with PostgreSQL, not test SQLite."""
    compiled: list[str] = []
    original = Query.with_for_update

    def _record(query, *args, **kwargs):
        locked = original(query, *args, **kwargs)
        compiled.append(
            str(locked.statement.compile(dialect=postgresql.dialect()))
        )
        return locked

    monkeypatch.setattr(Query, "with_for_update", _record)
    return compiled


def _org(db, *, balance: int) -> Organization:
    row = Organization(
        name="Reserved Org",
        slug=f"reserved-{id(db)}-{balance}",
        credits_balance=balance,
    )
    db.add(row)
    db.commit()
    return row


def test_reservation_payload_parser_requires_exact_json_scalar_types():
    valid = {
        "organization_id": 1,
        "feature": "score",
        "amount": 0,
        "external_ref": "usage-hold:valid",
        "live": True,
        "role_id": 2,
    }
    assert reservation_from_payload(valid) == CreditReservation(
        organization_id=1,
        feature="score",
        amount=0,
        external_ref="usage-hold:valid",
        live=True,
        role_id=2,
    )
    for key, invalid in (
        ("organization_id", True),
        ("organization_id", "1"),
        ("organization_id", 1.5),
        ("amount", False),
        ("amount", "1"),
        ("amount", 1.5),
        ("role_id", True),
        ("role_id", 0),
        ("role_id", -1),
        ("role_id", "2"),
        ("live", 1),
    ):
        assert reservation_from_payload({**valid, key: invalid}) is None
    assert reservation_from_payload({**valid, "unexpected": "field"}) is None
    assert reservation_from_payload({**valid, "feature": " score"}) is None
    assert reservation_from_payload(
        {**valid, "external_ref": "usage-hold:valid "}
    ) is None


def test_v2_reservation_payload_requires_explicit_exact_owner_identity():
    request_sha256 = "a" * 64
    valid = {
        "version": 2,
        "organization_id": 1,
        "feature": "score",
        "amount": 10,
        "external_ref": "usage-hold:v2",
        "live": True,
        "role_id": 2,
        "user_id": 3,
        "entity_id": "application:4",
        "candidate_id": 5,
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "request_sha256": request_sha256,
        "shadow_proof": None,
    }

    assert reservation_from_payload(valid) == CreditReservation(
        organization_id=1,
        feature="score",
        amount=10,
        external_ref="usage-hold:v2",
        live=True,
        role_id=2,
        version=2,
        user_id=3,
        entity_id="application:4",
        candidate_id=5,
        provider="anthropic",
        model="claude-haiku-4-5",
        request_sha256=request_sha256,
    )
    for missing in (
        "role_id",
        "user_id",
        "entity_id",
        "candidate_id",
        "provider",
        "model",
        "request_sha256",
    ):
        assert reservation_from_payload(
            {key: value for key, value in valid.items() if key != missing}
        ) is None
    for key, invalid in (
        ("version", 1),
        ("user_id", True),
        ("entity_id", ""),
        ("entity_id", " application:4"),
        ("candidate_id", "5"),
        ("provider", ""),
        ("provider", "anthropic "),
        ("model", 7),
        ("model", " claude-haiku-4-5"),
        ("request_sha256", "not-a-digest"),
    ):
        assert reservation_from_payload({**valid, key: invalid}) is None


def test_invalid_credit_reservation_instance_is_not_normalized_into_validity():
    malformed = CreditReservation(
        organization_id=True,
        feature="score",
        amount=1,
        external_ref="usage-hold:invalid-object",
        live=True,
    )
    assert reservation_from_payload(malformed) is None
    with pytest.raises(ValueError, match="identity is malformed"):
        malformed.as_metering_payload()


@pytest.mark.parametrize("amount", [True, False, -1, 1.5, "1"])
def test_new_reservations_reject_inexact_or_negative_amounts(
    db,
    monkeypatch,
    amount,
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        False,
    )
    org = _org(db, balance=100)

    with pytest.raises(ValueError, match="amount must be a non-negative integer"):
        reserve_credits(
            db,
            organization_id=int(org.id),
            feature=Feature.ASSESSMENT,
            external_ref="usage-reservation:strict-amount",
            amount=amount,
        )


def test_new_reservations_reject_normalized_external_references(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        False,
    )
    org = _org(db, balance=100)

    with pytest.raises(ValueError, match="surrounding whitespace"):
        reserve_credits(
            db,
            organization_id=int(org.id),
            feature=Feature.ASSESSMENT,
            external_ref=" usage-reservation:strict-ref",
            amount=1,
        )


def test_stale_reaper_scopes_postgres_lock_to_hold_rows(db, monkeypatch):
    lock_sql = _capture_postgres_lock_sql(monkeypatch)

    result = release_stale_credit_reservations(
        db,
        now=datetime.now(timezone.utc),
    )

    assert result["scanned"] == 0
    assert len(lock_sql) == 1
    assert "LEFT OUTER JOIN" in lock_sql[0]
    assert (
        lock_sql[0].rsplit("FOR UPDATE", 1)[-1].strip()
        == "OF billing_credit_ledger SKIP LOCKED"
    )


def test_stale_reaper_acquires_organization_locks_in_global_order(
    db, monkeypatch
):
    from app.services import usage_credit_reservation_recovery as recovery

    first_org = _org(db, balance=100)
    second_org = _org(db, balance=101)
    now = datetime.now(timezone.utc)
    # The newer low-ID organization is deliberately second in query order.
    db.add_all(
        [
            BillingCreditLedger(
                organization_id=second_org.id,
                delta=-10,
                balance_after=90,
                reason="reservation:assessment",
                external_ref="usage-reservation:deadlock-order:second",
                entry_metadata={
                    "feature": "assessment",
                    "reserved": 10,
                    "role_id": None,
                    "state": "held",
                },
                created_at=now - timedelta(hours=4),
            ),
            BillingCreditLedger(
                organization_id=first_org.id,
                delta=-10,
                balance_after=90,
                reason="reservation:assessment",
                external_ref="usage-reservation:deadlock-order:first",
                entry_metadata={
                    "feature": "assessment",
                    "reserved": 10,
                    "role_id": None,
                    "state": "held",
                },
                created_at=now - timedelta(hours=3),
            ),
        ]
    )
    db.commit()
    lock_order: list[int] = []

    def _record_release(_db, *, reservation, reason):
        lock_order.append(int(reservation.organization_id))
        return 0

    monkeypatch.setattr(recovery, "release_credit_reservation", _record_release)

    result = recovery.release_stale_credit_reservations(db, now=now)

    assert result["scanned"] == 2
    assert lock_order == sorted(lock_order)
    assert lock_order == [int(first_org.id), int(second_org.id)]


def test_hard_reservation_settles_to_actual_charge(db, monkeypatch):
    monkeypatch.setattr("app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True)
    monkeypatch.setattr("app.services.usage_metering_service.settings.USAGE_METER_LIVE", True)
    org = _org(db, balance=2_000_000)
    role = Role(organization_id=org.id, name="Reserved Role")
    db.add(role)
    db.commit()
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        external_ref="usage-reservation:settle-test",
        amount=1_000_000,
        metadata={"trace_id": "trace-1", "role_id": int(role.id)},
        role_id=int(role.id),
        entity_id=f"role:{int(role.id)}",
        model="claude-haiku-4-5-20251001",
    )
    db.commit()
    db.refresh(org)
    assert org.credits_balance == 1_000_000

    event = record_event(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        model="claude-haiku-4-5-20251001",
        input_tokens=1_000,
        output_tokens=100,
        role_id=int(role.id),
        entity_id=f"role:{int(role.id)}",
        metadata={"trace_id": "trace-1"},
        credit_reservation=reservation,
    )
    db.commit()
    db.refresh(org)
    db.refresh(event)

    assert org.credits_balance == 2_000_000 - int(event.credits_charged)
    assert event.event_metadata["credit_reservation"]["reserved"] == 1_000_000
    assert event.event_metadata["credit_reservation"]["shortfall"] == 0
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == "usage-reservation:settle-test:settled")
        .count()
        == 1
    )


def test_historical_v1_live_hold_remains_settleable(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE", True
    )
    org = _org(db, balance=100_000)
    amount = 50_000
    external_ref = "historical:v1:settle"
    org.credits_balance = 50_000
    db.add(
        BillingCreditLedger(
            organization_id=int(org.id),
            delta=-amount,
            balance_after=50_000,
            reason="reservation:assessment",
            external_ref=external_ref,
            entry_metadata={
                "feature": "assessment",
                "reserved": amount,
                "role_id": None,
                "state": "held",
            },
        )
    )
    db.commit()
    reservation = CreditReservation(
        organization_id=int(org.id),
        feature="assessment",
        amount=amount,
        external_ref=external_ref,
        live=True,
    )

    event = record_event(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        model="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=10,
        credit_reservation=reservation,
    )
    db.commit()
    db.refresh(org)

    assert org.credits_balance == 100_000 - int(event.credits_charged)
    assert event.event_metadata["credit_reservation"]["state"] == "settled"


def test_provider_failure_release_is_idempotent(db, monkeypatch):
    monkeypatch.setattr("app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True)
    monkeypatch.setattr("app.services.usage_metering_service.settings.USAGE_METER_LIVE", True)
    org = _org(db, balance=500_000)
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        external_ref="usage-reservation:release-test",
        amount=200_000,
    )

    db.commit()

    assert release_credit_reservation(db, reservation=reservation) == 200_000
    db.commit()
    assert release_credit_reservation(db, reservation=reservation) == 0
    db.commit()
    db.refresh(org)

    assert org.credits_balance == 500_000
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == "usage-reservation:release-test:settled")
        .count()
        == 1
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"feature": "score"},
        {"amount": 199_999},
        {"role_id": None},
    ],
)
def test_mismatched_payload_cannot_release_a_real_hold(db, monkeypatch, overrides):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    org = _org(db, balance=500_000)
    role = Role(organization_id=org.id, name="Release contract role")
    db.add(role)
    db.commit()
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        external_ref="usage-reservation:forged-release",
        amount=200_000,
        role_id=int(role.id),
    )
    db.commit()
    forged = {**reservation.as_metering_payload(), **overrides}

    assert release_credit_reservation(db, reservation=forged) == 0
    db.commit()
    db.refresh(org)

    assert org.credits_balance == 300_000
    assert db.query(BillingCreditLedger).filter_by(
        external_ref="usage-reservation:forged-release:settled"
    ).count() == 0


def test_mismatched_payload_cannot_consume_a_real_hold_during_settlement(
    db,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE",
        True,
    )
    org = _org(db, balance=500_000)
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        external_ref="usage-reservation:forged-settlement",
        amount=200_000,
    )
    db.commit()
    forged = {
        **reservation.as_metering_payload(),
        "amount": reservation.amount - 1,
    }

    event = record_event(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        model="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=10,
        credit_reservation=forged,
    )
    db.commit()
    db.refresh(org)

    assert org.credits_balance == 300_000 - int(event.credits_charged)
    assert db.query(BillingCreditLedger).filter_by(
        external_ref="usage-reservation:forged-settlement:settled"
    ).count() == 0
    assert db.query(BillingCreditLedger).filter_by(
        external_ref=f"usage:{int(event.id)}"
    ).count() == 1


def test_existing_reservation_ref_requires_exact_amount_and_role(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    org = _org(db, balance=500_000)
    first_role = Role(organization_id=org.id, name="First reservation role")
    second_role = Role(organization_id=org.id, name="Second reservation role")
    db.add_all([first_role, second_role])
    db.commit()
    kwargs = {
        "organization_id": int(org.id),
        "feature": Feature.ASSESSMENT,
        "external_ref": "usage-reservation:strict-idempotency",
        "amount": 200_000,
        "role_id": int(first_role.id),
    }
    original = reserve_credits(db, **kwargs)
    db.commit()

    repeated = reserve_credits(db, **kwargs)
    assert repeated == original
    with pytest.raises(ValueError, match="already used"):
        reserve_credits(db, **{**kwargs, "amount": 199_999})
    with pytest.raises(ValueError, match="already used"):
        reserve_credits(db, **{**kwargs, "role_id": int(second_role.id)})

    db.refresh(org)
    assert org.credits_balance == 300_000
    assert db.query(BillingCreditLedger).count() == 1


def test_under_reserved_actual_never_makes_balance_negative(db, monkeypatch):
    monkeypatch.setattr("app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True)
    monkeypatch.setattr("app.services.usage_metering_service.settings.USAGE_METER_LIVE", True)
    org = _org(db, balance=100)
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        external_ref="usage-reservation:no-overdraft-test",
        amount=100,
        model="claude-haiku-4-5-20251001",
    )
    db.commit()

    event = record_event(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        model="claude-haiku-4-5-20251001",
        input_tokens=10_000,
        output_tokens=10_000,
        credit_reservation=reservation,
    )
    db.commit()
    db.refresh(org)
    db.refresh(event)

    assert org.credits_balance == 0
    assert event.event_metadata["credit_reservation"]["shortfall"] > 0


def test_hard_reservation_enforces_role_monthly_cap(db, monkeypatch):
    monkeypatch.setattr("app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True)
    monkeypatch.setattr("app.services.usage_metering_service.settings.USAGE_METER_LIVE", True)
    org = _org(db, balance=2_000_000)
    role = Role(
        organization_id=org.id,
        name="Capped Role",
        monthly_usd_budget_cents=100,  # 1M microcredits remain
    )
    db.add(role)
    db.commit()

    try:
        reserve_credits(
            db,
            organization_id=int(org.id),
            feature=Feature.ASSESSMENT,
            external_ref="usage-reservation:role-cap-test",
            amount=1_440_000,
            metadata={"role_id": int(role.id)},
            role_id=int(role.id),
            enforce_role_budget=True,
        )
    except InsufficientRoleBudgetError as exc:
        assert exc.required == 1_440_000
        assert exc.available == 1_000_000
    else:  # pragma: no cover - assertion spelling keeps the exception explicit
        raise AssertionError("role cap should block the hard reservation")

    db.refresh(org)
    assert org.credits_balance == 2_000_000
    assert db.query(BillingCreditLedger).count() == 0


def test_legacy_zero_role_budget_uses_finite_default_for_admission(db):
    org = _org(db, balance=100_000_000)
    role = Role(
        organization_id=org.id,
        name="Legacy Zero Budget",
        monthly_usd_budget_cents=0,
    )
    db.add(role)
    db.commit()

    remaining = remaining_role_admission_microcredits(
        db,
        role=role,
        per_active_score_job=30_000,
    )

    assert remaining == 5_000 * 10_000


def test_assessment_creation_gate_checks_role_capacity_in_shadow_mode(
    db, monkeypatch,
):
    from app.components.assessments.service import get_assessment_creation_gate

    monkeypatch.setattr("app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", False)
    monkeypatch.setattr("app.services.usage_metering_service.settings.USAGE_METER_LIVE", False)
    org = _org(db, balance=0)
    role = Role(
        organization_id=org.id,
        name="Tiny Assessment Budget",
        monthly_usd_budget_cents=1,  # 10k < 60k assessment reservation
    )
    db.add(role)
    db.commit()

    gate = get_assessment_creation_gate(
        int(org.id),
        db,
        role_id=int(role.id),
    )

    assert gate["can_create"] is False
    assert gate["reason"] == "role_monthly_budget_insufficient"


def test_assessment_start_gate_checks_role_capacity_in_shadow_mode(
    db, monkeypatch,
):
    from app.components.assessments.service import get_assessment_start_gate
    from app.models.assessment import AssessmentStatus

    monkeypatch.setattr("app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", False)
    monkeypatch.setattr("app.components.assessments.service.settings.USAGE_METER_LIVE", False)
    org = _org(db, balance=0)
    role = Role(
        organization_id=org.id,
        name="Tiny Start Budget",
        monthly_usd_budget_cents=1,
    )
    db.add(role)
    db.commit()
    assessment = SimpleNamespace(
        status=AssessmentStatus.PENDING,
        is_demo=False,
        organization_id=int(org.id),
        role_id=int(role.id),
        credit_consumed_at=None,
    )

    gate = get_assessment_start_gate(assessment, db)

    assert gate["can_start"] is False
    assert gate["reason"] == "role_monthly_budget_insufficient"


def test_unsettled_holds_consume_role_capacity(db, monkeypatch):
    monkeypatch.setattr("app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True)
    monkeypatch.setattr("app.services.usage_metering_service.settings.USAGE_METER_LIVE", True)
    org = _org(db, balance=3_000_000)
    role = Role(
        organization_id=org.id,
        name="Concurrent Capped Role",
        monthly_usd_budget_cents=200,  # 2M microcredits
    )
    db.add(role)
    db.commit()
    first = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        external_ref="usage-reservation:concurrent-1",
        amount=1_200_000,
        role_id=int(role.id),
        enforce_role_budget=True,
    )
    db.commit()

    try:
        reserve_credits(
            db,
            organization_id=int(org.id),
            feature=Feature.ASSESSMENT,
            external_ref="usage-reservation:concurrent-2",
            amount=1_000_000,
            role_id=int(role.id),
            enforce_role_budget=True,
        )
    except InsufficientRoleBudgetError as exc:
        assert exc.available == 800_000
    else:  # pragma: no cover
        raise AssertionError("outstanding hold should consume role capacity")

    assert release_credit_reservation(db, reservation=first) == 1_200_000
    db.commit()


def test_running_score_job_is_not_double_counted_by_its_actual_provider_hold(
    db, monkeypatch
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    org = _org(db, balance=1_000_000)
    role = Role(
        organization_id=org.id,
        name="Exactly One Score",
        monthly_usd_budget_cents=3,  # exactly one SCORE reservation
    )
    candidate = Candidate(
        organization_id=org.id,
        email="one-score@example.test",
        full_name="One Score",
    )
    db.add_all([role, candidate])
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
    )
    db.add(application)
    db.flush()
    db.add(
        CvScoreJob(
            application_id=application.id,
            role_id=role.id,
            status=SCORE_JOB_RUNNING,
        )
    )
    db.commit()

    # Producer admission still treats the running row as a commitment.
    try:
        ensure_role_capacity(
            db,
            organization_id=org.id,
            role_id=role.id,
            required=1,
        )
    except InsufficientRoleBudgetError:
        pass
    else:  # pragma: no cover
        raise AssertionError("soft admission must count active score jobs")

    # The worker's actual hard hold replaces (rather than duplicates) that
    # pseudo-commitment and therefore can consume the one reserved slot.
    reservation = reserve_credits(
        db,
        organization_id=org.id,
        feature=Feature.SCORE,
        external_ref="usage-reservation:running-score-provider-call",
        amount=30_000,
        role_id=role.id,
        enforce_role_budget=True,
    )
    db.commit()

    assert reservation.amount == 30_000


def test_stale_reaper_covers_all_reservation_subfeatures_and_is_idempotent(
    db, monkeypatch,
):
    monkeypatch.setattr("app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True)
    monkeypatch.setattr("app.services.usage_metering_service.settings.USAGE_METER_LIVE", True)
    org = _org(db, balance=1_000_000)
    now = datetime.now(timezone.utc)
    subfeatures = (
        "task_spec_generation",
        "pre_screen",
        "interrogation_classifier",
        "rubric_scoring",
        "agent_sdk_chat",
    )
    stale_refs: list[str] = []
    for index, sub_feature in enumerate(subfeatures):
        ref = f"usage-reservation:stale:{index}"
        reserve_credits(
            db,
            organization_id=int(org.id),
            feature=Feature.ASSESSMENT,
            external_ref=ref,
            amount=100_000,
            metadata={"sub_feature": sub_feature},
        )
        stale_refs.append(ref)
    recent = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        external_ref="usage-reservation:recent",
        amount=100_000,
        metadata={"sub_feature": "agent_sdk_chat"},
    )
    db.commit()
    (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref.in_(stale_refs))
        .update(
            {BillingCreditLedger.created_at: now - timedelta(hours=3)},
            synchronize_session=False,
        )
    )
    db.commit()

    result = release_stale_credit_reservations(
        db,
        stale_after_minutes=120,
        now=now,
    )
    db.commit()
    db.refresh(org)

    assert result["released"] == 5
    assert result["released_credits"] == 500_000
    assert result["by_sub_feature"] == {name: 1 for name in sorted(subfeatures)}
    assert org.credits_balance == 900_000  # only the recent hold remains
    assert (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref
            == f"{recent.external_ref}:settled"
        )
        .count()
        == 0
    )

    again = release_stale_credit_reservations(
        db,
        stale_after_minutes=120,
        now=now,
    )
    db.commit()
    db.refresh(org)
    assert again["released"] == 0
    assert again["scanned"] == 0
    assert again["already_settled"] == 0
    assert org.credits_balance == 900_000


def test_stale_reaper_releases_role_attributed_abandoned_hold(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    org = _org(db, balance=200_000)
    role = Role(organization_id=int(org.id), name="Recovered role hold")
    db.add(role)
    db.commit()
    now = datetime.now(timezone.utc)
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        feature=Feature.GRAPH_SYNC,
        external_ref="usage-reservation:stale-role-hold",
        amount=100_000,
    )
    db.commit()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == reservation.external_ref)
        .one()
    )
    hold.created_at = now - timedelta(hours=3)
    db.commit()

    result = release_stale_credit_reservations(db, now=now)
    db.commit()
    db.refresh(org)

    assert result["released"] == 1
    assert result["released_credits"] == 100_000
    assert result["already_settled"] == 0
    assert result["protected_billable"] == 0
    assert org.credits_balance == 200_000


def test_stale_reaper_protects_hold_with_malformed_role_metadata(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    org = _org(db, balance=200_000)
    now = datetime.now(timezone.utc)
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.GRAPH_SYNC,
        external_ref="usage-reservation:malformed-role-hold",
        amount=100_000,
    )
    db.commit()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == reservation.external_ref)
        .one()
    )
    hold.entry_metadata = {**hold.entry_metadata, "role_id": "not-an-integer"}
    hold.created_at = now - timedelta(hours=3)
    db.commit()

    result = release_stale_credit_reservations(db, now=now)
    db.commit()
    db.refresh(org)

    assert result["released"] == 0
    assert result["already_settled"] == 0
    assert result["protected_billable"] == 1
    assert org.credits_balance == 100_000
    assert (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref
            == f"{reservation.external_ref}:settled"
        )
        .count()
        == 0
    )


def test_stale_reaper_protected_page_cannot_starve_refundable_hold(
    db,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    org = _org(db, balance=1_000_000)
    now = datetime.now(timezone.utc)
    protected = []
    for index in range(3):
        reservation = reserve_credits(
            db,
            organization_id=int(org.id),
            feature=Feature.GRAPH_SYNC,
            external_ref=f"usage-reservation:protected-page:{index}",
            amount=100_000,
            provider="anthropic",
        )
        db.commit()
        assert mark_provider_attempt_started(
            reservation,
            provider="anthropic",
        ) is True
        if index > 0:
            db.expire_all()
            hold = (
                db.query(BillingCreditLedger)
                .filter(BillingCreditLedger.external_ref == reservation.external_ref)
                .one()
            )
            updated_metadata = {
                **hold.entry_metadata,
                "state": PROVIDER_SUCCEEDED_PENDING_STATE,
            }
            updated_metadata.pop("deferred_usage_event", None)
            hold.entry_metadata = updated_metadata
            db.commit()
        protected.append(reservation)
    refundable = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.GRAPH_SYNC,
        external_ref="usage-reservation:after-protected-page",
        amount=100_000,
    )
    db.commit()
    db.query(BillingCreditLedger).filter(
        BillingCreditLedger.external_ref.in_(
            [reservation.external_ref for reservation in protected]
        )
    ).update(
        {BillingCreditLedger.created_at: now - timedelta(hours=4)},
        synchronize_session=False,
    )
    refundable_hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == refundable.external_ref)
        .one()
    )
    refundable_hold.entry_metadata = {
        key: value
        for key, value in refundable_hold.entry_metadata.items()
        if key != "state" and not key.startswith("reservation_")
    }
    refundable_hold.created_at = now - timedelta(hours=3)
    db.commit()

    result = release_stale_credit_reservations(db, now=now, limit=2)
    db.commit()
    db.refresh(org)

    assert result["released"] == 1
    assert org.credits_balance == 700_000
    assert (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref
            == f"{refundable.external_ref}:settled"
        )
        .count()
        == 1
    )
    assert all(
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref
            == f"{reservation.external_ref}:settled"
        )
        .count()
        == 0
        for reservation in protected
    )


def test_stale_reaper_quarantines_malformed_page_then_reaches_valid_hold(
    db,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    org = _org(db, balance=1_000_000)
    now = datetime.now(timezone.utc)
    malformed = []
    original_metadata = ["broken", ["broken"], 7]
    for index in range(3):
        reservation = reserve_credits(
            db,
            organization_id=int(org.id),
            feature=Feature.GRAPH_SYNC,
            external_ref=f"usage-reservation:malformed-page:{index}",
            amount=100_000,
        )
        malformed.append(reservation)
    valid = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.GRAPH_SYNC,
        external_ref="usage-reservation:after-malformed-page",
        amount=100_000,
    )
    db.commit()
    for reservation, original in zip(malformed, original_metadata, strict=True):
        hold = (
            db.query(BillingCreditLedger)
            .filter(BillingCreditLedger.external_ref == reservation.external_ref)
            .one()
        )
        hold.entry_metadata = original
        hold.created_at = now - timedelta(hours=4)
    valid_hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == valid.external_ref)
        .one()
    )
    valid_hold.created_at = now - timedelta(hours=3)
    db.commit()

    first = release_stale_credit_reservations(db, now=now, limit=2)
    db.commit()
    second = release_stale_credit_reservations(db, now=now, limit=2)
    db.commit()
    db.refresh(org)

    assert first["released"] == 0
    assert second["released"] == 1
    assert org.credits_balance == 700_000
    for reservation, original in zip(malformed, original_metadata, strict=True):
        hold = (
            db.query(BillingCreditLedger)
            .filter(BillingCreditLedger.external_ref == reservation.external_ref)
            .one()
        )
        assert hold.entry_metadata["_stale_recovery"]["state"] == (
            "invalid_reservation_identity"
        )
        assert hold.entry_metadata["_stale_recovery"][
            "original_entry_metadata"
        ] == original
        assert (
            db.query(BillingCreditLedger)
            .filter(
                BillingCreditLedger.external_ref
                == f"{reservation.external_ref}:settled"
            )
            .count()
            == 0
        )


def test_stale_reaper_quarantines_bad_receipt_then_reconciles_next_hold(
    db,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE",
        True,
    )
    org = _org(db, balance=1_000_000)
    now = datetime.now(timezone.utc)
    malformed = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.GRAPH_SYNC,
        external_ref="usage-reservation:malformed-deferred-receipt",
        amount=100_000,
        entity_id="candidate:41",
        provider="voyage",
        model="voyage-3",
        request_sha256="c" * 64,
    )
    valid = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.GRAPH_SYNC,
        external_ref="usage-reservation:valid-deferred-receipt",
        amount=100_000,
        entity_id="candidate:42",
        provider="voyage",
        model="voyage-3",
        request_sha256="d" * 64,
    )
    db.commit()
    assert mark_provider_attempt_started(malformed, provider="voyage") is True
    assert mark_provider_usage_succeeded(
        malformed,
        deferred_usage_event={
            "organization_id": int(org.id),
            "feature": Feature.GRAPH_SYNC.value,
            "model": "voyage-3",
        },
        provider="voyage",
    ) is True
    assert mark_provider_attempt_started(valid, provider="voyage") is True
    assert mark_provider_usage_succeeded(
        valid,
        deferred_usage_event={
            "organization_id": int(org.id),
            "feature": Feature.GRAPH_SYNC.value,
            "model": "voyage-3",
            "input_tokens": 100,
            "output_tokens": 0,
            "user_id": None,
            "role_id": None,
            "entity_id": "candidate:42",
            "candidate_id": None,
            "provider": "voyage",
            "request_sha256": "d" * 64,
        },
        provider="voyage",
    ) is True
    db.expire_all()
    db.query(BillingCreditLedger).filter(
        BillingCreditLedger.external_ref == malformed.external_ref
    ).update(
        {BillingCreditLedger.created_at: now - timedelta(hours=4)},
        synchronize_session=False,
    )
    db.query(BillingCreditLedger).filter(
        BillingCreditLedger.external_ref == valid.external_ref
    ).update(
        {BillingCreditLedger.created_at: now - timedelta(hours=3)},
        synchronize_session=False,
    )
    db.commit()

    first = release_stale_credit_reservations(db, now=now, limit=1)
    db.commit()
    second = release_stale_credit_reservations(db, now=now, limit=1)
    db.commit()
    db.expire_all()
    malformed_hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == malformed.external_ref)
        .one()
    )

    assert first["reconciled"] == 0
    assert first["protected_billable"] == 1
    assert second["reconciled"] == 1
    assert malformed_hold.entry_metadata["state"] == (
        PROVIDER_SUCCEEDED_PENDING_STATE
    )
    assert malformed_hold.entry_metadata["_stale_recovery"]["state"] == (
        "invalid_deferred_usage"
    )
    assert (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref
            == f"{malformed.external_ref}:settled"
        )
        .count()
        == 0
    )
    assert db.query(UsageEvent).count() == 1


def test_stale_reaper_reconciles_known_provider_success_instead_of_refunding(
    db, monkeypatch,
):
    monkeypatch.setattr("app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True)
    monkeypatch.setattr("app.services.usage_metering_service.settings.USAGE_METER_LIVE", True)
    org = _org(db, balance=1_000_000)
    now = datetime.now(timezone.utc)
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.GRAPH_SYNC,
        external_ref="usage-reservation:deferred-graph-meter",
        amount=100_000,
        metadata={"sub_feature": "graphiti_voyage"},
        entity_id="candidate:7",
        provider="voyage",
        model="voyage-3",
        request_sha256="a" * 64,
    )
    db.commit()
    assert mark_provider_attempt_started(reservation, provider="voyage") is True
    assert mark_provider_usage_succeeded(
        reservation,
        deferred_usage_event={
            "organization_id": int(org.id),
            "feature": Feature.GRAPH_SYNC.value,
            "model": "voyage-3",
            "input_tokens": 500_000,
            "output_tokens": 0,
            "provider_cost_usd_micro": 12_345,
            "user_id": None,
            "role_id": None,
            "entity_id": "candidate:7",
            "candidate_id": None,
            "provider": "voyage",
            "request_sha256": "a" * 64,
            "metadata": {"provider": "voyage", "trace_id": "graph:7"},
        },
        provider="voyage",
    ) is True
    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == reservation.external_ref)
        .one()
    )
    assert hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_PENDING_STATE
    hold.created_at = now - timedelta(hours=3)
    db.commit()

    result = release_stale_credit_reservations(db, now=now)
    db.commit()
    db.expire_all()
    event = db.query(UsageEvent).one()
    refreshed_org = db.query(Organization).filter(Organization.id == org.id).one()

    assert result["released"] == 0
    assert result["reconciled"] == 1
    assert result["protected_billable"] == 0
    assert event.event_metadata["deferred_metering_recovery"] is True
    assert event.event_metadata["credit_reservation"]["state"] == "settled"
    assert event.cost_usd_micro == 12_345
    assert refreshed_org.credits_balance == 1_000_000 - int(event.credits_charged)
    assert (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref
            == f"{reservation.external_ref}:settled"
        )
        .count()
        == 1
    )

    again = release_stale_credit_reservations(db, now=now)
    db.commit()
    assert again["scanned"] == 0
    assert again["reconciled"] == 0
    assert db.query(UsageEvent).count() == 1


def test_stale_reaper_reconciles_role_attributed_provider_success(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE", True
    )
    org = _org(db, balance=1_000_000)
    role = Role(organization_id=int(org.id), name="Recovered provider role")
    db.add(role)
    db.commit()
    now = datetime.now(timezone.utc)
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        feature=Feature.GRAPH_SYNC,
        external_ref="usage-reservation:deferred-role-graph-meter",
        amount=100_000,
        entity_id="candidate:8",
        provider="voyage",
        model="voyage-3",
        request_sha256="b" * 64,
    )
    db.commit()
    assert mark_provider_attempt_started(reservation, provider="voyage") is True
    assert mark_provider_usage_succeeded(
        reservation,
        deferred_usage_event={
            "organization_id": int(org.id),
            "feature": Feature.GRAPH_SYNC.value,
            "model": "voyage-3",
            "input_tokens": 500_000,
            "output_tokens": 0,
            "provider_cost_usd_micro": 12_345,
            "user_id": None,
            "role_id": int(role.id),
            "entity_id": "candidate:8",
            "candidate_id": None,
            "provider": "voyage",
            "request_sha256": "b" * 64,
            "metadata": {"provider": "voyage", "trace_id": "graph:8"},
        },
        provider="voyage",
    ) is True
    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == reservation.external_ref)
        .one()
    )
    hold.created_at = now - timedelta(hours=3)
    db.commit()

    result = release_stale_credit_reservations(db, now=now)
    db.commit()
    db.expire_all()
    event = db.query(UsageEvent).one()

    assert result["reconciled"] == 1
    assert result["protected_billable"] == 0
    assert event.role_id == int(role.id)
    assert event.event_metadata["credit_reservation"]["state"] == "settled"
    assert (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref
            == f"{reservation.external_ref}:settled"
        )
        .count()
        == 1
    )


def test_stale_reaper_never_refunds_provider_success_with_unknown_usage(
    db, monkeypatch,
):
    monkeypatch.setattr("app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True)
    monkeypatch.setattr("app.services.usage_metering_service.settings.USAGE_METER_LIVE", True)
    org = _org(db, balance=200_000)
    now = datetime.now(timezone.utc)
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.GRAPH_SYNC,
        external_ref="usage-reservation:unknown-graph-usage",
        amount=100_000,
        provider="anthropic",
    )
    db.commit()
    assert mark_provider_attempt_started(reservation, provider="anthropic") is True
    assert mark_provider_usage_succeeded(
        reservation,
        deferred_usage_event=None,
        provider="anthropic",
        provider_request_id="msg-unknown-usage",
    ) is True
    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == reservation.external_ref)
        .one()
    )
    assert (
        hold.entry_metadata["state"]
        == PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE
    )
    hold.created_at = now - timedelta(hours=3)
    db.commit()

    result = release_stale_credit_reservations(db, now=now)
    db.commit()
    db.expire_all()

    assert result["released"] == 0
    assert result["reconciled"] == 0
    assert result["protected_billable"] == 1
    assert db.query(UsageEvent).count() == 0
    assert db.query(Organization).filter(Organization.id == org.id).one().credits_balance == 100_000
    assert (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref
            == f"{reservation.external_ref}:settled"
        )
        .count()
        == 0
    )


def test_stale_reaper_protects_ambiguous_started_provider_attempt(
    db, monkeypatch,
):
    """Marker + meter failure cannot turn a returned provider call free."""

    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    org = _org(db, balance=200_000)
    now = datetime.now(timezone.utc)
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.GRAPH_SYNC,
        external_ref="usage-reservation:ambiguous-provider-attempt",
        amount=100_000,
        provider="anthropic",
    )
    db.commit()
    assert mark_provider_attempt_started(
        reservation,
        provider="anthropic",
    ) is True
    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == reservation.external_ref)
        .one()
    )
    assert hold.entry_metadata["state"] == PROVIDER_ATTEMPT_STARTED_STATE
    hold.created_at = now - timedelta(hours=3)
    db.commit()

    result = release_stale_credit_reservations(db, now=now)
    db.commit()
    db.expire_all()

    assert result["released"] == 0
    assert result["reconciled"] == 0
    assert result["protected_billable"] == 1
    assert db.query(UsageEvent).count() == 0
    assert (
        db.query(Organization)
        .filter(Organization.id == org.id)
        .one()
        .credits_balance
        == 100_000
    )


def test_broad_caller_release_cannot_refund_provider_success_marker(
    db, monkeypatch,
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    org = _org(db, balance=200_000)
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.GRAPH_SYNC,
        external_ref="usage-reservation:provider-success-no-refund",
        amount=100_000,
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        request_sha256="a" * 64,
    )
    db.commit()
    assert mark_provider_attempt_started(reservation, provider="anthropic")
    assert mark_provider_usage_succeeded(
        reservation,
        deferred_usage_event={
            "organization_id": int(org.id),
            "feature": Feature.GRAPH_SYNC.value,
            "model": "claude-haiku-4-5-20251001",
            "input_tokens": 100,
            "output_tokens": 10,
            "user_id": None,
            "role_id": None,
            "entity_id": None,
            "candidate_id": None,
            "provider": "anthropic",
            "request_sha256": "a" * 64,
        },
        provider="anthropic",
    )

    # A later parser/validation exception may execute a broad caller cleanup.
    # It must not turn the already-returned provider call free.
    assert release_credit_reservation(
        db,
        reservation=reservation,
        reason="post_response_validation_failed",
    ) == 0
    db.commit()
    db.expire_all()
    assert db.get(Organization, int(org.id)).credits_balance == 100_000
    assert (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref
            == f"{reservation.external_ref}:settled"
        )
        .count()
        == 0
    )


def test_late_provider_result_after_stale_release_is_charged_once_without_overdraft(
    db, monkeypatch,
):
    monkeypatch.setattr("app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True)
    monkeypatch.setattr("app.services.usage_metering_service.settings.USAGE_METER_LIVE", True)
    org = _org(db, balance=200_000)
    now = datetime.now(timezone.utc)
    reservation = reserve_credits(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        external_ref="usage-reservation:late-result",
        amount=200_000,
        metadata={"sub_feature": "agent_sdk_chat"},
        model="claude-haiku-4-5-20251001",
    )
    db.commit()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == reservation.external_ref)
        .one()
    )
    hold.created_at = now - timedelta(hours=3)
    db.commit()

    released = release_stale_credit_reservations(db, now=now)
    db.commit()
    assert released["released"] == 1
    db.refresh(org)
    assert org.credits_balance == 200_000

    event = record_event(
        db,
        organization_id=int(org.id),
        feature=Feature.ASSESSMENT,
        model="claude-haiku-4-5-20251001",
        input_tokens=20_000,
        output_tokens=2_000,
        credit_reservation=reservation,
    )
    db.commit()
    db.refresh(org)
    db.refresh(event)

    assert org.credits_balance == max(200_000 - int(event.credits_charged), 0)
    assert org.credits_balance >= 0
    assert event.event_metadata["credit_reservation"]["state"] == (
        "late_settled_after_release"
    )
    assert (
        db.query(BillingCreditLedger)
        .filter(
            BillingCreditLedger.external_ref
            == "usage-reservation:late-result:late-settled"
        )
        .count()
        == 1
    )
