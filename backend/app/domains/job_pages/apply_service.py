"""Native public candidate apply (write half of the job-page careers surface).

A person submits an application to a PUBLISHED job page. Deterministic and cheap:
resolve/create the candidate by identity keys, run the knockout gate, create the
application (idempotent per candidate+role), and — on a knockout failure — emit
the SAME deterministic reject the platform's other automatic rejects emit, so the
candidate lands on the Decision Hub for the recruiter to approve or override.

No LLM here (the knockout gate is the pre-LLM filter). The resume upload +
scoring fan-out is wired in the route, which owns the request/transaction. This
service flushes but does NOT commit.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.disqualification_reason import (
    DISPOSITION_WE_REJECTED,
    DisqualificationReason,
)
from ...models.role import Role
from ...services.candidate_identity_service import normalize_phone, resolve_candidate
from ...services.pre_screen_decision_emitter import queue_knockout_reject
from .screening_service import evaluate_knockouts, list_role_questions

# The catalog reason a knockout failure is dispositioned under. Prefer a
# skills-specific label; fall back to any active ``we_rejected`` reason the org
# has, so the emitted card always carries a real catalog reason.
_PREFERRED_KNOCKOUT_REASON = "Missing required skills"


@dataclass
class ApplyResult:
    application: CandidateApplication
    created: bool
    knockout_passed: bool


def _resolve_reject_reason(
    db: Session, organization_id: int
) -> DisqualificationReason | None:
    """Pick the org's disqualification-reason for a knockout failure: the
    preferred skills label if present, else the lowest-position active
    ``we_rejected`` reason, else None (org has no catalog rows)."""
    preferred = (
        db.query(DisqualificationReason)
        .filter(
            DisqualificationReason.organization_id == organization_id,
            DisqualificationReason.label == _PREFERRED_KNOCKOUT_REASON,
            DisqualificationReason.is_active.is_(True),
        )
        .first()
    )
    if preferred is not None:
        return preferred
    return (
        db.query(DisqualificationReason)
        .filter(
            DisqualificationReason.organization_id == organization_id,
            DisqualificationReason.category == DISPOSITION_WE_REJECTED,
            DisqualificationReason.is_active.is_(True),
        )
        .order_by(DisqualificationReason.position, DisqualificationReason.id)
        .first()
    )


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
    application rather than creating a duplicate. The unique
    ``(candidate_id, role_id)`` constraint is the backstop for a concurrent
    double-submit — the route catches the ``IntegrityError`` and re-reads.

    On a knockout failure the application is created with the answers recorded,
    and a pending ``skip_assessment_reject`` decision is queued (the outcome
    stays ``open`` — the reject is a recruiter-approved HITL step, exactly like a
    pre-screen reject). NO knockout detail is returned to the caller.
    """
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
        )

    questions = list_role_questions(db, org_id, role.id)
    passed, failed = evaluate_knockouts(questions, answers)

    application = CandidateApplication(
        organization_id=org_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        application_outcome="open",
        source="careers",
        source_strategy="inbound",
        source_name=source_name,
        screening_answers={
            **answers,
            "_knockout": {"passed": passed, "failed": failed},
        },
    )
    db.add(application)
    db.flush()

    if not passed:
        reason_row = _resolve_reject_reason(db, org_id)
        reason_label = (
            reason_row.label if reason_row is not None else "Missing required skills"
        )
        # Stamp the recommended-reject state (free-text), mirroring the
        # pre-screen reject path; the structured disposition is applied when the
        # recruiter approves the card (via the shared decision side-effects).
        application.auto_reject_state = "awaiting_recruiter_approval"
        application.auto_reject_reason = reason_label
        queue_knockout_reject(
            db,
            organization_id=org_id,
            role=role,
            application=application,
            reason=reason_label,
            failed_question_ids=failed,
            disqualification_reason_id=(
                reason_row.id if reason_row is not None else None
            ),
        )

    return ApplyResult(
        application=application,
        created=True,
        knockout_passed=passed,
    )
