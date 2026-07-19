"""Exact, secret-free Workable assessment-result delivery contracts."""

from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.assessment import Assessment, AssessmentStatus
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.components.assessments.submission_provider_boundary import (
    finalize_submission_snapshot,
    snapshot_terminal_submission,
)
from app.components.assessments.result_delivery_contracts import (
    AssessmentResultDispatch,
    DELIVERY_CANCELLED,
    DELIVERY_DISPATCH_FAILED,
    valid_receipt,
)
from app.components.assessments import result_delivery_outbox
from app.services import assessment_result_delivery_executor
from app.tasks import assessment_result_delivery_tasks
from app.services.assessment_result_workable_delivery import (
    DELIVERY_CONFIRMED,
    DELIVERY_DISPATCHING,
    DELIVERY_PENDING,
    DELIVERY_PROVIDER_STARTED,
    DELIVERY_RECONCILIATION_REQUIRED,
    DELIVERY_RETRY_WAIT,
    authorize_assessment_result_delivery,
    deliver_assessment_result,
    enqueue_assessment_result_delivery,
    publish_assessment_result_delivery,
    run_assessment_result_delivery_task,
    sweep_assessment_result_deliveries,
)
from app.tasks.celery_app import celery_app
from app.tasks import assessment_tasks


def _settings():
    return SimpleNamespace(
        MVP_DISABLE_WORKABLE=False,
        FRONTEND_URL="https://app.example.test",
    )


def _seed(db, *, token: str = "current-secret-token") -> tuple[int, int]:
    org = Organization(
        name="Assessment result delivery",
        slug=f"assessment-result-delivery-{id(db)}-{token}",
        workable_connected=True,
        workable_access_token=token,
        workable_subdomain="delivery-org",
        workable_config={
            "workable_writeback": True,
            "workable_actor_member_id": "member-current",
        },
    )
    db.add(org)
    db.flush()
    task = Task(
        organization_id=int(org.id),
        name="Assessment result task",
        task_key=f"assessment-result-task-{id(db)}-{token}",
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        organization_id=int(org.id),
        task_id=int(task.id),
        token=f"assessment-result-{id(db)}-{token}",
        status=AssessmentStatus.COMPLETED,
        completed_at=datetime.now(timezone.utc),
        duration_minutes=42,
        score=8.75,
        tests_passed=7,
        tests_total=8,
        workable_candidate_id="candidate-123",
        posted_to_workable=False,
        is_voided=False,
    )
    db.add(assessment)
    db.commit()
    return int(assessment.id), int(org.id)


def _factory(db, observed_sessions: list | None = None):
    maker = sessionmaker(bind=db.get_bind(), expire_on_commit=True)

    def create():
        session = maker()
        if observed_sessions is not None:
            observed_sessions.append(session)
        return session

    return create


class _Task:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls: list[dict] = []

    def delay(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("broker unavailable with secret-looking detail")


def _module_imports(module) -> set[str]:
    imports: set[str] = set()
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            source = str(node.module or "")
            imports.update(f"{source}.{alias.name}" for alias in node.names)
    return imports


def test_result_delivery_task_executor_graph_has_no_back_edge():
    outbox_imports = _module_imports(result_delivery_outbox)
    task_imports = _module_imports(assessment_result_delivery_tasks)
    executor_imports = _module_imports(assessment_result_delivery_executor)

    assert not any("tasks.assessment_tasks" in name for name in outbox_imports)
    assert any("assessment_result_delivery_executor" in name for name in task_imports)
    assert not any("result_delivery_outbox" in name for name in task_imports)
    assert not any("assessment_result_workable_delivery" in name for name in task_imports)
    assert not any("result_delivery_outbox" in name for name in executor_imports)
    assert not any("tasks." in name for name in executor_imports)
    assert assessment_tasks.post_results_to_workable is (
        assessment_result_delivery_tasks.post_results_to_workable
    )
    assert assessment_tasks.post_results_to_workable.name == (
        "app.tasks.assessment_tasks.post_results_to_workable"
    )


def test_default_publisher_resolves_live_monkeypatchable_celery_task(
    db, monkeypatch,
):
    assessment_id, organization_id = _seed(db, token="default-publisher")
    with _factory(db)() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )
    assert dispatch is not None
    calls = []
    monkeypatch.setattr(
        assessment_tasks.post_results_to_workable,
        "delay",
        lambda **kwargs: calls.append(kwargs),
    )

    outcome = publish_assessment_result_delivery(
        dispatch,
        session_factory=_factory(db),
    )

    assert outcome == "published"
    assert calls == [
        {
            "assessment_id": assessment_id,
            "organization_id": organization_id,
            "operation_id": dispatch.operation_id,
        }
    ]


