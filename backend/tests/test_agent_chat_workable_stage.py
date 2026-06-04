"""The role agent can SEE + filter by the synced Workable stage.

Regression: the agent told a recruiter it "couldn't see" candidates in Workable's
"Final Interview" stage even though `workable_stage` is synced onto the application
and loaded into the agent's candidate rows — `list_candidates` was just dropping the
field before the LLM saw it, and Taali's `pipeline_stage` doesn't track Workable's
interview stages (a Final-Interview candidate can be Taali `pipeline_stage='applied'`).
"""
from __future__ import annotations

from app.agent_chat import tools
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


def _org(db) -> Organization:
    org = Organization(name="WK Org", slug=f"wk-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _user(db, org) -> User:
    u = User(
        email=f"wk-{id(db)}@x.test", hashed_password="x", full_name="Rec",
        organization_id=org.id, is_active=True, is_verified=True, is_superuser=False,
    )
    db.add(u)
    db.flush()
    return u


def _role(db, org) -> Role:
    role = Role(organization_id=org.id, name="AI Engineer", source="manual",
                score_threshold=70, agentic_mode_enabled=True)
    db.add(role)
    db.flush()
    return role


def _app(db, org, role, *, name, score, workable_stage, pipeline_stage="applied"):
    cand = Candidate(organization_id=org.id, email=f"{name}@x.test", full_name=name)
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage=pipeline_stage, pipeline_stage_source="recruiter",
        application_outcome="open", source="manual",
        pre_screen_score_100=score, workable_stage=workable_stage,
    )
    db.add(app)
    db.flush()
    return app


def test_list_candidates_exposes_workable_stage(db):
    org = _org(db); user = _user(db, org); role = _role(db, org)
    _app(db, org, role, name="Jojo", score=59, workable_stage="Final Interview", pipeline_stage="advanced")
    _app(db, org, role, name="Rahaf", score=56.9, workable_stage="Final Interview", pipeline_stage="applied")
    _app(db, org, role, name="Tariq", score=40, workable_stage="Technical Interview", pipeline_stage="applied")

    res = tools.dispatch_tool("list_candidates", {"bucket": "all"}, db=db, role=role, user=user)
    by_name = {c["name"]: c for c in res["candidates"]}
    assert by_name["Jojo"]["workable_stage"] == "Final Interview"
    assert by_name["Rahaf"]["workable_stage"] == "Final Interview"
    assert by_name["Tariq"]["workable_stage"] == "Technical Interview"


def test_list_candidates_filters_by_workable_stage(db):
    org = _org(db); user = _user(db, org); role = _role(db, org)
    _app(db, org, role, name="Jojo", score=59, workable_stage="Final Interview", pipeline_stage="advanced")
    _app(db, org, role, name="Rahaf", score=56.9, workable_stage="Final Interview", pipeline_stage="applied")
    _app(db, org, role, name="Tariq", score=40, workable_stage="Technical Interview", pipeline_stage="applied")

    # "final interview" (case-insensitive) → both, best score first — INCLUDING the
    # one Taali calls 'applied'. That's the whole point: pipeline_stage != workable_stage.
    res = tools.dispatch_tool(
        "list_candidates", {"workable_stage": "final interview"}, db=db, role=role, user=user
    )
    assert res["count"] == 2
    assert [c["name"] for c in res["candidates"]] == ["Jojo", "Rahaf"]


def test_overview_has_workable_stage_funnel(db):
    org = _org(db); user = _user(db, org); role = _role(db, org)
    _app(db, org, role, name="Jojo", score=59, workable_stage="Final Interview")
    _app(db, org, role, name="Rahaf", score=56.9, workable_stage="Final Interview")
    _app(db, org, role, name="Tariq", score=40, workable_stage="Technical Interview")
    _app(db, org, role, name="NoStage", score=30, workable_stage=None)

    res = tools.dispatch_tool("get_role_overview", {}, db=db, role=role, user=user)
    wf = res["workable_stage_funnel"]
    assert wf["Final Interview"] == 2
    assert wf["Technical Interview"] == 1
    assert wf["(unsynced)"] == 1
