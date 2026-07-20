"""Score invalidation keeps prior numbers visible but unusable for decisions.

Changed role/candidate inputs create a durable stale generation, supersede old
decision cards, and require a current completed attempt before deciding again.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import event

from app.components.scoring.freshness import (
    application_scores_allow_decision,
    capture_score_generation,
    latest_score_attempts,
    score_generation_is_current,
)
from app.components.scoring.candidate_inputs import (
    candidate_input_fingerprint_from_db,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
from app.services.cv_score_orchestrator import (
    mark_application_scores_stale,
    mark_role_scores_stale,
)

from tests.sub_agents.conftest import make_full_application


def _seed_scored_app(db):
    """Standard fixture: org + role + candidate + application with both
    pre-screen and cv_match scores populated."""
    org, role, candidate, app = make_full_application(db)
    role.job_spec_text = "Hiring a senior engineer."
    app.pre_screen_score_100 = 75.0
    app.requirements_fit_score_100 = 75.0
    app.cv_match_score = 82.0
    app.cv_match_details = {"summary": "Looks good"}
    app.pre_screen_recommendation = "Proceed to screening"
    app.rank_score = 75.0
    db.flush()
    return org, role, candidate, app


def test_candidate_input_fingerprint_requires_exact_workspace_and_role(db):
    org, role, candidate, app = _seed_scored_app(db)

    assert candidate_input_fingerprint_from_db(
        db,
        application_id=int(app.id),
        candidate_id=int(candidate.id),
        organization_id=int(org.id),
        role_id=int(role.id),
    ) is not None
    assert candidate_input_fingerprint_from_db(
        db,
        application_id=int(app.id),
        candidate_id=int(candidate.id),
        organization_id=int(org.id) + 10_000,
        role_id=int(role.id),
    ) is None
    assert candidate_input_fingerprint_from_db(
        db,
        application_id=int(app.id),
        candidate_id=int(candidate.id),
        organization_id=int(org.id),
        role_id=int(role.id) + 10_000,
    ) is None


def test_score_generation_accepts_unchanged_latest_done_attempt(db):
    _org, role, _candidate, app = _seed_scored_app(db)
    job = CvScoreJob(application_id=app.id, role_id=role.id, status="done")
    db.add(job)
    db.flush()

    token = capture_score_generation(db, role=role, application_id=app.id)

    assert token is not None
    assert token.job_id == job.id
    assert score_generation_is_current(
        db, expected=token, locked_role=role, application=app
    )


def test_latest_score_attempts_returns_explicit_provenance_record(db):
    _org, role, _candidate, app = _seed_scored_app(db)
    db.add(
        CvScoreJob(
            application_id=app.id,
            role_id=role.id,
            status="done",
            queued_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    db.flush()
    job = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status="stale",
        queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(job)
    db.flush()

    attempt = latest_score_attempts(db, [int(app.id)])[int(app.id)]

    assert attempt.application_id == int(app.id)
    assert attempt.job_id == int(job.id)
    assert attempt.status == "stale"
    assert attempt.role_id == int(role.id)


def test_application_list_score_status_uses_causal_job_id_order(db):
    from app.domains.assessments_runtime.applications_routes import (
        _latest_score_status_map,
    )

    _org, role, _candidate, app = _seed_scored_app(db)
    older = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status="done",
        queued_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db.add(older)
    db.flush()
    newer = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status="stale",
        queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(newer)
    db.flush()

    assert int(newer.id) > int(older.id)
    assert newer.queued_at < older.queued_at
    assert _latest_score_status_map(db, [int(app.id)]) == {int(app.id): "stale"}


def test_detail_score_status_relationship_uses_causal_job_id_order(db):
    from sqlalchemy.orm import selectinload

    from app.domains.assessments_runtime.role_support import (
        _latest_score_job_status,
    )

    _org, role, _candidate, app = _seed_scored_app(db)
    older = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status="done",
        queued_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db.add(older)
    db.flush()
    newer = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status="stale",
        queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(newer)
    db.commit()
    loaded = (
        db.query(CandidateApplication)
        .options(selectinload(CandidateApplication.score_jobs))
        .filter(CandidateApplication.id == int(app.id))
        .one()
    )

    assert int(newer.id) > int(older.id)
    assert newer.queued_at < older.queued_at
    assert int(loaded.score_jobs[0].id) == int(newer.id)
    assert _latest_score_job_status(loaded) == "stale"


def test_score_generation_rejects_latest_done_attempt_from_another_role(db):
    org, role, _candidate, app = _seed_scored_app(db)
    from app.models.role import Role

    other_role = Role(
        organization_id=int(org.id),
        name="Other role",
        source="manual",
    )
    db.add(other_role)
    db.flush()
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(other_role.id),
            status="done",
        )
    )
    db.flush()

    assert capture_score_generation(db, role=role, application_id=app.id) is None


def test_preliminary_decision_guard_rejects_done_attempt_from_another_role(db):
    org, role, _candidate, app = _seed_scored_app(db)
    from app.models.role import Role

    other_role = Role(
        organization_id=int(org.id),
        name="Other role",
        source="manual",
    )
    db.add(other_role)
    db.flush()
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(other_role.id),
            status="done",
        )
    )
    db.flush()

    assert not application_scores_allow_decision(
        db, int(app.id), application=app, role=role
    )


def test_score_generation_rejects_mismatched_modern_role_intent_cache_key(db):
    _org, role, _candidate, app = _seed_scored_app(db)
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(role.id),
            status="done",
            cache_key="role-intent:obsolete-generation",
        )
    )
    db.flush()

    assert capture_score_generation(db, role=role, application_id=app.id) is None


def test_preliminary_decision_guard_rejects_obsolete_modern_generation(db):
    _org, role, _candidate, app = _seed_scored_app(db)
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(role.id),
            status="done",
            cache_key="role-intent:obsolete-generation",
        )
    )
    db.flush()

    assert not application_scores_allow_decision(
        db, int(app.id), application=app, role=role
    )


def test_score_generation_accepts_matching_modern_role_intent_cache_key(db):
    _org, role, _candidate, app = _seed_scored_app(db)
    from app.services.role_intent_fingerprint import role_intent_fingerprint

    fingerprint = role_intent_fingerprint(role, db=db)
    job = CvScoreJob(
        application_id=int(app.id),
        role_id=int(role.id),
        status="done",
        cache_key=f"role-intent:{fingerprint}",
    )
    db.add(job)
    db.flush()

    token = capture_score_generation(db, role=role, application_id=app.id)

    assert token is not None and token.job_id == int(job.id)
    assert score_generation_is_current(
        db, expected=token, locked_role=role, application=app
    )


def test_score_generation_live_fence_rejects_changed_modern_cache_key(db):
    _org, role, _candidate, app = _seed_scored_app(db)
    from app.services.role_intent_fingerprint import role_intent_fingerprint

    fingerprint = role_intent_fingerprint(role, db=db)
    job = CvScoreJob(
        application_id=int(app.id),
        role_id=int(role.id),
        status="done",
        cache_key=f"role-intent:{fingerprint}",
    )
    db.add(job)
    db.flush()
    token = capture_score_generation(db, role=role, application_id=app.id)
    assert token is not None

    job.cache_key = "role-intent:corrupt-or-superseded-generation"
    db.flush()

    assert not score_generation_is_current(
        db, expected=token, locked_role=role, application=app
    )


def test_score_generation_preserves_explicit_legacy_job_compatibility(db):
    _org, role, _candidate, app = _seed_scored_app(db)
    job = CvScoreJob(
        application_id=int(app.id),
        role_id=None,
        status="done",
        cache_key="legacy-score-cache-key",
    )
    db.add(job)
    db.flush()

    token = capture_score_generation(db, role=role, application_id=app.id)

    assert token is not None and token.job_id == int(job.id)
    assert score_generation_is_current(
        db, expected=token, locked_role=role, application=app
    )


def test_score_generation_rejects_when_newer_done_attempt_replaces_verdict(db):
    _org, role, _candidate, app = _seed_scored_app(db)
    first = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status="done",
        queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(first)
    db.flush()
    token = capture_score_generation(db, role=role, application_id=app.id)
    replacement = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status="done",
        queued_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db.add(replacement)
    db.flush()

    assert token is not None
    assert not score_generation_is_current(
        db, expected=token, locked_role=role, application=app
    )


def test_score_generation_equal_timestamp_uses_higher_job_id(db):
    _org, role, _candidate, app = _seed_scored_app(db)
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = CvScoreJob(
        application_id=app.id, role_id=role.id, status="done", queued_at=at
    )
    db.add(first)
    db.flush()
    token = capture_score_generation(db, role=role, application_id=app.id)
    second = CvScoreJob(
        application_id=app.id, role_id=role.id, status="done", queued_at=at
    )
    db.add(second)
    db.flush()

    assert token is not None and second.id > first.id
    assert not score_generation_is_current(
        db, expected=token, locked_role=role, application=app
    )


def test_legacy_score_generation_is_explicit_and_rejects_later_job(db):
    _org, role, _candidate, app = _seed_scored_app(db)
    token = capture_score_generation(db, role=role, application_id=app.id)
    assert token is not None and token.job_id is None

    db.add(CvScoreJob(application_id=app.id, role_id=role.id, status="done"))
    db.flush()

    assert not score_generation_is_current(
        db, expected=token, locked_role=role, application=app
    )


def test_cold_application_has_no_score_generation_token(db):
    _org, role, _candidate, app = make_full_application(db)
    assert capture_score_generation(db, role=role, application_id=app.id) is None


def test_mark_role_scores_stale_keeps_score_values_visible(db):
    """Invalidation must keep the existing score values populated so
    the UI can show "Strong match — 87 (stale)" until the rescore lands.
    Blanking the score causes the recruiter to see hundreds of orphan
    candidates after a single criterion edit, which destroys trust."""
    _, role, _, app = _seed_scored_app(db)
    assert app.pre_screen_score_100 == 75.0
    assert app.cv_match_score == 82.0

    marked = mark_role_scores_stale(db, role.id)
    assert marked == 1

    # Score values must still be visible.
    assert app.pre_screen_score_100 == 75.0
    assert app.cv_match_score == 82.0
    assert app.requirements_fit_score_100 == 75.0
    assert app.cv_match_details == {"summary": "Looks good"}
    assert app.pre_screen_recommendation == "Proceed to screening"
    assert app.rank_score == 75.0
    # But pre_screen_run_at IS cleared so the next orchestrator pass
    # re-runs Stage-1 against the updated criteria.
    assert app.pre_screen_run_at is None

    # And a stale CvScoreJob row exists so the listing endpoint
    # surfaces score_status="stale" → frontend renders the badge.
    stale = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == app.id, CvScoreJob.status == "stale")
        .all()
    )
    assert len(stale) == 1


def test_mark_role_scores_stale_skips_apps_never_scored(db):
    """Apps that were never scored stay untouched — invalidation only
    affects apps the agent has already produced an opinion on."""
    org, role, candidate, app = make_full_application(db)
    role.job_spec_text = "Hiring."
    # No pre_screen_score, no cv_match_score.
    db.flush()

    marked = mark_role_scores_stale(db, role.id)
    assert marked == 0
    stale = db.query(CvScoreJob).filter(CvScoreJob.application_id == app.id).all()
    assert stale == []


def test_role_intent_change_marks_first_score_in_flight_for_bounded_retry(db):
    _org, role, _candidate, app = make_full_application(db)
    role.job_spec_text = "Hiring."
    running = CvScoreJob(
        application_id=int(app.id), role_id=int(role.id), status="running"
    )
    db.add(running)
    db.flush()

    assert mark_role_scores_stale(
        db,
        int(role.id),
        reason="role_intent_changed",
    ) == 1

    latest = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == int(app.id))
        .order_by(CvScoreJob.id.desc())
        .first()
    )
    assert latest is not None
    assert latest.status == "stale"
    assert latest.error_message == "role_intent_changed"


def test_role_intent_change_retags_attempted_app_without_visible_score(db):
    _org, role, _candidate, app = make_full_application(db)
    role.job_spec_text = "Hiring."
    stale = CvScoreJob(
        application_id=int(app.id),
        role_id=int(role.id),
        status="stale",
        error_message="candidate_data_changed",
    )
    db.add(stale)
    db.flush()

    assert mark_role_scores_stale(
        db,
        int(role.id),
        reason="role_intent_changed",
    ) == 0

    db.refresh(stale)
    assert stale.error_message == "role_intent_changed"


def test_role_intent_change_invalidates_cache_only_legacy_score(db):
    _org, role, _candidate, app = make_full_application(db)
    role.job_spec_text = "Hiring."
    app.role_fit_score_cache_100 = 74.0
    db.flush()

    assert mark_role_scores_stale(
        db,
        int(role.id),
        reason="role_intent_changed",
    ) == 1

    latest = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == int(app.id))
        .one()
    )
    assert latest.status == "stale"
    assert latest.error_message == "role_intent_changed"


def test_mark_role_scores_stale_is_idempotent(db):
    """Re-running invalidation doesn't pile up duplicate stale rows."""
    _, role, _, app = _seed_scored_app(db)
    mark_role_scores_stale(db, role.id)
    # First run created one stale job. Second run finds it already
    # there and skips.
    second = mark_role_scores_stale(db, role.id)
    assert second == 0
    stale = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == app.id, CvScoreJob.status == "stale")
        .all()
    )
    assert len(stale) == 1