def test_enqueue_persists_secret_free_intent_and_publishes_only_identity(db):
    assessment_id, organization_id = _seed(db)
    task = _Task()

    result = enqueue_assessment_result_delivery(
        assessment_id=assessment_id,
        organization_id=organization_id,
        request_id="request-123",
        settings_obj=_settings(),
        task=task,
        session_factory=_factory(db),
    )

    assert result["status"] == "published"
    assert task.calls == [
        {
            "assessment_id": assessment_id,
            "organization_id": organization_id,
            "operation_id": result["operation_id"],
        }
    ]
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert row.workable_result_delivery_status == DELIVERY_DISPATCHING
    serialized = json.dumps(row.workable_result_delivery_receipt, sort_keys=True)
    assert "current-secret-token" not in serialized
    assert "access_token" not in serialized
    assert row.workable_result_delivery_receipt["intent"]["assessment_data"] == {
        "score": 8.75,
        "tests_passed": 7,
        "tests_total": 8,
        "time_taken": 42,
        "results_url": f"https://app.example.test/assessments/{assessment_id}",
    }


def test_delivery_loads_rotated_credential_detached_then_confirms_exact_success(db):
    assessment_id, organization_id = _seed(db, token="old-token")
    observed_sessions: list = []
    factory = _factory(db, observed_sessions)
    with factory() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )
    assert dispatch is not None
    org = db.query(Organization).filter(Organization.id == organization_id).one()
    org.workable_access_token = "rotated-current-token"
    db.commit()
    provider_calls: list[dict] = []

    class Adapter:
        def post_assessment_result(self, **kwargs):
            assert all(not session.in_transaction() for session in observed_sessions)
            provider_calls.append(kwargs)
            return {"success": True, "response": {"id": "remote-comment"}}

    def build_adapter(*, access_token, subdomain):
        assert access_token == "rotated-current-token"
        assert subdomain == "delivery-org"
        assert all(not session.in_transaction() for session in observed_sessions)
        return Adapter()

    result = deliver_assessment_result(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=dispatch.operation_id,
        settings_obj=_settings(),
        adapter_builder=build_adapter,
        session_factory=factory,
    )
    replay = deliver_assessment_result(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=dispatch.operation_id,
        settings_obj=_settings(),
        adapter_builder=lambda **_kwargs: pytest.fail("confirmed result replayed"),
        session_factory=factory,
    )

    assert result == {"status": DELIVERY_CONFIRMED, "success": True}
    assert replay == {"status": DELIVERY_CONFIRMED}
    assert provider_calls == [
        {
            "candidate_id": "candidate-123",
            "member_id": "member-current",
            "assessment_data": {
                "score": 8.75,
                "tests_passed": 7,
                "tests_total": 8,
                "time_taken": 42,
                "results_url": f"https://app.example.test/assessments/{assessment_id}",
            },
        }
    ]
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert row.posted_to_workable is True
    assert row.posted_to_workable_at is not None
    assert row.workable_result_delivery_status == DELIVERY_CONFIRMED
    assert "rotated-current-token" not in json.dumps(
        row.workable_result_delivery_receipt, sort_keys=True
    )


def test_ambiguous_provider_call_is_fenced_and_never_automatically_replayed(
    db, caplog
):
    assessment_id, organization_id = _seed(db)
    factory = _factory(db)
    with factory() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )
    assert dispatch is not None
    calls = 0

    class Adapter:
        def post_assessment_result(self, **_kwargs):
            nonlocal calls
            calls += 1
            raise TimeoutError("current-secret-token response lost after request write")

    first = deliver_assessment_result(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=dispatch.operation_id,
        settings_obj=_settings(),
        adapter_builder=lambda **_kwargs: Adapter(),
        session_factory=factory,
    )
    second = deliver_assessment_result(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=dispatch.operation_id,
        settings_obj=_settings(),
        adapter_builder=lambda **_kwargs: Adapter(),
        session_factory=factory,
    )

    assert first["status"] == DELIVERY_RECONCILIATION_REQUIRED
    assert second["status"] == DELIVERY_RECONCILIATION_REQUIRED
    assert calls == 1
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert row.posted_to_workable is False
    assert row.workable_result_delivery_receipt["provider_outcome_uncertain"] is True
    assert "response lost" not in json.dumps(row.workable_result_delivery_receipt)
    assert "current-secret-token" not in caplog.text


