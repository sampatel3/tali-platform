import pytest
from pydantic import ValidationError
from app.schemas.user import UserCreate, ResetPasswordRequest, TeamInviteRequest
from app.domains.identity_access.password_policy import check_password_strength
from app.schemas.assessment import AssessmentCreate, CodeExecutionRequest, SubmitRequest
from app.schemas.task import TaskCreate
from app.schemas.candidate import CandidateCreate


# ---------------------------------------------------------------------------
# UserCreate
# ---------------------------------------------------------------------------

class TestUserCreate:
    def test_valid_user_create(self):
        user = UserCreate(
            email="alice@example.com",
            password="secureP@ss1",
            full_name="Alice Smith",
            organization_name="Acme Inc.",
        )
        assert user.email == "alice@example.com"
        assert user.password == "secureP@ss1"
        assert user.full_name == "Alice Smith"
        assert user.organization_name == "Acme Inc."

    def test_valid_user_create_without_organization(self):
        user = UserCreate(
            email="bob@example.com",
            password="longpassword",
            full_name="Bob Jones",
        )
        assert user.organization_name is None

    def test_user_create_optional_names_may_be_omitted(self):
        user = UserCreate(
            email="test@example.com",
            password="ValidPass1!",
        )
        assert user.full_name is None
        assert user.organization_name is None

    def test_user_create_password_policy_accepts_min_boundary(self):
        assert check_password_strength("Q7!mR2#x", email="test@example.com") is None

    def test_user_create_password_policy_rejects_too_short(self):
        assert check_password_strength("A1b2C3d", email="test@example.com") == (
            "Password must be at least 8 characters."
        )

    def test_user_create_password_policy_rejects_over_72_utf8_bytes(self):
        assert check_password_strength("é" * 37, email="test@example.com") == (
            "Password must be 72 UTF-8 bytes or fewer."
        )

    def test_user_create_invalid_email(self):
        with pytest.raises(ValidationError):
            UserCreate(
                email="not-an-email",
                password="secureP@ss1",
                full_name="Test",
            )

    def test_user_create_missing_email(self):
        with pytest.raises(ValidationError):
            UserCreate(
                password="secureP@ss1",
                full_name="Test",
            )

    def test_user_create_full_name_empty(self):
        """full_name min_length=1 — empty string should fail."""
        with pytest.raises(ValidationError):
            UserCreate(
                email="test@example.com",
                password="secureP@ss1",
                full_name="",
            )

    def test_user_create_full_name_max_boundary(self):
        user = UserCreate(
            email="test@example.com",
            password="secureP@ss1",
            full_name="A" * 200,
        )
        assert len(user.full_name) == 200

    def test_user_create_full_name_too_long(self):
        with pytest.raises(ValidationError):
            UserCreate(
                email="test@example.com",
                password="secureP@ss1",
                full_name="A" * 201,
            )

    def test_user_create_organization_name_too_long(self):
        with pytest.raises(ValidationError):
            UserCreate(
                email="test@example.com",
                password="secureP@ss1",
                full_name="Test",
                organization_name="X" * 201,
            )


# ---------------------------------------------------------------------------
# AssessmentCreate
# ---------------------------------------------------------------------------

class TestAssessmentCreate:
    def test_valid_assessment_create(self):
        a = AssessmentCreate(
            candidate_email="c@example.com",
            candidate_name="Candidate One",
            task_id=1,
        )
        assert a.duration_minutes == 30  # default

    def test_valid_assessment_create_custom_duration(self):
        a = AssessmentCreate(
            candidate_email="c@example.com",
            candidate_name="Candidate One",
            task_id=5,
            duration_minutes=90,
        )
        assert a.duration_minutes == 90

    def test_assessment_create_invalid_email(self):
        with pytest.raises(ValidationError):
            AssessmentCreate(
                candidate_email="bad",
                candidate_name="Name",
                task_id=1,
            )

    def test_assessment_create_candidate_name_empty(self):
        with pytest.raises(ValidationError):
            AssessmentCreate(
                candidate_email="c@example.com",
                candidate_name="",
                task_id=1,
            )

    def test_assessment_create_task_id_zero(self):
        """task_id must be gt=0."""
        with pytest.raises(ValidationError):
            AssessmentCreate(
                candidate_email="c@example.com",
                candidate_name="Name",
                task_id=0,
            )

    def test_assessment_create_task_id_negative(self):
        with pytest.raises(ValidationError):
            AssessmentCreate(
                candidate_email="c@example.com",
                candidate_name="Name",
                task_id=-1,
            )

    def test_assessment_create_duration_min_boundary(self):
        a = AssessmentCreate(
            candidate_email="c@example.com",
            candidate_name="Name",
            task_id=1,
            duration_minutes=15,
        )
        assert a.duration_minutes == 15

    def test_assessment_create_duration_max_boundary(self):
        a = AssessmentCreate(
            candidate_email="c@example.com",
            candidate_name="Name",
            task_id=1,
            duration_minutes=180,
        )
        assert a.duration_minutes == 180

    def test_assessment_create_duration_below_min(self):
        with pytest.raises(ValidationError):
            AssessmentCreate(
                candidate_email="c@example.com",
                candidate_name="Name",
                task_id=1,
                duration_minutes=14,
            )

    def test_assessment_create_duration_above_max(self):
        with pytest.raises(ValidationError):
            AssessmentCreate(
                candidate_email="c@example.com",
                candidate_name="Name",
                task_id=1,
                duration_minutes=181,
            )


