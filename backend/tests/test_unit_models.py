"""Unit tests for SQLAlchemy models — creation, defaults, and relationships."""

import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError

from app.models.user import User
from app.models.organization import Organization
from app.models.task import Task
from app.models.candidate import Candidate
from app.models.assessment import Assessment, AssessmentStatus
from app.models.session import AssessmentSession


# ---------------------------------------------------------------------------
# Organization model
# ---------------------------------------------------------------------------

class TestOrganizationModel:

    def test_create_organization_with_defaults(self, db):
        org = Organization(name="Acme Corp", slug="acme-corp")
        db.add(org)
        db.commit()
        db.refresh(org)

        assert org.id is not None
        assert org.name == "Acme Corp"
        assert org.slug == "acme-corp"
        assert org.workable_connected is False
        assert org.plan == "pay_per_use"
        assert org.assessments_used == 0
        assert org.assessments_limit is None

    def test_organization_slug_unique(self, db):
        org1 = Organization(name="Org One", slug="unique-slug")
        org2 = Organization(name="Org Two", slug="unique-slug")
        db.add(org1)
        db.commit()
        db.add(org2)
        with pytest.raises(IntegrityError):
            db.commit()

    def test_organization_created_at_auto_set(self, db):
        org = Organization(name="Timestamped Org", slug="ts-org")
        db.add(org)
        db.commit()
        db.refresh(org)

        assert org.created_at is not None


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

