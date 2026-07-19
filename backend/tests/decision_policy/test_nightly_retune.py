"""nightly_retune: writes inactive revision by default; auto-applies opt-in.

Auto-apply removes the human approval click but NOT the safety gate: a
proposal is only flipped live when the promotion gate's synchronous
checks (non-bypassable bias audit + gold-set log-loss) pass against the
org's latest fitted candidate. A failing or cold-start gate leaves the
proposal inactive, identical to the default path.
"""

from __future__ import annotations


from app.decision_policy.bias_audit import AuditExample
from app.decision_policy.nightly_retune import run_for_org
from app.models.agent_run import AgentRun
from app.models.decision_policy import DecisionPolicy
from app.models.policy_version import PolicyVersion
from app.models.promotion_gate import GoldEvalExample

from .conftest import bootstrap, make_org, make_role


# A fitted candidate whose decision boundary is x = 0.5 (2x - 1 = 0):
# below it candidates fall under the 0.5 selection threshold, above it
# they clear it. Used to make balanced vs skewed audit holdouts behave
# predictably.
_CANDIDATE_MODEL_JSON = {"coefs": {"x": 2.0}, "intercept": -1.0}


def _make_fitted_candidate(db, *, org) -> PolicyVersion:
    pv = PolicyVersion(
        organization_id=org.id,
        role_id=None,
        model_kind="logistic_pooled",
        model_json=_CANDIDATE_MODEL_JSON,
        status="candidate",
    )
    db.add(pv)
    db.flush()
    return pv


def _seed_gold_set(db, *, org, n: int = 8) -> None:
    for i in range(n):
        db.add(
            GoldEvalExample(
                organization_id=org.id,
                role_id=None,
                features_json={"x": i / float(n)},
                expected_outcome=1.0 if i >= n // 2 else 0.0,
            )
        )
    db.flush()


def _balanced_audit_examples() -> list[AuditExample]:
    """Two segments with identical feature/label distributions → no gap."""
    examples: list[AuditExample] = []
    pattern = [(0.1, 0.0), (0.4, 0.0), (0.6, 1.0), (0.9, 1.0)] * 6  # 24 each
    for x, label in pattern:
        examples.append(
            AuditExample(features={"x": x}, label=label, segments={"gender": "F"})
        )
        examples.append(
            AuditExample(features={"x": x}, label=label, segments={"gender": "M"})
        )
    return examples


def _skewed_audit_examples() -> list[AuditExample]:
    """Female segment pushed below threshold, male above → disparate impact."""
    return [
        AuditExample(features={"x": 0.0}, label=0.0, segments={"gender": "F"})
        for _ in range(20)
    ] + [
        AuditExample(features={"x": 1.0}, label=1.0, segments={"gender": "M"})
        for _ in range(20)
    ]


def _enable_auto_apply(db, org) -> None:
    org.workspace_settings = {"decision_policy_auto_apply": True}
    db.flush()


def _add_recent_run(db, *, org, role) -> AgentRun:
    run = AgentRun(
        organization_id=org.id,
        role_id=role.id,
        trigger="manual",
        status="succeeded",
        model_version="m",
        prompt_version="p",
    )
    db.add(run)
    db.flush()
    return run


class _StubRetuner:
    """Returns a fixed proposal with a single shift."""

    def propose(self, current, signals):
        from app.decision_policy.retuner import RetuneProposal, _Shift

        new = current.model_dump()
        new["decision_points"]["send_assessment"]["thresholds"]["role_fit_min"] = (
            new["decision_points"]["send_assessment"]["thresholds"]["role_fit_min"] - 5
        )
        new.setdefault("metadata", {})["notes"] = "stub"
        return RetuneProposal(
            new_policy_json=new,
            shifts=[
                _Shift(
                    field_path="send_assessment.thresholds.role_fit_min",
                    old_value=65.0,
                    new_value=60.0,
                    cause_summary="stub",
                )
            ],
            signal_count=10,
            weighted_signal_total=10.0,
        )


def test_no_recent_runs_skips(db):
    org = make_org(db)
    bootstrap(db, org)
    result = run_for_org(db, organization_id=int(org.id))
    assert result.skipped_reason == "no agent runs in the last 7 days"
    assert result.policy_id is None