def test_repeat_invalidation_retags_latest_stale_reason_for_bounded_recovery(db):
    _, role, _, app = _seed_scored_app(db)
    assert mark_role_scores_stale(db, role.id, reason="salary_cap_lowered") == 1
    stale = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == int(app.id))
        .one()
    )
    assert stale.error_message == "salary_cap_lowered"

    assert mark_role_scores_stale(db, role.id, reason="role_intent_changed") == 0

    db.refresh(stale)
    assert stale.status == "stale"
    assert stale.error_message == "role_intent_changed"


def test_explicit_empty_role_scope_never_invalidates_role_or_provider_artifacts(db):
    _, role, _, app = _seed_scored_app(db)
    role.tech_questions_signature = "old-role-inputs"

    assert mark_role_scores_stale(
        db,
        role.id,
        application_ids=[],
    ) == 0
    assert db.query(CvScoreJob).filter_by(application_id=int(app.id)).count() == 0
    assert app.cv_match_score == 82.0
    assert role.tech_questions_signature == "old-role-inputs"


def test_role_invalidation_is_set_based_for_a_large_scored_cohort(db):
    org, role, _, first = _seed_scored_app(db)
    application_ids = [int(first.id)]
    for index in range(24):
        candidate = Candidate(
            organization_id=int(org.id),
            email=f"set-based-{index}-{role.id}@x.test",
            full_name=f"Candidate {index}",
        )
        db.add(candidate)
        db.flush()
        app = CandidateApplication(
            organization_id=int(org.id),
            candidate_id=int(candidate.id),
            role_id=int(role.id),
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="manual",
            cv_text="Experienced engineer",
            cv_match_score=70.0,
        )
        db.add(app)
        db.flush()
        application_ids.append(int(app.id))
    db.commit()

    statements = 0
    bind = db.get_bind()

    def count_statement(*_args, **_kwargs):
        nonlocal statements
        statements += 1

    event.listen(bind, "after_cursor_execute", count_statement)
    try:
        marked = mark_role_scores_stale(
            db,
            int(role.id),
            reason="role_intent_changed",
        )
    finally:
        event.remove(bind, "after_cursor_execute", count_statement)

    assert marked == len(application_ids)
    assert statements <= 10
    assert (
        db.query(CvScoreJob)
        .filter(
            CvScoreJob.application_id.in_(application_ids),
            CvScoreJob.status == "stale",
        )
        .count()
        == len(application_ids)
    )


