"""Coverage for the Hub's teach loop + new aggregate endpoints.

Covers:
- ``teach_decision.run`` happy path: feedback row written, decision flipped
  to ``reverted_for_feedback``, ``human_disposition='taught'``, and the
  decision is back-pointed at the new feedback row.
- Org-scope feedback marks the row ``cosign_required=True``.
- The teach action 409s on already-resolved decisions.
- The teach action accepts re-teaching a decision currently in
  ``reverted_for_feedback`` status (replacing the prior feedback pointer).
- ``snooze_decision`` route hides a pending row from
  ``GET /agent-decisions?status=pending``.
- Status enum gained ``reverted_for_feedback``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException

from app.actions import teach_decision
from app.actions.types import Actor
from app.models.agent_decision import (
    AGENT_DECISION_STATUSES,
    AgentDecision,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.decision_feedback import DecisionFeedback
from app.models.organization import Organization
from app.models.role import Role


def _seed(db):
    org = Organization(name="Hub Org", slug=f"hub-org-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Senior Backend Engineer",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    db.add(role)
    db.flush()
    cand = Candidate(organization_id=org.id, email="m@x.test", full_name="Maya Chen")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="pending",
        reasoning="Cleared all six dimensions.",
        confidence=0.94,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"test:{app.id}:advance",
    )
    db.add(decision)
    db.flush()
    return SimpleNamespace(org=org, role=role, application=app, decision=decision)


def _user(db, org: Organization, *, idx: int = 1):
    """Make a minimal user row; the only field teach_decision.run uses is .id."""
    from app.models.user import User
    user = User(
        email=f"hub-{idx}-{id(db)}@x.test",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        full_name="Hub User",
        organization_id=org.id,
    )
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------


def test_status_enum_includes_reverted_for_feedback():
    assert "reverted_for_feedback" in AGENT_DECISION_STATUSES


# ---------------------------------------------------------------------------
# teach_decision.run
# ---------------------------------------------------------------------------


def test_teach_role_scope_creates_feedback_and_flips_decision(db):
    s = _seed(db)
    user = _user(db, s.org)

    feedback, decision = teach_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        failure_mode="rubric_mismatch",
        correction_text="Score 88 should be ~78. Over-credited iteration axis.",
        scope="role",
    )
    db.commit()

    assert feedback.id is not None
    assert feedback.scope == "role"
    assert feedback.role_id == int(s.role.id)
    assert feedback.cosign_required is False
    assert feedback.reviewer_id == int(user.id)
    assert feedback.organization_id == int(s.org.id)

    db.refresh(decision)
    assert decision.status == "reverted_for_feedback"
    assert decision.human_disposition == "taught"
    assert int(decision.feedback_id) == int(feedback.id)
    assert decision.resolution_note  # the correction text was stored


def test_teach_org_scope_marks_cosign_required_and_clears_role(db):
    s = _seed(db)
    user = _user(db, s.org)

    feedback, _ = teach_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        failure_mode="policy_violation",
        correction_text="Workspace-wide rubric needs to deprioritise iteration.",
        scope="org",
    )
    db.commit()

    assert feedback.scope == "org"
    assert feedback.role_id is None
    assert feedback.cosign_required is True


def test_teach_decision_scope_logs_only(db):
    s = _seed(db)
    user = _user(db, s.org)
    feedback, _ = teach_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        failure_mode="other",
        correction_text="Just logging this one.",
        scope="decision",
    )
    db.commit()
    assert feedback.scope == "decision"
    assert feedback.role_id == int(s.role.id)  # falls back to the decision's role
    assert feedback.cosign_required is False


def test_teach_rejects_already_approved_decision(db):
    s = _seed(db)
    user = _user(db, s.org)
    s.decision.status = "approved"
    s.decision.resolved_at = datetime.now(timezone.utc)
    db.commit()

    try:
        teach_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=s.org.id,
            decision_id=int(s.decision.id),
            failure_mode="rubric_mismatch",
            correction_text="Too late, but still.",
            scope="role",
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        return
    raise AssertionError("expected 409 HTTPException")


def test_teach_allows_reteaching_already_reverted_decision(db):
    s = _seed(db)
    user = _user(db, s.org)
    teach_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        failure_mode="rubric_mismatch",
        correction_text="First take.",
        scope="role",
    )
    db.commit()

    feedback2, decision = teach_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        failure_mode="missing_signal",
        correction_text="Second take — also missed the test coverage signal.",
        scope="role",
    )
    db.commit()
    assert int(decision.feedback_id) == int(feedback2.id)
    assert decision.status == "reverted_for_feedback"


def test_teach_validates_failure_mode_and_scope(db):
    s = _seed(db)
    user = _user(db, s.org)

    try:
        teach_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=s.org.id,
            decision_id=int(s.decision.id),
            failure_mode="not_a_real_mode",
            correction_text="x",
            scope="role",
        )
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("expected 422")

    try:
        teach_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=s.org.id,
            decision_id=int(s.decision.id),
            failure_mode="other",
            correction_text="x",
            scope="bogus",
        )
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("expected 422")


# ---------------------------------------------------------------------------
# Snooze + pending filtering
# ---------------------------------------------------------------------------


def test_snoozed_decision_hidden_from_pending_filter(db):
    """The list endpoint filters status='pending' AND snoozed_until <= now."""
    from sqlalchemy import or_

    s = _seed(db)
    s.decision.snoozed_until = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()

    now = datetime.now(timezone.utc)
    rows = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == s.org.id,
            AgentDecision.status == "pending",
            or_(
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= now,
            ),
        )
        .all()
    )
    assert rows == []


def test_expired_snooze_returns_to_pending(db):
    from sqlalchemy import or_

    s = _seed(db)
    s.decision.snoozed_until = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()

    now = datetime.now(timezone.utc)
    rows = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == s.org.id,
            AgentDecision.status == "pending",
            or_(
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= now,
            ),
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].id == s.decision.id


# ---------------------------------------------------------------------------
# KPI strip: pending decisions grouped by decision_type (the Hub
# "Pending by type" breakdown). Counts the snooze-aware pending slice only
# and must reconcile with pending_decisions.
# ---------------------------------------------------------------------------


def test_compute_kpis_pending_by_type_groups_and_reconciles(db):
    from app.domains.agentic.hub_routes import _compute_kpis

    s = _seed(db)  # one pending advance_to_interview
    seq = iter(range(1, 1000))

    def _add(decision_type, *, status="pending", snoozed_minutes=None):
        d = AgentDecision(
            organization_id=s.org.id,
            role_id=s.role.id,
            application_id=s.application.id,
            decision_type=decision_type,
            recommendation=decision_type,
            status=status,
            reasoning="x",
            confidence=0.9,
            model_version="m",
            prompt_version="p",
            idempotency_key=f"t:{s.application.id}:{decision_type}:{status}:{next(seq)}",
        )
        if snoozed_minutes is not None:
            d.snoozed_until = datetime.now(timezone.utc) + timedelta(minutes=snoozed_minutes)
        db.add(d)
        db.flush()
        return d

    _add("send_assessment")
    _add("send_assessment")
    _add("skip_assessment_reject")
    _add("reject", status="approved")  # resolved → excluded
    _add("advance_to_interview", snoozed_minutes=30)  # still snoozed → excluded
    db.commit()

    kpis = _compute_kpis(db, organization_id=s.org.id, range_days=7)

    assert kpis.pending_by_type == {
        "advance_to_interview": 1,
        "send_assessment": 2,
        "skip_assessment_reject": 1,
    }
    assert sum(kpis.pending_by_type.values()) == kpis.pending_decisions


# ---------------------------------------------------------------------------
# approve / override now record human_disposition
# ---------------------------------------------------------------------------


def test_approve_sets_human_disposition(db):
    from app.actions import approve_decision

    s = _seed(db)
    user = _user(db, s.org)

    # Stub out the side-effect dispatch (advance_stage / reject_application)
    # so we don't need a real pipeline_service in these unit tests.
    from app.actions import advance_stage as advance_stage_action

    original = advance_stage_action.run
    advance_stage_action.run = lambda *a, **kw: None
    try:
        approve_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=s.org.id,
            decision_id=int(s.decision.id),
            note=None,
        )
    finally:
        advance_stage_action.run = original

    db.commit()
    db.refresh(s.decision)
    assert s.decision.status == "approved"
    assert s.decision.human_disposition == "approved"


def test_override_sets_human_disposition(db):
    from app.actions import override_decision

    s = _seed(db)
    user = _user(db, s.org)

    override_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        override_action="hold",
        note=None,
    )
    db.commit()
    db.refresh(s.decision)
    assert s.decision.status == "overridden"
    assert s.decision.human_disposition == "overridden"


# ---------------------------------------------------------------------------
# HTTP route tests — cosign / revert / list-feedback / snooze
#
# These exercise the FastAPI surface end-to-end (auth → route → action →
# DB). They use the standard ``client`` + ``auth_headers`` fixtures from
# tests/conftest.py and seed minimal state via the test session because the
# /agent/feedback POST requires a real pending agent_decision.
# ---------------------------------------------------------------------------


def _seed_via_session(*, org_name: str = "Hub HTTP Org") -> dict:
    """Seed an org/role/application/decision via the test session and return
    the IDs the HTTP tests need. The session is committed; the schema lives
    long enough for the client request to see the rows."""
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        org = Organization(name=org_name, slug=f"hub-http-{id(sess)}")
        sess.add(org)
        sess.flush()
        role = Role(
            organization_id=org.id,
            name="Sr. Backend",
            source="manual",
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=0,
        )
        sess.add(role)
        sess.flush()
        cand = Candidate(organization_id=org.id, email="h@x.test", full_name="H")
        sess.add(cand)
        sess.flush()
        app = CandidateApplication(
            organization_id=org.id,
            candidate_id=cand.id,
            role_id=role.id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="manual",
        )
        sess.add(app)
        sess.flush()
        decision = AgentDecision(
            organization_id=org.id,
            role_id=role.id,
            application_id=app.id,
            decision_type="advance_to_interview",
            recommendation="advance_to_interview",
            status="pending",
            reasoning="r",
            confidence=0.9,
            model_version="m",
            prompt_version="p",
            idempotency_key=f"http:{app.id}:advance",
        )
        sess.add(decision)
        sess.commit()
        return {
            "org_id": int(org.id),
            "role_id": int(role.id),
            "application_id": int(app.id),
            "decision_id": int(decision.id),
        }
    finally:
        sess.close()


def _attach_user_to_org(email: str, organization_id: int) -> None:
    """Reassign a fresh test user to an existing org so its API requests
    are scoped against that org's seeded fixtures."""
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        from app.models.user import User as _U

        user = sess.query(_U).filter(_U.email == email).first()
        assert user is not None, f"user {email} not found"
        user.organization_id = organization_id
        sess.commit()
    finally:
        sess.close()