def test_inactive_revision_written_by_default(db):
    org = make_org(db)
    role = make_role(db, org=org)
    bootstrap(db, org)
    _add_recent_run(db, org=org, role=role)
    result = run_for_org(
        db, organization_id=int(org.id), retuner=_StubRetuner()
    )
    assert result.revision_id is not None
    assert result.policy_id is not None
    assert result.activated is False
    new_policy = (
        db.query(DecisionPolicy).filter(DecisionPolicy.id == result.policy_id).one()
    )
    assert new_policy.activated_at is None


def test_auto_apply_activates_when_gate_passes(db):
    org = make_org(db)
    _enable_auto_apply(db, org)
    role = make_role(db, org=org)
    bootstrap(db, org)
    _add_recent_run(db, org=org, role=role)
    # Safety inputs the gate needs: a fitted candidate to audit, a gold
    # set to score, and a balanced (non-discriminatory) audit holdout.
    _make_fitted_candidate(db, org=org)
    _seed_gold_set(db, org=org)

    # Old policy is the bootstrap one — capture it.
    old_active = (
        db.query(DecisionPolicy)
        .filter(
            DecisionPolicy.organization_id == org.id,
            DecisionPolicy.role_id.is_(None),
            DecisionPolicy.activated_at.isnot(None),
            DecisionPolicy.deactivated_at.is_(None),
        )
        .one()
    )

    result = run_for_org(
        db,
        organization_id=int(org.id),
        retuner=_StubRetuner(),
        audit_examples=_balanced_audit_examples(),
    )
    assert result.activated is True
    assert result.gate_blocked_reason is None

    db.refresh(old_active)
    assert old_active.deactivated_at is not None
    new_policy = (
        db.query(DecisionPolicy).filter(DecisionPolicy.id == result.policy_id).one()
    )
    assert new_policy.activated_at is not None


def test_auto_apply_blocked_by_failing_bias_audit(db):
    org = make_org(db)
    _enable_auto_apply(db, org)
    role = make_role(db, org=org)
    bootstrap(db, org)
    _add_recent_run(db, org=org, role=role)
    _make_fitted_candidate(db, org=org)
    _seed_gold_set(db, org=org)

    old_active = (
        db.query(DecisionPolicy)
        .filter(
            DecisionPolicy.organization_id == org.id,
            DecisionPolicy.role_id.is_(None),
            DecisionPolicy.activated_at.isnot(None),
            DecisionPolicy.deactivated_at.is_(None),
        )
        .one()
    )

    result = run_for_org(
        db,
        organization_id=int(org.id),
        retuner=_StubRetuner(),
        audit_examples=_skewed_audit_examples(),
    )

    # The proposal is still written — just inactive, exactly like the
    # non-auto-apply path — and the bias failure is recorded.
    assert result.activated is False
    assert result.policy_id is not None
    assert result.gate_blocked_reason is not None
    assert "bias_audit_failed" in result.gate_blocked_reason

    db.refresh(old_active)
    assert old_active.deactivated_at is None  # prior policy untouched
    new_policy = (
        db.query(DecisionPolicy).filter(DecisionPolicy.id == result.policy_id).one()
    )
    assert new_policy.activated_at is None


def test_auto_apply_cold_start_no_fitted_candidate(db):
    org = make_org(db)
    _enable_auto_apply(db, org)
    role = make_role(db, org=org)
    bootstrap(db, org)
    _add_recent_run(db, org=org, role=role)
    # No fitted PolicyVersion and no audit holdout → nothing to judge.

    result = run_for_org(
        db, organization_id=int(org.id), retuner=_StubRetuner()
    )

    assert result.activated is False
    assert result.policy_id is not None
    assert result.gate_blocked_reason is not None
    assert "cold start" in result.gate_blocked_reason
    new_policy = (
        db.query(DecisionPolicy).filter(DecisionPolicy.id == result.policy_id).one()
    )
    assert new_policy.activated_at is None


def test_auto_apply_cold_start_no_gold_set(db):
    org = make_org(db)
    _enable_auto_apply(db, org)
    role = make_role(db, org=org)
    bootstrap(db, org)
    _add_recent_run(db, org=org, role=role)
    # Candidate + a clean audit holdout, but no gold set seeded → the
    # gold-set check can't run, so we refuse to activate into the vacuum.
    _make_fitted_candidate(db, org=org)

    result = run_for_org(
        db,
        organization_id=int(org.id),
        retuner=_StubRetuner(),
        audit_examples=_balanced_audit_examples(),
    )

    assert result.activated is False
    assert result.gate_blocked_reason is not None
    assert "gold_eval_failed" in result.gate_blocked_reason