def test_duplicate_delivery_during_live_call_does_not_poison_exact_success(db):
    assessment_id, organization_id = _seed(db)
    factory = _factory(db)
    with factory() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )
    assert dispatch is not None
    duplicate_results: list[dict] = []

    class Adapter:
        def post_assessment_result(self, **_kwargs):
            duplicate_results.append(
                deliver_assessment_result(
                    assessment_id=assessment_id,
                    organization_id=organization_id,
                    operation_id=dispatch.operation_id,
                    settings_obj=_settings(),
                    adapter_builder=lambda **_nested: self,
                    session_factory=factory,
                )
            )
            return {"success": True}

    result = deliver_assessment_result(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=dispatch.operation_id,
        settings_obj=_settings(),
        adapter_builder=lambda **_kwargs: Adapter(),
        session_factory=factory,
    )

    assert duplicate_results == [{"status": "provider_call_in_progress"}]
    assert result["status"] == DELIVERY_CONFIRMED
    db.expire_all()
    assert db.query(Assessment).filter(Assessment.id == assessment_id).one().posted_to_workable is True


def test_definitive_rate_limit_is_the_only_bounded_provider_retry(db):
    assessment_id, organization_id = _seed(db)
    factory = _factory(db)
    with factory() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )
    assert dispatch is not None
    calls = 0

    class Adapter:
        def post_assessment_result(self, **_kwargs):
            nonlocal calls
            calls += 1
            return {
                "success": False,
                "error_code": "workable_rate_limited",
                "status_code": 429,
            }

    first = deliver_assessment_result(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=dispatch.operation_id,
        settings_obj=_settings(),
        adapter_builder=lambda **_kwargs: Adapter(),
        session_factory=factory,
    )
    immediate = deliver_assessment_result(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=dispatch.operation_id,
        settings_obj=_settings(),
        adapter_builder=lambda **_kwargs: Adapter(),
        session_factory=factory,
    )

    assert first["status"] == DELIVERY_RETRY_WAIT
    assert immediate["status"] == "not_due"
    assert calls == 1
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert row.workable_result_delivery_next_attempt_at is not None
    assert row.posted_to_workable is False


@pytest.mark.parametrize(
    ("status_code", "error_code", "expected"),
    [
        (401, "workable_authorization_failed", "failed"),
        (503, "workable_unavailable", DELIVERY_RECONCILIATION_REQUIRED),
        (408, "workable_request_rejected", DELIVERY_RECONCILIATION_REQUIRED),
    ],
)
def test_only_proven_rejections_are_terminal(status_code, error_code, expected, db):
    assessment_id, organization_id = _seed(db, token=f"status-{status_code}")
    factory = _factory(db)
    with factory() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )
    calls = 0

    class Adapter:
        def post_assessment_result(self, **_kwargs):
            nonlocal calls
            calls += 1
            return {
                "success": False,
                "error_code": error_code,
                "status_code": status_code,
            }

    first = deliver_assessment_result(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=dispatch.operation_id,
        settings_obj=_settings(),
        adapter_builder=lambda **_kwargs: Adapter(),
        session_factory=factory,
    )
    second = deliver_assessment_result(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=dispatch.operation_id,
        settings_obj=_settings(),
        adapter_builder=lambda **_kwargs: Adapter(),
        session_factory=factory,
    )

    assert first["status"] == expected
    assert second["status"] == expected
    assert calls == 1


def test_missing_current_credential_waits_safely_without_calling_provider(db):
    assessment_id, organization_id = _seed(db)
    factory = _factory(db)
    with factory() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )
    org = db.query(Organization).filter(Organization.id == organization_id).one()
    org.workable_access_token = None
    db.commit()

    result = deliver_assessment_result(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=dispatch.operation_id,
        settings_obj=_settings(),
        adapter_builder=lambda **_kwargs: pytest.fail("provider must not be built"),
        session_factory=factory,
    )

    assert result["status"] == DELIVERY_RETRY_WAIT
    db.expire_all()
    receipt = db.query(Assessment).filter(Assessment.id == assessment_id).one().workable_result_delivery_receipt
    assert receipt["configuration_attempts"] == 1
    assert receipt["provider_called"] is False


