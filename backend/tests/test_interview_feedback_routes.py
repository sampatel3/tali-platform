"""API tests for structured interview feedback + calibration core math.

Covers POST/GET/PATCH/DELETE on
``/api/v1/applications/{id}/interview-feedback``: a create+list roundtrip,
org scoping (a foreign org gets 404), recommendation-enum validation, patch,
delete, that the detail payload carries ``interview_feedback`` but a
client-safe render strips it, and the calibration script's pure computation.
"""

from app.domains.assessments_runtime.role_support import application_detail_payload
from app.models.candidate_application import CandidateApplication
from tests.conftest import auth_headers


def _create_application(client, headers, candidate_email="ifb@example.com"):
    role = client.post(
        "/api/v1/roles",
        json={"name": "Backend Engineer", "description": "Hiring"},
        headers=headers,
    )
    assert role.status_code == 201, role.text
    role = role.json()
    app_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": candidate_email, "candidate_name": "Feedback Candidate"},
        headers=headers,
    )
    assert app_resp.status_code == 201, app_resp.text
    return app_resp.json()


def test_create_and_list_roundtrip(client):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers)
    aid = app["id"]

    resp = client.post(
        f"/api/v1/applications/{aid}/interview-feedback",
        headers=headers,
        json={
            "interview_round": "technical",
            "interviewer_name": "Dana Recruiter",
            "overall_recommendation": "yes",
            "dimension_ratings": {"delegation": 4, "deliverable": 5},
            "probe_results": [
                {"criterion_id": "c1", "criterion_text": "System design depth", "result": "confirmed"},
                {"criterion_id": "c2", "criterion_text": "On-call experience", "result": "not_probed"},
            ],
            "notes": "Strong on design, thin on ops.",
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["overall_recommendation"] == "yes"
    assert created["interview_round"] == "technical"
    assert created["role_id"] == app["role_id"]
    assert created["dimension_ratings"] == {"delegation": 4, "deliverable": 5}
    assert len(created["probe_results"]) == 2
    assert created["probe_results"][0]["result"] == "confirmed"

    # A second entry, then list newest-first.
    resp2 = client.post(
        f"/api/v1/applications/{aid}/interview-feedback",
        headers=headers,
        json={"interview_round": "final", "overall_recommendation": "strong_yes"},
    )
    assert resp2.status_code == 201, resp2.text

    listing = client.get(f"/api/v1/applications/{aid}/interview-feedback", headers=headers)
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert len(rows) == 2
    # newest-first: the 'final' round (created last) leads.
    assert rows[0]["interview_round"] == "final"
    assert rows[1]["interview_round"] == "technical"


def test_recorded_feedback_is_submitted_on_create(client):
    """The record endpoint captures a completed interview, so the row is
    SUBMITTED on create — otherwise the calibration script (which now reads only
    submitted rows) would silently drop recruiter-recorded feedback."""
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="recorded@example.com")
    resp = client.post(
        f"/api/v1/applications/{app['id']}/interview-feedback",
        headers=headers,
        json={"overall_recommendation": "yes"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["submitted_at"] is not None


def test_recommendation_validation(client):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="badrec@example.com")
    resp = client.post(
        f"/api/v1/applications/{app['id']}/interview-feedback",
        headers=headers,
        json={"overall_recommendation": "maybe"},
    )
    assert resp.status_code == 422, resp.text


def test_dimension_rating_out_of_range_rejected(client):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="badrating@example.com")
    resp = client.post(
        f"/api/v1/applications/{app['id']}/interview-feedback",
        headers=headers,
        json={"overall_recommendation": "yes", "dimension_ratings": {"delegation": 9}},
    )
    assert resp.status_code == 422, resp.text


def test_probe_result_validation(client):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="badprobe@example.com")
    resp = client.post(
        f"/api/v1/applications/{app['id']}/interview-feedback",
        headers=headers,
        json={
            "overall_recommendation": "yes",
            "probe_results": [{"criterion_text": "x", "result": "unsure"}],
        },
    )
    assert resp.status_code == 422, resp.text


def test_patch_updates_mutable_fields(client):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="patch@example.com")
    aid = app["id"]
    created = client.post(
        f"/api/v1/applications/{aid}/interview-feedback",
        headers=headers,
        json={"overall_recommendation": "neutral", "notes": "before"},
    ).json()

    patched = client.patch(
        f"/api/v1/applications/{aid}/interview-feedback/{created['id']}",
        headers=headers,
        json={"overall_recommendation": "no", "notes": "after"},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["overall_recommendation"] == "no"
    assert body["notes"] == "after"

    # Bad enum on patch is rejected too.
    bad = client.patch(
        f"/api/v1/applications/{aid}/interview-feedback/{created['id']}",
        headers=headers,
        json={"overall_recommendation": "definitely"},
    )
    assert bad.status_code == 422, bad.text


def test_delete_removes_entry(client):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="del@example.com")
    aid = app["id"]
    created = client.post(
        f"/api/v1/applications/{aid}/interview-feedback",
        headers=headers,
        json={"overall_recommendation": "yes"},
    ).json()

    resp = client.delete(
        f"/api/v1/applications/{aid}/interview-feedback/{created['id']}",
        headers=headers,
    )
    assert resp.status_code == 204, resp.text

    listing = client.get(f"/api/v1/applications/{aid}/interview-feedback", headers=headers)
    assert listing.json() == []


