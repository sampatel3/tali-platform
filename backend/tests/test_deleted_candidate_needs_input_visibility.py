"""Lifecycle matrix for candidate-scoped recruiter questions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.actions import ask_recruiter
from app.actions.types import Actor
from app.agent_chat import recruiter_inputs
from app.models.agent_needs_input import AgentNeedsInput
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from tests.conftest import auth_headers


def _application(db, *, organization_id: int, role_id: int, label: str):
    candidate = Candidate(
        organization_id=organization_id,
        email=f"{label}@private.example.test",
        full_name=f"{label} Private Person",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        candidate_id=int(candidate.id),
        role_id=role_id,
        source="manual",
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
    )
    db.add(application)
    db.flush()
    return candidate, application


def _question(
    db,
    *,
    organization_id: int,
    role_id: int,
    label: str,
    kind: str,
    subject_id: int | None,
) -> AgentNeedsInput:
    row = AgentNeedsInput(
        organization_id=organization_id,
        role_id=role_id,
        kind=kind,
        subject_id=subject_id,
        prompt=f"{label} private prompt",
        options=[{"value": label, "label": f"{label} Private Person"}],
        rationale=f"{label} private rationale",
    )
    db.add(row)
    db.flush()
    return row


def _seed_matrix(client, db):
    headers, user_email = auth_headers(
        client,
        organization_name="Needs-input lifecycle org",
    )
    user = db.query(User).filter(User.email == user_email).one()
    organization_id = int(user.organization_id)
    ordinary = Role(
        organization_id=organization_id,
        name="Ordinary questions",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    owner = Role(
        organization_id=organization_id,
        name="Related owner",
        source="manual",
        monthly_usd_budget_cents=0,
    )
    db.add_all([ordinary, owner])
    db.flush()
    related = Role(
        organization_id=organization_id,
        name="Related questions",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    db.add(related)
    db.flush()

    live_candidate, live_app = _application(
        db,
        organization_id=organization_id,
        role_id=int(ordinary.id),
        label="live-ordinary-question",
    )
    deleted_candidate, deleted_candidate_app = _application(
        db,
        organization_id=organization_id,
        role_id=int(ordinary.id),
        label="deleted-person-question",
    )
    removed_app_candidate, removed_app = _application(
        db,
        organization_id=organization_id,
        role_id=int(ordinary.id),
        label="removed-ordinary-question",
    )
    related_live_candidate, related_live_app = _application(
        db,
        organization_id=organization_id,
        role_id=int(owner.id),
        label="live-related-question",
    )
    related_removed_candidate, related_removed_app = _application(
        db,
        organization_id=organization_id,
        role_id=int(owner.id),
        label="removed-related-question",
    )
    db.add_all(
        [
            SisterRoleEvaluation(
                organization_id=organization_id,
                role_id=int(related.id),
                candidate_id=int(related_live_candidate.id),
                source_application_id=int(related_live_app.id),
                ats_application_id=int(related_live_app.id),
                status="done",
                membership_source="test",
                spec_fingerprint="live-related-question",
            ),
            SisterRoleEvaluation(
                organization_id=organization_id,
                role_id=int(related.id),
                candidate_id=int(related_removed_candidate.id),
                source_application_id=int(related_removed_app.id),
                ats_application_id=int(related_removed_app.id),
                status="done",
                membership_source="test",
                spec_fingerprint="removed-related-question",
            ),
        ]
    )
    db.flush()
    removed_related_membership = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(related.id),
            SisterRoleEvaluation.candidate_id == int(related_removed_candidate.id),
        )
        .one()
    )

    rows = {
        "live-ordinary-question": _question(
            db,
            organization_id=organization_id,
            role_id=int(ordinary.id),
            label="live-ordinary-question",
            kind="candidate_tie_break",
            subject_id=int(live_app.id),
        ),
        "deleted-person-question": _question(
            db,
            organization_id=organization_id,
            role_id=int(ordinary.id),
            label="deleted-person-question",
            kind="candidate_tie_break",
            subject_id=int(deleted_candidate_app.id),
        ),
        "removed-ordinary-question": _question(
            db,
            organization_id=organization_id,
            role_id=int(ordinary.id),
            label="removed-ordinary-question",
            kind="candidate_tie_break",
            subject_id=int(removed_app.id),
        ),
        "live-related-question": _question(
            db,
            organization_id=organization_id,
            role_id=int(related.id),
            label="live-related-question",
            kind="other",
            subject_id=int(related_live_app.id),
        ),
        "removed-related-question": _question(
            db,
            organization_id=organization_id,
            role_id=int(related.id),
            label="removed-related-question",
            kind="other",
            subject_id=int(related_removed_app.id),
        ),
        "general-question": _question(
            db,
            organization_id=organization_id,
            role_id=int(ordinary.id),
            label="general-question",
            kind="other",
            subject_id=None,
        ),
        "live-legacy-question": _question(
            db,
            organization_id=organization_id,
            role_id=int(ordinary.id),
            label="live-legacy-question",
            kind="send_assessment_approval",
            subject_id=int(live_app.id),
        ),
        "deleted-legacy-question": _question(
            db,
            organization_id=organization_id,
            role_id=int(ordinary.id),
            label="deleted-legacy-question",
            kind="resend_assessment_invite_approval",
            subject_id=int(deleted_candidate_app.id),
        ),
        "removed-legacy-question": _question(
            db,
            organization_id=organization_id,
            role_id=int(related.id),
            label="removed-legacy-question",
            kind="send_assessment_approval",
            subject_id=int(related_removed_app.id),
        ),
        "null-legacy-question": _question(
            db,
            organization_id=organization_id,
            role_id=int(ordinary.id),
            label="null-legacy-question",
            kind="resend_assessment_invite_approval",
            subject_id=None,
        ),
    }
    now = datetime.now(timezone.utc)
    deleted_candidate.deleted_at = now
    removed_app.deleted_at = now
    removed_related_membership.deleted_at = now
    db.commit()
    return SimpleNamespace(
        headers=headers,
        user=user,
        ordinary=ordinary,
        related=related,
        rows=rows,
        visible_ids={
            int(rows["live-ordinary-question"].id),
            int(rows["live-related-question"].id),
            int(rows["general-question"].id),
            int(rows["live-legacy-question"].id),
            int(rows["null-legacy-question"].id),
        },
        hidden_labels={
            "deleted-person-question",
            "removed-ordinary-question",
            "removed-related-question",
            "deleted-legacy-question",
            "removed-legacy-question",
        },
    )


def test_candidate_needs_input_lifecycle_matrix_across_active_surfaces(client, db):
    seeded = _seed_matrix(client, db)

    listed = client.get("/api/v1/agent-needs-input", headers=seeded.headers)
    assert listed.status_code == 200, listed.text
    assert {int(row["id"]) for row in listed.json()} == seeded.visible_ids

    activity = client.get(
        "/api/v1/agent/activity?limit=50",
        headers=seeded.headers,
    )
    assert activity.status_code == 200, activity.text
    question_entries = [
        row for row in activity.json()["entries"] if row["kind"] == "needs_input"
    ]
    assert {int(row["id"]) for row in question_entries} == seeded.visible_ids

    ordinary_chat = recruiter_inputs.list_open_recruiter_inputs(
        db,
        role=seeded.ordinary,
    )
    related_chat = recruiter_inputs.list_open_recruiter_inputs(
        db,
        role=seeded.related,
    )
    assert {
        int(row["needs_input_id"]) for row in ordinary_chat["requests"]
    } == {
        int(seeded.rows["live-ordinary-question"].id),
        int(seeded.rows["general-question"].id),
        int(seeded.rows["live-legacy-question"].id),
        int(seeded.rows["null-legacy-question"].id),
    }
    assert {
        int(row["needs_input_id"]) for row in related_chat["requests"]
    } == {int(seeded.rows["live-related-question"].id)}

    kpis = client.get("/api/v1/agent/kpis", headers=seeded.headers)
    assert kpis.status_code == 200, kpis.text
    assert kpis.json()["pending_questions"] == 5
    assert kpis.json()["pending"] == 5

    ordinary_status = client.get(
        f"/api/v1/roles/{int(seeded.ordinary.id)}/agent/status",
        headers=seeded.headers,
    )
    related_status = client.get(
        f"/api/v1/roles/{int(seeded.related.id)}/agent/status",
        headers=seeded.headers,
    )
    assert ordinary_status.status_code == 200, ordinary_status.text
    assert related_status.status_code == 200, related_status.text
    assert ordinary_status.json()["pending_breakdown"]["questions"] == 4
    assert related_status.json()["pending_breakdown"]["questions"] == 1

    serialized = json.dumps(
        {
            "listed": listed.json(),
            "activity": question_entries,
            "ordinary_chat": ordinary_chat,
            "related_chat": related_chat,
        }
    )
    for label in seeded.hidden_labels:
        assert label not in serialized


def test_candidate_needs_input_actions_fail_closed_but_general_rows_remain(client, db):
    seeded = _seed_matrix(client, db)
    actor = Actor.recruiter(seeded.user)

    with pytest.raises(HTTPException) as open_error:
        ask_recruiter.open(
            db,
            Actor.system(),
            organization_id=int(seeded.user.organization_id),
            role_id=int(seeded.ordinary.id),
            kind="candidate_tie_break",
            subject_id=int(
                seeded.rows["removed-ordinary-question"].subject_id
            ),
            prompt="must not revive removed candidate",
        )
    assert open_error.value.status_code == 404

    hidden_answer = seeded.rows["removed-ordinary-question"]
    with pytest.raises(HTTPException) as answer_error:
        ask_recruiter.answer(
            db,
            actor,
            organization_id=int(seeded.user.organization_id),
            needs_input_id=int(hidden_answer.id),
            response={"value": "private choice"},
            expected_version=int(seeded.ordinary.version or 1),
        )
    assert answer_error.value.status_code == 404

    hidden_dismiss = seeded.rows["removed-related-question"]
    with pytest.raises(HTTPException) as dismiss_error:
        ask_recruiter.dismiss(
            db,
            actor,
            organization_id=int(seeded.user.organization_id),
            needs_input_id=int(hidden_dismiss.id),
        )
    assert dismiss_error.value.status_code == 404

    policy_route = client.post(
        f"/api/v1/agent-needs-input/{int(seeded.rows['deleted-person-question'].id)}/dismiss",
        headers=seeded.headers,
    )
    assert policy_route.status_code == 404, policy_route.text

    general = ask_recruiter.dismiss(
        db,
        actor,
        organization_id=int(seeded.user.organization_id),
        needs_input_id=int(seeded.rows["general-question"].id),
    )
    assert general.dismissed_at is not None


def test_legacy_candidate_needs_input_rows_follow_lifecycle_without_reopening_kind(
    client,
    db,
):
    seeded = _seed_matrix(client, db)
    actor = Actor.recruiter(seeded.user)

    listed = client.get("/api/v1/agent-needs-input", headers=seeded.headers)
    assert listed.status_code == 200, listed.text
    listed_ids = {int(row["id"]) for row in listed.json()}
    assert int(seeded.rows["live-legacy-question"].id) in listed_ids
    assert int(seeded.rows["null-legacy-question"].id) in listed_ids
    assert int(seeded.rows["deleted-legacy-question"].id) not in listed_ids
    assert int(seeded.rows["removed-legacy-question"].id) not in listed_ids

    with pytest.raises(HTTPException) as answer_error:
        ask_recruiter.answer(
            db,
            actor,
            organization_id=int(seeded.user.organization_id),
            needs_input_id=int(seeded.rows["removed-legacy-question"].id),
            response={"value": "must not apply"},
            expected_version=int(seeded.related.version or 1),
        )
    assert answer_error.value.status_code == 404

    preserved_null = ask_recruiter.dismiss(
        db,
        actor,
        organization_id=int(seeded.user.organization_id),
        needs_input_id=int(seeded.rows["null-legacy-question"].id),
    )
    assert preserved_null.dismissed_at is not None

    with pytest.raises(HTTPException) as create_error:
        ask_recruiter.open(
            db,
            Actor.system(),
            organization_id=int(seeded.user.organization_id),
            role_id=int(seeded.ordinary.id),
            kind="send_assessment_approval",
            subject_id=int(seeded.rows["live-legacy-question"].subject_id),
            prompt="legacy creation must stay prohibited",
        )
    assert create_error.value.status_code == 422
