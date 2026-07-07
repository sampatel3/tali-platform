"""P3: interview scorecards — upsert, submit, panel summary, ownership."""
from app.models import Candidate, CandidateApplication, Organization, Role
from app.models.interview_scorecard import InterviewScorecard
from app.models.user import ROLE_RECRUITER, User
from tests.conftest import auth_headers


def _org_id(db, email: str) -> int:
    return db.query(User).filter(User.email == email).first().organization_id


def _seed_app(db, org_id: int) -> int:
    role = Role(organization_id=org_id, name="Eng", source="manual")
    db.add(role)
    db.flush()
    cand = Candidate(organization_id=org_id, email="s@sc.test", full_name="S")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="advanced", application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.commit()
    return app.id


def _member(db, org_id, email, role=ROLE_RECRUITER) -> User:
    u = User(
        email=email, hashed_password="x", is_active=True, is_superuser=False,
        is_verified=False, organization_id=org_id, role=role,
    )
    db.add(u)
    db.commit()
    return u


def test_scorecard_upsert_edits_in_place(client, db):
    headers, email = auth_headers(client)
    app_id = _seed_app(db, _org_id(db, email))

    r = client.post(
        f"/api/v1/applications/{app_id}/scorecards",
        json={"recommendation": "yes", "overall_rating": 3,
              "competencies": [{"name": "Coding", "rating": 3, "comment": "solid"}]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["submitted_at"] is None

    # Re-post (same caller, same application, no interview) edits in place.
    r = client.post(
        f"/api/v1/applications/{app_id}/scorecards",
        json={"recommendation": "strong_yes", "overall_rating": 4},
        headers=headers,
    )
    assert r.status_code == 201 and r.json()["id"] == sid
    assert r.json()["recommendation"] == "strong_yes"
    assert db.query(InterviewScorecard).filter_by(application_id=app_id).count() == 1


def test_scorecard_validation(client, db):
    headers, email = auth_headers(client)
    app_id = _seed_app(db, _org_id(db, email))
    assert client.post(
        f"/api/v1/applications/{app_id}/scorecards",
        json={"recommendation": "maybe"}, headers=headers,
    ).status_code == 422
    assert client.post(
        f"/api/v1/applications/{app_id}/scorecards",
        json={"overall_rating": 9}, headers=headers,
    ).status_code == 422


def test_submit_requires_recommendation_then_counts_in_summary(client, db):
    headers, email = auth_headers(client)
    app_id = _seed_app(db, _org_id(db, email))
    sid = client.post(
        f"/api/v1/applications/{app_id}/scorecards", json={"overall_rating": 3}, headers=headers
    ).json()["id"]

    # No recommendation yet -> can't submit.
    assert client.post(f"/api/v1/scorecards/{sid}/submit", headers=headers).status_code == 422
    # Draft is excluded from the summary.
    assert client.get(
        f"/api/v1/applications/{app_id}/scorecards/summary", headers=headers
    ).json()["submitted_count"] == 0

    client.post(
        f"/api/v1/applications/{app_id}/scorecards",
        json={"recommendation": "yes"}, headers=headers,
    )
    assert client.post(f"/api/v1/scorecards/{sid}/submit", headers=headers).status_code == 200

    summary = client.get(
        f"/api/v1/applications/{app_id}/scorecards/summary", headers=headers
    ).json()
    assert summary["submitted_count"] == 1
    assert summary["recommendations"]["yes"] == 1
    assert summary["mean_lean"] == 1.0


def test_cannot_submit_another_users_scorecard(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    app_id = _seed_app(db, org_id)
    # Caller is a recruiter (not admin) — admins may act on any scorecard.
    caller = db.query(User).filter(User.email == email).first()
    caller.role = ROLE_RECRUITER
    db.commit()
    # A scorecard owned by a different interviewer.
    other = _member(db, org_id, "other@sc.test")
    card = InterviewScorecard(
        organization_id=org_id, application_id=app_id,
        interviewer_user_id=other.id, recommendation="yes",
    )
    db.add(card)
    db.commit()
    r = client.post(f"/api/v1/scorecards/{card.id}/submit", headers=headers)
    assert r.status_code == 403


def test_scorecard_is_org_scoped(client, db):
    headers, email = auth_headers(client)
    other = Organization(name="Other", slug="other-sc")
    db.add(other)
    db.flush()
    other_app = _seed_app(db, other.id)
    r = client.get(f"/api/v1/applications/{other_app}/scorecards", headers=headers)
    assert r.status_code == 404