class TestUserModel:

    def _make_org(self, db):
        org = Organization(name="Test Org", slug="test-org")
        db.add(org)
        db.commit()
        db.refresh(org)
        return org

    def test_create_user_with_defaults(self, db):
        org = self._make_org(db)
        user = User(
            email="alice@example.com",
            hashed_password="fakehash",
            full_name="Alice Smith",
            organization_id=org.id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        assert user.id is not None
        assert user.email == "alice@example.com"
        assert user.is_active is True
        assert user.is_superuser is False
        assert user.is_verified is False

    def test_user_email_unique(self, db):
        org = self._make_org(db)
        user1 = User(email="dup@example.com", hashed_password="h1", organization_id=org.id)
        user2 = User(email="dup@example.com", hashed_password="h2", organization_id=org.id)
        db.add(user1)
        db.commit()
        db.add(user2)
        with pytest.raises(IntegrityError):
            db.commit()

    def test_user_organization_relationship(self, db):
        org = self._make_org(db)
        user = User(
            email="bob@example.com",
            hashed_password="fakehash",
            organization_id=org.id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        assert user.organization is not None
        assert user.organization.id == org.id
        assert user.organization.name == "Test Org"

    def test_organization_users_backref(self, db):
        org = self._make_org(db)
        u1 = User(email="u1@example.com", hashed_password="h1", organization_id=org.id)
        u2 = User(email="u2@example.com", hashed_password="h2", organization_id=org.id)
        db.add_all([u1, u2])
        db.commit()
        db.refresh(org)

        assert len(org.users) == 2
        emails = {u.email for u in org.users}
        assert emails == {"u1@example.com", "u2@example.com"}


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------

class TestTaskModel:

    def _make_org(self, db):
        org = Organization(name="Task Org", slug="task-org")
        db.add(org)
        db.commit()
        db.refresh(org)
        return org

    def test_create_task_with_defaults(self, db):
        org = self._make_org(db)
        task = Task(
            organization_id=org.id,
            name="FizzBuzz",
            description="Implement FizzBuzz",
            task_type="python",
            difficulty="easy",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        assert task.id is not None
        assert task.duration_minutes == 30
        assert task.is_template is False
        assert task.is_active is True
        assert task.proctoring_enabled is False
        assert task.claude_budget_limit_usd is None
        assert task.created_at is not None

    def test_task_custom_duration(self, db):
        org = self._make_org(db)
        task = Task(
            organization_id=org.id,
            name="Long Task",
            task_type="python",
            difficulty="hard",
            duration_minutes=90,
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        assert task.duration_minutes == 90


# ---------------------------------------------------------------------------
# Candidate model
# ---------------------------------------------------------------------------

class TestCandidateModel:

    def _make_org(self, db):
        org = Organization(name="Cand Org", slug="cand-org")
        db.add(org)
        db.commit()
        db.refresh(org)
        return org

    def test_create_candidate_defaults(self, db):
        org = self._make_org(db)
        cand = Candidate(
            organization_id=org.id,
            email="jane@example.com",
            full_name="Jane Doe",
            position="Backend Engineer",
        )
        db.add(cand)
        db.commit()
        db.refresh(cand)

        assert cand.id is not None
        assert cand.email == "jane@example.com"
        assert cand.cv_file_url is None
        assert cand.cv_filename is None
        assert cand.cv_text is None
        assert cand.cv_uploaded_at is None
        assert cand.job_spec_file_url is None
        assert cand.job_spec_filename is None
        assert cand.job_spec_text is None
        assert cand.job_spec_uploaded_at is None
        assert cand.created_at is not None


# ---------------------------------------------------------------------------
# Assessment model
# ---------------------------------------------------------------------------

class TestAssessmentModel:

    def _make_prerequisites(self, db):
        org = Organization(name="Assess Org", slug="assess-org")
        db.add(org)
        db.commit()
        db.refresh(org)

        task = Task(organization_id=org.id, name="Test Task", task_type="python", difficulty="medium")
        db.add(task)
        db.commit()
        db.refresh(task)

        cand = Candidate(organization_id=org.id, email="cand@example.com", full_name="Candidate")
        db.add(cand)
        db.commit()
        db.refresh(cand)

        return org, task, cand

    def test_assessment_default_status_pending(self, db):
        org, task, cand = self._make_prerequisites(db)
        assessment = Assessment(
            organization_id=org.id,
            candidate_id=cand.id,
            task_id=task.id,
            token="abc123",
        )
        db.add(assessment)
        db.commit()
        db.refresh(assessment)

        assert assessment.status == AssessmentStatus.PENDING

    def test_assessment_status_enum_values(self, db):
        assert AssessmentStatus.PENDING.value == "pending"
        assert AssessmentStatus.IN_PROGRESS.value == "in_progress"
        assert AssessmentStatus.COMPLETED.value == "completed"
        assert AssessmentStatus.EXPIRED.value == "expired"

    def test_assessment_relationships(self, db):
        org, task, cand = self._make_prerequisites(db)
        assessment = Assessment(
            organization_id=org.id,
            candidate_id=cand.id,
            task_id=task.id,
            token="rel-token",
        )
        db.add(assessment)
        db.commit()
        db.refresh(assessment)

        assert assessment.organization.id == org.id
        assert assessment.candidate.id == cand.id
        assert assessment.task.id == task.id


# ---------------------------------------------------------------------------
# AssessmentSession model
# ---------------------------------------------------------------------------

class TestAssessmentSessionModel:

    def _make_assessment(self, db):
        org = Organization(name="Sess Org", slug="sess-org")
        db.add(org)
        db.commit()
        db.refresh(org)

        task = Task(organization_id=org.id, name="Sess Task", task_type="python", difficulty="easy")
        db.add(task)
        db.commit()
        db.refresh(task)

        cand = Candidate(organization_id=org.id, email="sess@example.com", full_name="Session Cand")
        db.add(cand)
        db.commit()
        db.refresh(cand)

        assessment = Assessment(
            organization_id=org.id,
            candidate_id=cand.id,
            task_id=task.id,
            token="sess-token",
        )
        db.add(assessment)
        db.commit()
        db.refresh(assessment)
        return assessment

    def test_create_session_with_defaults(self, db):
        assessment = self._make_assessment(db)
        session = AssessmentSession(
            assessment_id=assessment.id,
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        assert session.id is not None
        assert session.assessment_id == assessment.id
        assert session.keystrokes == 0
        assert session.code_executions == 0
        assert session.ai_requests == 0
        assert session.session_end is None

    def test_session_assessment_relationship(self, db):
        assessment = self._make_assessment(db)
        session = AssessmentSession(
            assessment_id=assessment.id,
            keystrokes=150,
            code_executions=5,
            ai_requests=3,
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        assert session.assessment.id == assessment.id
        # Verify back-reference from assessment to sessions
        db.refresh(assessment)
        assert len(assessment.sessions) == 1
        assert assessment.sessions[0].id == session.id