def test_application_role_actor_override_is_preserved_when_assessment_role_is_null(db):
    assessment_id, organization_id = _seed(db)
    candidate = Candidate(
        organization_id=organization_id,
        email=f"result-role-{assessment_id}@example.test",
    )
    role = Role(
        organization_id=organization_id,
        name="Result role override",
        workable_actor_member_id="member-role-override",
    )
    db.add_all([candidate, role])
    db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        candidate_id=int(candidate.id),
        role_id=int(role.id),
    )
    db.add(application)
    db.flush()
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assessment.application_id = int(application.id)
    assessment.role_id = None
    db.commit()
    factory = _factory(db)

    with factory() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )

    assert dispatch is not None
    db.expire_all()
    receipt = db.query(Assessment).filter(Assessment.id == assessment_id).one().workable_result_delivery_receipt
    assert receipt["intent"]["member_id"] == "member-role-override"


def test_cross_org_assessment_role_cannot_supply_workable_actor(db):
    assessment_id, organization_id = _seed(db, token="cross-org-direct-role")
    other_org = Organization(
        name="Other result delivery organization",
        slug=f"other-result-delivery-{assessment_id}",
    )
    db.add(other_org)
    db.flush()
    other_role = Role(
        organization_id=int(other_org.id),
        name="Foreign result role",
        workable_actor_member_id="member-from-other-org",
    )
    db.add(other_role)
    db.flush()
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assessment.role_id = int(other_role.id)
    db.commit()

    with _factory(db)() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )

    assert dispatch is not None
    db.expire_all()
    receipt = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .one()
        .workable_result_delivery_receipt
    )
    assert receipt["intent"]["member_id"] == "member-current"


def test_cross_org_application_cannot_supply_workable_actor_role(db):
    assessment_id, organization_id = _seed(db, token="cross-org-application")
    current_role = Role(
        organization_id=organization_id,
        name="Current org role on foreign application",
        workable_actor_member_id="member-from-foreign-application",
    )
    other_org = Organization(
        name="Foreign application organization",
        slug=f"foreign-result-application-{assessment_id}",
    )
    db.add_all([current_role, other_org])
    db.flush()
    other_candidate = Candidate(
        organization_id=int(other_org.id),
        email=f"foreign-result-{assessment_id}@example.test",
    )
    db.add(other_candidate)
    db.flush()
    foreign_application = CandidateApplication(
        organization_id=int(other_org.id),
        candidate_id=int(other_candidate.id),
        role_id=int(current_role.id),
    )
    db.add(foreign_application)
    db.flush()
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assessment.role_id = None
    assessment.application_id = int(foreign_application.id)
    db.commit()

    with _factory(db)() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )

    assert dispatch is not None
    db.expire_all()
    receipt = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .one()
        .workable_result_delivery_receipt
    )
    assert receipt["intent"]["member_id"] == "member-current"


def test_publish_failure_is_recovered_by_bounded_sweep_without_secrets(db, caplog):
    assessment_id, organization_id = _seed(db)
    factory = _factory(db)
    failed_task = _Task(fail=True)
    result = enqueue_assessment_result_delivery(
        assessment_id=assessment_id,
        organization_id=organization_id,
        settings_obj=_settings(),
        task=failed_task,
        session_factory=factory,
    )
    assert result["status"] == "publish_failed"
    assert "secret-looking detail" not in caplog.text
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert row.workable_result_delivery_status == DELIVERY_PENDING
    row.workable_result_delivery_next_attempt_at = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    db.commit()
    recovered_task = _Task()

    summary = sweep_assessment_result_deliveries(
        limit=10,
        task=recovered_task,
        session_factory=factory,
    )

    assert summary["published"] == 1
    assert recovered_task.calls == [
        {
            "assessment_id": assessment_id,
            "organization_id": organization_id,
            "operation_id": result["operation_id"],
        }
    ]
    assert "token" not in json.dumps(recovered_task.calls)


def test_late_accepted_message_can_finish_after_broker_recovery_exhaustion(db):
    assessment_id, organization_id = _seed(db)
    factory = _factory(db)
    with factory() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    receipt = dict(row.workable_result_delivery_receipt)
    receipt["status"] = DELIVERY_DISPATCH_FAILED
    receipt["publish_attempts"] = 8
    row.workable_result_delivery_receipt = receipt
    row.workable_result_delivery_status = DELIVERY_DISPATCH_FAILED
    db.commit()

    result = deliver_assessment_result(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=dispatch.operation_id,
        settings_obj=_settings(),
        adapter_builder=lambda **_kwargs: SimpleNamespace(
            post_assessment_result=lambda **_payload: {"success": True}
        ),
        session_factory=factory,
    )

    assert result["status"] == DELIVERY_CONFIRMED


