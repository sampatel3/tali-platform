"""P1: public candidate apply.

The write half of the careers surface: a person submits an application to a
published role. Deterministic and cheap — resolve/create the candidate by
identity keys, run the knockout gate, create the application. No LLM here (the
knockout gate is the pre-LLM filter). Mutators flush but do NOT commit; the
route owns the transaction.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.disqualification_reason import DISPOSITION_WE_REJECTED
from ...models.role import Role
from ...services.candidate_identity_service import normalize_phone, resolve_candidate
from .screening_service import evaluate_knockouts, list_role_questions


@dataclass
class ApplyResult:
    application: CandidateApplication
    created: bool
    knockout_passed: bool
    failed_question_ids: list[int]


def submit_application(
    db: Session,
    org_id: int,
    role: Role,
    *,
    full_name: str,
    email: str | None = None,
    phone: str | None = None,
    answers: dict | None = None,
    source_name: str | None = None,
) -> ApplyResult:
    """Idempotent per (candidate, role): a second submit returns the existing
    application rather than creating a duplicate (the unique
    (candidate_id, role_id) constraint would reject it anyway)."""
    answers = answers or {}

    candidate = resolve_candidate(db, org_id, email=email, phone=phone)
    if candidate is None:
        candidate = Candidate(
            organization_id=org_id,
            email=(email or "").strip().lower() or None,
            full_name=(full_name or "").strip() or None,
            phone=(phone or "").strip() or None,
            phone_normalized=normalize_phone(phone),
            lead_source="careers",
        )
        db.add(candidate)
        db.flush()
    else:
        # Backfill only empty fields — never overwrite what we already hold.
        if not candidate.full_name and full_name:
            candidate.full_name = full_name.strip()
        if not candidate.phone and phone:
            candidate.phone = phone.strip()
            candidate.phone_normalized = normalize_phone(phone)

    existing = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.candidate_id == candidate.id,
            CandidateApplication.role_id == role.id,
            CandidateApplication.deleted_at.is_(None),
        )
        .first()
    )
    if existing is not None:
        meta = (existing.screening_answers or {}).get("_knockout", {})
        return ApplyResult(
            application=existing,
            created=False,
            knockout_passed=bool(meta.get("passed", True)),
            failed_question_ids=list(meta.get("failed", [])),
        )

    questions = list_role_questions(db, org_id, role.id)
    passed, failed = evaluate_knockouts(questions, answers)

    application = CandidateApplication(
        organization_id=org_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        application_outcome="open" if passed else "rejected",
        source="careers",
        source_strategy="inbound",
        source_name=source_name,
        screening_answers={**answers, "_knockout": {"passed": passed, "failed": failed}},
    )
    if not passed:
        application.disposition_category = DISPOSITION_WE_REJECTED
    db.add(application)
    db.flush()
    return ApplyResult(
        application=application,
        created=True,
        knockout_passed=passed,
        failed_question_ids=failed,
    )
