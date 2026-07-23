"""P2 core analytics: pipeline funnel + time-to-fill (service + API)."""
from datetime import datetime, timedelta, timezone

from app.domains.assessments_runtime.pipeline_analytics_service import (
    pipeline_funnel,
    time_to_fill,
)
from app.models import Candidate, CandidateApplication, Organization, Role
from app.models.role import ROLE_KIND_SISTER
from app.models.sister_role_evaluation import SisterRoleEvaluation
from tests.conftest import auth_headers


def _org(db, slug):
    org = Organization(name="Acme", slug=slug)
    db.add(org)
    db.flush()
    return org


def _role(db, org, name="Eng"):
    role = Role(organization_id=org.id, name=name, source="manual")
    db.add(role)
    db.flush()
    return role


def _related_role(db, org, owner, name="Related"):
    role = Role(
        organization_id=org.id,
        name=name,
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(role)
    db.flush()
    return role


def _app(db, org, role, *, stage="applied", outcome="open", created_at=None, outcome_updated_at=None):
    cand = Candidate(organization_id=org.id, email=f"c{db.query(Candidate).count()}@t.test", full_name="C")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage=stage, application_outcome=outcome,
        source="manual",
    )
    if created_at is not None:
        app.created_at = created_at
    if outcome_updated_at is not None:
        app.application_outcome_updated_at = outcome_updated_at
    db.add(app)
    db.flush()
    return app


def _membership(
    db,
    org,
    role,
    app,
    *,
    stage="applied",
    outcome="open",
    created_at=None,
    outcome_updated_at=None,
    deleted_at=None,
):
    # A direct application in the related role is a complete logical
    # membership but is not automatically the shared ATS transport. Only an
    # application in the role's declared ATS owner may populate that optional
    # link; otherwise the membership remains deliberately transport-free.
    ats_application_id = (
        app.id
        if role.ats_owner_role_id is not None
        and int(app.role_id) == int(role.ats_owner_role_id)
        else None
    )
    row = SisterRoleEvaluation(
        organization_id=org.id,
        role_id=role.id,
        candidate_id=app.candidate_id,
        source_application_id=app.id,
        ats_application_id=ats_application_id,
        status="done",
        pipeline_stage=stage,
        application_outcome=outcome,
        membership_source="test_ground_truth",
        spec_fingerprint=f"spec-{role.id}-{app.id}",
        deleted_at=deleted_at,
    )
    if created_at is not None:
        row.created_at = created_at
    if outcome_updated_at is not None:
        row.application_outcome_updated_at = outcome_updated_at
    db.add(row)
    db.flush()
    return row


def test_pipeline_funnel_counts_by_stage_and_outcome(db):
    org = _org(db, "acme-pf")
    role = _role(db, org)
    _app(db, org, role, stage="applied", outcome="open")
    _app(db, org, role, stage="applied", outcome="open")
    _app(db, org, role, stage="review", outcome="open")
    _app(db, org, role, stage="advanced", outcome="hired")

    out = pipeline_funnel(db, org.id)
    assert out["total"] == 4
    by_slug = {s["slug"]: s["count"] for s in out["stages"]}
    assert by_slug["applied"] == 2
    assert by_slug["review"] == 1
    assert by_slug["advanced"] == 1
    # Stages come back in funnel order (canonical seed when unconfigured).
    slugs = [s["slug"] for s in out["stages"]]
    assert slugs.index("applied") < slugs.index("review") < slugs.index("advanced")
    assert out["outcomes"] == {"open": 3, "hired": 1}


def test_pipeline_funnel_reconciles_unknown_slug_to_other(db):
    org = _org(db, "acme-cfg")
    role = _role(db, org)
    _app(db, org, role, stage="applied")
    _app(db, org, role, stage="legacy_stage")  # slug outside the canonical funnel

    out = pipeline_funnel(db, org.id)
    # The funnel is the fixed canonical Tali stage set, in order.
    assert [s["slug"] for s in out["stages"][:5]] == [
        "applied", "invited", "in_assessment", "review", "advanced",
    ]
    # The unknown-slug application is surfaced under _other, not dropped.
    other = [s for s in out["stages"] if s["slug"] == "_other"]
    assert other and other[0]["count"] == 1
    assert out["total"] == 2


def test_pipeline_funnel_scopes_to_org_and_role(db):
    org = _org(db, "acme-scope")
    other_org = _org(db, "other-scope")
    role_a = _role(db, org, "A")
    role_b = _role(db, org, "B")
    _app(db, org, role_a, stage="applied")
    _app(db, org, role_b, stage="applied")
    _app(db, other_org, _role(db, other_org), stage="applied")

    assert pipeline_funnel(db, org.id)["total"] == 2
    assert pipeline_funnel(db, org.id, role_id=role_a.id)["total"] == 1