def test_invalidation_supersedes_pending_agent_decisions(db):
    """Pending agent decisions reference an underlying score. When that
    score is invalidated, the decision is stale — the agent will likely
    flip its mind once the rescore lands. Leaving the decision in
    'pending' means the recruiter could approve a recommendation the
    agent itself would reverse. Supersede on invalidation."""
    from datetime import datetime, timezone
    from app.models.agent_decision import AgentDecision
    from app.models.role import Role

    _, role, _, app = _seed_scored_app(db)
    # Seed a pending decision (and an already-resolved one to prove the
    # supersede only touches pending rows).
    # BigInteger PKs don't autoincrement on SQLite test DB; set explicitly.
    pending = AgentDecision(
        id=1,
        organization_id=app.organization_id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="Strong match — advance to interview",
        status="pending",
        reasoning="Score 87, comfortably above threshold",
        model_version="v3",
        prompt_version="v3",
        idempotency_key=f"pending-{app.id}",
    )
    resolved = AgentDecision(
        id=2,
        organization_id=app.organization_id,
        role_id=role.id,
        application_id=app.id,
        decision_type="reject",
        recommendation="Reject",
        status="approved",
        reasoning="Earlier attempt — recruiter approved",
        model_version="v3",
        prompt_version="v3",
        resolved_at=datetime.now(timezone.utc),
        idempotency_key=f"approved-{app.id}",
    )
    sister_role = Role(
        organization_id=app.organization_id,
        name="Related role",
        source="manual",
    )
    db.add(sister_role)
    db.flush()
    cross_role_pending = AgentDecision(
        id=3,
        organization_id=app.organization_id,
        role_id=sister_role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="Related-role opportunity",
        status="pending",
        reasoning="Separate related-role evaluation",
        model_version="related-role",
        prompt_version="related-role-v1",
        idempotency_key=f"related-role-{app.id}",
    )
    processing = AgentDecision(
        id=4,
        organization_id=app.organization_id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="Approval already accepted",
        status="processing",
        reasoning="Provider write is in flight",
        model_version="v3",
        prompt_version="v3",
        idempotency_key=f"processing-{app.id}",
    )
    db.add(pending)
    db.add(resolved)
    db.add(cross_role_pending)
    db.add(processing)
    db.flush()

    mark_role_scores_stale(db, role.id, reason="salary_cap_lowered")

    db.refresh(pending)
    db.refresh(resolved)
    db.refresh(cross_role_pending)
    db.refresh(processing)
    # Pending → discarded with audit trail.
    assert pending.status == "discarded"
    assert pending.resolved_at is not None
    assert "salary_cap_lowered" in (pending.resolution_note or "")
    # Already-resolved decisions are NOT touched (audit history preserved).
    assert resolved.status == "approved"
    # The same application can have a separate sister-role recommendation;
    # standard-role invalidation must not cross that role boundary.
    assert cross_role_pending.status == "pending"
    # A concurrent recruiter approval owns pending -> processing. The
    # conditional supersession update can never overwrite it.
    assert processing.status == "processing"


