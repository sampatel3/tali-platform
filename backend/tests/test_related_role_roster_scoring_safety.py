"""Regression coverage for live related-role roster and scoring boundaries."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.services.sister_role_service import (
    ensure_sister_evaluations,
    text_fingerprint,
)
from tests.conftest import auth_headers


def _seed_related_role(db, *, organization_id: int) -> tuple[Role, Role]:
    owner = Role(
        organization_id=organization_id,
        name="Canonical ATS owner",
        source="workable",
        workable_job_id="RELATED-ROSTER-SAFETY",
        workable_job_data={"state": "published"},
        job_spec_text="Canonical role specification.",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=organization_id,
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
        job_spec_text="Independent related role specification with Python.",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5_000,
    )
    db.add(related)
    db.flush()
    return owner, related


def _seed_application(
    db,
    *,
    organization_id: int,
    role_id: int,
    suffix: str,
    deleted: bool = False,
    candidate_deleted: bool = False,
) -> CandidateApplication:
    deleted_at = datetime.now(timezone.utc)
    candidate = Candidate(
        organization_id=organization_id,
        email=f"related-roster-{suffix}@example.com",
        full_name=f"Related roster {suffix}",
        cv_text="Python production systems experience.",
        deleted_at=deleted_at if candidate_deleted else None,
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        candidate_id=candidate.id,
        role_id=role_id,
        source="workable",
        pipeline_stage="review",
        application_outcome="open",
        cv_text=candidate.cv_text,
        deleted_at=deleted_at if deleted else None,
    )
    db.add(application)
    db.flush()
    return application


def _seed_pending_evaluation(db, *, suffix: str):
    organization = Organization(
        name=f"Related scoring boundary {suffix}",
        slug=f"related-scoring-boundary-{suffix}-{id(db)}",
    )
    db.add(organization)
    db.flush()
    owner, related = _seed_related_role(
        db,
        organization_id=int(organization.id),
    )
    application = _seed_application(
        db,
        organization_id=int(organization.id),
        role_id=int(owner.id),
        suffix=suffix,
    )
    evaluation = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        source_application_id=int(application.id),
        status="pending",
        spec_fingerprint="queued-before-boundary",
    )
    db.add(evaluation)
    db.commit()
    return organization, owner, related, application, evaluation


def _successful_match_output(*, score: float = 87.0):
    return SimpleNamespace(
        scoring_status=SimpleNamespace(value="ok"),
        role_fit_score=score,
        summary="Boundary-safe related-role score.",
        error_reason=None,
        model_version="test-model",
        prompt_version="test-prompt",
        trace_id="related-boundary-trace",
        cache_hit=False,
        model_dump=lambda **_: {
            "role_fit_score": score,
            "summary": "Boundary-safe related-role score.",
        },
    )


def test_restored_excluded_evaluation_reactivates_without_fingerprint_change(
    client, db
):
    _, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, related = _seed_related_role(db, organization_id=user.organization_id)
    application = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="restored",
    )
    evaluation = SisterRoleEvaluation(
        organization_id=user.organization_id,
        role_id=related.id,
        source_application_id=application.id,
        status="done",
        spec_fingerprint=text_fingerprint(related.job_spec_text),
        cv_fingerprint=text_fingerprint(application.cv_text),
        role_fit_score=91,
        summary="Prior result retained for audit.",
        details={"role_fit_score": 91},
        scored_at=datetime.now(timezone.utc),
    )
    db.add(evaluation)
    db.commit()

    application.deleted_at = datetime.now(timezone.utc)
    db.commit()
    ensure_sister_evaluations(db, related)
    assert evaluation.status == "excluded"
    assert evaluation.role_fit_score == 91

    application.deleted_at = None
    db.commit()
    counts = ensure_sister_evaluations(db, related)

    assert counts == {"total": 1, "pending": 1, "unscorable": 0}
    assert evaluation.status == "pending"
    assert evaluation.role_fit_score is None
    assert evaluation.last_error_code is None
    assert evaluation.history[-1]["role_fit_score"] == 91
    assert evaluation.history[-1]["summary"] == "Prior result retained for audit."


def test_scoring_status_top_candidates_excludes_invalid_live_roster_rows(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, related = _seed_related_role(db, organization_id=user.organization_id)
    reassigned_owner = Role(
        organization_id=user.organization_id,
        name="Different canonical role",
    )
    db.add(reassigned_owner)
    db.flush()

    valid = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="valid-top",
    )
    deleted_candidate = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="deleted-candidate",
        candidate_deleted=True,
    )
    deleted_application = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="deleted-application",
        deleted=True,
    )
    reassigned = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=reassigned_owner.id,
        suffix="reassigned",
    )
    for application, score in (
        (valid, 80),
        (deleted_candidate, 99),
        (deleted_application, 98),
        (reassigned, 97),
    ):
        db.add(
            SisterRoleEvaluation(
                organization_id=user.organization_id,
                role_id=related.id,
                source_application_id=application.id,
                status="done",
                spec_fingerprint="spec",
                role_fit_score=score,
            )
        )
    db.commit()

    response = client.get(
        f"/api/v1/roles/{related.id}/sister-scoring-status",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["top_candidates"] == [
        {
            "application_id": valid.id,
            "candidate_name": valid.candidate.full_name,
            "score": 80.0,
        }
    ]


@pytest.mark.parametrize("invalid_owner_state", ["deleted", "wrong_org", "related"])
def test_scoring_status_top_candidates_requires_live_standard_owner(
    client, db, invalid_owner_state
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, related = _seed_related_role(db, organization_id=user.organization_id)
    application = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix=f"invalid-owner-{invalid_owner_state}",
    )
    db.add(
        SisterRoleEvaluation(
            organization_id=user.organization_id,
            role_id=related.id,
            source_application_id=application.id,
            status="done",
            spec_fingerprint="spec",
            role_fit_score=99,
        )
    )
    if invalid_owner_state == "deleted":
        owner.deleted_at = datetime.now(timezone.utc)
    elif invalid_owner_state == "wrong_org":
        other = Organization(
            name="Other related-roster organization",
            slug=f"other-related-roster-{related.id}",
        )
        db.add(other)
        db.flush()
        owner.organization_id = other.id
    else:
        owner.role_kind = ROLE_KIND_SISTER
    db.commit()

    response = client.get(
        f"/api/v1/roles/{related.id}/sister-scoring-status",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["top_candidates"] == []


def test_related_applications_applied_filter_includes_missing_evaluation(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, related = _seed_related_role(db, organization_id=user.organization_id)
    application = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="missing-evaluation",
    )
    db.commit()

    response = client.get(
        f"/api/v1/roles/{related.id}/applications",
        params={"pipeline_stage": "applied"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [application.id]
    assert response.json()[0]["pipeline_stage"] == "applied"


@pytest.mark.parametrize(
    "revocation",
    [
        "application_deleted",
        "candidate_deleted",
        "application_reassigned",
        "application_wrong_org",
        "candidate_wrong_org",
        "owner_deleted",
        "owner_wrong_org",
        "owner_related",
        "evaluation_wrong_org",
    ],
)
def test_delayed_scoring_worker_excludes_rows_outside_live_roster(db, revocation):
    organization = Organization(
        name=f"Delayed related scoring {revocation}",
        slug=f"delayed-related-scoring-{revocation}-{id(db)}",
    )
    other_organization = Organization(
        name=f"Other delayed related scoring {revocation}",
        slug=f"other-delayed-related-scoring-{revocation}-{id(db)}",
    )
    db.add_all([organization, other_organization])
    db.flush()
    owner, related = _seed_related_role(db, organization_id=int(organization.id))
    application = _seed_application(
        db,
        organization_id=int(organization.id),
        role_id=int(owner.id),
        suffix=f"delayed-{revocation}",
    )
    evaluation = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        source_application_id=int(application.id),
        status="pending",
        spec_fingerprint="queued-before-roster-change",
    )
    db.add(evaluation)
    db.flush()

    if revocation == "application_deleted":
        application.deleted_at = datetime.now(timezone.utc)
    elif revocation == "candidate_deleted":
        application.candidate.deleted_at = datetime.now(timezone.utc)
    elif revocation == "application_reassigned":
        replacement_owner = Role(
            organization_id=int(organization.id),
            name="Replacement owner",
        )
        db.add(replacement_owner)
        db.flush()
        application.role_id = int(replacement_owner.id)
    elif revocation == "application_wrong_org":
        application.organization_id = int(other_organization.id)
    elif revocation == "candidate_wrong_org":
        application.candidate.organization_id = int(other_organization.id)
    elif revocation == "owner_deleted":
        owner.deleted_at = datetime.now(timezone.utc)
    elif revocation == "owner_wrong_org":
        owner.organization_id = int(other_organization.id)
    elif revocation == "owner_related":
        owner.role_kind = ROLE_KIND_SISTER
    else:
        evaluation.organization_id = int(other_organization.id)
    db.commit()

    from app.tasks.sister_role_tasks import score_sister_evaluation

    with (
        patch("app.cv_matching.holistic.run_holistic_match") as paid_call,
        patch(
            "app.services.claude_client_resolver.get_metered_client"
        ) as metered_client,
    ):
        result = score_sister_evaluation.run(int(evaluation.id))

    paid_call.assert_not_called()
    metered_client.assert_not_called()
    db.expire_all()
    saved = db.get(SisterRoleEvaluation, int(evaluation.id))
    assert result == {"status": "excluded", "evaluation_id": int(evaluation.id)}
    assert saved.status == "excluded"
    assert saved.last_error_code == "source_application_outside_owner_roster"
    assert saved.error_message == "Source application left the owner roster"
    assert saved.next_attempt_at is None
    assert saved.scored_at is not None


@pytest.mark.parametrize(
    ("revocation", "expected_status", "expected_error_code"),
    [
        (
            "application_deleted",
            "excluded",
            "source_application_outside_owner_roster",
        ),
        (
            "candidate_deleted",
            "excluded",
            "source_application_outside_owner_roster",
        ),
        (
            "owner_deleted",
            "excluded",
            "source_application_outside_owner_roster",
        ),
        ("shared_application_closed", "excluded", "shared_application_closed"),
        ("related_role_paused", "authority_blocked", "authority_blocked"),
    ],
)
def test_scoring_revalidates_scope_before_each_provider_phase(
    db,
    revocation,
    expected_status,
    expected_error_code,
):
    from tests.conftest import TestingSessionLocal

    _, owner, related, application, evaluation = _seed_pending_evaluation(
        db,
        suffix=f"provider-phase-{revocation}",
    )
    provider_phases: list[str] = []

    def _score(*_args, before_provider_call, **_kwargs):
        provider_phases.append("full_score.requirements")
        before_provider_call("full_score.requirements")
        with TestingSessionLocal() as concurrent:
            if revocation == "application_deleted":
                concurrent.get(
                    CandidateApplication,
                    int(application.id),
                ).deleted_at = datetime.now(timezone.utc)
            elif revocation == "candidate_deleted":
                live_application = concurrent.get(
                    CandidateApplication,
                    int(application.id),
                )
                concurrent.get(
                    Candidate,
                    int(live_application.candidate_id),
                ).deleted_at = datetime.now(timezone.utc)
            elif revocation == "owner_deleted":
                concurrent.get(Role, int(owner.id)).deleted_at = datetime.now(
                    timezone.utc
                )
            elif revocation == "shared_application_closed":
                concurrent.get(
                    CandidateApplication,
                    int(application.id),
                ).application_outcome = "withdrawn"
            else:
                concurrent.get(Role, int(related.id)).agent_paused_at = datetime.now(
                    timezone.utc
                )
            concurrent.commit()

        provider_phases.append("full_score.main")
        before_provider_call("full_score.main")
        raise AssertionError("revoked scoring reached a later provider call")

    from app.tasks.sister_role_tasks import score_sister_evaluation

    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            side_effect=_score,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        result = score_sister_evaluation.run(int(evaluation.id))

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, int(evaluation.id))
    assert provider_phases == ["full_score.requirements", "full_score.main"]
    assert result["status"] == expected_status
    assert result["error_code"] == expected_error_code
    assert saved.status == (
        "retry_wait" if expected_status == "authority_blocked" else "excluded"
    )
    assert saved.last_error_code == expected_error_code
    assert saved.role_fit_score is None
    assert saved.details is None


@pytest.mark.parametrize(
    ("revocation", "expected_status", "expected_error_code"),
    [
        (
            "application_deleted",
            "excluded",
            "source_application_outside_owner_roster",
        ),
        ("related_role_paused", "authority_blocked", "authority_blocked"),
        ("job_spec_changed", "retry_wait", "scoring_inputs_changed"),
    ],
)
def test_scoring_revalidates_scope_after_provider_before_persistence(
    db,
    revocation,
    expected_status,
    expected_error_code,
):
    from tests.conftest import TestingSessionLocal

    _, _, related, application, evaluation = _seed_pending_evaluation(
        db,
        suffix=f"persist-{revocation}",
    )

    def _score(*_args, before_provider_call, **_kwargs):
        before_provider_call("full_score.main")
        with TestingSessionLocal() as concurrent:
            if revocation == "application_deleted":
                concurrent.get(
                    CandidateApplication,
                    int(application.id),
                ).deleted_at = datetime.now(timezone.utc)
            elif revocation == "related_role_paused":
                concurrent.get(Role, int(related.id)).agent_paused_at = datetime.now(
                    timezone.utc
                )
            else:
                concurrent.get(
                    Role, int(related.id)
                ).job_spec_text = "A materially changed specification that requires Go."
            concurrent.commit()
        return _successful_match_output(score=93.0)

    from app.tasks.sister_role_tasks import score_sister_evaluation

    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            side_effect=_score,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        result = score_sister_evaluation.run(int(evaluation.id))

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, int(evaluation.id))
    assert result["status"] == expected_status
    assert result["error_code"] == expected_error_code
    assert saved.status == (
        "excluded" if expected_status == "excluded" else "retry_wait"
    )
    assert saved.last_error_code == expected_error_code
    assert saved.role_fit_score is None
    assert saved.details is None


def test_scoring_read_fences_close_before_provider_and_final_save_locks_row(db):
    _, _, _, _, evaluation = _seed_pending_evaluation(
        db,
        suffix="transaction-fences",
    )
    from app.tasks import sister_role_tasks

    real_scope_check = sister_role_tasks._require_live_related_scoring_scope
    phase_calls: list[tuple[str, bool, object]] = []

    def _tracked_scope_check(session, **kwargs):
        phase_calls.append(
            (
                str(kwargs["phase"]),
                bool(kwargs.get("lock_for_update", False)),
                session,
            )
        )
        return real_scope_check(session, **kwargs)

    def _score(*_args, before_provider_call, **_kwargs):
        # The cache/provider admission read completed immediately before the
        # scoring gateway was entered and must not span its network latency.
        assert phase_calls[-1][0] == "full_score.cache_or_provider"
        assert phase_calls[-1][2].in_transaction() is False
        before_provider_call("full_score.main")
        assert phase_calls[-1][0] == "full_score.main"
        assert phase_calls[-1][2].in_transaction() is False
        return _successful_match_output(score=89.0)

    with (
        patch.object(
            sister_role_tasks,
            "_require_live_related_scoring_scope",
            side_effect=_tracked_scope_check,
        ),
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            side_effect=_score,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        result = sister_role_tasks.score_sister_evaluation.run(int(evaluation.id))

    assert result["status"] == "done"
    assert [(phase, locked) for phase, locked, _session in phase_calls] == [
        ("full_score.client_and_context", False),
        ("full_score.cache_or_provider", False),
        ("full_score.main", False),
        ("full_score.persist", True),
    ]


@pytest.mark.parametrize(
    "failure_kind",
    ["roster", "authority", "inputs", "attempt_revoked", "provider"],
)
def test_stale_worker_failure_handlers_preserve_explicit_rescore_reset(
    db,
    failure_kind,
):
    from tests.conftest import TestingSessionLocal

    _, _, related, _, evaluation = _seed_pending_evaluation(
        db,
        suffix=f"stale-handler-{failure_kind}",
    )
    from app.tasks import sister_role_tasks

    def _score(*_args, before_provider_call, **_kwargs):
        before_provider_call("full_score.main")
        with TestingSessionLocal() as concurrent:
            current_role = concurrent.get(Role, int(related.id))
            assert current_role is not None
            ensure_sister_evaluations(
                concurrent,
                current_role,
                reset_existing=True,
            )
            concurrent.commit()

        if failure_kind == "roster":
            raise sister_role_tasks._RelatedRosterRevoked(
                code="source_application_outside_owner_roster",
                message="stale worker roster failure",
                phase="test.failure_handler",
            )
        if failure_kind == "authority":
            raise sister_role_tasks._RelatedAuthorityRevoked(
                phase="test.failure_handler",
                message="stale worker authority failure",
            )
        if failure_kind == "inputs":
            raise sister_role_tasks._RelatedInputsChanged(
                phase="test.failure_handler"
            )
        if failure_kind == "provider":
            raise RuntimeError("stale worker provider failure")
        return _successful_match_output(score=98.0)

    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            side_effect=_score,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        result = sister_role_tasks.score_sister_evaluation.run(int(evaluation.id))

    assert result == {
        "status": "skipped",
        "evaluation_id": int(evaluation.id),
        "current_status": "pending",
    }
    db.expire_all()
    saved = db.get(SisterRoleEvaluation, int(evaluation.id))
    assert saved is not None
    assert saved.status == "pending"
    assert saved.started_at is None
    assert saved.attempts == 0
    assert saved.last_error_code is None
    assert saved.error_message is None
    assert saved.role_fit_score is None
    assert saved.next_attempt_at is None


def test_stale_provider_failure_cannot_overwrite_replacement_running_attempt(db):
    from tests.conftest import TestingSessionLocal

    _, _, _, _, evaluation = _seed_pending_evaluation(
        db,
        suffix="replacement-running-attempt",
    )
    replacement_started_at: list[datetime] = []

    def _score(*_args, before_provider_call, **_kwargs):
        before_provider_call("full_score.main")
        with TestingSessionLocal() as concurrent:
            replacement = concurrent.get(
                SisterRoleEvaluation,
                int(evaluation.id),
            )
            assert replacement is not None
            assert replacement.started_at is not None
            replacement.started_at = replacement.started_at + timedelta(seconds=1)
            replacement.attempts = int(replacement.attempts or 0) + 1
            replacement_started_at.append(replacement.started_at)
            concurrent.commit()
        raise RuntimeError("superseded provider attempt failed late")

    from app.tasks import sister_role_tasks

    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            side_effect=_score,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        result = sister_role_tasks.score_sister_evaluation.run(int(evaluation.id))

    assert result == {
        "status": "skipped",
        "evaluation_id": int(evaluation.id),
        "current_status": "running",
    }
    db.expire_all()
    saved = db.get(SisterRoleEvaluation, int(evaluation.id))
    assert saved is not None
    assert saved.status == "running"
    assert saved.attempts == 2
    assert saved.started_at == replacement_started_at[0]
    assert saved.last_error_code is None
    assert saved.error_message is None
    assert saved.next_attempt_at is None


def test_failed_broker_publish_cannot_overwrite_explicit_rescore_reset(db):
    from tests.conftest import TestingSessionLocal

    _, _, related, _, evaluation = _seed_pending_evaluation(
        db,
        suffix="dispatch-reset-race",
    )
    from app.tasks.sister_role_tasks import (
        dispatch_sister_evaluation,
        score_sister_evaluation,
    )

    def _reset_then_fail_publish(*_args, **_kwargs):
        with TestingSessionLocal() as concurrent:
            current_role = concurrent.get(Role, int(related.id))
            assert current_role is not None
            ensure_sister_evaluations(
                concurrent,
                current_role,
                reset_existing=True,
            )
            concurrent.commit()
        raise RuntimeError("broker unavailable after explicit reset")

    with patch.object(
        score_sister_evaluation,
        "apply_async",
        side_effect=_reset_then_fail_publish,
    ):
        result = dispatch_sister_evaluation(
            db,
            evaluation_id=int(evaluation.id),
        )

    assert result == {
        "status": "skipped",
        "evaluation_id": int(evaluation.id),
        "current_status": "pending",
    }
    db.expire_all()
    saved = db.get(SisterRoleEvaluation, int(evaluation.id))
    assert saved is not None
    assert saved.status == "pending"
    assert saved.dispatch_attempted_at is None
    assert saved.next_attempt_at is None
    assert saved.last_error_code is None
    assert saved.error_message is None


def test_related_provider_reservation_requires_live_role_authority(db):
    _, _, related, _, evaluation = _seed_pending_evaluation(
        db,
        suffix="metered-authority",
    )
    inner_calls: list[dict] = []

    class _Messages:
        def create(self, **kwargs):
            inner_calls.append(kwargs)
            return SimpleNamespace()

    class _Client:
        messages = _Messages()

    reservation = SimpleNamespace(
        as_metering_payload=lambda: {"external_ref": "related-authority-hold"}
    )

    def _score(*_args, client, before_provider_call, **_kwargs):
        before_provider_call("full_score.main")
        client.messages.create(
            model="test-model",
            max_tokens=10,
            messages=[],
            metering={
                "feature": "score",
                "organization_id": -1,
                "role_id": -1,
                "entity_id": "untrusted",
                "trace_id": "related-authority-trace",
            },
        )
        return _successful_match_output()

    from app.tasks.sister_role_tasks import score_sister_evaluation

    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            side_effect=_score,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=_Client(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
        patch(
            "app.services.provider_usage_admission.reserve_provider_usage",
            return_value=reservation,
        ) as reserve,
    ):
        result = score_sister_evaluation.run(int(evaluation.id))

    assert result["status"] == "done"
    reserve.assert_called_once()
    admission = reserve.call_args.kwargs
    assert admission["organization_id"] == int(related.organization_id)
    assert admission["role_id"] == int(related.id)
    assert admission["require_role_authority"] is True
    assert admission["entity_id"] == "untrusted"
    assert admission["metadata"]["sister_evaluation_id"] == int(evaluation.id)
    assert len(inner_calls) == 1
    metering = inner_calls[0]["metering"]
    assert metering["organization_id"] == int(related.organization_id)
    assert metering["role_id"] == int(related.id)
    assert metering["credit_reservation"] == {"external_ref": "related-authority-hold"}


def test_metered_authority_rejection_cannot_be_reclassified_as_provider_failure(db):
    from app.services.provider_usage_admission import AutomaticProviderAuthorityError

    _, _, _, _, evaluation = _seed_pending_evaluation(
        db,
        suffix="metered-authority-revoked",
    )
    inner_calls: list[dict] = []

    class _Messages:
        def create(self, **kwargs):
            inner_calls.append(kwargs)
            return SimpleNamespace()

    class _Client:
        messages = _Messages()

    def _score(*_args, client, before_provider_call, **_kwargs):
        before_provider_call("full_score.main")
        try:
            client.messages.create(
                model="test-model",
                max_tokens=10,
                messages=[],
                metering={"feature": "score"},
            )
        except RuntimeError:
            # The structured gateway converts provider exceptions into a failed
            # output. The admitted client must retain the authority classification
            # so the worker does not misreport this as a provider outage.
            return SimpleNamespace(
                scoring_status=SimpleNamespace(value="failed"),
                error_reason="claude_call_failed",
                role_fit_score=None,
                cache_hit=False,
            )
        raise AssertionError("authority-revoked call reached the provider")

    from app.tasks.sister_role_tasks import score_sister_evaluation

    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            side_effect=_score,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=_Client(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
        patch(
            "app.services.provider_usage_admission.reserve_provider_usage",
            side_effect=AutomaticProviderAuthorityError("role agent is paused"),
        ),
    ):
        result = score_sister_evaluation.run(int(evaluation.id))

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, int(evaluation.id))
    assert result["status"] == "authority_blocked"
    assert result["error_code"] == "authority_blocked"
    assert saved.status == "retry_wait"
    assert saved.last_error_code == "authority_blocked"
    assert inner_calls == []


def test_role_wake_releases_only_authority_wait_and_preserves_provider_backoff(db):
    organization = Organization(
        name="Related retry wake safety",
        slug=f"related-retry-wake-{id(db)}",
    )
    db.add(organization)
    db.flush()
    owner, related = _seed_related_role(db, organization_id=int(organization.id))
    authority_application = _seed_application(
        db,
        organization_id=int(organization.id),
        role_id=int(owner.id),
        suffix="authority-wait",
    )
    provider_application = _seed_application(
        db,
        organization_id=int(organization.id),
        role_id=int(owner.id),
        suffix="provider-backoff",
    )
    future_retry = datetime.now(timezone.utc) + timedelta(hours=2)
    authority_evaluation = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        source_application_id=int(authority_application.id),
        status="retry_wait",
        spec_fingerprint="authority-wait",
        last_error_code="authority_blocked",
        next_attempt_at=future_retry,
    )
    provider_evaluation = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        source_application_id=int(provider_application.id),
        status="retry_wait",
        spec_fingerprint="provider-backoff",
        last_error_code="provider_scoring_failed",
        next_attempt_at=future_retry,
    )
    db.add_all([authority_evaluation, provider_evaluation])
    db.commit()

    from app.tasks.sister_role_tasks import score_sister_role

    published: list[int] = []
    with patch(
        "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async",
        side_effect=lambda *, args, queue: published.append(int(args[0])),
    ):
        result = score_sister_role.run(int(related.id))

    db.expire_all()
    saved_authority = db.get(SisterRoleEvaluation, int(authority_evaluation.id))
    saved_provider = db.get(SisterRoleEvaluation, int(provider_evaluation.id))
    assert result["queued"] == 1
    assert published == [int(authority_evaluation.id)]
    assert saved_authority.status == "pending"
    assert saved_authority.last_error_code is None
    assert saved_authority.next_attempt_at is None
    assert saved_provider.status == "retry_wait"
    assert saved_provider.last_error_code == "provider_scoring_failed"
    assert saved_provider.next_attempt_at is not None
    assert saved_provider.next_attempt_at.replace(tzinfo=timezone.utc) == future_retry