def test_stale_started_provider_call_requires_reconciliation_not_republish(db):
    assessment_id, organization_id = _seed(db)
    factory = _factory(db)
    with factory() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )
    assert dispatch is not None
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    receipt = dict(row.workable_result_delivery_receipt)
    receipt["status"] = DELIVERY_PROVIDER_STARTED
    receipt["provider_called"] = True
    row.workable_result_delivery_receipt = receipt
    row.workable_result_delivery_status = DELIVERY_PROVIDER_STARTED
    row.workable_result_delivery_claimed_at = datetime.now(timezone.utc) - timedelta(
        hours=1
    )
    db.commit()
    task = _Task()

    summary = sweep_assessment_result_deliveries(
        limit=10,
        task=task,
        session_factory=factory,
    )

    assert summary["reconciliation_required"] == 1
    assert task.calls == []
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert row.workable_result_delivery_status == DELIVERY_RECONCILIATION_REQUIRED
    assert row.posted_to_workable is False


def test_legacy_payload_binds_exact_report_but_never_blindly_sends(db):
    assessment_id, organization_id = _seed(db, token="current-token-only")
    factory = _factory(db)
    result = run_assessment_result_delivery_task(
        access_token="serialized-legacy-secret",
        subdomain="delivery-org",
        candidate_id="candidate-123",
        assessment_data={
            "score": 1,
            "results_url": f"https://old.example.test/assessments/{assessment_id}",
        },
        member_id="stale-member",
        request_id="legacy-request",
        settings_obj=_settings(),
        adapter_builder=lambda **_kwargs: pytest.fail(
            "legacy payload must never build a provider adapter"
        ),
        session_factory=factory,
    )

    assert result == {
        "status": "legacy_reconciliation_required",
        "success": False,
    }
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    serialized = json.dumps(row.workable_result_delivery_receipt)
    assert "serialized-legacy-secret" not in serialized
    assert "stale-member" not in serialized
    assert row.workable_result_delivery_receipt["provider_called"] is False
    assert row.workable_result_delivery_status == "legacy_reconciliation_required"
    assert row.workable_result_delivery_receipt["intent"]["assessment_data"][
        "score"
    ] == 8.75
    legacy_evidence = row.workable_result_delivery_receipt[
        "legacy_payload_evidence"
    ]
    assert legacy_evidence["candidate_id"] == "candidate-123"
    assert legacy_evidence["subdomain"] == "delivery-org"
    assert legacy_evidence["assessment_data"] == {
        "score": 1,
        "results_url": f"https://old.example.test/assessments/{assessment_id}",
    }
    assert len(legacy_evidence["payload_sha256"]) == 64
    assert row.organization_id == organization_id


def test_legacy_payload_with_unknown_or_secret_fields_is_fenced_without_storage(db):
    assessment_id, _organization_id = _seed(db, token="legacy-unsafe")

    result = run_assessment_result_delivery_task(
        access_token="serialized-secret",
        subdomain="delivery-org",
        candidate_id="candidate-123",
        assessment_data={
            "results_url": f"https://old.example.test/assessments/{assessment_id}",
            "access_token": "must-not-be-retained",
        },
        session_factory=_factory(db),
    )

    assert result == {"status": "legacy_payload_unsafe", "success": False}
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert row.workable_result_delivery_status is None
    assert row.workable_result_delivery_receipt is None


def test_submission_finalization_returns_only_primitive_delivery_identity(db):
    assessment_id, organization_id = _seed(db, token="must-not-cross-commit")
    snapshot = snapshot_terminal_submission(
        db,
        assessment_id=assessment_id,
        terminal_statuses={AssessmentStatus.COMPLETED},
    )

    side_effects = finalize_submission_snapshot(
        db,
        snapshot,
        terminal_statuses={AssessmentStatus.COMPLETED},
        retry_scoring=False,
        grading_incomplete=False,
        suppress_completion_side_effects=False,
        request_id="finalization-request",
        settings_obj=_settings(),
    )

    assert side_effects.workable_payload is not None
    assert side_effects.workable_payload["assessment_id"] == assessment_id
    assert side_effects.workable_payload["organization_id"] == organization_id
    assert side_effects.workable_payload["operation_id"]
    assert "must-not-cross-commit" not in repr(side_effects)