def test_mark_application_scores_stale_scopes_to_single_app(db):
    """Per-candidate invalidation (used by CV upload + Workable digest
    changes) only touches that one application."""
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication

    _, role, _, app_a = _seed_scored_app(db)

    # A second application on the same role — must remain untouched.
    # Reuse role/org to avoid the slug-uniqueness collision in the test
    # fixture's slug=f"sa-org-{id(db)}" recipe.
    candidate_b = Candidate(
        organization_id=app_a.organization_id, email="b@x.test", full_name="B"
    )
    db.add(candidate_b)
    db.flush()
    app_b = CandidateApplication(
        organization_id=app_a.organization_id,
        candidate_id=candidate_b.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        cv_text="Another senior engineer.",
        pre_screen_score_100=75.0,
        cv_match_score=82.0,
    )
    db.add(app_b)
    db.flush()

    ok = mark_application_scores_stale(db, app_a.id)
    assert ok is True
    db.flush()

    from app.models.candidate_application import CandidateApplication as CA
    from app.models.cv_score_job import CvScoreJob
    fresh_a = db.query(CA).filter(CA.id == app_a.id).one()
    fresh_b = db.query(CA).filter(CA.id == app_b.id).one()
    # app_a's scores stay VISIBLE (kept as stale numbers, badge from
    # CvScoreJob status). pre_screen_run_at is cleared so Stage-1 reruns.
    assert fresh_a.pre_screen_score_100 == 75.0
    assert fresh_a.cv_match_score == 82.0
    assert fresh_a.pre_screen_run_at is None
    stale_jobs_a = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == fresh_a.id, CvScoreJob.status == "stale")
        .count()
    )
    assert stale_jobs_a == 1
    # Other app's scores untouched AND no stale job row added for it.
    assert fresh_b.pre_screen_score_100 == 75.0
    assert fresh_b.cv_match_score == 82.0
    stale_jobs_b = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == fresh_b.id, CvScoreJob.status == "stale")
        .count()
    )
    assert stale_jobs_b == 0