def test_org_scoping_foreign_app_404(client):
    headers_a, _ = auth_headers(client)
    headers_b, _ = auth_headers(client)
    app = _create_application(client, headers_a, candidate_email="scoped-ifb@example.com")
    aid = app["id"]

    # Foreign org can't create...
    forbidden = client.post(
        f"/api/v1/applications/{aid}/interview-feedback",
        headers=headers_b,
        json={"overall_recommendation": "yes"},
    )
    assert forbidden.status_code == 404, forbidden.text

    # ...nor list.
    forbidden_list = client.get(
        f"/api/v1/applications/{aid}/interview-feedback", headers=headers_b
    )
    assert forbidden_list.status_code == 404, forbidden_list.text


def test_org_scoping_foreign_feedback_id_404(client):
    """A feedback id that belongs to another org's app 404s even if the caller
    owns *an* application."""
    headers_a, _ = auth_headers(client)
    headers_b, _ = auth_headers(client)
    app_a = _create_application(client, headers_a, candidate_email="owner@example.com")
    created = client.post(
        f"/api/v1/applications/{app_a['id']}/interview-feedback",
        headers=headers_a,
        json={"overall_recommendation": "yes"},
    ).json()

    # Org B tries to patch org A's feedback via org A's application id → 404
    # (the application itself is foreign to org B).
    resp = client.patch(
        f"/api/v1/applications/{app_a['id']}/interview-feedback/{created['id']}",
        headers=headers_b,
        json={"notes": "hijack"},
    )
    assert resp.status_code == 404, resp.text


def test_detail_payload_carries_feedback_and_client_safe_strips_it(client, db):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="detail@example.com")
    aid = app["id"]
    client.post(
        f"/api/v1/applications/{aid}/interview-feedback",
        headers=headers,
        json={"overall_recommendation": "strong_yes", "notes": "recruiter-internal"},
    )

    app_obj = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.id == aid)
        .first()
    )

    recruiter_payload = application_detail_payload(app_obj, include_cv_text=False)
    assert isinstance(recruiter_payload["interview_feedback"], list)
    assert len(recruiter_payload["interview_feedback"]) == 1
    assert recruiter_payload["interview_feedback"][0]["overall_recommendation"] == "strong_yes"

    client_payload = application_detail_payload(
        app_obj, include_cv_text=False, client_safe=True
    )
    assert client_payload["interview_feedback"] is None


# --------------------------------------------------------------------------
# Calibration script core computation (importable pure function).
# --------------------------------------------------------------------------
def test_calibration_core_computation():
    from scripts.score_outcome_calibration import (
        FeedbackRow,
        compute_calibration,
        point_biserial,
    )

    rows = [
        # High score → positive recs / hired.
        FeedbackRow(1, "Backend", 90.0, "strong_yes", "hired", "advanced"),
        FeedbackRow(1, "Backend", 80.0, "yes", "open", "review"),
        # Low score → negative recs.
        FeedbackRow(1, "Backend", 30.0, "no", "rejected", "review"),
        FeedbackRow(1, "Backend", 20.0, "strong_no", "rejected", "review"),
        # Contradiction: advanced but a strong_no interview verdict.
        FeedbackRow(1, "Backend", 60.0, "strong_no", "open", "advanced"),
    ]

    stats = compute_calibration(rows)
    assert stats.n == 5
    assert stats.n_scored == 5

    means = stats.band_means()
    assert means["strong_yes"] == 90.0
    assert means["yes"] == 80.0
    # strong_no band has two rows: 20 and 60 → mean 40.
    assert means["strong_no"] == 40.0

    # Higher score tracks positive recommendation → positive correlation.
    assert stats.corr_recommendation is not None and stats.corr_recommendation > 0
    # Higher score tracks hire → positive correlation.
    assert stats.corr_hired is not None and stats.corr_hired > 0

    # Two negative-verdict rows sit on advanced/hired candidates
    # (the hired strong_yes doesn't count; only no/strong_no do).
    assert stats.contradicted_advances == 1

    # Degenerate inputs return None rather than raising.
    assert point_biserial([1.0], [1]) is None
    assert point_biserial([1.0, 2.0], [1, 1]) is None
    assert point_biserial([5.0, 5.0], [0, 1]) is None