# ---------------------------------------------------------------------------
# TaskCreate
# ---------------------------------------------------------------------------

class TestTaskCreate:
    def _valid_payload(self, **overrides):
        defaults = dict(
            name="My Task",
            description="A meaningful description here",
            task_type="coding",
            difficulty="medium",
            duration_minutes=30,
            starter_code="print('hello')",
            test_code="assert True",
        )
        defaults.update(overrides)
        return defaults

    def test_valid_task_create(self):
        t = TaskCreate(**self._valid_payload())
        assert t.name == "My Task"
        assert t.is_template is False
        assert t.proctoring_enabled is False
        assert t.claude_budget_limit_usd is None

    def test_task_create_name_min_boundary(self):
        t = TaskCreate(**self._valid_payload(name="abc"))
        assert len(t.name) == 3

    def test_task_create_name_below_min(self):
        with pytest.raises(ValidationError):
            TaskCreate(**self._valid_payload(name="ab"))

    def test_task_create_name_max_boundary(self):
        t = TaskCreate(**self._valid_payload(name="N" * 200))
        assert len(t.name) == 200

    def test_task_create_name_above_max(self):
        with pytest.raises(ValidationError):
            TaskCreate(**self._valid_payload(name="N" * 201))

    def test_task_create_description_below_min(self):
        with pytest.raises(ValidationError):
            TaskCreate(**self._valid_payload(description="ab"))

    def test_task_create_description_max_boundary(self):
        t = TaskCreate(**self._valid_payload(description="D" * 5000))
        assert len(t.description) == 5000

    def test_task_create_description_above_max(self):
        with pytest.raises(ValidationError):
            TaskCreate(**self._valid_payload(description="D" * 5001))

    def test_task_create_duration_below_min(self):
        with pytest.raises(ValidationError):
            TaskCreate(**self._valid_payload(duration_minutes=14))

    def test_task_create_duration_above_max(self):
        with pytest.raises(ValidationError):
            TaskCreate(**self._valid_payload(duration_minutes=181))

    def test_task_create_starter_code_empty(self):
        with pytest.raises(ValidationError):
            TaskCreate(**self._valid_payload(starter_code=""))

    def test_task_create_test_code_empty(self):
        with pytest.raises(ValidationError):
            TaskCreate(**self._valid_payload(test_code=""))

    def test_task_create_optional_fields_default_none(self):
        t = TaskCreate(**self._valid_payload())
        assert t.sample_data is None
        assert t.dependencies is None
        assert t.success_criteria is None
        assert t.test_weights is None
        assert t.calibration_prompt is None
        assert t.score_weights is None
        assert t.recruiter_weight_preset is None
        assert t.claude_budget_limit_usd is None

    def test_task_create_budget_accepts_decimal(self):
        t = TaskCreate(**self._valid_payload(claude_budget_limit_usd=5.25))
        assert t.claude_budget_limit_usd == 5.25

    def test_task_create_budget_rejects_zero_or_negative(self):
        with pytest.raises(ValidationError):
            TaskCreate(**self._valid_payload(claude_budget_limit_usd=0))
        with pytest.raises(ValidationError):
            TaskCreate(**self._valid_payload(claude_budget_limit_usd=-1))


# ---------------------------------------------------------------------------
# CandidateCreate
# ---------------------------------------------------------------------------

