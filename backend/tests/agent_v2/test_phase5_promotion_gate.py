"""Phase 5 — bias audit, shadow mode, and the promotion gate.

These tests exercise the gate components against in-memory models so
the ML pipeline isn't a dependency. The gate's compliance configuration
(D2 / D5 / D7) is treated as a YAML-loaded default with engineering
fallback values.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import event

from app.decision_policy import (
    bias_audit,
    promotion_gate,
    shadow_mode,
)
from app.decision_policy.fitted_policy import FittedModel
from app.models.organization import Organization
from app.models.policy_version import PolicyVersion
from app.models.promotion_gate import (
    BiasAuditResult,
    GoldEvalExample,
    ShadowRun,
)
from app.models.role import Role


_BIG_PK_COUNTERS: dict[str, int] = {
    "policy_versions": 0,
    "bias_audit_results": 0,
    "shadow_runs": 0,
    "gold_eval_examples": 0,
}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(PolicyVersion, "before_insert", _assign_big_pk)
event.listen(BiasAuditResult, "before_insert", _assign_big_pk)
event.listen(ShadowRun, "before_insert", _assign_big_pk)
event.listen(GoldEvalExample, "before_insert", _assign_big_pk)


# ---------------------------------------------------------------------------
# bias_audit
# ---------------------------------------------------------------------------


def test_bias_audit_passes_when_selection_rates_balanced():
    model = FittedModel(coefs={"x": 1.0}, intercept=0.0)
    # 30 examples per segment, similar distributions.
    examples = []
    for i in range(30):
        examples.append(bias_audit.AuditExample(
            features={"x": (i % 10) / 10.0},
            label=1.0 if i % 2 == 0 else 0.0,
            segments={"gender": "F"},
        ))
        examples.append(bias_audit.AuditExample(
            features={"x": (i % 10) / 10.0},
            label=1.0 if i % 2 == 0 else 0.0,
            segments={"gender": "M"},
        ))
    thresholds = bias_audit.BiasThresholds(protected_attributes=("gender",))
    metrics, violations = bias_audit.audit(
        model=model, examples=examples, thresholds=thresholds
    )
    assert violations == []
    assert "gender" in metrics


def test_bias_audit_flags_disparate_impact():
    # Female segment is systematically pushed below threshold by the
    # model (sigmoid(-5) ≈ 0.007 → not selected); male segment is
    # pushed above (sigmoid(+5) ≈ 0.99 → all selected). DIR = 0.
    model = FittedModel(coefs={"x": 5.0}, intercept=0.0)
    examples = [
        bias_audit.AuditExample(features={"x": -1.0}, label=0.0, segments={"gender": "F"})
        for _ in range(20)
    ] + [
        bias_audit.AuditExample(features={"x": 1.0}, label=1.0, segments={"gender": "M"})
        for _ in range(20)
    ]
    thresholds = bias_audit.BiasThresholds(protected_attributes=("gender",))
    _, violations = bias_audit.audit(
        model=model, examples=examples, thresholds=thresholds
    )
    assert any(v["kind"] == "disparate_impact" for v in violations)


def test_bias_audit_handles_single_segment_gracefully():
    model = FittedModel(coefs={"x": 1.0}, intercept=0.0)
    examples = [
        bias_audit.AuditExample(features={"x": 0.5}, label=1.0, segments={"gender": "F"})
        for _ in range(10)
    ]
    thresholds = bias_audit.BiasThresholds(protected_attributes=("gender",))
    metrics, violations = bias_audit.audit(
        model=model, examples=examples, thresholds=thresholds
    )
    # Only one segment → not measurable, not a blocking violation.
    assert metrics["gender"]["status"] == "insufficient_segments"
    assert violations == []


def test_load_thresholds_returns_defaults_when_yaml_missing():
    thresholds = bias_audit.load_thresholds(path="/tmp/definitely-not-a-real-path.yaml")
    assert thresholds.disparate_impact_ratio_min == 0.80


def test_load_thresholds_reads_repo_yaml():
    # The actual file we shipped should parse cleanly when PyYAML is available.
    try:
        import yaml  # noqa: F401
    except Exception:
        return  # skip when PyYAML isn't installed in this env
    thresholds = bias_audit.load_thresholds()
    assert thresholds.disparate_impact_ratio_min == 0.80
    assert "gender" in thresholds.protected_attributes


# ---------------------------------------------------------------------------
# shadow_mode
# ---------------------------------------------------------------------------


def test_shadow_run_records_disagreements(db):
    org = Organization(name="ShadowOrg", slug=f"s-{id(db)}")
    db.add(org); db.flush()
    candidate = PolicyVersion(
        organization_id=org.id, role_id=None, model_kind="logistic_pooled",
        model_json={"coefs": {"x": 1.0}}, status="candidate",
    )
    db.add(candidate); db.flush()
    run = shadow_mode.open_shadow_run(db, candidate=candidate, live=None)
    assert candidate.status == "shadow"

    # Three decisions, one disagreement.
    shadow_mode.record_shadow_decision(db, shadow_run=run, live_prediction=0.8, candidate_prediction=0.7)
    shadow_mode.record_shadow_decision(db, shadow_run=run, live_prediction=0.2, candidate_prediction=0.3)
    shadow_mode.record_shadow_decision(db, shadow_run=run, live_prediction=0.8, candidate_prediction=0.3)
    assert run.decisions_compared == 3
    assert run.disagreements == 1


def test_shadow_eligibility_high_volume(db):
    org = Organization(name="ShadowEligOrg", slug=f"se-{id(db)}")
    db.add(org); db.flush()
    candidate = PolicyVersion(
        organization_id=org.id, role_id=None, model_kind="logistic_pooled",
        model_json={}, status="candidate",
    )
    db.add(candidate); db.flush()
    run = shadow_mode.open_shadow_run(db, candidate=candidate, live=None)
    run.decisions_compared = 200
    assert shadow_mode.is_eligible_for_conclusion(run, role_volume="high") is True
    run.decisions_compared = 50
    # 50 decisions, but the started_at is recent → not eligible.
    assert shadow_mode.is_eligible_for_conclusion(run, role_volume="high") is False


def test_conclude_shadow_run_produces_summary(db):
    org = Organization(name="ConcludeOrg", slug=f"c-{id(db)}")
    db.add(org); db.flush()
    candidate = PolicyVersion(
        organization_id=org.id, role_id=None, model_kind="logistic_pooled",
        model_json={}, status="candidate",
    )
    db.add(candidate); db.flush()
    run = shadow_mode.open_shadow_run(db, candidate=candidate, live=None)
    # Two decisions with realised outcomes; candidate was right both times,
    # live was right once.
    shadow_mode.record_shadow_decision(db, shadow_run=run, live_prediction=0.4, candidate_prediction=0.8, realised_label=1.0)
    shadow_mode.record_shadow_decision(db, shadow_run=run, live_prediction=0.6, candidate_prediction=0.2, realised_label=0.0)
    summary = shadow_mode.conclude_shadow_run(db, shadow_run=run)
    assert summary["decisions_compared"] == 2
    assert summary["candidate_correct"] == 2
    assert summary["live_correct"] == 0
    assert summary["candidate_accuracy_delta"] > 0.0


# ---------------------------------------------------------------------------
# promotion_gate
# ---------------------------------------------------------------------------


def _seed_for_gate(db):
    org = Organization(name="GateOrg", slug=f"g-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="Gate Engineer", source="manual",
        agentic_mode_enabled=True, monthly_usd_budget_cents=0,
    )
    db.add(role); db.flush()
    candidate = PolicyVersion(
        organization_id=org.id, role_id=role.id, model_kind="logistic_pooled",
        model_json={"coefs": {"x": 2.0}, "intercept": -1.0},
        status="candidate",
    )
    live = PolicyVersion(
        organization_id=org.id, role_id=role.id, model_kind="logistic_pooled",
        model_json={"coefs": {"x": 1.0}, "intercept": 0.0},
        status="live",
        promoted_at=datetime.now(timezone.utc) - timedelta(days=14),
    )
    db.add_all([candidate, live]); db.flush()

    # Seed a small gold eval set.
    for i in range(8):
        db.add(GoldEvalExample(
            organization_id=org.id, role_id=role.id,
            features_json={"x": i / 8.0},
            expected_outcome=1.0 if i >= 4 else 0.0,
        ))

    # Seed a concluded shadow run with healthy stats.
    run = ShadowRun(
        candidate_policy_version_id=candidate.id,
        live_policy_version_id=live.id,
        status="concluded",
        decisions_compared=20,
        disagreements=3,
        metrics_json={
            "summary": {
                "decisions_compared": 20,
                "disagreements": 3,
                "disagreement_rate": 0.15,
                "outcomes_observed": 5,
                "live_correct": 3,
                "candidate_correct": 4,
                "candidate_accuracy_delta": 0.2,
            }
        },
        ended_at=datetime.now(timezone.utc),
    )
    db.add(run); db.flush()
    return SimpleNamespace(org=org, role=role, candidate=candidate, live=live, run=run)


def test_gate_promotes_when_all_checks_pass(db):
    s = _seed_for_gate(db)
    # Audit examples — both segments have identical feature/label
    # distributions, so no parity gap can fire.
    examples = []
    pattern = [(0.1, 0.0), (0.4, 0.0), (0.6, 1.0), (0.9, 1.0)] * 6  # 24 each
    for x, label in pattern:
        examples.append(bias_audit.AuditExample(
            features={"x": x}, label=label, segments={"gender": "F"},
        ))
        examples.append(bias_audit.AuditExample(
            features={"x": x}, label=label, segments={"gender": "M"},
        ))
    result = promotion_gate.run_gate(
        db,
        candidate=s.candidate,
        live=s.live,
        audit_examples=examples,
        role_volume="high",
        thresholds=bias_audit.BiasThresholds(protected_attributes=("gender",)),
    )
    assert result.gold_passed is True
    assert result.bias_passed is True
    assert result.shadow_passed is True
    assert result.promoted is True
    db.refresh(s.candidate)
    db.refresh(s.live)
    assert s.candidate.status == "live"
    assert s.live.status == "archived"


def test_gate_blocks_when_bias_audit_fails(db):
    s = _seed_for_gate(db)
    # Skewed examples → disparate impact.
    examples = [
        bias_audit.AuditExample(features={"x": 0.0}, label=0.0, segments={"gender": "F"})
        for _ in range(20)
    ] + [
        bias_audit.AuditExample(features={"x": 1.0}, label=1.0, segments={"gender": "M"})
        for _ in range(20)
    ]
    result = promotion_gate.run_gate(
        db,
        candidate=s.candidate,
        live=s.live,
        audit_examples=examples,
        role_volume="high",
        thresholds=bias_audit.BiasThresholds(protected_attributes=("gender",)),
    )
    assert result.bias_passed is False
    assert result.promoted is False
    db.refresh(s.candidate)
    assert s.candidate.status == "rejected"


def test_gate_refuses_without_gold_set(db):
    org = Organization(name="NoGoldOrg", slug=f"ng-{id(db)}")
    db.add(org); db.flush()
    candidate = PolicyVersion(
        organization_id=org.id, role_id=None, model_kind="logistic_pooled",
        model_json={"coefs": {"x": 1.0}}, status="candidate",
    )
    db.add(candidate); db.flush()
    examples = [
        bias_audit.AuditExample(features={"x": 0.5}, label=1.0, segments={"gender": "F"})
        for _ in range(10)
    ]
    result = promotion_gate.run_gate(
        db, candidate=candidate, live=None, audit_examples=examples,
        thresholds=bias_audit.BiasThresholds(protected_attributes=("gender",)),
    )
    assert result.gold_passed is False
    assert result.promoted is False