def test_pipeline_funnel_uses_logical_related_membership_truth(db):
    """Ground truth: each live (role, application) membership counts once."""

    org = _org(db, "acme-logical-funnel")
    owner = _role(db, org, "Owner")
    related = _related_role(db, org, owner)
    now = datetime.now(timezone.utc)

    snapshot = _app(db, org, owner, stage="advanced", outcome="rejected")
    _membership(db, org, related, snapshot, stage="review", outcome="open")

    direct = _app(db, org, related, stage="invited", outcome="open")
    _membership(db, org, related, direct, stage="in_assessment", outcome="hired")

    deleted_evidence = _app(db, org, owner, stage="applied", outcome="open")
    deleted_evidence.deleted_at = now
    _membership(db, org, related, deleted_evidence, stage="advanced", outcome="hired")

    deleted_membership_source = _app(db, org, owner, stage="applied", outcome="open")
    _membership(
        db,
        org,
        related,
        deleted_membership_source,
        stage="review",
        outcome="hired",
        deleted_at=now,
    )
    db.flush()

    owner_out = pipeline_funnel(db, org.id, role_id=owner.id)
    related_out = pipeline_funnel(db, org.id, role_id=related.id)
    org_out = pipeline_funnel(db, org.id)

    assert owner_out["total"] == 2
    assert owner_out["outcomes"] == {"rejected": 1, "open": 1}
    assert related_out["total"] == 3
    assert related_out["outcomes"] == {"open": 1, "hired": 2}
    related_stages = {row["slug"]: row["count"] for row in related_out["stages"]}
    assert related_stages["review"] == 1
    assert related_stages["in_assessment"] == 1
    assert related_stages["advanced"] == 1

    # Owner + snapshot membership are independent; direct app + evaluation is
    # one related membership; deleted evidence survives only through its live
    # related membership; a soft-deleted membership contributes nothing.
    assert org_out["total"] == 5
    assert org_out["outcomes"] == {"rejected": 1, "open": 2, "hired": 2}
    assert pipeline_funnel(db, org.id, role_id=999_999)["total"] == 0


def test_time_to_fill_over_hired_applications(db):
    org = _org(db, "acme-ttf")
    role = _role(db, org)
    now = datetime.now(timezone.utc)
    # Two hired applications: 10 and 20 days from application to hired.
    for days in (10, 20):
        _app(
            db, org, role, outcome="hired",
            created_at=now - timedelta(days=days), outcome_updated_at=now,
        )
    # An open (not-yet-hired) application is ignored.
    _app(db, org, role, outcome="open", created_at=now - timedelta(days=99),
         outcome_updated_at=now)
    db.flush()

    out = time_to_fill(db, org.id)
    assert out["overall"]["count"] == 2
    assert out["overall"]["avg"] == 15.0
    assert out["overall"]["min"] == 10.0 and out["overall"]["max"] == 20.0
    assert len(out["by_role"]) == 1 and out["by_role"][0]["role_id"] == role.id


def test_time_to_fill_uses_each_logical_memberships_own_timestamps(db):
    org = _org(db, "acme-logical-ttf")
    owner = _role(db, org, "Owner")
    related = _related_role(db, org, owner)
    now = datetime.now(timezone.utc)

    shared = _app(
        db,
        org,
        owner,
        outcome="hired",
        created_at=now - timedelta(days=30),
        outcome_updated_at=now - timedelta(days=20),
    )
    _membership(
        db,
        org,
        related,
        shared,
        outcome="hired",
        created_at=now - timedelta(days=7),
        outcome_updated_at=now,
    )

    direct = _app(
        db,
        org,
        related,
        outcome="open",
        created_at=now - timedelta(days=100),
        outcome_updated_at=now,
    )
    _membership(
        db,
        org,
        related,
        direct,
        outcome="hired",
        created_at=now - timedelta(days=4),
        outcome_updated_at=now,
    )

    deleted_evidence = _app(db, org, owner, outcome="open")
    deleted_evidence.deleted_at = now
    _membership(
        db,
        org,
        related,
        deleted_evidence,
        outcome="hired",
        created_at=now - timedelta(days=2),
        outcome_updated_at=now,
    )
    db.flush()

    owner_out = time_to_fill(db, org.id, role_id=owner.id)
    related_out = time_to_fill(db, org.id, role_id=related.id)
    org_out = time_to_fill(db, org.id)

    assert owner_out["overall"]["count"] == 1
    assert owner_out["overall"]["avg"] == 10.0
    assert related_out["overall"]["count"] == 3
    assert related_out["overall"]["avg"] == 4.3
    assert org_out["overall"]["count"] == 4
    assert org_out["overall"]["avg"] == 5.8
    assert {row["role_id"] for row in org_out["by_role"]} == {
        owner.id,
        related.id,
    }


def test_analytics_endpoints_require_auth_and_return_shape(client, db):
    headers, _ = auth_headers(client)
    r = client.get("/api/v1/analytics/pipeline-funnel", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "stages" in body and "outcomes" in body and "total" in body

    r = client.get("/api/v1/analytics/time-to-fill", headers=headers)
    assert r.status_code == 200, r.text
    assert "overall" in r.json() and "by_role" in r.json()

    # Unauthenticated is rejected.
    assert client.get("/api/v1/analytics/pipeline-funnel").status_code == 401