class TestCandidateCreate:
    def test_valid_candidate_create(self):
        c = CandidateCreate(
            email="candidate@example.com",
            full_name="Jane Doe",
            position="Backend Engineer",
        )
        assert c.email == "candidate@example.com"

    def test_candidate_create_only_email(self):
        c = CandidateCreate(email="minimal@example.com")
        assert c.full_name is None
        assert c.position is None

    def test_candidate_create_invalid_email(self):
        with pytest.raises(ValidationError):
            CandidateCreate(email="nope")

    def test_candidate_create_full_name_too_long(self):
        with pytest.raises(ValidationError):
            CandidateCreate(
                email="c@example.com",
                full_name="A" * 201,
            )

    def test_candidate_create_position_too_long(self):
        with pytest.raises(ValidationError):
            CandidateCreate(
                email="c@example.com",
                position="P" * 201,
            )


# ---------------------------------------------------------------------------
# CodeExecutionRequest
# ---------------------------------------------------------------------------

class TestCodeExecutionRequest:
    def test_valid_code_execution_request(self):
        r = CodeExecutionRequest(code="print(1)")
        assert r.code == "print(1)"

    def test_code_execution_request_empty_code(self):
        with pytest.raises(ValidationError):
            CodeExecutionRequest(code="")

    def test_code_execution_request_max_boundary(self):
        r = CodeExecutionRequest(code="x" * 100000)
        assert len(r.code) == 100000

    def test_code_execution_request_above_max(self):
        with pytest.raises(ValidationError):
            CodeExecutionRequest(code="x" * 100001)


# ---------------------------------------------------------------------------
# SubmitRequest
# ---------------------------------------------------------------------------

class TestSubmitRequest:
    def test_valid_submit_request(self):
        r = SubmitRequest(final_code="print('done')")
        assert r.tab_switch_count == 0

    def test_submit_request_empty_code(self):
        with pytest.raises(ValidationError):
            SubmitRequest(final_code="")

    def test_submit_request_max_code_boundary(self):
        r = SubmitRequest(final_code="c" * 100000)
        assert len(r.final_code) == 100000

    def test_submit_request_code_above_max(self):
        with pytest.raises(ValidationError):
            SubmitRequest(final_code="c" * 100001)


# ---------------------------------------------------------------------------
# ResetPasswordRequest
# ---------------------------------------------------------------------------

class TestResetPasswordRequest:
    def test_valid_reset_password_request(self):
        r = ResetPasswordRequest(
            token="issued-token",
            password="newpass99",
        )
        assert r.token == "issued-token"

    def test_reset_password_token_is_required(self):
        with pytest.raises(ValidationError):
            ResetPasswordRequest(
                password="newpass99",
            )

    def test_reset_password_password_field_matches_live_route(self):
        r = ResetPasswordRequest(
            token="issued-token",
            password="newpass99",
        )
        assert r.password == "newpass99"

    def test_reset_password_retired_new_password_field_is_not_a_substitute(self):
        with pytest.raises(ValidationError):
            ResetPasswordRequest(
                new_password="newpass99",
                token="issued-token",
            )

    def test_reset_password_password_is_required(self):
        with pytest.raises(ValidationError):
            ResetPasswordRequest(
                token="issued-token",
            )

    def test_reset_password_openapi_uses_exact_compatibility_fields(self):
        from app.main import app

        spec = app.openapi()
        request_schema = spec["paths"]["/api/v1/auth/reset-password"]["post"][
            "requestBody"
        ]["content"]["application/json"]["schema"]
        schema_name = request_schema["$ref"].rsplit("/", 1)[-1]
        body_schema = spec["components"]["schemas"][schema_name]
        assert set(body_schema["properties"]) == {"token", "password"}
        assert set(body_schema["required"]) == {"token", "password"}


# ---------------------------------------------------------------------------
# TeamInviteRequest
# ---------------------------------------------------------------------------

class TestTeamInviteRequest:
    def test_valid_team_invite_request(self):
        r = TeamInviteRequest(
            email="invitee@example.com",
            full_name="New Member",
        )
        assert r.email == "invitee@example.com"

    def test_team_invite_invalid_email(self):
        with pytest.raises(ValidationError):
            TeamInviteRequest(email="bad", full_name="Name")

    def test_team_invite_full_name_empty(self):
        with pytest.raises(ValidationError):
            TeamInviteRequest(email="x@example.com", full_name="")

    def test_team_invite_full_name_max_boundary(self):
        r = TeamInviteRequest(
            email="x@example.com",
            full_name="N" * 200,
        )
        assert len(r.full_name) == 200

    def test_team_invite_full_name_above_max(self):
        with pytest.raises(ValidationError):
            TeamInviteRequest(
                email="x@example.com",
                full_name="N" * 201,
            )
