"""Scorecard lifecycle on interview_feedback — draft/submit, panel summary,
per-interviewer upsert, own-card-only editing, and the calibration/summary
exclusions (drafts + no_decision).

The scorecard endpoints drive the SAME interview_feedback row as a per-
interviewer draft/submit card. These tests cover only the added lifecycle; the
legacy CRUD lives in test_interview_feedback_routes.py.
"""

from datetime import datetime, timezone

from app.models.interview_feedback import InterviewFeedback
from app.models.user import User
from tests.conftest import auth_headers


def _create_application(client, headers, candidate_email="sc@example.com"):
    role = client.post(
        "/api/v1/roles",
        json={"name": "Backend Engineer", "description": "Hiring"},
        headers=headers,
    )
    assert role.status_code == 201, role.text
    role = role.json()
    app_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": candidate_email, "candidate_name": "SC Candidate"},
        headers=headers,
    )
    assert app_resp.status_code == 201, app_resp.text
    return app_resp.json()


def _member(db, org_id, email) -> User:
    u = User(
        email=email,
        hashed_password="x",
        full_name="Interviewer",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        organization_id=org_id,
    )
    db.add(u)
    db.commit()
    return u


# --------------------------------------------------------------------------
# Per-interviewer upsert — one card per (application, interviewer), edited in
# place on re-post.
# --------------------------------------------------------------------------
def test_scorecard_upsert_edits_in_place(client, db):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="upsert@example.com")
    aid = app["id"]

    r = client.post(
        f"/api/v1/applications/{aid}/scorecards",
        json={
            "overall_recommendation": "yes",
            "overall_rating": 3,
            "competencies": [{"name": "Coding", "rating": 3, "comment": "solid"}],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["submitted_at"] is None  # draft
    assert r.json()["interviewer_user_id"] is not None

    # Re-post as the same caller — edits in place, no new row.
    r2 = client.post(
        f"/api/v1/applications/{aid}/scorecards",
        json={"overall_recommendation": "strong_yes", "overall_rating": 4},
        headers=headers,
    )
    assert r2.status_code == 201 and r2.json()["id"] == sid
    assert r2.json()["overall_recommendation"] == "strong_yes"
    assert db.query(InterviewFeedback).filter_by(application_id=aid).count() == 1


def test_scorecard_validation(client):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="val@example.com")
    aid = app["id"]
    assert (
        client.post(
            f"/api/v1/applications/{aid}/scorecards",
            json={"overall_recommendation": "maybe"},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/applications/{aid}/scorecards",
            json={"overall_rating": 9},
            headers=headers,
        ).status_code
        == 422
    )


# --------------------------------------------------------------------------
# Draft/submit lifecycle + panel summary math.
# --------------------------------------------------------------------------
def test_draft_excluded_then_submitted_counts_in_summary(client, db):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="submit@example.com")
    aid = app["id"]

    sid = client.post(
        f"/api/v1/applications/{aid}/scorecards",
        json={"overall_rating": 3},
        headers=headers,
    ).json()["id"]

    # Draft: excluded from the summary.
    summary = client.get(
        f"/api/v1/applications/{aid}/scorecards/summary", headers=headers
    ).json()
    assert summary["submitted_count"] == 0
    assert summary["mean_lean"] is None
    assert summary["mean_overall_rating"] is None

    # No recommendation yet (seeded no_decision) → can't submit.
    assert (
        client.post(
            f"/api/v1/applications/{aid}/scorecards/{sid}/submit", headers=headers
        ).status_code
        == 422
    )

    # Give it a real recommendation, then submit.
    client.post(
        f"/api/v1/applications/{aid}/scorecards",
        json={"overall_recommendation": "yes"},
        headers=headers,
    )
    submitted = client.post(
        f"/api/v1/applications/{aid}/scorecards/{sid}/submit", headers=headers
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["submitted_at"] is not None

    summary = client.get(
        f"/api/v1/applications/{aid}/scorecards/summary", headers=headers
    ).json()
    assert summary["submitted_count"] == 1
    assert summary["recommendations"]["yes"] == 1
    assert summary["mean_lean"] == 1.0
    assert summary["mean_overall_rating"] == 3.0


def test_panel_summary_mean_lean_math(client, db):
    """Two submitted cards from different interviewers: strong_yes (2) + no (-1)
    → mean lean 0.5. A no_decision card is tallied but excluded from the mean."""
    headers_a, email_a = auth_headers(client)
    app = _create_application(client, headers_a, candidate_email="panel@example.com")
    aid = app["id"]
    org_id = db.query(User).filter(User.email == email_a).first().organization_id

    # Card A (caller): strong_yes.
    a = client.post(
        f"/api/v1/applications/{aid}/scorecards",
        json={"overall_recommendation": "strong_yes", "overall_rating": 4},
        headers=headers_a,
    ).json()
    client.post(f"/api/v1/applications/{aid}/scorecards/{a['id']}/submit", headers=headers_a)

    # Two more submitted cards owned by other interviewers, written directly.
    now = datetime.now(timezone.utc)
    other1 = _member(db, org_id, "panel-b@sc.test")
    other2 = _member(db, org_id, "panel-c@sc.test")
    db.add_all(
        [
            InterviewFeedback(
                organization_id=org_id,
                application_id=aid,
                role_id=app["role_id"],
                interviewer_user_id=other1.id,
                interview_round="interview",
                overall_recommendation="no",
                overall_rating=2,
                submitted_at=now,
            ),
            InterviewFeedback(
                organization_id=org_id,
                application_id=aid,
                role_id=app["role_id"],
                interviewer_user_id=other2.id,
                interview_round="interview",
                overall_recommendation="no_decision",
                submitted_at=now,
            ),
        ]
    )
    db.commit()

    summary = client.get(
        f"/api/v1/applications/{aid}/scorecards/summary", headers=headers_a
    ).json()
    assert summary["submitted_count"] == 3
    assert summary["recommendations"]["strong_yes"] == 1
    assert summary["recommendations"]["no"] == 1
    assert summary["recommendations"]["no_decision"] == 1
    # Lean over {2, -1}; no_decision abstains → mean 0.5.
    assert summary["mean_lean"] == 0.5
    # Ratings over {4, 2} → mean 3.0.
    assert summary["mean_overall_rating"] == 3.0


# --------------------------------------------------------------------------
# Own-card-only editing.
# --------------------------------------------------------------------------
def test_cannot_submit_another_users_scorecard(client, db):
    headers, email = auth_headers(client)
    app = _create_application(client, headers, candidate_email="own@example.com")
    aid = app["id"]
    org_id = db.query(User).filter(User.email == email).first().organization_id

    other = _member(db, org_id, "owner@sc.test")
    card = InterviewFeedback(
        organization_id=org_id,
        application_id=aid,
        role_id=app["role_id"],
        interviewer_user_id=other.id,
        interview_round="interview",
        overall_recommendation="yes",
    )
    db.add(card)
    db.commit()

    # The caller isn't this card's interviewer → 404 (not their card).
    r = client.post(
        f"/api/v1/applications/{aid}/scorecards/{card.id}/submit", headers=headers
    )
    assert r.status_code == 404


def test_upsert_does_not_touch_another_users_card(client, db):
    """When another interviewer already owns a card, the caller's upsert creates
    a SEPARATE row rather than editing theirs (upsert is per-interviewer)."""
    headers_a, email_a = auth_headers(client)
    app = _create_application(client, headers_a, candidate_email="two@example.com")
    aid = app["id"]
    org_id = db.query(User).filter(User.email == email_a).first().organization_id

    # A pre-existing card owned by a different interviewer.
    other = _member(db, org_id, "two-other@sc.test")
    other_card = InterviewFeedback(
        organization_id=org_id,
        application_id=aid,
        role_id=app["role_id"],
        interviewer_user_id=other.id,
        interview_round="interview",
        overall_recommendation="no",
    )
    db.add(other_card)
    db.commit()
    other_id = other_card.id

    # The caller upserts their own card → a new row, other's untouched.
    mine = client.post(
        f"/api/v1/applications/{aid}/scorecards",
        json={"overall_recommendation": "yes"},
        headers=headers_a,
    ).json()
    assert mine["id"] != other_id
    assert db.query(InterviewFeedback).filter_by(application_id=aid).count() == 2
    db.expire_all()
    assert db.get(InterviewFeedback, other_id).overall_recommendation == "no"


def test_scorecard_summary_org_scoped(client, db):
    headers_a, _ = auth_headers(client)
    headers_b, _ = auth_headers(client)
    app = _create_application(client, headers_a, candidate_email="scoped@example.com")
    aid = app["id"]
    # A foreign org gets a 404 on the summary of an app it doesn't own.
    r = client.get(f"/api/v1/applications/{aid}/scorecards/summary", headers=headers_b)
    assert r.status_code == 404


# --------------------------------------------------------------------------
# Calibration: drafts + no_decision excluded, output unchanged.
# --------------------------------------------------------------------------
def test_calibration_excludes_no_decision_row():
    """A no_decision row must not change any calibration output vs the same
    rows without it — it carries no lean."""
    from scripts.score_outcome_calibration import FeedbackRow, compute_calibration

    base = [
        FeedbackRow(1, "Backend", 90.0, "strong_yes", "hired", "advanced"),
        FeedbackRow(1, "Backend", 80.0, "yes", "open", "review"),
        FeedbackRow(1, "Backend", 30.0, "no", "rejected", "review"),
        FeedbackRow(1, "Backend", 20.0, "strong_no", "rejected", "review"),
    ]
    with_abstain = base + [
        FeedbackRow(1, "Backend", 55.0, "no_decision", "open", "review"),
    ]

    a = compute_calibration(base)
    b = compute_calibration(with_abstain)

    assert b.n == a.n  # abstention doesn't count
    assert b.n_scored == a.n_scored
    assert b.band_means() == a.band_means()
    assert b.corr_recommendation == a.corr_recommendation
    assert b.corr_hired == a.corr_hired
    assert b.contradicted_advances == a.contradicted_advances
