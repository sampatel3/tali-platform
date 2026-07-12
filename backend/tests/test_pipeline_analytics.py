"""P2 core analytics: pipeline funnel + time-to-fill (service + API)."""
from datetime import datetime, timedelta, timezone

from app.domains.assessments_runtime.pipeline_analytics_service import (
    pipeline_funnel,
    time_to_fill,
)
from app.models import Candidate, CandidateApplication, Organization, Role
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