def test_finalization_commit_closes_crash_window_and_sweep_recovers(db):
    assessment_id, organization_id = _seed(db, token="atomic-secret")
    snapshot = snapshot_terminal_submission(
        db,
        assessment_id=assessment_id,
        terminal_statuses={AssessmentStatus.COMPLETED},
    )
    task = _Task()

    side_effects = finalize_submission_snapshot(
        db,
        snapshot,
        terminal_statuses={AssessmentStatus.COMPLETED},
        retry_scoring=False,
        grading_incomplete=False,
        suppress_completion_side_effects=False,
        request_id="crash-before-publish",
        settings_obj=_settings(),
    )

    assert task.calls == []
    assert side_effects.workable_payload is not None
    dispatch = AssessmentResultDispatch(**side_effects.workable_payload)
    db.expire_all()
    stored = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    receipt = dict(stored.workable_result_delivery_receipt)
    assert stored.status == AssessmentStatus.COMPLETED
    assert stored.workable_result_delivery_status == DELIVERY_PENDING
    assert valid_receipt(receipt, dispatch=dispatch)
    assert receipt["request_id"] == "crash-before-publish"
    assert "atomic-secret" not in json.dumps(receipt, sort_keys=True)
    db.rollback()

    result = sweep_assessment_result_deliveries(
        task=task,
        session_factory=_factory(db),
    )

    assert result == {
        "scanned": 1,
        "published": 1,
        "reconciliation_required": 0,
    }
    assert task.calls == [side_effects.workable_payload]


def test_result_delivery_sweep_is_registered_on_beat():
    entry = celery_app.conf.beat_schedule[
        "sweep-assessment-result-workable-deliveries-every-minute"
    ]
    assert entry["task"] == (
        "app.tasks.assessment_tasks.sweep_assessment_result_deliveries"
    )
    assert entry["schedule"] == 60.0


@pytest.mark.parametrize(
    "malformed_receipt",
    [
        "not-a-json-object",
        ["unexpected", "list"],
        {
            "operation_id": "malformed-op",
            "intent": {"assessment_id": [], "organization_id": {}},
            "intent_sha256": "not-valid",
            "status": DELIVERY_PENDING,
        },
    ],
)
def test_malformed_receipt_fails_closed_without_breaking_finalization(
    db, malformed_receipt,
):
    assessment_id, _organization_id = _seed(db)
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    row.workable_result_delivery_receipt = malformed_receipt
    row.workable_result_delivery_status = DELIVERY_PENDING
    db.commit()
    snapshot = snapshot_terminal_submission(
        db,
        assessment_id=assessment_id,
        terminal_statuses={AssessmentStatus.COMPLETED},
    )

    side_effects = finalize_submission_snapshot(
        db,
        snapshot,
        terminal_statuses={AssessmentStatus.COMPLETED},
        retry_scoring=False,
        grading_incomplete=False,
        suppress_completion_side_effects=False,
        settings_obj=_settings(),
    )

    assert side_effects.workable_payload is None
    db.expire_all()
    stored = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert stored.workable_result_delivery_status == DELIVERY_RECONCILIATION_REQUIRED
    assert stored.workable_result_delivery_receipt["last_error_code"] == (
        "delivery_receipt_invalid"
    )


@pytest.mark.parametrize(
    ("field", "malformed_value"),
    [
        ("publish_attempts", {}),
        ("provider_attempts", []),
        ("configuration_attempts", "many"),
        ("intent_revisions", [1]),
        ("prior_intent_sha256", {"not": "a list"}),
        ("status", "unknown_delivery_state"),
        ("version", 2),
    ],
)
def test_valid_intent_with_malformed_metadata_fails_closed_at_finalization(
    db, field, malformed_value,
):
    assessment_id, organization_id = _seed(db, token=f"malformed-{field}")
    dispatch = authorize_assessment_result_delivery(
        db,
        assessment_id=assessment_id,
        organization_id=organization_id,
        settings_obj=_settings(),
    )
    assert dispatch is not None
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    receipt = dict(row.workable_result_delivery_receipt)
    assert receipt["intent_sha256"]
    receipt[field] = malformed_value
    row.workable_result_delivery_receipt = receipt
    db.commit()
    snapshot = snapshot_terminal_submission(
        db,
        assessment_id=assessment_id,
        terminal_statuses={AssessmentStatus.COMPLETED},
    )

    side_effects = finalize_submission_snapshot(
        db,
        snapshot,
        terminal_statuses={AssessmentStatus.COMPLETED},
        retry_scoring=False,
        grading_incomplete=False,
        suppress_completion_side_effects=False,
        settings_obj=_settings(),
    )

    assert side_effects.workable_payload is None
    db.expire_all()
    stored = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert stored.workable_result_delivery_status == DELIVERY_RECONCILIATION_REQUIRED
    assert stored.workable_result_delivery_receipt["last_error_code"] == (
        "delivery_receipt_invalid"
    )


