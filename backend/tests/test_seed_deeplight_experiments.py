"""Tests for the deeplight A/B experiment seed script."""

from __future__ import annotations

from app.models.assessment_experiment import AssessmentExperiment, AssessmentExperimentArm
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.scripts.seed_deeplight_experiments import seed_deeplight_experiments


def _template(db, task_key: str) -> Task:
    t = Task(name=task_key, task_key=task_key, is_template=True, is_active=True, organization_id=None)
    db.add(t)
    db.flush()
    return t


def _setup(db):
    org = Organization(name="Deeplight FZE", slug=f"deeplight-{id(db)}", credits_balance=1000)
    db.add(org)
    db.flush()
    genai = Role(organization_id=org.id, name="GenAI Engineer", source="manual")
    data = Role(organization_id=org.id, name="Data Engineer", source="manual")
    db.add_all([genai, data])
    db.flush()
    for key in (
        "ai_eng_genai_production_readiness",
        "ai_eng_rag_eval_harness",
        "data_eng_aws_glue_pipeline_recovery",
        "data_eng_data_quality_contract_framework",
    ):
        _template(db, key)
    db.flush()
    return org, genai, data


def test_seed_creates_experiments_arms_and_links(db):
    org, genai, data = _setup(db)

    summary = seed_deeplight_experiments(db, apply=True, org_id=int(org.id))
    assert summary["experiments_created"] == 2
    assert summary["arms_upserted"] == 4
    assert summary["links_added"] == 4

    exps = db.query(AssessmentExperiment).filter(AssessmentExperiment.organization_id == org.id).all()
    by_role = {e.role_id: e for e in exps}
    assert genai.id in by_role and data.id in by_role
    genai_exp = by_role[genai.id]
    assert genai_exp.status == "active"
    arms = db.query(AssessmentExperimentArm).filter(AssessmentExperimentArm.experiment_id == genai_exp.id).all()
    assert {a.arm_key for a in arms} == {"A", "B"}
    # Both arm tasks are linked to the role.
    db.refresh(genai)
    linked_keys = {t.task_key for t in genai.tasks}
    assert {"ai_eng_genai_production_readiness", "ai_eng_rag_eval_harness"} <= linked_keys


def test_seed_is_idempotent(db):
    org, genai, data = _setup(db)
    seed_deeplight_experiments(db, apply=True, org_id=int(org.id))
    second = seed_deeplight_experiments(db, apply=True, org_id=int(org.id))
    assert second["experiments_created"] == 0
    assert second["experiments_updated"] == 2
    # No duplicate experiments or arms on re-run.
    assert db.query(AssessmentExperiment).filter(AssessmentExperiment.organization_id == org.id).count() == 2
    assert db.query(AssessmentExperimentArm).count() == 4


def test_seed_skips_when_role_ambiguous(db):
    org = Organization(name="Deeplight Two", slug=f"deeplight2-{id(db)}", credits_balance=1000)
    db.add(org)
    db.flush()
    # Two roles both matching the data matcher → ambiguous, should skip that experiment.
    db.add_all([
        Role(organization_id=org.id, name="Data Engineer", source="manual"),
        Role(organization_id=org.id, name="Senior Data Engineer", source="manual"),
    ])
    db.flush()
    for key in ("data_eng_aws_glue_pipeline_recovery", "data_eng_data_quality_contract_framework"):
        _template(db, key)
    db.flush()

    summary = seed_deeplight_experiments(db, apply=True, org_id=int(org.id))
    # genai role missing + data role ambiguous → both skipped.
    assert summary["experiments_created"] == 0
    assert summary["skipped"] == 2
