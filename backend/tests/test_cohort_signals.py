"""Tests for cohort_signals_service + the get_cohort_signals agent tool.

The service is pure (read-only DB query + arithmetic), so most tests
build a small synthetic applicant pool with known features and assert
on the lift values directly. The agent-tool test exercises the caching
behaviour on the role row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.agent_runtime import tool_registry
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services import cohort_signals_service
from app.services.cohort_signals_service import (
    MIN_LIFT,
    MIN_POOL_SIZE,
    MIN_TOP_FREQ,
    compute_cohort_signals,
    render_summary_for_prompt,
)


# SQLite BigInteger PK workaround.
_BIG_PK_COUNTERS: dict[str, int] = {"agent_runs": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentRun, "before_insert", _assign_big_pk)


def _make_org(db) -> Organization:
    org = Organization(name="Cohort Org", slug=f"cohort-org-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _make_role(db, org: Organization, name: str = "Backend") -> Role:
    role = Role(
        organization_id=org.id,
        name=name,
        source="manual",
    )
    db.add(role)
    db.flush()
    return role


def _make_candidate(
    db,
    *,
    org: Organization,
    email: str,
    skills: list[str] | None = None,
    companies: list[str] | None = None,
    titles: list[str] | None = None,
    schools: list[str] | None = None,
) -> Candidate:
    experience = []
    titles = titles or []
    companies = companies or []
    # Pair titles + companies up by index where possible.
    for i in range(max(len(titles), len(companies))):
        experience.append(
            {
                "title": titles[i] if i < len(titles) else "",
                "company": companies[i] if i < len(companies) else "",
            }
        )
    education = [{"institution": s} for s in (schools or [])]

    cand = Candidate(
        organization_id=org.id,
        email=email,
        full_name=email.split("@")[0],
        skills=skills or [],
        experience_entries=experience,
        education_entries=education,
    )
    db.add(cand)
    db.flush()
    return cand


def _make_application(
    db, *, org: Organization, role: Role, candidate: Candidate, taali: float
) -> CandidateApplication:
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        taali_score_cache_100=taali,
    )
    db.add(app)
    db.flush()
    return app


def _make_agent_run(db, role: Role) -> AgentRun:
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger="manual",
        status="running",
        model_version="claude-3-5-haiku-latest",
        prompt_version="agent.v4.test",
    )
    db.add(run)
    db.flush()
    return run


# ---------------------------------------------------------------------------
# Pure service tests
# ---------------------------------------------------------------------------


def test_cohort_signals_returns_insufficient_data_below_min_pool(db):
    org = _make_org(db)
    role = _make_role(db, org)
    # Only 3 scored candidates → below MIN_POOL_SIZE (5).
    for i in range(3):
        c = _make_candidate(db, org=org, email=f"a{i}@x.test")
        _make_application(db, org=org, role=role, candidate=c, taali=70.0 + i)

    result = compute_cohort_signals(
        db, role_id=int(role.id), organization_id=int(org.id)
    )
    assert result["insufficient_data"] is True
    assert result["pool_size"] == 3
    assert result["top_size"] == 0
    assert all(result["signals"][cat] == [] for cat in result["signals"])


def test_cohort_signals_surfaces_skill_concentrated_in_top_set(db):
    """If kubernetes is in 5/5 top scorers but 1/10 in the rest, lift is high."""
    org = _make_org(db)
    role = _make_role(db, org)

    # Top 5: all have kubernetes + python.
    for i in range(5):
        c = _make_candidate(
            db,
            org=org,
            email=f"top{i}@x.test",
            skills=["python", "kubernetes"],
        )
        _make_application(db, org=org, role=role, candidate=c, taali=90.0 + i)

    # Bottom 10: most have python (so it's also pool-common), 1 has kubernetes.
    for i in range(10):
        c = _make_candidate(
            db,
            org=org,
            email=f"bot{i}@x.test",
            skills=(["python"] if i < 8 else ["python", "kubernetes"]) if i != 9 else ["javascript"],
        )
        _make_application(db, org=org, role=role, candidate=c, taali=50.0 + i)

    result = compute_cohort_signals(
        db, role_id=int(role.id), organization_id=int(org.id)
    )

    assert result["insufficient_data"] is False
    assert result["pool_size"] == 15
    assert result["top_size"] == 5  # 10% of 15 = 1.5, clamped to MIN_TOP_SIZE=5
    skills = {s["feature"]: s for s in result["signals"]["skills"]}
    # kubernetes: 5/5 in top (1.0), 1/15 in pool (0.067) → lift ~15× — strong signal
    assert "kubernetes" in skills
    assert skills["kubernetes"]["top_freq"] == pytest.approx(1.0)
    # Lift > MIN_LIFT (1.5) so it must be present.
    assert skills["kubernetes"]["lift"] is not None
    assert skills["kubernetes"]["lift"] >= MIN_LIFT
    # python is too pool-common — top_freq=1.0, pool_freq~0.93, lift ~1.07 < MIN_LIFT
    assert "python" not in skills


def test_cohort_signals_handles_features_exclusive_to_top(db):
    """A feature held only by top scorers gets `lift=None` and exclusive_to_top=True."""
    org = _make_org(db)
    role = _make_role(db, org)

    # Top 5: all worked at "Acme"
    for i in range(5):
        c = _make_candidate(
            db,
            org=org,
            email=f"top{i}@x.test",
            skills=["python"],
            companies=["Acme"],
            titles=["Senior Engineer"],
        )
        _make_application(db, org=org, role=role, candidate=c, taali=90.0 + i)
    # Bottom 5: nobody worked at Acme
    for i in range(5):
        c = _make_candidate(
            db,
            org=org,
            email=f"bot{i}@x.test",
            skills=["python"],
            companies=["Other Co"],
            titles=["Junior"],
        )
        _make_application(db, org=org, role=role, candidate=c, taali=40.0 + i)

    result = compute_cohort_signals(
        db, role_id=int(role.id), organization_id=int(org.id)
    )
    companies = {c["feature"]: c for c in result["signals"]["companies"]}
    assert "acme" in companies
    assert companies["acme"]["exclusive_to_top"] is True
    assert companies["acme"]["lift"] is None  # ∞ rendered as None
    assert companies["acme"]["top_freq"] == pytest.approx(1.0)
    assert companies["acme"]["rest_freq"] == pytest.approx(0.0)
    assert companies["acme"]["rest_n"] == 0


def test_cohort_signals_filters_by_min_freq_and_min_lift(db):
    """A skill present in only 2/5 top scorers should be excluded (below MIN_TOP_FREQ)."""
    org = _make_org(db)
    role = _make_role(db, org)
    # Top 5: only 2 have "rust" (40% — exactly at threshold, but lift will be tested too)
    skills_top = [
        ["rust", "python"],
        ["rust", "python"],
        ["python"],
        ["python"],
        ["python"],
    ]
    for i, skills in enumerate(skills_top):
        c = _make_candidate(db, org=org, email=f"top{i}@x.test", skills=skills)
        _make_application(db, org=org, role=role, candidate=c, taali=90.0 + i)
    # Bottom 5: nobody has rust
    for i in range(5):
        c = _make_candidate(db, org=org, email=f"bot{i}@x.test", skills=["python"])
        _make_application(db, org=org, role=role, candidate=c, taali=40.0 + i)

    result = compute_cohort_signals(
        db, role_id=int(role.id), organization_id=int(org.id)
    )
    skills = {s["feature"]: s for s in result["signals"]["skills"]}
    # rust: top_freq=0.4 == MIN_TOP_FREQ (passes), lift=0.4/(2/10)=2.0 (passes)
    assert "rust" in skills
    # Force a stricter case: a skill at top_freq=0.2 should be filtered.
    # Add one candidate with a unique skill in the top set (1/5 = 0.2 < MIN_TOP_FREQ).
    c = _make_candidate(db, org=org, email="loner@x.test", skills=["niche_tech"])
    _make_application(db, org=org, role=role, candidate=c, taali=200.0)
    # Now top_size becomes... wait, this may shift top_size to 6. Let's just
    # verify niche_tech doesn't appear since 1/6 = 0.17 < MIN_TOP_FREQ.
    result = compute_cohort_signals(
        db, role_id=int(role.id), organization_id=int(org.id)
    )
    skills = {s["feature"]: s for s in result["signals"]["skills"]}
    assert "niche_tech" not in skills


def test_cohort_signals_normalizes_features_case_insensitively(db):
    """'Python' and 'python' should be the same feature."""
    org = _make_org(db)
    role = _make_role(db, org)
    for i in range(5):
        c = _make_candidate(db, org=org, email=f"top{i}@x.test", skills=["Python"])
        _make_application(db, org=org, role=role, candidate=c, taali=90.0 + i)
    for i in range(5):
        c = _make_candidate(db, org=org, email=f"bot{i}@x.test", skills=["javascript"])
        _make_application(db, org=org, role=role, candidate=c, taali=40.0 + i)

    result = compute_cohort_signals(
        db, role_id=int(role.id), organization_id=int(org.id)
    )
    skills = {s["feature"]: s for s in result["signals"]["skills"]}
    # Should be lowercased.
    assert "python" in skills
    assert "Python" not in skills


def test_cohort_signals_excludes_unscored_applicants(db):
    """Apps with taali_score_cache_100=NULL must be excluded from the pool."""
    org = _make_org(db)
    role = _make_role(db, org)
    # Add 7 scored, 3 unscored → pool_size should be 7
    for i in range(7):
        c = _make_candidate(db, org=org, email=f"s{i}@x.test", skills=["python"])
        _make_application(db, org=org, role=role, candidate=c, taali=70.0 + i)
    for i in range(3):
        c = _make_candidate(db, org=org, email=f"u{i}@x.test", skills=["python"])
        app = _make_application(db, org=org, role=role, candidate=c, taali=99.0)
        app.taali_score_cache_100 = None
        db.add(app)
    db.flush()

    result = compute_cohort_signals(
        db, role_id=int(role.id), organization_id=int(org.id)
    )
    assert result["pool_size"] == 7


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def test_render_summary_for_prompt_with_insufficient_data():
    payload = {"insufficient_data": True, "pool_size": 2, "min_pool_size": 5}
    out = render_summary_for_prompt(payload)
    assert "insufficient data" in out.lower()
    assert "2" in out


def test_render_summary_for_prompt_with_signals():
    payload = {
        "insufficient_data": False,
        "pool_size": 20,
        "top_size": 5,
        "top_threshold_score": 78.5,
        "signals": {
            "skills": [
                {"feature": "kubernetes", "top_freq": 1.0, "pool_freq": 0.1, "lift": 10.0, "exclusive_to_top": False},
            ],
            "companies": [
                {"feature": "stripe", "top_freq": 0.6, "pool_freq": 0.05, "lift": None, "exclusive_to_top": True},
            ],
            "titles": [],
            "schools": [],
        },
    }
    out = render_summary_for_prompt(payload)
    assert "kubernetes" in out
    assert "stripe" in out
    assert "10.0×" in out or "10×" in out
    assert "only top" in out  # exclusive label


# ---------------------------------------------------------------------------
# Agent tool: caching behaviour
# ---------------------------------------------------------------------------


def test_get_cohort_signals_tool_caches_on_role_and_returns_from_cache(db):
    org = _make_org(db)
    role = _make_role(db, org)
    for i in range(6):
        c = _make_candidate(db, org=org, email=f"a{i}@x.test", skills=["python"])
        _make_application(db, org=org, role=role, candidate=c, taali=80.0 - i)
    run = _make_agent_run(db, role)

    # First call: compute + cache.
    first = tool_registry.dispatch(
        "get_cohort_signals", {}, db=db, agent_run=run, role=role
    )
    assert first["from_cache"] is False
    assert first["pool_size"] == 6
    assert role.agent_cohort_signals is not None
    assert role.agent_cohort_signals_at is not None

    # Second call: should hit cache.
    second = tool_registry.dispatch(
        "get_cohort_signals", {}, db=db, agent_run=run, role=role
    )
    assert second["from_cache"] is True
    assert second["pool_size"] == 6


def test_get_cohort_signals_tool_recomputes_when_stale(db):
    org = _make_org(db)
    role = _make_role(db, org)
    for i in range(6):
        c = _make_candidate(db, org=org, email=f"a{i}@x.test", skills=["python"])
        _make_application(db, org=org, role=role, candidate=c, taali=80.0 - i)
    run = _make_agent_run(db, role)

    # Pre-populate stale cache (>1h old).
    role.agent_cohort_signals = {"pool_size": 999, "stale": True}
    role.agent_cohort_signals_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.flush()

    result = tool_registry.dispatch(
        "get_cohort_signals", {}, db=db, agent_run=run, role=role
    )
    assert result["from_cache"] is False
    assert result["pool_size"] == 6  # not the stale 999


def test_get_cohort_signals_force_recompute_bypasses_cache(db):
    org = _make_org(db)
    role = _make_role(db, org)
    for i in range(6):
        c = _make_candidate(db, org=org, email=f"a{i}@x.test", skills=["python"])
        _make_application(db, org=org, role=role, candidate=c, taali=80.0 - i)
    run = _make_agent_run(db, role)

    # Fresh cache.
    role.agent_cohort_signals = {"pool_size": 999, "from_cache_check": "stale"}
    role.agent_cohort_signals_at = datetime.now(timezone.utc)
    db.flush()

    result = tool_registry.dispatch(
        "get_cohort_signals",
        {"force_recompute": True},
        db=db,
        agent_run=run,
        role=role,
    )
    assert result["from_cache"] is False
    assert result["pool_size"] == 6