def test_rank_score_preserved_on_invalidation(db):
    """``rank_score`` powers the directory ordering. The old behavior
    fell it back to workable_score on invalidation; the new "honest
    stale" UX keeps the agent's rank visible (with a stale badge from
    the CvScoreJob row) so the candidate stays in their familiar
    position while the rescore lands."""
    _, role, _, app = _seed_scored_app(db)
    app.workable_score = 60.0
    db.flush()

    mark_role_scores_stale(db, role.id)
    # rank_score stays at the prior agent score (75) — not 60.
    assert app.rank_score == 75.0


def test_invalidation_resets_pre_screen_run_at_so_next_pass_reruns_stage1(db):
    """Codex P1 (post-merge): if invalidation leaves ``pre_screen_run_at``
    populated, ``application_needs_pre_screen`` returns False on the
    next orchestrator pass — meaning Stage-1 is skipped and the
    orchestrator falls through to v3 cv_match scoring without ever
    re-evaluating the updated must/constraint criteria. Invalidation
    must clear the timestamp."""
    from datetime import datetime, timezone

    from app.services.pre_screening_service import application_needs_pre_screen

    _, role, _, app = _seed_scored_app(db)
    # Seed a "previously screened" timestamp.
    app.pre_screen_run_at = datetime.now(timezone.utc)
    db.flush()
    assert application_needs_pre_screen(app) is False

    mark_role_scores_stale(db, role.id)

    # After invalidation, the next orchestrator pass MUST re-run Stage-1.
    assert app.pre_screen_run_at is None
    assert application_needs_pre_screen(app) is True


