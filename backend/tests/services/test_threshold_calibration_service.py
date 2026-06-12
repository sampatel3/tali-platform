"""Threshold-calibration orchestration: shadow-by-default, cold-start blocks
auto-apply, and the learned threshold drives resolve_role_fit_threshold once
activated."""

import itertools

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.threshold_calibration import (
    STATUS_ACTIVE,
    STATUS_PROPOSED,
    ThresholdCalibration,
)
from app.services.auto_threshold_service import resolve_role_fit_threshold
from app.services.threshold_calibration import service as svc

_ctr = itertools.count(1)


def _seed(db, *, auto_apply=False, mode="auto"):
    ws = {"decision_policy_auto_apply": True} if auto_apply else {}
    org = Organization(name="O", slug=f"o-{next(_ctr)}", workspace_settings=ws)
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id, name="R", source="manual",
        agentic_mode_enabled=True, auto_reject_threshold_mode=mode,
        score_threshold=None,
    )
    db.add(role)
    db.flush()
    db.commit()
    return org, role


def _app(db, org, role, *, score, positive):
    n = next(_ctr)
    cand = Candidate(organization_id=org.id, email=f"c{n}@x.test", full_name=f"C{n}")
    db.add(cand)
    db.flush()
    a = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied",
        pipeline_stage=("advanced" if positive else "applied"),
        pipeline_stage_source="recruiter",
        application_outcome=("open" if positive else "rejected"),
        source="manual", cv_text="x", cv_match_score=float(score),
    )
    db.add(a)
    db.commit()


def _seed_separable(db, org, role):
    for s in [72, 75, 78, 80, 82, 85, 88, 90, 73, 76]:   # 10 positives >= 72
        _app(db, org, role, score=s, positive=True)
    for s in [10, 20, 30, 35, 40] * 6:                   # 30 negatives <= 40
        _app(db, org, role, score=s, positive=False)


def test_proposes_shadow_on_cold_start(db):
    org, role = _seed(db, auto_apply=False)
    _seed_separable(db, org, role)
    summary = svc.run_for_org(db, organization_id=org.id)
    db.commit()
    assert summary["org_proposed"] is True
    rows = (
        db.query(ThresholdCalibration)
        .filter(ThresholdCalibration.organization_id == org.id,
                ThresholdCalibration.role_id.is_(None))
        .all()
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.status == STATUS_PROPOSED           # shadow, never live until activated
    assert r.bias_gate_cold_start is True
    assert 50 <= r.learned_threshold <= 72       # separating cut, abs-clamped to >= 50


def test_cold_start_blocks_auto_apply(db):
    org, role = _seed(db, auto_apply=True)        # auto-apply ON
    _seed_separable(db, org, role)
    svc.run_for_org(db, organization_id=org.id)
    db.commit()
    actives = (
        db.query(ThresholdCalibration)
        .filter(ThresholdCalibration.organization_id == org.id,
                ThresholdCalibration.status == STATUS_ACTIVE)
        .count()
    )
    assert actives == 0                           # no protected holdout => never auto-activates


def test_resolve_uses_learned_after_activation(db):
    org, role = _seed(db, auto_apply=False, mode="auto")
    _seed_separable(db, org, role)
    svc.run_for_org(db, organization_id=org.id)
    db.commit()
    prop = (
        db.query(ThresholdCalibration)
        .filter(ThresholdCalibration.organization_id == org.id,
                ThresholdCalibration.role_id.is_(None),
                ThresholdCalibration.status == STATUS_PROPOSED)
        .one()
    )
    svc.activate(db, prop)
    db.commit()
    assert resolve_role_fit_threshold(db, role=role) == float(prop.learned_threshold)


def test_below_floor_no_proposal(db):
    org, role = _seed(db)
    # only 3 positives — below the floor
    for s in [80, 85, 90]:
        _app(db, org, role, score=s, positive=True)
    for s in [10, 20, 30, 40] * 6:
        _app(db, org, role, score=s, positive=False)
    summary = svc.run_for_org(db, organization_id=org.id)
    db.commit()
    assert summary["org_proposed"] is False
    assert db.query(ThresholdCalibration).filter_by(organization_id=org.id).count() == 0