def test_sweep_fences_bad_metadata_without_aborting_other_rows(db):
    bad_id, bad_org_id = _seed(db, token="malformed-sweep")
    good_id, good_org_id = _seed(db, token="healthy-sweep")
    bad_dispatch = authorize_assessment_result_delivery(
        db,
        assessment_id=bad_id,
        organization_id=bad_org_id,
        settings_obj=_settings(),
    )
    good_dispatch = authorize_assessment_result_delivery(
        db,
        assessment_id=good_id,
        organization_id=good_org_id,
        settings_obj=_settings(),
    )
    assert bad_dispatch is not None and good_dispatch is not None
    bad = db.query(Assessment).filter(Assessment.id == bad_id).one()
    malformed = dict(bad.workable_result_delivery_receipt)
    malformed["prior_intent_sha256"] = {"not": "a list"}
    bad.workable_result_delivery_receipt = malformed
    db.commit()
    task = _Task()

    summary = sweep_assessment_result_deliveries(
        limit=10,
        task=task,
        session_factory=_factory(db),
    )

    assert summary == {
        "scanned": 2,
        "published": 1,
        "reconciliation_required": 1,
    }
    assert task.calls == [
        {
            "assessment_id": good_id,
            "organization_id": good_org_id,
            "operation_id": good_dispatch.operation_id,
        }
    ]
    db.expire_all()
    stored_bad = db.query(Assessment).filter(Assessment.id == bad_id).one()
    assert stored_bad.workable_result_delivery_status == (
        DELIVERY_RECONCILIATION_REQUIRED
    )


def test_column_and_receipt_status_mismatch_is_fenced_by_sweep(db):
    assessment_id, organization_id = _seed(db, token="status-mismatch")
    dispatch = authorize_assessment_result_delivery(
        db,
        assessment_id=assessment_id,
        organization_id=organization_id,
        settings_obj=_settings(),
    )
    assert dispatch is not None
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    receipt = dict(row.workable_result_delivery_receipt)
    receipt["status"] = DELIVERY_RETRY_WAIT
    row.workable_result_delivery_receipt = receipt
    row.workable_result_delivery_status = DELIVERY_PENDING
    db.commit()

    summary = sweep_assessment_result_deliveries(
        task=_Task(),
        session_factory=_factory(db),
    )

    assert summary["reconciliation_required"] == 1
    db.expire_all()
    stored = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert stored.workable_result_delivery_status == (
        DELIVERY_RECONCILIATION_REQUIRED
    )
    assert stored.workable_result_delivery_receipt["last_error_code"] == (
        "delivery_receipt_invalid"
    )


def test_missing_candidate_config_wait_survives_more_than_eight_handoffs(db):
    assessment_id, organization_id = _seed(db, token="long-config-wait")
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    row.workable_candidate_id = None
    db.commit()
    factory = _factory(db)
    with factory() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )
    assert dispatch is not None
    task = _Task()

    for _cycle in range(10):
        db.expire_all()
        waiting = db.query(Assessment).filter(Assessment.id == assessment_id).one()
        waiting.workable_result_delivery_next_attempt_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        db.commit()
        assert publish_assessment_result_delivery(
            dispatch,
            task=task,
            session_factory=factory,
        ) == "published"
        result = deliver_assessment_result(
            assessment_id=assessment_id,
            organization_id=organization_id,
            operation_id=dispatch.operation_id,
            settings_obj=_settings(),
            adapter_builder=lambda **_kwargs: pytest.fail(
                "missing candidate must not reach the provider"
            ),
            session_factory=factory,
        )
        assert result["status"] == DELIVERY_RETRY_WAIT

    db.expire_all()
    waiting = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert waiting.workable_result_delivery_receipt["configuration_attempts"] == 10
    assert waiting.workable_result_delivery_receipt["publish_attempts"] == 0
    assert waiting.workable_result_delivery_status == DELIVERY_RETRY_WAIT
    waiting.workable_candidate_id = "candidate-restored-after-ten-cycles"
    waiting.workable_result_delivery_next_attempt_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    db.commit()
    assert publish_assessment_result_delivery(
        dispatch,
        task=task,
        session_factory=factory,
    ) == "published"

    delivered = deliver_assessment_result(
        assessment_id=assessment_id,
        organization_id=organization_id,
        operation_id=dispatch.operation_id,
        settings_obj=_settings(),
        adapter_builder=lambda **_kwargs: SimpleNamespace(
            post_assessment_result=lambda **_payload: {"success": True}
        ),
        session_factory=factory,
    )

    assert delivered["status"] == DELIVERY_CONFIRMED
    assert len(task.calls) == 11


