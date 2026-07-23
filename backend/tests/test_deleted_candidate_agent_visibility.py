"""Deleted-person and removed-membership boundaries for agent decision reads."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.mcp import handlers
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
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


def _decision(
    db,
    *,
    organization_id: int,
    role_id: int,
    application: CandidateApplication,
    label: str,
) -> AgentDecision:
    decision = AgentDecision(
        organization_id=organization_id,
        role_id=role_id,
        application_id=int(application.id),
        candidate_id=int(application.candidate_id),
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="pending",
        reasoning=f"{label} secret reasoning",
        evidence={"private_quote": f"{label} secret evidence"},
        confidence=0.9,
        model_version="offline-test",
        prompt_version="deleted-subject-boundary",
        input_fingerprint={"private_marker": f"{label} secret fingerprint"},
        cv_fingerprint=f"{label}-private-cv-fingerprint",
        resolution_note=f"{label} secret resolution note",
        idempotency_key=f"deleted-subject:{label}:{application.id}",
    )
    db.add(decision)
    db.flush()
    return decision


def _seed_visibility_matrix(client, db):
    headers, user_email = auth_headers(client)
    user = db.query(User).filter(User.email == user_email).one()
    organization_id = int(user.organization_id)
    ordinary = Role(
        organization_id=organization_id,
        name="Ordinary Visibility",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    owner = Role(
        organization_id=organization_id,
        name="Related ATS Owner",
        source="manual",
        monthly_usd_budget_cents=0,
    )
    db.add_all([ordinary, owner])
    db.flush()
    related = Role(
        organization_id=organization_id,
        name="Related Visibility",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    db.add(related)
    db.flush()

    _live_ordinary_candidate, live_ordinary_app = _application(
        db,
        organization_id=organization_id,
        role_id=int(ordinary.id),
        label="live-ordinary",
    )
    deleted_ordinary_candidate, deleted_ordinary_app = _application(
        db,
        organization_id=organization_id,
        role_id=int(ordinary.id),
        label="deleted-ordinary-person",
    )
    _removed_ordinary_candidate, removed_ordinary_app = _application(
        db,
        organization_id=organization_id,
        role_id=int(ordinary.id),
        label="removed-ordinary-membership",
    )

    related_rows = {}
    for label in (
        "live-related",
        "deleted-related-person",
        "removed-related-membership",
    ):
        candidate, source_application = _application(
            db,
            organization_id=organization_id,
            role_id=int(owner.id),
            label=label,
        )
        membership = SisterRoleEvaluation(
            organization_id=organization_id,
            role_id=int(related.id),
            candidate_id=int(candidate.id),
            source_application_id=int(source_application.id),
            ats_application_id=int(source_application.id),
            status="done",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            application_outcome_source="recruiter",
            membership_source="test",
            spec_fingerprint=f"spec-{label}",
        )
        db.add(membership)
        db.flush()
        related_rows[label] = (candidate, source_application, membership)

    now = datetime.now(timezone.utc)
    deleted_ordinary_candidate.deleted_at = now
    removed_ordinary_app.deleted_at = now
    related_rows["deleted-related-person"][0].deleted_at = now
    related_rows["removed-related-membership"][2].deleted_at = now

    decisions = {
        "live-ordinary": _decision(
            db,
            organization_id=organization_id,
            role_id=int(ordinary.id),
            application=live_ordinary_app,
            label="live-ordinary",
        ),
        "deleted-ordinary-person": _decision(
            db,
            organization_id=organization_id,
            role_id=int(ordinary.id),
            application=deleted_ordinary_app,
            label="deleted-ordinary-person",
        ),
        "removed-ordinary-membership": _decision(
            db,
            organization_id=organization_id,
            role_id=int(ordinary.id),
            application=removed_ordinary_app,
            label="removed-ordinary-membership",
        ),
    }
    for label, (_candidate, application, _membership) in related_rows.items():
        decisions[label] = _decision(
            db,
            organization_id=organization_id,
            role_id=int(related.id),
            application=application,
            label=label,
        )

    event_subjects = {
        "live-ordinary": (ordinary, live_ordinary_app, 20),
        "deleted-ordinary-person": (ordinary, deleted_ordinary_app, 3),
        "removed-ordinary-membership": (ordinary, removed_ordinary_app, 2),
        "live-related": (related, related_rows["live-related"][1], 10),
        "deleted-related-person": (
            related,
            related_rows["deleted-related-person"][1],
            4,
        ),
        "removed-related-membership": (
            related,
            related_rows["removed-related-membership"][1],
            1,
        ),
    }
    events = {}
    for label, (role, application, age_minutes) in event_subjects.items():
        event = CandidateApplicationEvent(
            organization_id=organization_id,
            role_id=int(role.id),
            application_id=int(application.id),
            event_type="pipeline_stage_changed",
            from_stage="applied",
            to_stage="review",
            actor_type="agent",
            reason=f"{label} secret event",
            idempotency_key=f"deleted-subject-event:{label}:{application.id}",
            created_at=now - timedelta(minutes=age_minutes),
        )
        db.add(event)
        db.flush()
        events[label] = event
    db.commit()
    return SimpleNamespace(
        headers=headers,
        user=user,
        ordinary=ordinary,
        related=related,
        decisions=decisions,
        events=events,
        live_ids={
            int(decisions["live-ordinary"].id),
            int(decisions["live-related"].id),
        },
        live_event_ids={
            int(events["live-ordinary"].id),
            int(events["live-related"].id),
        },
        hidden_labels={
            "deleted-ordinary-person",
            "removed-ordinary-membership",
            "deleted-related-person",
            "removed-related-membership",
        },
    )


def test_agentic_home_and_mcp_hide_deleted_or_unowned_decisions(client, db):
    seeded = _seed_visibility_matrix(client, db)

    queue = client.get(
        "/api/v1/agent-decisions",
        params={"status": "pending", "limit": 200},
        headers=seeded.headers,
    )
    assert queue.status_code == 200, queue.text
    assert {int(row["id"]) for row in queue.json()} == seeded.live_ids

    kpis = client.get("/api/v1/agent/kpis", headers=seeded.headers)
    assert kpis.status_code == 200, kpis.text
    assert kpis.json()["pending_decisions"] == 2
    assert kpis.json()["pending"] == 2
    assert kpis.json()["today"] == 2

    breakdown = client.get(
        "/api/v1/agent/roles/breakdown",
        headers=seeded.headers,
    )
    assert breakdown.status_code == 200, breakdown.text
    by_role = {int(row["role_id"]): row for row in breakdown.json()}
    assert by_role[int(seeded.ordinary.id)]["pending"] == 1
    assert by_role[int(seeded.related.id)]["pending"] == 1
    assert by_role[int(seeded.ordinary.id)]["decisions_total"] == 1
    assert by_role[int(seeded.related.id)]["decisions_total"] == 1

    panel = client.get("/api/v1/agent/panel", headers=seeded.headers)
    assert panel.status_code == 200, panel.text
    panel_body = panel.json()
    assert panel_body["kpis"]["pending_decisions"] == 2
    assert {int(row["id"]) for row in panel_body["recent_decisions"]} == (
        seeded.live_ids
    )
    panel_by_role = {
        int(row["role_id"]): row for row in panel_body["agents"]
    }
    assert panel_by_role[int(seeded.ordinary.id)]["pending"] == 1
    assert panel_by_role[int(seeded.related.id)]["pending"] == 1

    for role in (seeded.ordinary, seeded.related):
        status = client.get(
            f"/api/v1/roles/{int(role.id)}/agent/status",
            headers=seeded.headers,
        )
        assert status.status_code == 200, status.text
        assert status.json()["pending_breakdown"]["decisions"] == 1

    activity = client.get(
        "/api/v1/agent/activity?limit=50",
        headers=seeded.headers,
    )
    assert activity.status_code == 200, activity.text
    decision_entries = [
        row for row in activity.json()["entries"] if row["kind"] == "decision"
    ]
    assert {int(row["id"]) for row in decision_entries} == seeded.live_ids
    event_entries = [
        row for row in activity.json()["entries"] if row["kind"] == "event"
    ]
    assert {int(row["id"]) for row in event_entries} == seeded.live_event_ids
    event_payload = json.dumps(event_entries)
    for label in seeded.hidden_labels:
        assert label not in event_payload

    sidebar = client.get(
        "/api/v1/agent-chat/conversations",
        headers=seeded.headers,
    )
    assert sidebar.status_code == 200, sidebar.text
    sidebar_by_role = {
        int(row["role_id"]): row for row in sidebar.json()["agents"]
    }
    assert sidebar_by_role[int(seeded.ordinary.id)]["pending_decisions"] == 1
    assert sidebar_by_role[int(seeded.related.id)]["pending_decisions"] == 1

    timeline_rows = []
    for role, live_id in (
        (seeded.ordinary, seeded.decisions["live-ordinary"].id),
        (seeded.related, seeded.decisions["live-related"].id),
    ):
        timeline = client.get(
            f"/api/v1/agent-chat/conversations/{int(role.id)}/timeline",
            headers=seeded.headers,
        )
        assert timeline.status_code == 200, timeline.text
        decision_cards = [
            row
            for row in timeline.json()["timeline"]
            if row["kind"] == "decision"
        ]
        assert {int(row["decision_id"]) for row in decision_cards} == {
            int(live_id)
        }
        timeline_rows.extend(decision_cards)
    timeline_payload = json.dumps(timeline_rows)
    for label in seeded.hidden_labels:
        assert label not in timeline_payload

    org_status = client.get(
        "/api/v1/agent/org-status",
        headers=seeded.headers,
    )
    assert org_status.status_code == 200, org_status.text
    assert (
        org_status.json()["last_activity"]["candidate_name"]
        == "live-related Private Person"
    )
    assert "live-related secret event" in json.dumps(
        org_status.json()["last_activity"]
    )
    for label in seeded.hidden_labels:
        assert label not in json.dumps(org_status.json()["last_activity"])

    for role, label in (
        (seeded.ordinary, "live-ordinary"),
        (seeded.related, "live-related"),
    ):
        status = client.get(
            f"/api/v1/roles/{int(role.id)}/agent/status",
            headers=seeded.headers,
        )
        assert status.status_code == 200, status.text
        last_activity = status.json()["last_activity"]
        assert last_activity["candidate_name"] == f"{label} Private Person"
        assert last_activity["reason"] == f"{label} secret event"
        for hidden_label in seeded.hidden_labels:
            assert hidden_label not in json.dumps(last_activity)

    recent = handlers.list_recent_agent_decisions(db, seeded.user, limit=100)
    assert recent["total"] == 2
    assert {int(row["id"]) for row in recent["items"]} == seeded.live_ids
    serialized = json.dumps(recent)
    for label in seeded.hidden_labels:
        assert label not in serialized

    live = handlers.explain_agent_decision(
        db,
        seeded.user,
        decision_id=int(seeded.decisions["live-related"].id),
    )
    assert live["decision"]["reasoning"] == "live-related secret reasoning"
    for label in seeded.hidden_labels:
        with pytest.raises(ValueError, match="not found"):
            handlers.explain_agent_decision(
                db,
                seeded.user,
                decision_id=int(seeded.decisions[label].id),
            )


def test_audit_keeps_metadata_but_redacts_unavailable_candidate_content(client, db):
    seeded = _seed_visibility_matrix(client, db)

    response = client.get(
        "/api/v1/agent-decisions/export",
        params={"format": "json"},
        headers=seeded.headers,
    )
    assert response.status_code == 200, response.text
    rows = {int(row["id"]): row for row in response.json()["rows"]}
    assert set(rows) == {
        int(decision.id) for decision in seeded.decisions.values()
    }

    for label in seeded.hidden_labels:
        decision = seeded.decisions[label]
        row = rows[int(decision.id)]
        assert row["status"] == "pending"
        assert row["decision_type"] == "advance_to_interview"
        assert row["reasoning"] is None
        assert row["evidence"] is None
        assert row["resolution_note"] is None
        assert row["input_fingerprint"] is None
        assert row["cv_fingerprint"] is None

    live_row = rows[int(seeded.decisions["live-ordinary"].id)]
    assert live_row["reasoning"] == "live-ordinary secret reasoning"
    assert "live-ordinary secret evidence" in live_row["evidence"]
