from __future__ import annotations

from datetime import datetime, timezone

from app.models.background_job_run import (
    JOB_KIND_PRE_SCREEN_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from scripts import fast_prescreen_roles


def _role(db, suffix: str) -> tuple[Organization, Role]:
    org = Organization(name=f"Org {suffix}", slug=f"prescreen-cli-{suffix}-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name=f"Role {suffix}",
        job_spec_text="Build reliable Python services",
    )
    db.add(role)
    db.flush()
    return org, role


def _application(db, org: Organization, role: Role, suffix: str):
    candidate = Candidate(
        organization_id=org.id,
        email=f"prescreen-cli-{suffix}-{id(db)}@example.test",
        full_name="CLI Candidate",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        application_outcome="open",
        cv_text="Python engineer",
    )
    db.add(application)
    db.flush()
    return application


def test_dispatch_persists_durable_intent_before_broker_publish(db, monkeypatch):
    org, role = _role(db, "order")
    _application(db, org, role, "order")
    db.commit()
    calls: list[tuple[str, object]] = []

    def create_run(**kwargs):
        calls.append(("create", kwargs))
        return 91

    monkeypatch.setattr(fast_prescreen_roles, "create_run", create_run)
    monkeypatch.setattr(
        fast_prescreen_roles,
        "dispatch_prescreen_batch_roots",
        lambda **kwargs: calls.append(("publish", kwargs)) or {"dispatch_errors": 0},
    )

    result = fast_prescreen_roles.dispatch_role(db, role_id=role.id, dry_run=False)

    assert result == {
        "status": "started",
        "role_id": role.id,
        "organization_id": org.id,
        "run_id": 91,
        "total": 1,
        "dispatch_recovering": False,
    }
    assert [name for name, _value in calls] == ["create", "publish"]
    create_kwargs = calls[0][1]
    assert create_kwargs["kind"] == JOB_KIND_PRE_SCREEN_BATCH
    assert create_kwargs["scope_kind"] == SCOPE_KIND_ROLE
    assert create_kwargs["scope_id"] == role.id
    assert create_kwargs["organization_id"] == org.id
    assert calls[1][1] == {"run_id": 91}


def test_initial_publish_failure_remains_recoverable_without_logging_provider_text(
    db,
    monkeypatch,
    caplog,
):
    org, role = _role(db, "broker")
    _application(db, org, role, "broker")
    db.commit()
    secret = "redis-url-with-password private-broker-body"
    monkeypatch.setattr(fast_prescreen_roles, "create_run", lambda **_kwargs: 92)
    monkeypatch.setattr(
        fast_prescreen_roles,
        "dispatch_prescreen_batch_roots",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    result = fast_prescreen_roles.dispatch_role(db, role_id=role.id, dry_run=False)

    assert result["status"] == "recovering"
    assert result["run_id"] == 92
    assert result["dispatch_recovering"] is True
    assert secret not in caplog.text
    assert "stage=root_claim error_type=RuntimeError" in caplog.text


def test_active_run_is_reused_without_duplicate_publish(db, monkeypatch):
    org, role = _role(db, "active")
    _application(db, org, role, "active")
    run = BackgroundJobRun(
        kind=JOB_KIND_PRE_SCREEN_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=org.id,
        status="running",
        counters={"total": 1},
    )
    db.add(run)
    db.commit()
    monkeypatch.setattr(
        fast_prescreen_roles,
        "create_run",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate run")),
    )
    monkeypatch.setattr(
        fast_prescreen_roles,
        "dispatch_prescreen_batch_roots",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate publish")),
    )

    result = fast_prescreen_roles.dispatch_role(db, role_id=role.id, dry_run=False)

    assert result == {
        "status": "already_running",
        "role_id": role.id,
        "organization_id": org.id,
        "run_id": run.id,
        "total": 1,
    }


def test_target_count_is_scoped_to_role_organization(db, monkeypatch):
    org, role = _role(db, "scope")
    other_org, _other_role = _role(db, "scope-other")
    _application(db, org, role, "correct")
    cross_tenant = _application(db, other_org, role, "wrong-org")
    assert cross_tenant.organization_id != role.organization_id
    db.commit()
    monkeypatch.setattr(fast_prescreen_roles, "create_run", lambda **_kwargs: 93)
    monkeypatch.setattr(fast_prescreen_roles, "dispatch_prescreen_batch_roots", lambda **_kwargs: {"dispatch_errors": 0})

    result = fast_prescreen_roles.dispatch_role(db, role_id=role.id, dry_run=False)

    assert result["total"] == 1


def test_dry_run_counts_without_creating_or_publishing(db, monkeypatch):
    org, role = _role(db, "dry")
    _application(db, org, role, "dry")
    db.commit()
    before = db.query(BackgroundJobRun).count()
    monkeypatch.setattr(
        fast_prescreen_roles,
        "create_run",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("dry run mutated")),
    )
    monkeypatch.setattr(
        fast_prescreen_roles,
        "dispatch_prescreen_batch_roots",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("dry run published")),
    )

    result = fast_prescreen_roles.dispatch_role(db, role_id=role.id, dry_run=True)

    assert result == {
        "status": "dry_run",
        "role_id": role.id,
        "organization_id": org.id,
        "total": 1,
    }
    assert db.query(BackgroundJobRun).count() == before


def test_finished_run_does_not_block_a_new_dispatch(db, monkeypatch):
    org, role = _role(db, "terminal")
    _application(db, org, role, "terminal")
    db.add(
        BackgroundJobRun(
            kind=JOB_KIND_PRE_SCREEN_BATCH,
            scope_kind=SCOPE_KIND_ROLE,
            scope_id=role.id,
            organization_id=org.id,
            status="completed",
            counters={"total": 1},
            finished_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    monkeypatch.setattr(fast_prescreen_roles, "create_run", lambda **_kwargs: 94)
    published: list[int] = []
    monkeypatch.setattr(
        fast_prescreen_roles,
        "dispatch_prescreen_batch_roots",
        lambda **kwargs: published.append(int(kwargs["run_id"])) or {"dispatch_errors": 0},
    )

    result = fast_prescreen_roles.dispatch_role(db, role_id=role.id, dry_run=False)

    assert result["status"] == "started"
    assert published == [94]
