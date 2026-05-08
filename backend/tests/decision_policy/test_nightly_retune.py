"""nightly_retune: writes inactive revision by default; auto-applies opt-in."""

from __future__ import annotations

from datetime import datetime, timezone

from app.decision_policy.feedback_aggregator import AggregatedSignals, Signal
from app.decision_policy.nightly_retune import run_for_org
from app.decision_policy.retuner import HeuristicRetuner
from app.models.agent_run import AgentRun
from app.models.decision_policy import DecisionPolicy
from sqlalchemy import event

from .conftest import bootstrap, make_org, make_role


_BIG_PK_COUNTERS_NIGHTLY: dict[str, int] = {"agent_runs": 0}


def _assign(mapper, connection, target):  # pragma: no cover — SQLA hook
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS_NIGHTLY:
        _BIG_PK_COUNTERS_NIGHTLY[table] += 1
        target.id = _BIG_PK_COUNTERS_NIGHTLY[table]


event.listen(AgentRun, "before_insert", _assign)


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


def test_auto_apply_flips_activation(db):
    org = make_org(db)
    org.workspace_settings = {"decision_policy_auto_apply": True}
    db.flush()
    role = make_role(db, org=org)
    bootstrap(db, org)
    _add_recent_run(db, org=org, role=role)

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
        db, organization_id=int(org.id), retuner=_StubRetuner()
    )
    assert result.activated is True

    db.refresh(old_active)
    assert old_active.deactivated_at is not None
    new_policy = (
        db.query(DecisionPolicy).filter(DecisionPolicy.id == result.policy_id).one()
    )
    assert new_policy.activated_at is not None
