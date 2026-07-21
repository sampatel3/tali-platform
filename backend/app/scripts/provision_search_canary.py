"""Provision the isolated production-search truth fixture exactly once.

This command is intentionally separate from the recurring read-only canary.
It performs no model, embedding, graph, ATS, email, or other provider calls.

Usage (from ``backend/``)::

    python -m app.scripts.provision_search_canary \
      --apply --confirm PROVISION_SEARCH_CANARY

The command prints the role id and a one-year route-specific API key to store
as protected CI secrets. Re-running is idempotent and rotates the key.
"""

from __future__ import annotations

import argparse
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..domains.assessments_runtime.search_canary_auth import (
    SEARCH_CANARY_API_KEY_NAME,
    SEARCH_CANARY_ROLE_NAME,
)
from ..models.assessment import Assessment, AssessmentStatus
from ..models.api_key import (
    KEY_PREFIX_LIVE,
    ApiKey,
    SCOPE_INTERNAL_SEARCH_CANARY_READ,
)
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..models.task import Task
from ..models.user import User
from ..platform.database import SessionLocal
from ..services.api_key_service import hash_token


CONFIRMATION = "PROVISION_SEARCH_CANARY"
ORG_SLUG = "taali-search-canary-v1"
ORG_NAME = "Taali Search Canary v1 (synthetic; do not edit)"
ROLE_NAME = SEARCH_CANARY_ROLE_NAME
TASK_KEY = "internal_search_canary_v1"
EXPECTED_EMAIL = "search-canary-hit@example.com"
EXCLUDED_EMAILS = (
    "search-canary-wrong-skill@example.com",
    "search-canary-pending-assessment@example.com",
    "search-canary-wrong-location@example.com",
)
TOKEN_LIFETIME_DAYS = 365

_CANDIDATE_TRUTH = (
    {
        "email": EXPECTED_EMAIL,
        "skills": ["Python", "PostgreSQL"],
        "country": "United Arab Emirates",
        "assessment_status": AssessmentStatus.COMPLETED,
    },
    {
        "email": EXCLUDED_EMAILS[0],
        "skills": ["Python"],
        "country": "United Arab Emirates",
        "assessment_status": AssessmentStatus.COMPLETED,
    },
    {
        "email": EXCLUDED_EMAILS[1],
        "skills": ["Python", "PostgreSQL"],
        "country": "United Arab Emirates",
        "assessment_status": AssessmentStatus.PENDING,
    },
    {
        "email": EXCLUDED_EMAILS[2],
        "skills": ["Python", "PostgreSQL"],
        "country": "Germany",
        "assessment_status": AssessmentStatus.COMPLETED,
    },
)


def _one_or_none(query, *, label: str):
    rows = query.limit(2).all()
    if len(rows) > 1:
        raise RuntimeError(f"canary fixture has duplicate {label} rows")
    return rows[0] if rows else None


def _upsert_org(db: Session) -> Organization:
    org = _one_or_none(
        db.query(Organization).filter(Organization.slug == ORG_SLUG),
        label="organization",
    )
    if org is None:
        org = Organization(
            name=ORG_NAME,
            slug=ORG_SLUG,
            sync_mode="standalone",
            workable_connected=False,
            bullhorn_connected=False,
            credits_balance=0,
        )
        db.add(org)
        db.flush()
    else:
        org.name = ORG_NAME
        org.sync_mode = "standalone"
        org.workable_connected = False
        org.bullhorn_connected = False
    return org


def _rotate_read_only_key(db: Session, org: Organization) -> str:
    key = _one_or_none(
        db.query(ApiKey).filter(
            ApiKey.organization_id == org.id,
            ApiKey.name == SEARCH_CANARY_API_KEY_NAME,
        ),
        label="API key",
    )
    token = f"{KEY_PREFIX_LIVE}{secrets.token_urlsafe(32)}"
    if key is None:
        key = ApiKey(
            organization_id=org.id,
            created_by_user_id=None,
            name=SEARCH_CANARY_API_KEY_NAME,
        )
        db.add(key)
    key.prefix = token[: len(KEY_PREFIX_LIVE) + 6]
    key.is_test = False
    key.hashed_secret = hash_token(token)
    key.scopes = [SCOPE_INTERNAL_SEARCH_CANARY_READ]
    key.last_used_at = None
    key.revoked_at = None
    key.expires_at = datetime.now(timezone.utc) + timedelta(
        days=TOKEN_LIFETIME_DAYS
    )
    db.flush()
    return token


def _upsert_role_and_task(db: Session, org: Organization) -> tuple[Role, Task]:
    role = _one_or_none(
        db.query(Role).filter(
            Role.organization_id == org.id,
            Role.name == ROLE_NAME,
        ),
        label="role",
    )
    if role is None:
        role = Role(
            organization_id=org.id,
            name=ROLE_NAME,
            source="manual",
            job_status="open",
        )
        db.add(role)
        db.flush()

    task = _one_or_none(
        db.query(Task).filter(
            Task.organization_id == org.id,
            Task.task_key == TASK_KEY,
        ),
        label="task",
    )
    if task is None:
        task = Task(
            organization_id=org.id,
            task_key=TASK_KEY,
            name="Internal search canary marker",
            description="Synthetic fixture only; never delivered to a candidate.",
            task_type="internal_canary",
            duration_minutes=1,
            is_template=False,
            is_active=False,
        )
        db.add(task)
        db.flush()
    return role, task


