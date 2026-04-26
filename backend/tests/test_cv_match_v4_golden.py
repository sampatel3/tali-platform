"""Golden-fixture regression tests for the cv_match_v4 validation pipeline.

These pin the *behaviour* of the validator + the orchestrator's hard-cap
and cache layers against fixed Claude responses. They don't test the
real Claude prompt — only what happens once a payload comes back. The point
is to catch regressions like:

  - dropping unverifiable quotes silently → showing fabricated grounding
  - the must-have hard cap being lifted accidentally
  - the cache key losing sensitivity to a load-bearing input
  - ranking stability across two near-identical CVs

If you change the validation contract (add fields, change cap thresholds,
adjust quote tolerance), update these fixtures intentionally — that's the
signal you're shipping a contract change.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Candidate,
    CandidateApplication,
    CvScoreCache,
    Organization,
    Role,
    SCORE_JOB_DONE,
)
from app.platform.config import settings
from app.platform.database import Base
from app.services import cv_score_orchestrator
from app.services.cv_score_orchestrator import enqueue_score
from app.services.fit_matching_service import (
    CV_MATCH_V4_PROMPT_VERSION,
    _validate_v4_payload,
)
from app.services.role_criteria_service import sync_recruiter_criteria


CV_TEXT_STRONG = """Jane Doe — Senior Backend Engineer

Experience:
- Led a 6-engineer team building payments services on AWS for 4 years.
- Owned a Postgres-backed billing platform handling 12M requests/day.
- Mentored two junior engineers; ran weekly architecture reviews.
- Stack: Python, FastAPI, PostgreSQL, Kafka, AWS (ECS, RDS, SQS).
"""

CV_TEXT_WEAK = """Pat Sample — Frontend Developer