def test_sweep_inventories_legacy_null_state_without_sending(db):
    assessment_id, _organization_id = _seed(db, token="legacy-inventory")
    task = _Task()

    summary = sweep_assessment_result_deliveries(
        task=task,
        session_factory=_factory(db),
    )

    assert summary == {
        "scanned": 1,
        "published": 0,
        "reconciliation_required": 1,
    }
    assert task.calls == []
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert row.workable_result_delivery_status == "legacy_reconciliation_required"
    receipt = row.workable_result_delivery_receipt
    assert receipt["legacy_inventory_only"] is True
    assert receipt["provider_called"] is False
    assert receipt["provider_outcome_uncertain"] is True
    assert any(
        event.get("event_type") == "workable_result_delivery_status_changed"
        and event.get("status") == "legacy_reconciliation_required"
        for event in row.timeline
    )

    replay = sweep_assessment_result_deliveries(
        task=task,
        session_factory=_factory(db),
    )

    assert replay["scanned"] == 0
    assert task.calls == []


def test_completion_without_token_persists_config_wait_then_sweep_recovers(db):
    assessment_id, organization_id = _seed(db, token="")
    snapshot = snapshot_terminal_submission(
        db,
        assessment_id=assessment_id,
        terminal_statuses={AssessmentStatus.COMPLETED},
    )

    side_effects = finalize_submission_snapshot(
        db,
        snapshot,
        terminal_statuses={AssessmentStatus.COMPLETED},
        retry_scoring=False,
        grading_incomplete=False,
        suppress_completion_side_effects=False,
        request_id="config-wait",
        settings_obj=_settings(),
    )

    assert side_effects.workable_payload is not None
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert row.workable_result_delivery_status == DELIVERY_RETRY_WAIT
    assert row.workable_result_delivery_receipt["last_error_code"] == (
        "workable_credential_missing"
    )
    assert "access_token" not in json.dumps(row.workable_result_delivery_receipt)
    org = db.query(Organization).filter(Organization.id == organization_id).one()
    org.workable_access_token = "restored-current-token"
    db.commit()
    task = _Task()

    result = sweep_assessment_result_deliveries(
        task=task,
        session_factory=_factory(db),
    )

    assert result["published"] == 1
    assert task.calls == [side_effects.workable_payload]


def test_intentionally_disabled_writeback_persists_explicit_cancelled_evidence(db):
    assessment_id, _organization_id = _seed(db)
    snapshot = snapshot_terminal_submission(
        db,
        assessment_id=assessment_id,
        terminal_statuses={AssessmentStatus.COMPLETED},
    )
    disabled = SimpleNamespace(
        MVP_DISABLE_WORKABLE=True,
        FRONTEND_URL="https://app.example.test",
    )

    side_effects = finalize_submission_snapshot(
        db,
        snapshot,
        terminal_statuses={AssessmentStatus.COMPLETED},
        retry_scoring=False,
        grading_incomplete=False,
        suppress_completion_side_effects=False,
        settings_obj=disabled,
    )

    assert side_effects.workable_payload is None
    db.expire_all()
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert row.workable_result_delivery_status == DELIVERY_CANCELLED
    assert row.workable_result_delivery_receipt["last_error_code"] == (
        "workable_disabled"
    )


def test_intent_reports_actual_duration_rounded_up_not_allotted_time(db):
    assessment_id, organization_id = _seed(db)
    row = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    row.duration_minutes = 42
    row.total_duration_seconds = 61
    db.commit()

    with _factory(db)() as claim_db:
        dispatch = authorize_assessment_result_delivery(
            claim_db,
            assessment_id=assessment_id,
            organization_id=organization_id,
            settings_obj=_settings(),
        )

    assert dispatch is not None
    db.expire_all()
    stored = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    assert stored.workable_result_delivery_receipt["intent"]["assessment_data"][
        "time_taken"
    ] == 2