def _assert_isolated_org(db: Session, org: Organization) -> None:
    known_emails = {item["email"] for item in _CANDIDATE_TRUTH}
    unexpected_candidates = (
        db.query(Candidate.email)
        .filter(
            Candidate.organization_id == org.id,
            Candidate.email.notin_(known_emails),
        )
        .limit(1)
        .first()
    )
    if unexpected_candidates is not None:
        raise RuntimeError("canary organization contains an unexpected candidate")
    unexpected_roles = (
        db.query(Role.id)
        .filter(Role.organization_id == org.id, Role.name != ROLE_NAME)
        .limit(1)
        .first()
    )
    if unexpected_roles is not None:
        raise RuntimeError("canary organization contains an unexpected role")
    unexpected_users = (
        db.query(User.id)
        .filter(User.organization_id == org.id)
        .limit(1)
        .first()
    )
    if unexpected_users is not None:
        raise RuntimeError("canary organization contains an unexpected user")
    unexpected_tasks = (
        db.query(Task.id)
        .filter(Task.organization_id == org.id, Task.task_key != TASK_KEY)
        .limit(1)
        .first()
    )
    if unexpected_tasks is not None:
        raise RuntimeError("canary organization contains an unexpected task")
    unexpected_keys = (
        db.query(ApiKey.id)
        .filter(
            ApiKey.organization_id == org.id,
            ApiKey.name != SEARCH_CANARY_API_KEY_NAME,
        )
        .limit(1)
        .first()
    )
    if unexpected_keys is not None:
        raise RuntimeError("canary organization contains an unexpected API key")


def _upsert_candidate_truth(
    db: Session,
    *,
    org: Organization,
    role: Role,
    task: Task,
    truth: dict,
) -> None:
    candidate = _one_or_none(
        db.query(Candidate).filter(
            Candidate.organization_id == org.id,
            Candidate.email == truth["email"],
        ),
        label=f"candidate {truth['email']}",
    )
    if candidate is None:
        candidate = Candidate(organization_id=org.id, email=truth["email"])
        db.add(candidate)
        db.flush()
    candidate.full_name = "Synthetic Search Canary"
    candidate.position = "Data Engineer"
    candidate.skills = list(truth["skills"])
    candidate.location_country = truth["country"]
    candidate.deleted_at = None

    application = _one_or_none(
        db.query(CandidateApplication).filter(
            CandidateApplication.candidate_id == candidate.id,
            CandidateApplication.role_id == role.id,
        ),
        label=f"application {truth['email']}",
    )
    transition_at = datetime.now(timezone.utc)
    if application is None:
        application = CandidateApplication(
            organization_id=org.id,
            candidate_id=candidate.id,
            role_id=role.id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_updated_at=transition_at,
            pipeline_stage_source="system",
            application_outcome="open",
            application_outcome_updated_at=transition_at,
            version=1,
            source="manual",
            external_refs={"internal_canary": "search-v1"},
        )
        db.add(application)
    application.status = "applied"
    if application.pipeline_stage != "review":
        application.pipeline_stage = "review"
        application.pipeline_stage_updated_at = transition_at
    elif application.pipeline_stage_updated_at is None:
        application.pipeline_stage_updated_at = transition_at
    application.pipeline_stage_source = "system"
    if application.application_outcome != "open":
        application.application_outcome = "open"
        application.application_outcome_updated_at = transition_at
    elif application.application_outcome_updated_at is None:
        application.application_outcome_updated_at = transition_at
    application.source = "manual"
    application.external_refs = {"internal_canary": "search-v1"}
    application.deleted_at = None
    db.flush()

    assessment = _one_or_none(
        db.query(Assessment).filter(
            Assessment.application_id == application.id,
            Assessment.is_voided.isnot(True),
        ),
        label=f"active assessment {truth['email']}",
    )
    if assessment is None:
        assessment = Assessment(
            organization_id=org.id,
            candidate_id=candidate.id,
            role_id=role.id,
            application_id=application.id,
            task_id=task.id,
            # Candidate runtime routes treat this as a bearer capability. The
            # recurring search canary never uses it, so mint it randomly once
            # and leave it unchanged on idempotent reruns.
            token=secrets.token_urlsafe(48),
            duration_minutes=1,
            is_voided=False,
        )
        db.add(assessment)
    elif not assessment.token or str(assessment.token).startswith(
        "internal-search-canary-v1-"
    ):
        # Repair only the predictable token shape used by an early local
        # draft. Strong existing capabilities remain stable across reruns.
        assessment.token = secrets.token_urlsafe(48)
    desired_status = truth["assessment_status"]
    was_completed = assessment.status == AssessmentStatus.COMPLETED
    assessment.status = desired_status
    if desired_status is AssessmentStatus.COMPLETED:
        if not was_completed or assessment.completed_at is None:
            assessment.completed_at = datetime.now(timezone.utc)
    else:
        assessment.completed_at = None


def provision(db: Session) -> tuple[Role, str]:
    org = _upsert_org(db)
    role, task = _upsert_role_and_task(db, org)
    token = _rotate_read_only_key(db, org)
    _assert_isolated_org(db, org)
    for truth in _CANDIDATE_TRUTH:
        _upsert_candidate_truth(db, org=org, role=role, task=task, truth=truth)
    db.flush()
    return role, token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    if not args.apply or args.confirm != CONFIRMATION:
        parser.error(
            f"writes require --apply --confirm {CONFIRMATION}"
        )

    with SessionLocal() as db:
        try:
            role, token = provision(db)
            role_id = int(role.id)
            db.commit()
        except Exception:
            db.rollback()
            raise

    print(f"TALI_SEARCH_CANARY_ROLE_ID={role_id}")
    print(f"TALI_SEARCH_CANARY_TOKEN={token}")
    print(f"Grounded inclusion: {EXPECTED_EMAIL}")
    print("Grounded exclusions: " + ",".join(EXCLUDED_EMAILS))
    print("Rotate the protected token secret within 365 days.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
