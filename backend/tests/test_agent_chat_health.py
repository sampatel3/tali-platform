"""Tests for the proactive role-health scan (``agent_chat.health``).

Pure DB, no LLM: covers each finding family the agent leads with — calibration
drift from recruiter overrides, requirement pathologies (dead / unverifiable /
redundant), threshold too strict / too loose, stale scores, decision backlog —
plus ranking (calibration is the strongest signal) and the all-clear path.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import event

from app.agent_chat import health, tools
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_criterion import RoleCriterion
from app.models.user import User


# SQLite BigInteger PK workaround for the agent_decisions table.
_BIG_PK = {"agent_decisions": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    if target.id is None:
        _BIG_PK["agent_decisions"] += 1
        target.id = _BIG_PK["agent_decisions"]


event.listen(AgentDecision, "before_insert", _assign_big_pk)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _org(db, name="Health Org") -> Organization:
    org = Organization(name=name, slug=f"{name.lower().replace(' ', '-')}-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _user(db, org) -> User:
    u = User(
        email=f"u-{id(db)}-{org.id}@x.test",
        hashed_password="x",
        full_name="Recruiter",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(u)
    db.flush()
    return u


def _role(db, org, *, name="Backend", threshold=70) -> Role:
    role = Role(
        organization_id=org.id,
        name=name,
        source="manual",
        score_threshold=threshold,
        auto_reject_threshold_mode="manual",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()
    return role


def _crit(db, role, *, text, bucket="must") -> RoleCriterion:
    c = RoleCriterion(
        role_id=role.id,
        source="recruiter",
        bucket=bucket,
        must_have=(bucket == "must"),
        text=text,
        ordering=0,
        weight=1.0,
    )
    db.add(c)
    db.flush()
    return c


def _app(
    db,
    org,
    role,
    *,
    score,
    name="Cand",
    assessment=None,
    engine="2.1.0",
    cv_match_score=None,
):
    """One open application. ``assessment`` is {criterion_id: status} folded into
    the stored requirements_assessment so the criterion scan can read it."""
    cand = Candidate(
        organization_id=org.id, email=f"{name}-{id(db)}-{score}@x.test", full_name=name
    )
    db.add(cand)
    db.flush()
    details = {"engine_version": engine}
    if assessment:
        details["requirements_assessment"] = [
            {"requirement_id": f"crit_{cid}", "status": st, "reasoning": "r"}
            for cid, st in assessment.items()
        ]
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        pre_screen_score_100=score,
        cv_match_score=cv_match_score,
        cv_match_details=details,
    )
    db.add(app)
    db.flush()
    return app


def _decision(db, org, role, app, *, decision_type, status, key=None):
    d = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type=decision_type,
        recommendation=decision_type,
        status=status,
        reasoning="r",
        model_version="m",
        prompt_version="p",
        created_at=datetime.now(timezone.utc),
        idempotency_key=key or f"k:{app.id}:{decision_type}:{status}",
    )
    db.add(d)
    db.flush()
    return d


def _balanced_pool(db, org, role, n=10):
    """n open apps split evenly above/below a 70 cut-off so no threshold finding
    fires — a neutral backdrop for isolating other findings."""
    apps = []
    for i in range(n):
        apps.append(_app(db, org, role, score=80 if i % 2 == 0 else 60, name=f"P{i}"))
    return apps


# ---------------------------------------------------------------------------
# All-clear
# ---------------------------------------------------------------------------


def test_healthy_role_is_all_clear(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    _balanced_pool(db, org, role, n=10)
    db.commit()

    out = health.role_health_check(db, role)
    assert out["type"] == "role_health"
    assert out["all_clear"] is True
    assert out["findings"] == []
    assert out["top_finding"] is None


# ---------------------------------------------------------------------------
# Requirement pathologies
# ---------------------------------------------------------------------------


def test_dead_must_have_flagged(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    crit = _crit(db, role, text="Based in UAE", bucket="must")
    # 10 assessed on the criterion: 1 met, 9 missing → met_frac 0.1 ≤ 0.15.
    for i in range(10):
        status = "met" if i == 0 else "missing"
        _app(db, org, role, score=80 if i % 2 == 0 else 60, name=f"C{i}",
             assessment={crit.id: status})
    db.commit()

    out = health.role_health_check(db, role)
    top = out["top_finding"]
    assert top["type"] == "dead_requirement"
    assert top["criterion_id"] == crit.id
    assert top["met"] == 1 and top["assessed"] == 10
    assert top["severity"] == "high"


def test_unverifiable_requirement_flagged(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    crit = _crit(db, role, text="5+ years leadership", bucket="must")
    # 10 assessed: 2 met, 2 missing, 6 unknown → met_frac 0.2 (not dead),
    # unknown_frac 0.6 ≥ 0.5 → unverifiable.
    statuses = ["met", "met", "missing", "missing"] + ["unknown"] * 6
    for i, st in enumerate(statuses):
        _app(db, org, role, score=80 if i % 2 == 0 else 60, name=f"U{i}",
             assessment={crit.id: st})
    db.commit()

    out = health.role_health_check(db, role)
    types = {f["type"] for f in out["findings"]}
    assert "unverifiable_requirement" in types
    f = next(f for f in out["findings"] if f["type"] == "unverifiable_requirement")
    assert f["unknown"] == 6 and f["criterion_id"] == crit.id


def test_requirement_everyone_meets_is_redundant(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    crit = _crit(db, role, text="Has a CV", bucket="must")
    for i in range(10):
        _app(db, org, role, score=80 if i % 2 == 0 else 60, name=f"R{i}",
             assessment={crit.id: "met"})
    db.commit()

    out = health.role_health_check(db, role)
    f = next(f for f in out["findings"] if f["type"] == "redundant_requirement")
    assert f["severity"] == "low"
    assert f["met"] == 10 and f["assessed"] == 10


# ---------------------------------------------------------------------------
# Threshold pathologies
# ---------------------------------------------------------------------------


def test_threshold_too_strict(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    _app(db, org, role, score=85, name="Top")  # only one clears
    for i in range(9):
        _app(db, org, role, score=40, name=f"Low{i}")
    db.commit()

    out = health.role_health_check(db, role)
    top = out["top_finding"]
    assert top["type"] == "threshold_too_strict"
    assert top["qualified"] == 1 and top["total_open"] == 10


def test_threshold_too_loose(db):
    org = _org(db)
    role = _role(db, org, threshold=30)
    for i in range(10):
        _app(db, org, role, score=80, name=f"Hi{i}")  # all clear 30
    db.commit()

    out = health.role_health_check(db, role)
    types = {f["type"] for f in out["findings"]}
    assert "threshold_too_loose" in types


# ---------------------------------------------------------------------------
# Calibration drift from overrides (the strongest signal)
# ---------------------------------------------------------------------------


def test_override_pattern_flags_too_strict(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    pool = _balanced_pool(db, org, role, n=10)
    # Recruiter rescued 4 of the agent's rejects → agent screening too hard.
    for i in range(4):
        _decision(db, org, role, pool[i], decision_type="reject", status="overridden",
                  key=f"ov{i}")
    db.commit()

    out = health.role_health_check(db, role)
    top = out["top_finding"]
    assert top["type"] == "calibration_drift"
    assert top["direction"] == "too_strict"
    assert top["overridden_rejects"] == 4 and top["overridden_advances"] == 0


def test_override_pattern_flags_too_loose(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    pool = _balanced_pool(db, org, role, n=10)
    for i in range(4):
        _decision(db, org, role, pool[i], decision_type="advance_to_interview",
                  status="overridden", key=f"ov{i}")
    db.commit()

    out = health.role_health_check(db, role)
    top = out["top_finding"]
    assert top["type"] == "calibration_drift"
    assert top["direction"] == "too_loose"


def test_few_overrides_below_threshold_not_flagged(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    pool = _balanced_pool(db, org, role, n=10)
    _decision(db, org, role, pool[0], decision_type="reject", status="overridden", key="o1")
    _decision(db, org, role, pool[1], decision_type="reject", status="overridden", key="o2")
    db.commit()

    out = health.role_health_check(db, role)
    assert not any(f["type"] == "calibration_drift" for f in out["findings"])


def test_calibration_outranks_threshold(db):
    """When both fire, the override signal leads."""
    org = _org(db)
    role = _role(db, org, threshold=70)
    _app(db, org, role, score=85, name="Top")
    lows = [_app(db, org, role, score=40, name=f"Low{i}") for i in range(9)]
    for i in range(4):  # strict-leaning override pattern
        _decision(db, org, role, lows[i], decision_type="reject", status="overridden",
                  key=f"ov{i}")
    db.commit()

    out = health.role_health_check(db, role)
    assert out["top_finding"]["type"] == "calibration_drift"
    types = {f["type"] for f in out["findings"]}
    assert "threshold_too_strict" in types  # still surfaced, just ranked lower


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------


def test_stale_scores_flagged(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    _balanced_pool(db, org, role, n=8)
    # Two candidates carry an OLD-engine score (needs cv_match_score set).
    _app(db, org, role, score=75, name="Old1", engine="1.18.0", cv_match_score=75)
    _app(db, org, role, score=72, name="Old2", engine="1.18.0", cv_match_score=72)
    db.commit()

    out = health.role_health_check(db, role)
    f = next(f for f in out["findings"] if f["type"] == "stale_scores")
    assert f["stale_count"] == 2


def test_decision_backlog_flagged(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    pool = _balanced_pool(db, org, role, n=12)  # 6/6 → no threshold finding
    for i in range(10):
        _decision(db, org, role, pool[i], decision_type="reject", status="pending",
                  key=f"p{i}")
    db.commit()

    out = health.role_health_check(db, role)
    f = next(f for f in out["findings"] if f["type"] == "decision_backlog")
    assert f["pending"] == 10


# ---------------------------------------------------------------------------
# Tool wiring
# ---------------------------------------------------------------------------


def test_dispatch_role_health_check(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    _balanced_pool(db, org, role, n=10)
    db.commit()
    user = _user(db, org)

    out = tools.dispatch_tool("role_health_check", {}, db=db, role=role, user=user)
    assert out["type"] == "role_health"
    assert "findings" in out and "all_clear" in out
