"""Bootstrap is safe to re-run."""

from __future__ import annotations

from app.decision_policy.bootstrap import bootstrap_org
from app.models.decision_policy import DecisionPolicy
from app.models.rubric_revision import RubricRevision

from .conftest import make_org, make_role


def test_bootstrap_creates_one_org_default(db):
    org = make_org(db)
    policy = bootstrap_org(db, organization_id=int(org.id))
    assert policy.role_id is None
    assert policy.activated_at is not None
    assert policy.deactivated_at is None
    assert policy.policy_json["schema_version"] == "v1"


def test_bootstrap_is_idempotent(db):
    org = make_org(db)
    first = bootstrap_org(db, organization_id=int(org.id))
    second = bootstrap_org(db, organization_id=int(org.id))
    assert first.id == second.id
    assert (
        db.query(DecisionPolicy)
        .filter(DecisionPolicy.organization_id == org.id)
        .count()
        == 1
    )
    # Only one paired RubricRevision should have been created.
    assert (
        db.query(RubricRevision)
        .filter(RubricRevision.organization_id == org.id)
        .count()
        == 1
    )


def test_bootstrap_uses_median_role_score_threshold(db):
    org = make_org(db, default_score_threshold=50)
    make_role(db, org=org, score_threshold=60)
    make_role(db, org=org, score_threshold=70)
    make_role(db, org=org, score_threshold=80)
    policy = bootstrap_org(db, organization_id=int(org.id))
    role_fit_min = policy.policy_json["decision_points"]["send_assessment"][
        "thresholds"
    ]["role_fit_min"]
    # Median of [60, 70, 80] is 70.
    assert role_fit_min == 70.0


def test_bootstrap_falls_back_to_org_default(db):
    org = make_org(db, default_score_threshold=42)
    policy = bootstrap_org(db, organization_id=int(org.id))
    role_fit_min = policy.policy_json["decision_points"]["send_assessment"][
        "thresholds"
    ]["role_fit_min"]
    assert role_fit_min == 42.0


def test_bootstrap_falls_back_to_constant_when_nothing_configured(db):
    org = make_org(db)
    policy = bootstrap_org(db, organization_id=int(org.id))
    role_fit_min = policy.policy_json["decision_points"]["send_assessment"][
        "thresholds"
    ]["role_fit_min"]
    # DEFAULT_ORG_FALLBACK_SCORE_THRESHOLD = 65.0
    assert role_fit_min == 65.0
