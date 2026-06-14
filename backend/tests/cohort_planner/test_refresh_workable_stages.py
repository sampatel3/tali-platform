"""POST /workable/roles/{id}/refresh-stages — targeted on-demand stage sync."""

from __future__ import annotations

from app.domains.workable_sync import routes as wr
from app.models.user import User

from .conftest import make_world


def _user(db, org) -> User:
    u = User(
        email=f"rs-{id(db)}@x.test",
        hashed_password="x",
        full_name="RS",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
    )
    db.add(u)
    db.flush()
    return u


def _connect(org):
    org.workable_connected = True
    org.workable_access_token = "t"
    org.workable_subdomain = "sub"


def test_refresh_updates_only_changed_stages(db, monkeypatch):
    org, role, _, app = make_world(db)
    _connect(org)
    role.workable_job_id = "JOB123"
    app.workable_candidate_id = "cand-1"
    app.workable_stage = "Applied"
    db.flush()
    user = _user(db, org)

    monkeypatch.setattr(wr.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(wr.WorkableService, "__init__", lambda self, **k: None)
    monkeypatch.setattr(
        wr.WorkableService,
        "list_job_candidates",
        lambda self, job, **k: [{"id": "cand-1", "stage": "Technical Interview"}],
    )

    out = wr.refresh_role_workable_stages(int(role.id), db=db, current_user=user)
    assert out.job_linked is True
    assert out.checked == 1
    assert out.updated == 1
    db.refresh(app)
    assert app.workable_stage == "Technical Interview"


def test_refresh_noop_when_stage_already_matches(db, monkeypatch):
    org, role, _, app = make_world(db)
    _connect(org)
    role.workable_job_id = "JOB123"
    app.workable_candidate_id = "cand-1"
    app.workable_stage = "Technical Interview"
    db.flush()
    user = _user(db, org)
    monkeypatch.setattr(wr.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(wr.WorkableService, "__init__", lambda self, **k: None)
    monkeypatch.setattr(
        wr.WorkableService, "list_job_candidates",
        lambda self, job, **k: [{"id": "cand-1", "stage": "Technical Interview"}],
    )
    out = wr.refresh_role_workable_stages(int(role.id), db=db, current_user=user)
    assert out.updated == 0


def test_refresh_no_workable_job_link(db, monkeypatch):
    org, role, _, app = make_world(db)
    _connect(org)
    role.workable_job_id = None
    db.flush()
    user = _user(db, org)
    monkeypatch.setattr(wr.settings, "MVP_DISABLE_WORKABLE", False)
    out = wr.refresh_role_workable_stages(int(role.id), db=db, current_user=user)
    assert out.job_linked is False
    assert out.updated == 0
