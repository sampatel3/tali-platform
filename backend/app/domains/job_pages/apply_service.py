"""Native public candidate apply (write half of the job-page careers surface).

A person submits an application to a PUBLISHED job page. Deterministic and cheap:
resolve/create the candidate by identity keys, run the knockout gate, create the
application (idempotent per candidate+role), and — on a knockout failure —
resolve the deterministic reject for an opted-in running role or emit the same
Decision Hub fallback the platform's other automatic rejects use.

No LLM here (the knockout gate is the pre-LLM filter). The resume upload +
scoring fan-out is wired in the route, which owns the request/transaction. This
service flushes but does NOT commit.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...services.candidate_identity_service import normalize_phone, resolve_candidate
from ...services.pre_screen_decision_emitter import queue_knockout_reject
from .knockout_automation import try_auto_resolve_knockout
from .screening_service import evaluate_knockouts, list_role_questions

# The recruiter-facing free-text reason a knockout failure is rejected under. A
# plain constant — the ATS owns any structured disposition-reason catalog, so
# Tali no longer resolves a catalog row here.
_KNOCKOUT_REJECT_REASON = "Missing required skills"


@dataclass
class ApplyResult:
    application: CandidateApplication
    created: bool
    knockout_passed: bool
    # The opaque single-purpose token for the OPTIONAL voluntary-EEO step. It
    # resolves to exactly this application (no raw application_id is ever accepted
    # from the public). Always populated for a resolved application.
    eeo_token: str


def _new_eeo_token() -> str:
    """A random, opaque, single-purpose token — follows the platform's
    ``prefix_ + secrets.token_urlsafe`` pattern (share/report/submittal links)."""
    return f"eeo_{secrets.token_urlsafe(24)}"


def _ensure_eeo_token(db: Session, application: CandidateApplication) -> None:
    """Mint the application's EEO token once and reuse it (overwrite-own-only —
    the applicant may re-submit to correct their self-ID). Pre-existing
    applications from before this column landed get a token on their next apply."""
    if not getattr(application, "eeo_token", None):
        application.eeo_token = _new_eeo_token()
        db.flush()


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


def _promote_matching_prospect(
    db: Session,
    org_id: int,
    candidate: Candidate,
    application: CandidateApplication,
    *,
    email: str | None,
) -> None:
    """Carry a sourced prospect's provenance onto the application it engaged into.

    When the applicant matches an existing prospect for this org (same
    normalized email — prospects are email-keyed), stamp the application's
    ``source_strategy`` as ``"sourced"`` (it would otherwise read ``"inbound"``)
    and flip the prospect to ``converted``, linking it to the resolved
    candidate. This runs only for an ENGAGED applicant (someone who actually
    applied), so it never triggers scoring for un-engaged prospects — scoring
    stays event-triggered on the apply itself. A no-op when there's no email or
    no matching prospect; never creates a prospect.
    """
    from ...models.prospect import Prospect, PROSPECT_STATUS_CONVERTED
    from ...services.email_suppression_service import normalize_email

    email_clean = normalize_email(email or "")
    if not email_clean:
        return
    prospect = (
        db.query(Prospect)
        .filter(
            Prospect.organization_id == org_id,
            Prospect.email == email_clean,
        )
        .first()
    )
    if prospect is None:
        return
    application.source_strategy = "sourced"
    # Only fill an empty source_name — never clobber an attribution the apply
    # form already supplied.
    if not (application.source_name or "").strip() and (prospect.source_name or "").strip():
        application.source_name = prospect.source_name
    if prospect.candidate_id is None:
        prospect.candidate_id = candidate.id
    prospect.status = PROSPECT_STATUS_CONVERTED
    db.flush()


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


def _engage_sourced_application(
    db: Session,
    application: CandidateApplication,
    *,
    answers: dict,
    passed: bool,
    failed: list[int],
    source_name: str | None,
) -> CandidateApplication:
    """Engage a `sourced` prospect into a real application.

    A sourced lead was added to the role BEFORE it applied (un-scored, never in
    the decision queue). When that person actually applies, the SAME row moves
    `sourced -> applied` (respecting the ``uq_candidate_role_application``
    unique constraint — we never insert a duplicate) and, from that point,
    scoring runs. Records the knockout verdict + an audit event, then hands the
    application back to the shared apply tail (score / knockout-reject).
    """
    from ..assessments_runtime.pipeline_service import transition_stage

    application.screening_answers = {
        **answers,
        "_knockout": {"passed": passed, "failed": failed},
    }
    # Carry the "sourced" provenance onto the engaged application.
    application.source_strategy = "sourced"
    if not (application.source_name or "").strip() and (source_name or "").strip():
        application.source_name = source_name
    # Clear any stale reject stamps from the sourced life — this is a fresh apply.
    application.auto_reject_state = None
    application.auto_reject_reason = None
    application.auto_reject_triggered_at = None
    # The engagement transition (system-driven: the candidate applied). This is
    # the ONLY forward edge out of `sourced`; scoring is fanned out by the route
    # AFTER this, never before.
    transition_stage(
        db,
        app=application,
        to_stage="applied",
        source="system",
        actor_type="system",
        reason="Sourced prospect engaged — applied via the public job page",
        idempotency_key=f"sourced_engaged:{int(application.id)}",
    )
    db.flush()
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

    On a knockout failure the application is created with the answers recorded.
    A running role that explicitly enables deterministic auto-reject resolves it
    directly; otherwise a pending ``skip_assessment_reject`` decision is queued
    for recruiter HITL. NO knockout detail is returned to the caller.
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
    # A `sourced` prospect that now applies is an ENGAGEMENT, not an idempotent
    # re-submit: fall through so it transitions sourced -> applied and gets
    # scored. Every OTHER active (candidate, role) application is idempotent.
    is_sourced_engagement = (
        existing is not None
        and existing.deleted_at is None
        and (existing.pipeline_stage or "").strip().lower() == "sourced"
    )
    if existing is not None and existing.deleted_at is None and not is_sourced_engagement:
        meta = (existing.screening_answers or {}).get("_knockout", {})
        _ensure_eeo_token(db, existing)
        return ApplyResult(
            application=existing,
            created=False,
            knockout_passed=bool(meta.get("passed", True)),
            eeo_token=existing.eeo_token,
        )

    questions = list_role_questions(db, org_id, role.id)
    passed, failed = evaluate_knockouts(questions, answers)

    if is_sourced_engagement:
        application = _engage_sourced_application(
            db,
            existing,
            answers=answers,
            passed=passed,
            failed=failed,
            source_name=source_name,
        )
    elif existing is not None:
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

    # Provenance: if this applicant was a sourced prospect, the application it
    # engaged into should carry the "sourced" strategy and the prospect flips to
    # converted. Runs for both a fresh and a reactivated application.
    _promote_matching_prospect(db, org_id, candidate, application, email=email)

    if not passed:
        resolved = try_auto_resolve_knockout(
            db,
            role=role,
            application=application,
            reason=_KNOCKOUT_REJECT_REASON,
            failed_question_ids=failed,
        )
        if not resolved:
            # Policy off, role no longer runnable, or ATS write-back failure:
            # retain the existing HITL path. The ATS owns any structured
            # disposition reason, so the card carries only the free-text reason.
            application.auto_reject_state = "awaiting_recruiter_approval"
            application.auto_reject_reason = _KNOCKOUT_REJECT_REASON
            queue_knockout_reject(
                db,
                organization_id=org_id,
                role=role,
                application=application,
                reason=_KNOCKOUT_REJECT_REASON,
                failed_question_ids=failed,
                disqualification_reason_id=None,
            )

    _ensure_eeo_token(db, application)
    return ApplyResult(
        application=application,
        created=True,
        knockout_passed=passed,
        eeo_token=application.eeo_token,
    )
