"""Deterministic bulk decisioning gives EVERY scored candidate a verdict,
on the single role threshold, with no LLM calls and full dedup.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.decision_policy.bootstrap import bootstrap_org
from app.models.assessment import Assessment, AssessmentStatus
from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.task import Task
from app.models.usage_event import UsageEvent
from app.services import bulk_decision_service
from app.services.bulk_decision_service import decide_role_cohort

def _seed_role(db, *, score_threshold=50, with_task=False):
    org = Organization(name="O", slug=f"o-{id(db)}-{score_threshold}-{with_task}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id, name="R", source="manual",
        score_threshold=score_threshold,
        # These tests exercise a recruiter-pinned threshold; the product default
        # is now ``auto`` (dynamic), so opt into manual to honour score_threshold.
        auto_reject_threshold_mode="manual",
    )
    db.add(role)
    db.flush()
    if with_task:
        task = Task(name="Take-home assessment")
        db.add(task)
        db.flush()
        role.tasks.append(task)
        db.flush()
    bootstrap_org(db, organization_id=int(org.id))
    db.commit()
    return org, role


def _add_app(db, org, role, *, role_fit, pre_screen=70.0, cv_match_details=None):
    cand = Candidate(
        organization_id=org.id,
        email=f"c{role_fit}-{id(db)}@x.test",
        full_name=f"C{role_fit}",
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="applied", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual", cv_text="cv text",
        cv_match_score=role_fit, pre_screen_score_100=pre_screen,
        cv_match_details=cv_match_details,
    )
    db.add(app)
    db.commit()
    return app


def _pending(db, role):
    return (
        db.query(AgentDecision)
        .filter(AgentDecision.role_id == role.id, AgentDecision.status == "pending")
        .all()
    )


def _attach_completed_assessment(
    db,
    *,
    app,
    role,
    status=AssessmentStatus.COMPLETED,
    assessment_score=80.0,
    taali_score=82.0,
):
    task = role.tasks[0]
    row = Assessment(
        organization_id=app.organization_id,
        candidate_id=app.candidate_id,
        application_id=app.id,
        role_id=role.id,
        task_id=task.id,
        token=f"completed-{app.id}-{status.value}",
        status=status,
        completed_at=datetime.now(timezone.utc),
        completed_due_to_timeout=(
            status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
        ),
        assessment_score=assessment_score,
        taali_score=taali_score,
    )
    db.add(row)
    app.pipeline_stage = "review"
    app.pipeline_stage_source = "system"
    app.assessment_score_cache_100 = assessment_score
    app.taali_score_cache_100 = taali_score
    db.commit()
    return row


def test_every_candidate_decided_no_task_advances_strong(db):
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    _add_app(db, org, role, role_fit=80.0)  # >= 50 -> advance (no task)
    _add_app(db, org, role, role_fit=55.0)  # >= 50 -> advance
    _add_app(db, org, role, role_fit=40.0)  # < 50  -> reject
    _add_app(db, org, role, role_fit=20.0)  # < 50  -> reject

    summary = decide_role_cohort(db, role=role)

    decs = _pending(db, role)
    assert len(decs) == 4, "every scored candidate must get a decision"
    types = sorted(d.decision_type for d in decs)
    assert types == ["advance_to_interview", "advance_to_interview", "reject", "reject"]
    assert summary["created"] == 4
    # No LLM: the deterministic pass writes zero usage_events.
    assert db.query(UsageEvent).count() == 0


def test_bulk_auto_promote_executes_positive_and_holds_reject(db):
    """The deterministic cohort producer shares the same autonomy rail:
    reversible positives execute, irreversible rejects remain reviewable."""
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    role.agentic_mode_enabled = True
    role.auto_promote = True
    db.commit()
    strong = _add_app(db, org, role, role_fit=80.0)
    weak = _add_app(db, org, role, role_fit=20.0)

    summary = decide_role_cohort(db, role=role)

    db.refresh(strong)
    db.refresh(weak)
    assert strong.pipeline_stage == "advanced"
    assert strong.pipeline_stage_source == "agent"
    assert weak.pipeline_stage == "applied"
    assert summary["auto_executed"] == 1
    pending = _pending(db, role)
    assert len(pending) == 1 and pending[0].decision_type == "reject"


def test_activation_recovery_drains_existing_deterministic_positive(db):
    """A positive card created while autonomy was off cannot block the role
    forever after Turn on; the next cohort bootstrap revalidates and executes it."""
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    role.agentic_mode_enabled = False
    strong = _add_app(db, org, role, role_fit=80.0)
    first = decide_role_cohort(db, role=role)
    assert first["created"] == 1
    assert len(_pending(db, role)) == 1

    role.agentic_mode_enabled = True
    role.auto_promote = True
    db.commit()
    recovered = decide_role_cohort(db, role=role)

    db.refresh(strong)
    assert recovered["existing_auto_executed"] == 1
    assert strong.pipeline_stage == "advanced"
    assert _pending(db, role) == []


def test_reasoning_sourced_from_cv_match_summary(db):
    """Recruiter-facing reasoning is the CV-match narrative (same source as
    the report hero), with the threshold mechanics demoted to evidence."""
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    summary = "Strong backend depth; ships production Python and owns CI."
    _add_app(
        db, org, role, role_fit=80.0,
        cv_match_details={"summary": summary, "score_rationale_bullets": ["x"]},
    )

    decide_role_cohort(db, role=role)

    dec = _pending(db, role)[0]
    assert dec.reasoning == summary
    # Audit basis lives in evidence, not the headline.
    assert "role-fit 80 vs threshold" in dec.evidence["policy_basis"]
    assert "Deterministic policy" not in dec.reasoning


def test_reasoning_falls_back_to_rationale_then_policy(db):
    """No summary -> first rationale bullet; nothing at all -> policy basis,
    so reasoning is never blank (queue_decision rejects empty reasoning)."""
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    _add_app(
        db, org, role, role_fit=80.0,
        cv_match_details={"summary": "", "score_rationale_bullets": ["Clears the bar on core skills."]},
    )
    _add_app(db, org, role, role_fit=75.0, cv_match_details={"summary": ""})

    decide_role_cohort(db, role=role)

    reasons = {d.reasoning for d in _pending(db, role)}
    assert "Clears the bar on core skills." in reasons
    assert any(r.startswith("Deterministic policy:") for r in reasons)


def test_hard_rule_evidence_names_blockers_without_misrepresenting_score(db):
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    _add_app(
        db,
        org,
        role,
        role_fit=80.0,
        cv_match_details={
            "summary": "Strong overall fit. Longer report detail that should not be copied to the card.",
            "requirements_assessment": [
                {
                    "requirement_id": "crit_42",
                    "requirement": "Production knowledge-graph delivery",
                    "priority": "must_have",
                    "status": "missing",
                    "blocker": True,
                }
            ],
        },
    )

    decide_role_cohort(db, role=role)

    dec = _pending(db, role)[0]
    assert dec.decision_type == "reject"
    assert dec.reasoning == (
        "Strong overall fit. Longer report detail that should not be copied to the card."
    )
    assert dec.evidence["candidate_summary"] == (
        "Strong overall fit. Longer report detail that should not be copied to the card."
    )
    assert dec.evidence["decision_trigger"] == "must_have_blocked"
    assert dec.evidence["decision_source"] == "policy"
    assert dec.evidence["policy_revision_id"] is not None
    assert dec.evidence["decision_factors"] == [
        {
            "label": "Production knowledge-graph delivery",
            "status": "missing",
            "priority": "must_have",
        }
    ]
    assert "Candidate fails a must-have requirement" in dec.evidence["policy_basis"]
    assert "hard rule took priority" in dec.evidence["policy_basis"]
    assert "80 vs threshold 50" in dec.evidence["policy_basis"]


def test_holistic_inferred_must_have_does_not_override_role_threshold(db):
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    _add_app(
        db,
        org,
        role,
        role_fit=80.0,
        cv_match_details={
            "requirements_assessment": [
                {
                    "requirement_id": "holistic_3",
                    "requirement": "Knowledge graph development",
                    "priority": "must_have",
                    "status": "missing",
                }
            ]
        },
    )

    decide_role_cohort(db, role=role)

    assert _pending(db, role)[0].decision_type == "advance_to_interview"


def test_strong_candidate_sends_assessment_when_task_assigned(db):
    org, role = _seed_role(db, score_threshold=50, with_task=True)
    _add_app(db, org, role, role_fit=80.0)  # >= 50 + task -> send_assessment
    _add_app(db, org, role, role_fit=30.0)  # < 50 -> reject

    decide_role_cohort(db, role=role)

    decs = _pending(db, role)
    types = sorted(d.decision_type for d in decs)
    assert types == ["reject", "send_assessment"]


def test_completed_assessment_advances_or_rejects_instead_of_resending(db):
    """The post-assessment wake re-enters this bulk path. Persisted completion
    and result scores must skip send_assessment and drive the downstream points."""
    org, role = _seed_role(db, score_threshold=50, with_task=True)
    strong = _add_app(db, org, role, role_fit=80.0)
    weak = _add_app(db, org, role, role_fit=20.0)
    _attach_completed_assessment(
        db,
        app=strong,
        role=role,
        assessment_score=85.0,
        taali_score=88.0,
    )
    _attach_completed_assessment(
        db,
        app=weak,
        role=role,
        assessment_score=35.0,
        taali_score=30.0,
    )

    decide_role_cohort(db, role=role)

    decisions = {d.application_id: d.decision_type for d in _pending(db, role)}
    assert decisions == {
        strong.id: "advance_to_interview",
        weak.id: "reject",
    }
    assert "send_assessment" not in decisions.values()


def test_strong_cv_failed_assessment_queues_hitl_reject(db):
    """Auto-promote cannot advance or auto-reject a completed failed attempt.

    The failed assessment is decisive despite strong CV/TAALI signals, and the
    irreversible reject remains a pending recruiter-approval card.
    """
    org, role = _seed_role(db, score_threshold=50, with_task=True)
    role.agentic_mode_enabled = True
    role.auto_promote = True
    db.commit()
    app = _add_app(db, org, role, role_fit=95.0, pre_screen=95.0)
    _attach_completed_assessment(
        db,
        app=app,
        role=role,
        assessment_score=20.0,
        taali_score=90.0,
    )

    summary = decide_role_cohort(db, role=role)

    db.refresh(app)
    pending = _pending(db, role)
    assert len(pending) == 1
    assert pending[0].decision_type == "reject"
    assert pending[0].status == "pending"
    assert app.pipeline_stage == "review"
    assert summary.get("auto_executed", 0) == 0
    assert "assessment_score < assessment_score_min" in " | ".join(
        pending[0].evidence["rule_path"]
    )


def test_timeout_completion_is_terminal_and_never_resends_assessment(db):
    org, role = _seed_role(db, score_threshold=50, with_task=True)
    app = _add_app(db, org, role, role_fit=80.0)
    _attach_completed_assessment(
        db,
        app=app,
        role=role,
        status=AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
        assessment_score=76.0,
        taali_score=79.0,
    )

    decide_role_cohort(db, role=role)

    decisions = _pending(db, role)
    assert len(decisions) == 1
    assert decisions[0].decision_type == "advance_to_interview"


def test_incomplete_rubric_blocks_all_autonomous_decisions_with_stale_scores(db):
    """A provider failure cannot turn a partial zero or stale cache into an
    advance/reject, and a terminal attempt must not trigger a second invite."""
    org, role = _seed_role(db, score_threshold=50, with_task=True)
    app = _add_app(db, org, role, role_fit=95.0)
    assessment = _attach_completed_assessment(
        db,
        app=app,
        role=role,
        assessment_score=92.0,
        taali_score=94.0,
    )
    assessment.scoring_partial = True
    assessment.score_breakdown = {
        "rubric_grading": {
            "status": "partial",
            "fully_graded": False,
            "failed_dimension_ids": ["quality"],
        }
    }
    db.commit()

    summary = decide_role_cohort(db, role=role)

    assert _pending(db, role) == []
    assert summary.get("created", 0) == 0


def test_idempotent_no_double_queue(db):
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    _add_app(db, org, role, role_fit=80.0)
    _add_app(db, org, role, role_fit=20.0)

    first = decide_role_cohort(db, role=role)
    assert first["created"] == 2
    second = decide_role_cohort(db, role=role)
    # Second pass selects nothing (all have a pending decision now).
    assert second.get("created", 0) == 0
    assert len(_pending(db, role)) == 2


def test_skips_candidate_with_existing_pending(db):
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    app = _add_app(db, org, role, role_fit=80.0)
    # Pre-existing pending decision for this app (e.g. from the LLM agent).
    db.add(AgentDecision(
        organization_id=org.id, role_id=role.id, application_id=app.id,
        decision_type="advance_to_interview", recommendation="advance_to_interview",
        status="pending", reasoning="manual", model_version="x", prompt_version="x",
        idempotency_key=f"pre:{app.id}",
    ))
    db.commit()

    summary = decide_role_cohort(db, role=role)
    assert summary["candidates"] == 0  # excluded by the pending filter
    assert len(_pending(db, role)) == 1


def test_standard_cohort_ignores_pending_cards_from_two_related_roles(db):
    org, owner = _seed_role(db, score_threshold=50, with_task=False)
    app = _add_app(db, org, owner, role_fit=80.0)
    related_roles = [
        Role(
            organization_id=int(org.id),
            name=f"Related {index}",
            source="taali",
            role_kind=ROLE_KIND_SISTER,
            ats_owner_role_id=int(owner.id),
        )
        for index in (1, 2)
    ]
    db.add_all(related_roles)
    db.flush()
    for related in related_roles:
        db.add(
            AgentDecision(
                organization_id=int(org.id),
                role_id=int(related.id),
                application_id=int(app.id),
                decision_type="send_assessment",
                recommendation="send_assessment",
                status="pending",
                reasoning="Related-role verdict",
                model_version="related-role-deterministic",
                prompt_version="related-role-runtime-v1",
                idempotency_key=f"related:{related.id}:{app.id}",
            )
        )
    db.commit()

    summary = decide_role_cohort(db, role=owner)

    assert summary["candidates"] == 1
    assert summary["created"] == 1
    assert len(_pending(db, owner)) == 1
    assert all(len(_pending(db, related)) == 1 for related in related_roles)


def test_volume_guard_raises_threshold_question(db, monkeypatch):
    monkeypatch.setattr(bulk_decision_service.cohort, "VOLUME_GUARD_PENDING_LIMIT", 2)
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    _add_app(db, org, role, role_fit=80.0)
    _add_app(db, org, role, role_fit=75.0)
    _add_app(db, org, role, role_fit=70.0)

    decide_role_cohort(db, role=role)

    qs = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == role.id,
            AgentNeedsInput.kind == "threshold_ambiguous",
            AgentNeedsInput.resolved_at.is_(None),
        )
        .all()
    )
    assert len(qs) == 1, "high review load should open one threshold question"


def test_reconcile_re_decides_pending_on_threshold_shift(db):
    """When the threshold recalibrates, an existing pending bulk decision
    whose band flipped is discarded and re-decided with the new bar."""
    org, role = _seed_role(db, score_threshold=40, with_task=False)  # manual mode
    _add_app(db, org, role, role_fit=55.0)  # 55 >= 40 -> advance (no task)

    decide_role_cohort(db, role=role)
    decs = _pending(db, role)
    assert len(decs) == 1 and decs[0].decision_type == "advance_to_interview"

    # Raise the threshold above the candidate's score -> should flip to reject.
    role.score_threshold = 70
    db.commit()
    summary = decide_role_cohort(db, role=role)
    assert summary.get("reconciled_discarded", 0) >= 1

    decs = _pending(db, role)
    assert len(decs) == 1, "still exactly one pending decision (re-decided, not duplicated)"
    assert decs[0].decision_type == "reject"


def test_reconcile_noop_when_threshold_unchanged(db):
    """No churn: re-running with the same threshold discards nothing."""
    org, role = _seed_role(db, score_threshold=40, with_task=False)
    _add_app(db, org, role, role_fit=55.0)
    decide_role_cohort(db, role=role)
    summary = decide_role_cohort(db, role=role)
    assert summary.get("reconciled_discarded", 0) == 0
    assert len(_pending(db, role)) == 1


def test_reconcile_explains_policy_input_flip_without_blaming_threshold(db):
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    app = _add_app(db, org, role, role_fit=80.0, cv_match_details={"requirements_assessment": []})
    decide_role_cohort(db, role=role)
    first = _pending(db, role)[0]
    assert first.decision_type == "advance_to_interview"

    app.cv_match_details = {
        "requirements_assessment": [
            {
                "requirement_id": "crit_7",
                "requirement": "Required clearance",
                "priority": "must_have",
                "status": "missing",
                "blocker": True,
            }
        ]
    }
    db.commit()

    decide_role_cohort(db, role=role)

    db.refresh(first)
    assert first.status == "discarded"
    assert first.resolution_note.startswith("policy inputs changed;")
    assert "threshold recalibrated" not in first.resolution_note
    assert _pending(db, role)[0].decision_type == "reject"


def test_scored_below_pre_screen_line_still_rejected(db):
    """A scored candidate below the pre-screen line (pre_screen < 50) is still
    decided deterministically — previously it was skipped by the pre_screen>=50
    gate and could only be decided by the (possibly unreachable) LLM."""
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    # Mirrors the stranded prod rows: pre_screen == cv_match, both < 50.
    _add_app(db, org, role, role_fit=20.0, pre_screen=20.0)
    _add_app(db, org, role, role_fit=35.0, pre_screen=35.0)

    summary = decide_role_cohort(db, role=role)

    decs = _pending(db, role)
    assert len(decs) == 2, "sub-50 scored candidates must now be decided"
    assert sorted(d.decision_type for d in decs) == ["reject", "reject"]
    assert summary["created"] == 2
    assert db.query(UsageEvent).count() == 0  # still no LLM


def test_high_role_fit_low_pre_screen_is_no_action(db):
    """Safety: a candidate above the role-fit bar but below pre_screen_min is
    NOT auto-sent/advanced — the send rule's independent pre_screen_min gate
    holds, so it falls through to no_action and is left to the LLM/recruiter."""
    org, role = _seed_role(db, score_threshold=50, with_task=True)
    _add_app(db, org, role, role_fit=80.0, pre_screen=20.0)

    summary = decide_role_cohort(db, role=role)

    assert summary["candidates"] == 1  # selected (no longer gated out)
    assert _pending(db, role) == []  # but no decision emitted
    assert summary.get("created", 0) == 0


def test_bulk_skips_workable_disqualified(db):
    """(c) A candidate disqualified in Workable is frozen — even with a low
    role-fit that would otherwise reject, the bulk pass must not queue a
    decision (the recruiter already dismissed them externally)."""
    from datetime import datetime, timezone

    org, role = _seed_role(db, score_threshold=50, with_task=False)
    app = _add_app(db, org, role, role_fit=20.0)
    app.workable_disqualified_at = datetime.now(timezone.utc)
    db.commit()

    summary = decide_role_cohort(db, role=role)
    assert summary["candidates"] == 0
    assert _pending(db, role) == []


def test_bulk_decides_post_handover_interview_normally(db):
    """(d) A candidate the recruiter advanced past handover in Workable (e.g.
    moved to Technical Interview before the application entered Taali) is
    decided like everyone else: the reject verdict becomes a normal HITL card
    whose evidence carries the Workable stage so approve surfaces can warn.
    Nothing is auto-executed."""
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    app = _add_app(db, org, role, role_fit=20.0)  # low score → reject verdict
    app.workable_stage = "Technical Interview"
    db.commit()

    summary = decide_role_cohort(db, role=role)
    assert summary.get("skipped_post_handover", 0) == 0
    pending = _pending(db, role)
    assert len(pending) == 1
    assert pending[0].decision_type == "reject"
    assert pending[0].status == "pending"  # HITL card, never auto-applied
    assert pending[0].evidence.get("workable_stage") == "Technical Interview"


def test_decide_post_handover_mid_interview_reject_cards(db):
    """A reject-worthy candidate the recruiter is INTERVIEWING in Workable
    (non-terminal post-handover) still gets the HITL reject card — Taali's
    honest second opinion. A candidate frozen as 'advanced' is pulled back to
    review so the card reads live; nothing writes to Workable."""
    from app.services.bulk_decision_service import decide_post_handover

    org, role = _seed_role(db, score_threshold=50, with_task=False)
    app = _add_app(db, org, role, role_fit=20.0)  # below threshold → would reject
    app.workable_stage = "Final Interview"
    app.pipeline_stage = "advanced"
    app.pipeline_stage_source = "sync"
    db.commit()

    result = decide_post_handover(db, app=app, role=role)
    db.commit()

    assert result == "reject"
    assert app.pipeline_stage == "review"  # pulled back to host the live card
    pending = _pending(db, role)
    assert len(pending) == 1
    assert pending[0].status == "pending"  # HITL — never auto-applied


def test_decide_post_handover_terminal_reject_still_surfaces(db):
    """A reject verdict on a TERMINAL hand-off (offer) is imminent enough to still
    surface: pull back to review + queue the live reject card (unchanged)."""
    from app.services.bulk_decision_service import decide_post_handover

    org, role = _seed_role(db, score_threshold=50, with_task=False)
    app = _add_app(db, org, role, role_fit=20.0)
    app.workable_stage = "Offer"
    app.pipeline_stage = "advanced"
    app.pipeline_stage_source = "sync"
    db.commit()

    result = decide_post_handover(db, app=app, role=role)
    db.commit()

    assert result == "reject"
    assert app.pipeline_stage == "review"  # pulled back to host the live card
    assert len(_pending(db, role)) == 1


def test_post_handover_ignores_two_related_roles_live_decisions(db):
    from app.services.bulk_decision_service import decide_post_handover

    org, owner = _seed_role(db, score_threshold=50, with_task=False)
    app = _add_app(db, org, owner, role_fit=20.0)
    app.workable_stage = "Final Interview"
    app.pipeline_stage = "advanced"
    app.pipeline_stage_source = "sync"
    related_roles = [
        Role(
            organization_id=int(org.id),
            name=f"Related {index}",
            source="sister",
            role_kind=ROLE_KIND_SISTER,
            ats_owner_role_id=int(owner.id),
        )
        for index in (1, 2)
    ]
    db.add_all(related_roles)
    db.flush()
    for related, status in zip(related_roles, ("pending", "processing")):
        db.add(
            AgentDecision(
                organization_id=int(org.id),
                role_id=int(related.id),
                application_id=int(app.id),
                decision_type="reject",
                recommendation="reject",
                status=status,
                reasoning="Related-role second opinion",
                model_version="related-role-deterministic",
                prompt_version="related-role-runtime-v1",
                idempotency_key=f"post-handover:{related.id}:{app.id}",
            )
        )
    db.commit()

    assert decide_post_handover(db, app=app, role=owner) == "reject"
    db.commit()

    assert len(_pending(db, owner)) == 1
    assert [
        db.query(AgentDecision)
        .filter(AgentDecision.role_id == int(role.id))
        .count()
        for role in related_roles
    ] == [1, 1]


def test_bulk_excludes_processing_decision(db):
    """(b) A 'processing' decision (approved, writeback in flight/stuck) blocks
    a duplicate — the cohort must exclude the candidate, not re-decide."""
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    app = _add_app(db, org, role, role_fit=20.0)
    db.add(AgentDecision(
        organization_id=org.id, role_id=role.id, application_id=app.id,
        decision_type="advance_to_interview", recommendation="advance_to_interview",
        status="processing", reasoning="in flight", model_version="x", prompt_version="x",
        idempotency_key=f"proc:{app.id}",
    ))
    db.commit()

    summary = decide_role_cohort(db, role=role)
    assert summary["candidates"] == 0
    assert len(_pending(db, role)) == 0  # _pending only counts pending, not processing


def test_null_pre_screen_scored_candidate_evaluated(db):
    """A scored candidate with no stored pre_screen score is still evaluated —
    pre_screen falls back to role_fit so the reject band applies, rather than
    the candidate being silently dropped."""
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    _add_app(db, org, role, role_fit=20.0, pre_screen=None)

    decide_role_cohort(db, role=role)

    decs = _pending(db, role)
    assert len(decs) == 1
    assert decs[0].decision_type == "reject"