def test_post_feedback_route_creates_row_and_flips_decision(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_via_session()
    _attach_user_to_org(email, seeded["org_id"])

    resp = client.post(
        "/api/v1/agent/feedback",
        headers=headers,
        json={
            "decision_id": seeded["decision_id"],
            "failure_mode": "rubric_mismatch",
            "correction_text": "Score should be lower.",
            "scope": "role",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision_status"] == "reverted_for_feedback"
    assert body["feedback"]["scope"] == "role"
    assert body["feedback"]["cosign_required"] is False
    # Spec change — payload no longer includes a ``queued_retune`` block
    # (we don't promise automated retunes).
    assert "queued_retune" not in body


def test_post_feedback_org_scope_marks_cosign_required(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_via_session(org_name="Hub HTTP Org Org")
    _attach_user_to_org(email, seeded["org_id"])

    resp = client.post(
        "/api/v1/agent/feedback",
        headers=headers,
        json={
            "decision_id": seeded["decision_id"],
            "failure_mode": "policy_violation",
            "correction_text": "Workspace-wide policy correction.",
            "scope": "org",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["feedback"]["scope"] == "org"
    assert body["feedback"]["cosign_required"] is True
    assert body["feedback"]["cosigned_at"] is None


def test_cosign_route_blocks_self_cosign_and_succeeds_for_second_admin(client):
    """Reviewer A submits org-scope feedback, A can't cosign their own,
    Reviewer B (different user, same org) can."""
    from tests.conftest import auth_headers

    headers_a, email_a = auth_headers(client)
    seeded = _seed_via_session(org_name="Cosign Test Org")
    _attach_user_to_org(email_a, seeded["org_id"])

    create = client.post(
        "/api/v1/agent/feedback",
        headers=headers_a,
        json={
            "decision_id": seeded["decision_id"],
            "failure_mode": "other",
            "correction_text": "Org-wide correction.",
            "scope": "org",
        },
    )
    assert create.status_code == 200, create.text
    feedback_id = create.json()["feedback"]["id"]

    # Reviewer A cannot co-sign their own submission.
    self_cosign = client.post(
        f"/api/v1/agent/feedback/{feedback_id}/cosign",
        headers=headers_a,
    )
    assert self_cosign.status_code == 403

    # Reviewer B (different user) — sign in, attach to the same org, then
    # they can co-sign.
    headers_b, email_b = auth_headers(client)
    _attach_user_to_org(email_b, seeded["org_id"])
    resp = client.post(
        f"/api/v1/agent/feedback/{feedback_id}/cosign",
        headers=headers_b,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["feedback"]["cosigned_by_user_id"] is not None
    assert body["feedback"]["cosigned_at"] is not None

    # Already-cosigned → 409 on a second cosign attempt.
    repeat = client.post(
        f"/api/v1/agent/feedback/{feedback_id}/cosign",
        headers=headers_b,
    )
    assert repeat.status_code == 409


def test_cosign_route_404s_for_other_org(client):
    """A user in a different org can't see (or co-sign) feedback that
    doesn't belong to their workspace."""
    from tests.conftest import auth_headers

    headers_a, email_a = auth_headers(client)
    seeded = _seed_via_session(org_name="Org A")
    _attach_user_to_org(email_a, seeded["org_id"])

    create = client.post(
        "/api/v1/agent/feedback",
        headers=headers_a,
        json={
            "decision_id": seeded["decision_id"],
            "failure_mode": "other",
            "correction_text": "Org A correction.",
            "scope": "org",
        },
    )
    assert create.status_code == 200, create.text
    feedback_id = create.json()["feedback"]["id"]

    # Reviewer in a different org (gets a fresh org by default).
    headers_other, _ = auth_headers(client, organization_name="Org B")
    resp = client.post(
        f"/api/v1/agent/feedback/{feedback_id}/cosign",
        headers=headers_other,
    )
    assert resp.status_code == 404


def test_revert_route_restores_decision_to_pending(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_via_session(org_name="Revert Org")
    _attach_user_to_org(email, seeded["org_id"])

    create = client.post(
        "/api/v1/agent/feedback",
        headers=headers,
        json={
            "decision_id": seeded["decision_id"],
            "failure_mode": "missing_signal",
            "correction_text": "Wait, never mind.",
            "scope": "role",
        },
    )
    assert create.status_code == 200, create.text
    feedback_id = create.json()["feedback"]["id"]

    revert = client.post(
        f"/api/v1/agent/feedback/{feedback_id}/revert",
        headers=headers,
    )
    assert revert.status_code == 200, revert.text
    body = revert.json()
    assert body["decision_status"] == "pending"
    assert body["feedback_id"] == feedback_id


def test_revert_route_rejects_outside_grace_window(client):
    """Manually backdate the feedback row past the 1h window, then revert
    should 409."""
    from datetime import datetime, timedelta, timezone

    from tests.conftest import TestingSessionLocal, auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_via_session(org_name="Stale Revert Org")
    _attach_user_to_org(email, seeded["org_id"])

    create = client.post(
        "/api/v1/agent/feedback",
        headers=headers,
        json={
            "decision_id": seeded["decision_id"],
            "failure_mode": "other",
            "correction_text": "x",
            "scope": "role",
        },
    )
    feedback_id = create.json()["feedback"]["id"]

    sess = TestingSessionLocal()
    try:
        fb = sess.query(DecisionFeedback).filter(DecisionFeedback.id == feedback_id).first()
        fb.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
        sess.commit()
    finally:
        sess.close()

    revert = client.post(
        f"/api/v1/agent/feedback/{feedback_id}/revert",
        headers=headers,
    )
    assert revert.status_code == 409
    assert "grace window" in revert.json()["detail"]


def test_snooze_route_hides_decision_from_pending(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_via_session(org_name="Snooze Org")
    _attach_user_to_org(email, seeded["org_id"])

    snooze = client.post(
        f"/api/v1/agent-decisions/{seeded['decision_id']}/snooze",
        headers=headers,
    )
    assert snooze.status_code == 200, snooze.text
    assert snooze.json()["decision_id"] == seeded["decision_id"]

    listing = client.get("/api/v1/agent-decisions?status=pending", headers=headers)
    assert listing.status_code == 200
    rows = listing.json()
    ids = [row["id"] for row in rows]
    assert seeded["decision_id"] not in ids, "snoozed row should be hidden from pending"


def test_list_feedback_route_returns_seeded_rows(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_via_session(org_name="List FB Org")
    _attach_user_to_org(email, seeded["org_id"])

    client.post(
        "/api/v1/agent/feedback",
        headers=headers,
        json={
            "decision_id": seeded["decision_id"],
            "failure_mode": "rubric_mismatch",
            "correction_text": "x",
            "scope": "role",
        },
    )

    resp = client.get("/api/v1/agent/feedback", headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["decision_id"] == seeded["decision_id"]
    assert rows[0]["scope"] == "role"


def _seed_two_scored_decisions(*, org_name: str) -> dict:
    """Seed one org with two scored applications: an advance decision and a
    pre-screen reject. BOTH applications carry a cached Tali score so the test
    can prove the pre-screen card suppresses it regardless of cache state."""
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        org = Organization(name=org_name, slug=f"twoscore-{id(sess)}")
        sess.add(org)
        sess.flush()
        role = Role(
            organization_id=org.id,
            name="Sr. Backend",
            source="manual",
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=0,
        )
        sess.add(role)
        sess.flush()
        out = {"org_id": int(org.id)}
        for dtype, score, key in (
            ("advance_to_interview", 88.0, "adv"),
            ("skip_assessment_reject", 72.0, "psr"),
        ):
            cand = Candidate(organization_id=org.id, email=f"{key}@x.test", full_name=key)
            sess.add(cand)
            sess.flush()
            app = CandidateApplication(
                organization_id=org.id,
                candidate_id=cand.id,
                role_id=role.id,
                status="applied",
                pipeline_stage="review",
                pipeline_stage_source="recruiter",
                application_outcome="open",
                source="manual",
                taali_score_cache_100=score,
            )
            sess.add(app)
            sess.flush()
            dec = AgentDecision(
                organization_id=org.id,
                role_id=role.id,
                application_id=app.id,
                decision_type=dtype,
                recommendation=dtype,
                status="pending",
                reasoning="r",
                model_version="m",
                prompt_version="p",
                idempotency_key=f"twoscore:{app.id}:{dtype}",
            )
            sess.add(dec)
            sess.flush()
            out[f"{key}_decision_id"] = int(dec.id)
        sess.commit()
        return out
    finally:
        sess.close()


def test_taali_score_shown_on_advance_but_never_on_pre_screen_reject(client):
    """Hub surfaces taali_score on a scored advance card, but NEVER on a
    pre-screen reject (skip_assessment_reject) — even when the underlying
    application carries a cached score."""
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_two_scored_decisions(org_name="Two Score Org")
    _attach_user_to_org(email, seeded["org_id"])

    listing = client.get("/api/v1/agent-decisions?status=pending", headers=headers)
    assert listing.status_code == 200, listing.text
    by_id = {row["id"]: row for row in listing.json()}

    adv = by_id[seeded["adv_decision_id"]]
    assert adv["taali_score"] == 88.0, "scored advance card should expose the Tali score"

    psr = by_id[seeded["psr_decision_id"]]
    assert psr["taali_score"] is None, "pre-screen reject must never expose a score"


def _seed_advance_with_evidence_only_score(*, org_name: str) -> dict:
    """Seed an advance decision whose application has NO cached score (the
    'pending' cache case), with the Tali score present only in the decision's
    evidence — mirrors the Rajesh Yadla report where the card showed no score."""
    from tests.conftest import TestingSessionLocal

    sess = TestingSessionLocal()
    try:
        org = Organization(name=org_name, slug=f"evonly-{id(sess)}")
        sess.add(org)
        sess.flush()
        role = Role(
            organization_id=org.id,
            name="Sr. Backend",
            source="manual",
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=0,
        )
        sess.add(role)
        sess.flush()
        cand = Candidate(organization_id=org.id, email="evonly@x.test", full_name="Ev")
        sess.add(cand)
        sess.flush()
        app = CandidateApplication(
            organization_id=org.id,
            candidate_id=cand.id,
            role_id=role.id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="manual",
            # No cached score — the cause of the missing-score bug.
            taali_score_cache_100=None,
            role_fit_score_cache_100=None,
        )
        sess.add(app)
        sess.flush()
        dec = AgentDecision(
            organization_id=org.id,
            role_id=role.id,
            application_id=app.id,
            decision_type="advance_to_interview",
            recommendation="advance_to_interview",
            status="pending",
            reasoning="Top scorer (73.7).",
            evidence={"taali_score": 73.7, "cv_match_score": 73.74},
            model_version="m",
            prompt_version="p",
            idempotency_key=f"evonly:{app.id}:advance",
        )
        sess.add(dec)
        sess.commit()
        return {"org_id": int(org.id), "decision_id": int(dec.id)}
    finally:
        sess.close()


def test_taali_score_falls_back_to_decision_evidence_when_cache_empty(client):
    """When the application's score cache is empty, the card still shows the
    Tali score the agent stamped on the decision's evidence."""
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_advance_with_evidence_only_score(org_name="Evidence Only Org")
    _attach_user_to_org(email, seeded["org_id"])

    listing = client.get("/api/v1/agent-decisions?status=pending", headers=headers)
    assert listing.status_code == 200, listing.text
    row = next(r for r in listing.json() if r["id"] == seeded["decision_id"])
    assert row["taali_score"] == 73.7, "should fall back to evidence taali_score"