def test_invalidation_preserves_aggregate_score_caches(db):
    """Under the new "honest stale" UX the aggregate cache columns
    (taali_score_cache_100, assessment_score_cache_100,
    role_fit_score_cache_100) stay populated so list/detail endpoints
    keep rendering the stale number alongside a stale badge — instead
    of orphaning hundreds of candidates with no number at all whenever
    a recruiter edits a must-have criterion."""
    _, role, _, app = _seed_scored_app(db)
    app.taali_score_cache_100 = 80.0
    app.assessment_score_cache_100 = 70.0
    app.role_fit_score_cache_100 = 82.0
    app.score_mode_cache = "v3"
    db.flush()

    mark_role_scores_stale(db, role.id)

    # Cached aggregates stay visible. The UI uses the CvScoreJob
    # status="stale" row (added by invalidation) to render the badge.
    assert app.taali_score_cache_100 == 80.0
    assert app.assessment_score_cache_100 == 70.0
    assert app.role_fit_score_cache_100 == 82.0
    assert app.score_mode_cache == "v3"


def test_sweeper_skips_apps_whose_latest_job_is_no_longer_stale(db):
    """Codex P1 #4: ``CvScoreJob`` rows are append-only. A successful
    rescore adds a fresh ``pending``/``done`` row but doesn't update
    the old ``stale`` row. The sweeper must filter to apps whose
    LATEST job is stale, not "any historical stale row exists",
    otherwise it re-enqueues already-fixed apps every 30 min.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.cv_score_job import CvScoreJob
    from sqlalchemy import func

    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication

    _, role, _, app_a = _seed_scored_app(db)
    # Second app on the SAME role to avoid the make_full_application
    # slug-uniqueness collision.
    candidate_b = Candidate(
        organization_id=app_a.organization_id, email="b@x.test", full_name="B"
    )
    db.add(candidate_b)
    db.flush()
    app_b = CandidateApplication(
        organization_id=app_a.organization_id,
        candidate_id=candidate_b.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        cv_text="Another engineer CV.",
        pre_screen_score_100=75.0,
        cv_match_score=82.0,
    )
    db.add(app_b)
    db.flush()

    now = datetime.now(timezone.utc)

    # app_a: stale → succeeded since. The later DONE insert is causal latest
    # even though its timestamp is backdated relative to the stale row.
    db.add(CvScoreJob(application_id=app_a.id, role_id=role.id, status="stale", queued_at=now))
    db.flush()
    db.add(CvScoreJob(application_id=app_a.id, role_id=role.id, status="done", queued_at=now - timedelta(hours=1)))
    # app_b: stale and not yet picked up.
    db.add(CvScoreJob(application_id=app_b.id, role_id=role.id, status="stale", queued_at=now))
    db.flush()

    # Mirror the sweeper's window query inline so the test is hermetic.
    latest_subq = (
        db.query(
            CvScoreJob.application_id,
            func.max(CvScoreJob.id).label("max_id"),
        )
        .group_by(CvScoreJob.application_id)
        .subquery()
    )
    latest_stale = (
        db.query(CvScoreJob)
        .join(
            latest_subq,
            (CvScoreJob.application_id == latest_subq.c.application_id)
            & (CvScoreJob.id == latest_subq.c.max_id),
        )
        .filter(CvScoreJob.status == "stale")
        .all()
    )
    app_ids = {j.application_id for j in latest_stale}
    assert app_b.id in app_ids
    assert app_a.id not in app_ids, (
        "app_a was rescored since the stale row was added; sweeper must not re-enqueue it"
    )


def test_mark_application_scores_stale_no_op_when_no_prior_stale_job(db):
    """If the same app was already marked stale, re-marking returns
    False (idempotent)."""
    _, _, _, app = _seed_scored_app(db)
    assert mark_application_scores_stale(db, app.id) is True
    # Already stale → second call returns False.
    assert mark_application_scores_stale(db, app.id) is False


def test_mark_application_scores_stale_reuses_dispatched_pending_attempt(db):
    _org, role, _candidate, app = _seed_scored_app(db)
    done = CvScoreJob(
        application_id=int(app.id),
        role_id=int(role.id),
        status="done",
        queued_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db.add(done)
    db.flush()
    pending = CvScoreJob(
        application_id=int(app.id),
        role_id=int(role.id),
        status="pending",
        queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(pending)
    db.commit()

    assert mark_application_scores_stale(
        db, int(app.id), reason="workable_context_changed"
    ) is False
    db.commit()

    attempts = db.query(CvScoreJob).filter_by(application_id=int(app.id)).all()
    assert [int(row.id) for row in attempts] == [int(done.id), int(pending.id)]
    assert attempts[-1].status == "pending"
    assert attempts[-1].error_message == "workable_context_changed"


def test_mark_role_scores_stale_reuses_dispatched_pending_attempt(db):
    _org, role, _candidate, app = _seed_scored_app(db)
    pending = CvScoreJob(
        application_id=int(app.id), role_id=int(role.id), status="pending"
    )
    db.add(pending)
    db.commit()

    assert mark_role_scores_stale(
        db, int(role.id), reason="role_intent_changed"
    ) == 0
    db.commit()

    attempts = db.query(CvScoreJob).filter_by(application_id=int(app.id)).all()
    assert [int(row.id) for row in attempts] == [int(pending.id)]
    assert attempts[0].status == "pending"
    assert attempts[0].error_message == "role_intent_changed"
