"""One-time synthetic truth-fixture provisioning is isolated and idempotent."""

from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

from app.models.api_key import (
    KEY_PREFIX_LIVE,
    ApiKey,
    SCOPE_APPLICATIONS_READ,
    SCOPE_INTERNAL_SEARCH_CANARY_READ,
)
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.scripts import provision_search_canary as provisioner
from app.services.api_key_service import hash_token


def _counts(db) -> tuple[int, ...]:
    return (
        db.query(Organization).count(),
        db.query(User).count(),
        db.query(Role).count(),
        db.query(Candidate).count(),
        db.query(CandidateApplication).count(),
        db.query(Assessment).count(),
        db.query(ApiKey).count(),
    )


def test_search_canary_fixture_is_idempotent_and_has_grounded_exclusions(db):
    first_role, first_key = provisioner.provision(db)
    db.flush()
    first_counts = _counts(db)
    assessment_tokens = {
        int(row.id): str(row.token) for row in db.query(Assessment).all()
    }
    completed_at = {
        int(row.id): row.completed_at for row in db.query(Assessment).all()
    }
    transition_timestamps = {
        int(row.id): (
            row.pipeline_stage_updated_at,
            row.application_outcome_updated_at,
        )
        for row in db.query(CandidateApplication).all()
    }

    second_role, second_key = provisioner.provision(db)
    db.flush()
    second_counts = _counts(db)

    assert second_counts == first_counts == (1, 0, 1, 4, 4, 4, 1)
    assert second_role.id == first_role.id
    assert second_key != first_key
    assert {
        int(row.id): str(row.token) for row in db.query(Assessment).all()
    } == assessment_tokens
    assert {
        int(row.id): row.completed_at for row in db.query(Assessment).all()
    } == completed_at
    assert {
        int(row.id): (
            row.pipeline_stage_updated_at,
            row.application_outcome_updated_at,
        )
        for row in db.query(CandidateApplication).all()
    } == transition_timestamps
    assert all(
        pipeline_at is not None and outcome_at is not None
        for pipeline_at, outcome_at in transition_timestamps.values()
    )
    assert len(set(assessment_tokens.values())) == 4
    assert all(
        token and not token.startswith("internal-search-canary-v1-")
        for token in assessment_tokens.values()
    )

    rows = (
        db.query(
            Candidate.email,
            Candidate.skills,
            Candidate.location_country,
            Assessment.status,
        )
        .join(CandidateApplication, CandidateApplication.candidate_id == Candidate.id)
        .join(Assessment, Assessment.application_id == CandidateApplication.id)
        .order_by(Candidate.email)
        .all()
    )
    matching = [
        email
        for email, skills, country, status in rows
        if skills == ["Python", "PostgreSQL"]
        and country == "United Arab Emirates"
        and status is AssessmentStatus.COMPLETED
    ]
    assert matching == [provisioner.EXPECTED_EMAIL]
    assert {row[0] for row in rows} == {
        provisioner.EXPECTED_EMAIL,
        *provisioner.EXCLUDED_EMAILS,
    }


def test_canary_key_is_hash_only_and_has_no_general_application_scope(db):
    _role, token = provisioner.provision(db)
    db.flush()

    key = db.query(ApiKey).one()
    assert token.startswith(KEY_PREFIX_LIVE)
    assert key.hashed_secret == hash_token(token)
    assert token not in key.hashed_secret
    assert key.scopes == [SCOPE_INTERNAL_SEARCH_CANARY_READ]
    assert SCOPE_APPLICATIONS_READ not in key.scopes
    assert key.created_by_user_id is None
    assert key.last_used_at is None
    assert key.revoked_at is None
    assert key.expires_at is not None


def test_provision_repairs_transition_state_without_rewinding_version(db):
    role, _token = provisioner.provision(db)
    application = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.role_id == role.id)
        .order_by(CandidateApplication.id)
        .first()
    )
    assert application is not None
    stale_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    application.status = "hired"
    application.pipeline_stage = "in_assessment"
    application.pipeline_stage_updated_at = stale_at
    application.application_outcome = "hired"
    application.application_outcome_updated_at = stale_at
    application.version = 7
    db.flush()

    provisioner.provision(db)
    db.flush()

    assert application.status == "applied"
    assert application.pipeline_stage == "review"
    assert application.pipeline_stage_updated_at != stale_at
    assert application.application_outcome == "open"
    assert application.application_outcome_updated_at != stale_at
    assert application.version == 7
    repaired_timestamps = (
        application.pipeline_stage_updated_at,
        application.application_outcome_updated_at,
    )

    provisioner.provision(db)
    db.flush()
    assert (
        application.pipeline_stage_updated_at,
        application.application_outcome_updated_at,
    ) == repaired_timestamps
    assert application.version == 7


def test_provisioning_command_prints_secrets_after_session_closes(
    db, monkeypatch, capsys
):
    isolated_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db.get_bind(),
    )
    monkeypatch.setattr(provisioner, "SessionLocal", isolated_factory)

    result = provisioner.main(
        ["--apply", "--confirm", provisioner.CONFIRMATION]
    )

    assert result == 0
    output = capsys.readouterr().out.splitlines()
    role_line = next(
        line for line in output if line.startswith("TALI_SEARCH_CANARY_ROLE_ID=")
    )
    token_line = next(
        line for line in output if line.startswith("TALI_SEARCH_CANARY_TOKEN=")
    )
    token = token_line.partition("=")[2]
    assert int(role_line.partition("=")[2]) > 0
    assert token.startswith(KEY_PREFIX_LIVE)
    assert hash_token(token) == db.query(ApiKey).one().hashed_secret
