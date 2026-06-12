"""Admin review routes: admin-gate, org-scoping, activate-supersedes, discard."""

import itertools
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.organization import Organization
from app.models.threshold_calibration import (
    STATUS_ACTIVE,
    STATUS_DISCARDED,
    STATUS_PROPOSED,
    STATUS_SUPERSEDED,
    ThresholdCalibration,
)
from app.services.threshold_calibration import routes

_ctr = itertools.count(1)


def _org(db):
    org = Organization(name="O", slug=f"o-{next(_ctr)}")
    db.add(org)
    db.commit()
    return org


def _admin(org):
    return SimpleNamespace(organization_id=org.id, is_superuser=True)


def _propose(db, org, *, role_id=None, threshold=60.0):
    row = ThresholdCalibration(
        organization_id=org.id, role_id=role_id,
        scope=("role" if role_id else "org"),
        learned_threshold=threshold, metric_name="youden_j", metric_value=0.5,
        status=STATUS_PROPOSED, n_positive=10, n_negative=30,
        pooled_from_org=False, bias_gate_cold_start=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_requires_admin(db):
    org = _org(db)
    with pytest.raises(HTTPException) as e:
        routes.list_active(user=SimpleNamespace(organization_id=org.id, is_superuser=False), db=db)
    assert e.value.status_code == 403


def test_activate_supersedes_prior_active(db):
    org = _org(db)
    admin = _admin(org)
    p1 = _propose(db, org, threshold=60)
    routes.activate_calibration(calibration_id=p1.id, user=admin, db=db)
    db.refresh(p1)
    assert p1.status == STATUS_ACTIVE
    p2 = _propose(db, org, threshold=65)
    routes.activate_calibration(calibration_id=p2.id, user=admin, db=db)
    db.refresh(p1)
    db.refresh(p2)
    assert p2.status == STATUS_ACTIVE
    assert p1.status == STATUS_SUPERSEDED


def test_activate_already_active_conflicts(db):
    org = _org(db)
    admin = _admin(org)
    p = _propose(db, org)
    routes.activate_calibration(calibration_id=p.id, user=admin, db=db)
    with pytest.raises(HTTPException) as e:
        routes.activate_calibration(calibration_id=p.id, user=admin, db=db)
    assert e.value.status_code == 409


def test_discard(db):
    org = _org(db)
    p = _propose(db, org)
    routes.discard_calibration(calibration_id=p.id, user=_admin(org), db=db)
    db.refresh(p)
    assert p.status == STATUS_DISCARDED


def test_org_scoping_blocks_cross_org(db):
    org1, org2 = _org(db), _org(db)
    p = _propose(db, org1)
    with pytest.raises(HTTPException) as e:
        routes.activate_calibration(
            calibration_id=p.id, user=_admin(org2), db=db
        )
    assert e.value.status_code == 404
