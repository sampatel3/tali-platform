"""WS3: cross-candidate duplicate / mass-apply signals (flag-only, bounded).

detect_cross_candidate_signals looks ACROSS candidates in the same org:
  (a) duplicate_identity — same phone_normalized / email on another candidate
  (b) cv_mill — near-duplicate CV text vs another application on the SAME role
Both are bounded (indexed equality / capped same-role scan), no LLM, no score
change.
"""

from __future__ import annotations

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.fraud_cross_candidate import detect_cross_candidate_signals


def _org_role(db, slug):
    org = Organization(name="X", slug=slug)
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="manual",
        description="jd",
        # The end-to-end pre-screen case goes through the real role-level
        # usage admission rail. Give this fixture an explicit funded cap so
        # the test reaches the fraud-signal behavior it is meant to cover.
        monthly_usd_budget_cents=100,
    )
    db.add(role)
    db.flush()
    return org, role


def _cand(db, org, *, email="a@x.test", phone=None, cv=None):
    c = Candidate(organization_id=org.id, email=email, full_name="C",
                  phone_normalized=phone, cv_text=cv)
    db.add(c)
    db.flush()
    return c


def _app(db, org, role, cand, *, cv=None):
    a = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        cv_text=cv,
    )
    db.add(a)
    db.flush()
    return a


def test_duplicate_identity_by_phone(db):
    org, role = _org_role(db, "dup-phone")
    # Two DIFFERENT candidate rows, same normalized phone → identity dup.
    _cand(db, org, email="first@x.test", phone="123456789")
    dup = _cand(db, org, email="second@x.test", phone="123456789")
    app = _app(db, org, role, dup)

    signals = detect_cross_candidate_signals(db, app)
    assert signals["duplicate_identity"]["triggered"] is True
    assert signals["duplicate_identity"]["matched_on"] == "phone"
    assert signals["duplicate_identity"]["duplicate_candidate_count"] == 1


def test_duplicate_identity_by_email_case_insensitive(db):
    org, role = _org_role(db, "dup-email")
    _cand(db, org, email="Person@X.test")
    dup = _cand(db, org, email="person@x.test")  # same email, different case
    app = _app(db, org, role, dup)

    signals = detect_cross_candidate_signals(db, app)
    assert signals["duplicate_identity"]["matched_on"] == "email"


def test_no_duplicate_for_unique_identity(db):
    org, role = _org_role(db, "unique")
    solo = _cand(db, org, email="solo@x.test", phone="999888777")
    app = _app(db, org, role, solo)
    assert "duplicate_identity" not in detect_cross_candidate_signals(db, app)


_LONG_CV = (
    "Experienced backend engineer specialising in distributed data pipelines. "
    "Built and owned Spark and Airflow batch jobs processing billions of events. "
    "Led migration to a Kafka event-driven architecture and mentored four juniors. "
    "Comfortable owning production services end to end and being on-call rotation. "
) * 4


def test_cv_mill_flags_near_duplicate_same_role(db):
    org, role = _org_role(db, "cvmill")
    # First application on the role carries the template CV.
    c1 = _cand(db, org, email="one@x.test", cv=_LONG_CV)
    _app(db, org, role, c1, cv=_LONG_CV)
    # Second candidate: a lightly-reworded near-duplicate on the SAME role.
    near = _LONG_CV.replace("mentored four juniors", "coached several juniors")
    c2 = _cand(db, org, email="two@x.test", cv=near)
    app2 = _app(db, org, role, c2, cv=near)

    signals = detect_cross_candidate_signals(db, app2)
    assert signals["cv_mill"]["triggered"] is True
    assert signals["cv_mill"]["similarity"] >= 0.7
    assert signals["cv_mill"]["compared_against"] == 1


def test_cv_mill_ignores_distinct_cvs(db):
    org, role = _org_role(db, "cvmill-clean")
    c1 = _cand(db, org, email="one@x.test", cv=_LONG_CV)
    _app(db, org, role, c1, cv=_LONG_CV)
    distinct = (
        "Frontend designer with a decade of accessibility work across React and "
        "design systems. Ran a component library used by twelve product teams. "
    ) * 4
    c2 = _cand(db, org, email="two@x.test", cv=distinct)
    app2 = _app(db, org, role, c2, cv=distinct)
    assert "cv_mill" not in detect_cross_candidate_signals(db, app2)


def test_cv_mill_bounded_to_same_role(db):
    org, role_a = _org_role(db, "role-a")
    role_b = Role(organization_id=org.id, name="Other", source="manual", description="jd")
    db.add(role_b)
    db.flush()
    # Template CV lives on role_a; the near-duplicate applies to role_b → the
    # same-role-bounded scan must NOT compare across roles, so no cv_mill flag.
    c1 = _cand(db, org, email="one@x.test", cv=_LONG_CV)
    _app(db, org, role_a, c1, cv=_LONG_CV)
    c2 = _cand(db, org, email="two@x.test", cv=_LONG_CV)
    app2 = _app(db, org, role_b, c2, cv=_LONG_CV)
    assert "cv_mill" not in detect_cross_candidate_signals(db, app2)


def test_returns_empty_without_db():
    assert detect_cross_candidate_signals(None, object()) == {}


def test_prescreen_persists_cross_candidate_signals(db):
    # End-to-end: execute_pre_screen_only(db=db) runs the cross-candidate check
    # and stores it under pre_screen_evidence.fraud_signals (flag-only).
    from unittest.mock import patch

    org, role = _org_role(db, "prescreen-dup")
    role.job_spec_text = "Looking for a backend engineer."
    first = _cand(db, org, email="dup@x.test", phone="555111222")
    _app(db, org, role, first, cv="A perfectly ordinary CV about backend work.")
    dup = _cand(db, org, email="other@x.test", phone="555111222")
    app = _app(db, org, role, dup, cv="Another ordinary CV, no copy-paste at all.")
    # Hard usage admission runs in an independent metering session so the
    # hold is durable before any provider call. Mirror the production worker
    # boundary: the application, role and cap must already be committed.
    db.commit()

    class _LLM:
        decision = "yes"; reason = "ok"; score = 70.0; unverified_claim = False
        cache_hit = False; prompt_version = "v"; trace_id = "t"
        input_tokens = 1; output_tokens = 1; cache_read_tokens = 0; cache_creation_tokens = 0

    from app.services.pre_screening_service import execute_pre_screen_only

    with patch("app.cv_matching.runner_pre_screen.run_pre_screen", return_value=_LLM()):
        result = execute_pre_screen_only(app, db=db)

    assert result["status"] == "ok"
    dup_sig = app.pre_screen_evidence["fraud_signals"]["duplicate_identity"]
    assert dup_sig["triggered"] is True
    assert dup_sig["matched_on"] == "phone"
