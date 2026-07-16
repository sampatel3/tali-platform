"""Transaction isolation for best-effort related-role outcome projection."""

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.domains.assessments_runtime.pipeline_service import transition_outcome
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation


def _seed_close_and_reopen_cases(db):
    organization = Organization(name="Related-role savepoint regression")
    db.add(organization)
    db.flush()
    source = Role(
        organization_id=organization.id,
        name="Canonical ATS role",
        source="workable",
        workable_job_id="SAVEPOINT-SOURCE",
        job_spec_text="Canonical role specification for savepoint regression coverage.",
    )
    sister = Role(
        organization_id=organization.id,
        name="Related-role projection",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role=source,
        job_spec_text="Related role specification for savepoint regression coverage.",
    )
    closing_candidate = Candidate(
        organization_id=organization.id,
        email="savepoint-close@example.com",
        full_name="Close Candidate",
        cv_text="Production Python and distributed systems experience.",
    )
    reopening_candidate = Candidate(
        organization_id=organization.id,
        email="savepoint-reopen@example.com",
        full_name="Reopen Candidate",
    )
    closing_application = CandidateApplication(
        organization_id=organization.id,
        candidate=closing_candidate,
        role=source,
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="system",
        application_outcome="open",
        version=4,
        source="workable",
        cv_text=closing_candidate.cv_text,
    )
    reopening_application = CandidateApplication(
        organization_id=organization.id,
        candidate=reopening_candidate,
        role=source,
        status="rejected",
        pipeline_stage="applied",
        pipeline_stage_source="system",
        application_outcome="rejected",
        version=7,
        source="workable",
    )
    db.add_all(
        [
            source,
            sister,
            closing_candidate,
            reopening_candidate,
            closing_application,
            reopening_application,
        ]
    )
    db.flush()

    closing_evaluation = SisterRoleEvaluation(
        organization_id=organization.id,
        role_id=sister.id,
        source_application_id=closing_application.id,
        status="done",
        spec_fingerprint="closing-spec",
        cv_fingerprint="closing-cv",
        role_fit_score=81.0,
    )
    reopening_evaluation = SisterRoleEvaluation(
        organization_id=organization.id,
        role_id=sister.id,
        source_application_id=reopening_application.id,
        status="excluded",
        spec_fingerprint="reopening-spec",
        error_message="Shared ATS application is disqualified or closed",
    )
    db.add_all([closing_evaluation, reopening_evaluation])
    db.commit()
    return (
        closing_application,
        reopening_application,
        closing_evaluation,
        reopening_evaluation,
    )


def test_related_role_flush_failure_does_not_poison_close_or_reopen(db, caplog):
    """Real projection constraint failures stay inside their savepoints."""
    (
        closing_application,
        reopening_application,
        closing_evaluation,
        reopening_evaluation,
    ) = _seed_close_and_reopen_cases(db)
    application_ids = (int(closing_application.id), int(reopening_application.id))
    evaluation_ids = (int(closing_evaluation.id), int(reopening_evaluation.id))
    initial_versions = (
        int(closing_application.version),
        int(reopening_application.version),
    )
    targeted_ids = set(evaluation_ids)
    failed_flushes: list[int] = []

    def _violate_spec_fingerprint_not_null(session, _flush_context, _instances):
        for row in tuple(session.dirty):
            if isinstance(row, SisterRoleEvaluation) and int(row.id) in targeted_ids:
                failed_flushes.append(int(row.id))
                row.spec_fingerprint = None

    caplog.set_level("ERROR", logger="taali.pipeline_service")
    event.listen(db, "before_flush", _violate_spec_fingerprint_not_null)
    try:
        transition_outcome(
            db,
            app=closing_application,
            to_outcome="rejected",
            actor_type="recruiter",
            reason="Canonical reject survives projection failure",
        )
        db.commit()

        transition_outcome(
            db,
            app=reopening_application,
            to_outcome="open",
            actor_type="recruiter",
            reason="Canonical reopen survives projection failure",
        )
        db.commit()
    finally:
        event.remove(db, "before_flush", _violate_spec_fingerprint_not_null)

    db.expire_all()
    closed = db.get(CandidateApplication, application_ids[0])
    reopened = db.get(CandidateApplication, application_ids[1])
    assert (closed.application_outcome, closed.status, closed.version) == (
        "rejected",
        "rejected",
        initial_versions[0] + 1,
    )
    assert (reopened.application_outcome, reopened.status, reopened.version) == (
        "open",
        "applied",
        initial_versions[1] + 1,
    )

    # Both projection mutations rolled back without poisoning the Session.
    saved_close = db.get(SisterRoleEvaluation, evaluation_ids[0])
    saved_reopen = db.get(SisterRoleEvaluation, evaluation_ids[1])
    assert (saved_close.status, saved_close.spec_fingerprint) == (
        "done",
        "closing-spec",
    )
    assert (saved_reopen.status, saved_reopen.spec_fingerprint) == (
        "excluded",
        "reopening-spec",
    )

    assert failed_flushes == list(evaluation_ids)
    failures = [
        record
        for record in caplog.records
        if record.name == "taali.pipeline_service"
        and "related-role outcome reconcile failed" in record.getMessage()
    ]
    assert len(failures) == 2
    assert all(
        record.exc_info is not None and isinstance(record.exc_info[1], IntegrityError)
        for record in failures
    )

    outcome_events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id.in_(application_ids),
            CandidateApplicationEvent.event_type == "application_outcome_changed",
        )
        .all()
    )
    assert {
        (row.application_id, row.from_outcome, row.to_outcome) for row in outcome_events
    } == {
        (application_ids[0], "open", "rejected"),
        (application_ids[1], "rejected", "open"),
    }
