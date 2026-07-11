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
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
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


def _backfill_identity(
    candidate: Candidate,
    *,
    full_name: str | None,
    email: str | None,
    phone: str | None,
) -> None:
    """Fill EMPTY identity fields from the submission — never overwrite a
    populated value. Symmetric: a phone-matched row gains its missing email, an
    email-matched row gains its missing phone (so the person's next apply via
    the other key resolves to this same row instead of forking a duplicate)."""
    if not candidate.full_name and full_name:
        candidate.full_name = full_name.strip()
    if not candidate.email and (email or "").strip():
        candidate.email = email.strip().lower()
    if not candidate.phone and phone:
        candidate.phone = phone.strip()
        candidate.phone_normalized = normalize_phone(phone)


def _resolve_or_create_candidate(
    db: Session,
    org_id: int,
    *,
    full_name: str,
    email: str | None,
    phone: str | None,
) -> Candidate:
    """Resolve the candidate by identity keys, creating them on a miss.

    Two concurrent FIRST-TIME applies for the same person can both miss the
    initial resolve (stale read) and both insert. Two guards:

    - The insert runs under a SAVEPOINT: if it collides with an identity
      constraint, we roll back to the savepoint and adopt the row the
      concurrent request created (re-resolve).
    - After a successful insert we re-resolve; if an OLDER row now matches
      (a concurrent insert became visible), our fresh row is discarded and the
      older row wins — both requests converge on one candidate.

    Residual race: ``candidates`` carries NO org-scoped unique constraint on
    email/phone today, so two applies committing in separate transactions at
    the exact same moment can still fork duplicate rows — closing that fully
    needs a DB constraint (a follow-up migration, deliberately not added in
    this fix). These guards close the in-process stale-read window and make
    the code constraint-correct the day one lands.
    """
    candidate = resolve_candidate(db, org_id, email=email, phone=phone)
    if candidate is not None:
        _backfill_identity(candidate, full_name=full_name, email=email, phone=phone)
        return candidate

    try:
        with db.begin_nested():
            fresh = Candidate(
                organization_id=org_id,
                email=(email or "").strip().lower() or None,
                full_name=(full_name or "").strip() or None,
                phone=(phone or "").strip() or None,
                phone_normalized=normalize_phone(phone),
                lead_source="careers",
            )
            db.add(fresh)
            db.flush()
    except IntegrityError:
        candidate = resolve_candidate(db, org_id, email=email, phone=phone)
        if candidate is None:
            raise
        _backfill_identity(candidate, full_name=full_name, email=email, phone=phone)
        return candidate

    # Stale-read double-check: prefer an older row that matches the same keys.
    winner = resolve_candidate(db, org_id, email=email, phone=phone)
    if winner is not None and winner.id != fresh.id:
        db.delete(fresh)
        db.flush()
        _backfill_identity(winner, full_name=full_name, email=email, phone=phone)
        return winner
    return fresh


def _restore_soft_deleted_application(
    db: Session,
    application: CandidateApplication,
    *,
    answers: dict,
    passed: bool,
    failed: list[int],
    source_name: str | None,
) -> CandidateApplication:
    """Reactivate a soft-deleted (candidate, role) application as a fresh
    re-application. The ``uq_candidate_role_application`` unique constraint
    spans soft-deleted rows (a known platform gotcha), so a re-apply must reuse
    the row — inserting would violate the constraint. Mirrors the sync
    services' restore pattern (``app.deleted_at = None`` + lifecycle reset)
    and records a fresh ``reapplied`` event."""
    from ..assessments_runtime.pipeline_service import append_application_event

    now = datetime.now(timezone.utc)
    application.deleted_at = None
    application.status = "applied"
    application.pipeline_stage = "applied"
    application.pipeline_stage_source = "system"
    application.pipeline_stage_updated_at = now
    application.application_outcome = "open"
    application.application_outcome_updated_at = now
    application.source = "careers"
    application.source_strategy = "inbound"
    application.source_name = source_name
    application.screening_answers = {
        **answers,
        "_knockout": {"passed": passed, "failed": failed},
    }
    # Clear the previous life's reject stamps — this is a fresh application.
    application.auto_reject_state = None
    application.auto_reject_reason = None
    application.auto_reject_triggered_at = None
    application.disposition_reason_id = None
    application.disposition_category = None
    db.flush()
    append_application_event(
        db,
        app=application,
        event_type="reapplied",
        actor_type="system",
        reason="Candidate re-applied via the public job page",
        to_stage="applied",
        to_outcome="open",
        idempotency_key=f"reapplied:{int(application.id)}:{now.isoformat()}",
    )
    return application


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
    ACTIVE application rather than creating a duplicate. A SOFT-DELETED prior
    application is reactivated in place (the unique ``(candidate_id, role_id)``
    constraint spans soft-deletes, so inserting a second row is impossible).
    The constraint stays the backstop for a concurrent double-submit — the
    route catches the ``IntegrityError`` and re-reads.

    On a knockout failure the application is created with the answers recorded,
    and a pending ``skip_assessment_reject`` decision is queued (the outcome
    stays ``open`` — the reject is a recruiter-approved HITL step, exactly like a
    pre-screen reject). NO knockout detail is returned to the caller.
    """
    answers = answers or {}

    candidate = _resolve_or_create_candidate(
        db, org_id, full_name=full_name, email=email, phone=phone
    )

    # NO deleted_at filter: the unique constraint spans soft-deleted rows, so
    # the soft-deleted row (at most one exists per candidate+role) must be
    # found and reactivated, never raced with an insert.
    existing = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.candidate_id == candidate.id,
            CandidateApplication.role_id == role.id,
        )
        .first()
    )
    if existing is not None and existing.deleted_at is None:
        meta = (existing.screening_answers or {}).get("_knockout", {})
        return ApplyResult(
            application=existing,
            created=False,
            knockout_passed=bool(meta.get("passed", True)),
        )

    questions = list_role_questions(db, org_id, role.id)
    passed, failed = evaluate_knockouts(questions, answers)

    if existing is not None:
        application = _restore_soft_deleted_application(
            db,
            existing,
            answers=answers,
            passed=passed,
            failed=failed,
            source_name=source_name,
        )
    else:
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