Experience:
- 2 years building React UI for a marketing site.
- Familiar with HTML, CSS, JavaScript, and basic Node.
"""

CRITERIA = [
    {"id": 1, "text": "5+ years Python", "must_have": True, "source": "recruiter"},
    {"id": 2, "text": "AWS experience", "must_have": True, "source": "recruiter"},
    {"id": 3, "text": "Mentorship", "must_have": False, "source": "derived_from_spec"},
]


# ---------------------------------------------------------------------------
# Pure validator fixtures — no DB, no Claude
# ---------------------------------------------------------------------------


def _entry(criterion_id: int, **overrides):
    base = {
        "criterion_id": criterion_id,
        "status": "met",
        "confidence": 0.85,
        "cv_quote": None,
        "evidence_type": "explicit",
        "blocker": False,
        "risk_level": "low",
        "screening_recommendation": "advance",
        "interview_probe": "Generic probe.",
    }
    base.update(overrides)
    return base


def _full_payload(entries, *, overall=82, recommendation="yes"):
    return {
        "overall_match_score": overall,
        "skills_match_score": 80,
        "experience_relevance_score": 85,
        "requirements_match_score": 78,
        "recommendation": recommendation,
        "summary": "Stub summary.",
        "matching_skills": ["Python"],
        "missing_skills": [],
        "experience_highlights": ["6 years Python"],
        "concerns": [],
        "requirements_assessment": entries,
    }


def test_golden_strong_fit_validates_clean() -> None:
    payload = _full_payload(
        [
            _entry(1, status="met", cv_quote="Senior Backend Engineer", confidence=0.95),
            _entry(2, status="met", cv_quote="payments services on AWS", confidence=0.9),
            _entry(3, status="met", cv_quote="Mentored two junior engineers", confidence=0.85),
        ],
        overall=88,
        recommendation="strong_yes",
    )
    validated = _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT_STRONG)
    assert validated["overall_match_score"] == 88.0
    assert validated["recommendation"] == "strong_yes"
    statuses = [a["status"] for a in validated["requirements_assessment"]]
    assert statuses == ["met", "met", "met"]
    quotes = [a["cv_quote"] for a in validated["requirements_assessment"]]
    assert all(q is not None for q in quotes), "verifiable quotes should be retained verbatim"


def test_golden_weak_fit_with_missing_must_have_marks_blocker() -> None:
    payload = _full_payload(
        [
            _entry(1, status="missing", cv_quote=None, evidence_type="absent", blocker=True, confidence=0.1, risk_level="high", screening_recommendation="reject"),
            _entry(2, status="missing", cv_quote=None, evidence_type="absent", blocker=True, confidence=0.1, risk_level="high", screening_recommendation="reject"),
            _entry(3, status="missing", cv_quote=None, evidence_type="absent", blocker=False, confidence=0.2, risk_level="med", screening_recommendation="reject"),
        ],
        overall=22,
        recommendation="no",
    )
    validated = _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT_WEAK)
    assert validated["recommendation"] == "no"
    blockers = [a for a in validated["requirements_assessment"] if a["blocker"]]
    assert {a["criterion_id"] for a in blockers} == {1, 2}, "both must_haves should remain blockers"
    assert validated["requirements_assessment"][2]["blocker"] is False, "non-must_have can't be a blocker"


def test_golden_fabricated_quote_is_dropped_and_marked_absent() -> None:
    payload = _full_payload(
        [
            _entry(1, status="met", cv_quote="led a team of 50 engineers", evidence_type="explicit", confidence=0.7),
            _entry(2, status="met", cv_quote="payments services on AWS", evidence_type="explicit", confidence=0.9),
            _entry(3, status="met", cv_quote="Mentored two junior engineers", evidence_type="explicit", confidence=0.85),
        ],
    )
    validated = _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT_STRONG)
    fabricated = next(a for a in validated["requirements_assessment"] if a["criterion_id"] == 1)
    real = next(a for a in validated["requirements_assessment"] if a["criterion_id"] == 2)
    assert fabricated["cv_quote"] is None, "fabricated quote must be stripped"
    assert fabricated["evidence_type"] == "absent", "evidence_type must be downgraded to absent"
    assert real["cv_quote"] == "payments services on AWS", "real quote must survive intact"


def test_golden_blocker_only_applies_to_must_have_criteria() -> None:
    payload = _full_payload(
        [
            _entry(1, status="met", cv_quote="Senior Backend Engineer", confidence=0.9),
            _entry(2, status="met", cv_quote="payments services on AWS", confidence=0.9),
            _entry(3, status="missing", evidence_type="absent", blocker=True, confidence=0.2),
        ],
    )
    validated = _validate_v4_payload(payload, criteria=CRITERIA, cv_text=CV_TEXT_STRONG)
    nice_to_have = next(a for a in validated["requirements_assessment"] if a["criterion_id"] == 3)
    assert nice_to_have["blocker"] is False, "non-must_have criteria can never be blockers"


# ---------------------------------------------------------------------------
# End-to-end orchestrator fixtures — DB + cache, with stubbed Claude
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _force_inline_celery(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "MVP_DISABLE_CELERY", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key-not-used")


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    org = Organization(name="Acme", slug="acme")
    db.add(org)
    db.commit()
    db.refresh(org)
    role = Role(
        organization_id=org.id,
        name="Backend Engineer",
        job_spec_text="Description\nA backend role.\nRequirements\n- 5+ years Python\n- AWS experience\n",
        additional_requirements="- 5+ years Python\n- AWS experience",
    )
    db.add(role)
    db.flush()
    sync_recruiter_criteria(db, role)
    db.commit()
    db.refresh(role)
    yield db, org, role


def _make_application(db, org, role, *, email: str, cv_text: str) -> CandidateApplication:
    candidate = Candidate(organization_id=org.id, email=email)
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        cv_text=cv_text,
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


def _stub_v4_result_factory(score: float, *, must_have_blocked: bool = False):
    def factory(**kwargs):
        return {
            "cv_job_match_score": score,
            "match_details": {
                "scoring_version": CV_MATCH_V4_PROMPT_VERSION,
                "model_overall_score_100": score,
                "final_score_100": score,
                "recommendation": "yes" if score >= 60 else "lean_no" if score >= 40 else "no",
                "summary": "stub",
                "matching_skills": [],
                "missing_skills": [],
                "experience_highlights": [],
                "concerns": [],
                "requirements_assessment": [],
                "requirements_coverage": {"met": 0, "partially_met": 0, "missing": 0, "unknown": 0},
                "must_have_blocked": must_have_blocked,
                "score_scale": "0-100",
            },
        }
    return factory


def test_golden_ranking_stability_two_near_duplicate_cvs(monkeypatch, session) -> None:
    """Two applications with the SAME inputs must get the same score (cache hit)."""
    db, org, role = session
    app_a = _make_application(db, org, role, email="a@example.com", cv_text=CV_TEXT_STRONG)
    app_b = _make_application(db, org, role, email="b@example.com", cv_text=CV_TEXT_STRONG)

    call_count = {"n": 0}

    def fake_v4(**kwargs):
        call_count["n"] += 1
        return _stub_v4_result_factory(82.0)(**kwargs)

    monkeypatch.setattr(cv_score_orchestrator, "calculate_cv_job_match_v4_sync", fake_v4)

    job_a = enqueue_score(db, app_a)
    job_b = enqueue_score(db, app_b)
    db.commit()
    db.refresh(app_a)
    db.refresh(app_b)

    assert job_a.status == SCORE_JOB_DONE
    assert job_b.status == SCORE_JOB_DONE
    assert app_a.cv_match_score == app_b.cv_match_score == 82.0
    assert call_count["n"] == 1, "identical inputs must hit the cache; only one Claude call total"
    assert job_a.cache_hit == "miss"
    assert job_b.cache_hit == "hit"


def test_golden_different_cvs_get_different_cache_keys(monkeypatch, session) -> None:
    db, org, role = session
    app_strong = _make_application(db, org, role, email="strong@example.com", cv_text=CV_TEXT_STRONG)
    app_weak = _make_application(db, org, role, email="weak@example.com", cv_text=CV_TEXT_WEAK)

    scores = {"strong": 88.0, "weak": 22.0}
    call_count = {"n": 0}

    def fake_v4(*, cv_text, **kwargs):
        call_count["n"] += 1
        if "Frontend Developer" in cv_text:
            return _stub_v4_result_factory(scores["weak"])(**kwargs)
        return _stub_v4_result_factory(scores["strong"])(**kwargs)

    monkeypatch.setattr(cv_score_orchestrator, "calculate_cv_job_match_v4_sync", fake_v4)

    enqueue_score(db, app_strong)
    enqueue_score(db, app_weak)
    db.commit()
    db.refresh(app_strong)
    db.refresh(app_weak)

    assert call_count["n"] == 2, "distinct CVs should each trigger a Claude call"
    assert app_strong.cv_match_score == 88.0
    assert app_weak.cv_match_score == 22.0
    assert app_strong.cv_match_score > app_weak.cv_match_score
    assert db.query(CvScoreCache).count() == 2


def test_golden_cache_persists_across_app_deletions(monkeypatch, session) -> None:
    """Deleting an application doesn't evict its cached score — a new app with
    the same inputs still hits the cache."""
    db, org, role = session
    app_first = _make_application(db, org, role, email="first@example.com", cv_text=CV_TEXT_STRONG)

    call_count = {"n": 0}

    def fake_v4(**kwargs):
        call_count["n"] += 1
        return _stub_v4_result_factory(75.0)(**kwargs)

    monkeypatch.setattr(cv_score_orchestrator, "calculate_cv_job_match_v4_sync", fake_v4)

    enqueue_score(db, app_first)
    db.commit()
    assert call_count["n"] == 1

    db.delete(app_first)
    db.commit()

    app_second = _make_application(db, org, role, email="second@example.com", cv_text=CV_TEXT_STRONG)
    enqueue_score(db, app_second)
    db.commit()
    db.refresh(app_second)

    assert call_count["n"] == 1, "deleting the source application must not invalidate the cache"
    assert app_second.cv_match_score == 75.0
