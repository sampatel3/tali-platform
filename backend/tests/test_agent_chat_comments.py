"""The role agent can SEE + filter by synced Workable recruiter comments.

Regression: a recruiter asked for "the top 5 in technical interview with a 'Yes'
comment from Workable" and the agent said it "doesn't have a tool that can filter
by Workable comments". The comments ARE synced (onto Candidate.workable_comments)
— the agent just never loaded them. `list_candidates` now exposes them
(include_comments) and filters on them (comment_contains), reusing the canonical
`workable_recruiter_comments` serializer.
"""
from __future__ import annotations

from app.agent_chat import tools
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


def _org(db) -> Organization:
    org = Organization(name="CM Org", slug=f"cm-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _user(db, org) -> User:
    u = User(
        email=f"cm-{id(db)}@x.test", hashed_password="x", full_name="Rec",
        organization_id=org.id, is_active=True, is_verified=True, is_superuser=False,
    )
    db.add(u)
    db.flush()
    return u


def _role(db, org) -> Role:
    role = Role(organization_id=org.id, name="Azure Engineer", source="workable",
                score_threshold=70, agentic_mode_enabled=True)
    db.add(role)
    db.flush()
    return role


def _app(db, org, role, *, name, score, workable_stage="Technical Interview", comments=None):
    cand = Candidate(organization_id=org.id, email=f"{name}@x.test", full_name=name)
    if comments is not None:
        # Shape mirrors a Workable activities-feed comment entry.
        cand.workable_comments = [
            {"member": {"name": "Recruiter"}, "body": b, "created_at": f"2026-06-0{i + 1}T10:00:00Z"}
            for i, b in enumerate(comments)
        ]
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="applied", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual",
        pre_screen_score_100=score, workable_stage=workable_stage,
    )
    db.add(app)
    db.flush()
    return app


def test_list_candidates_filters_by_comment_whole_word(db):
    org = _org(db); user = _user(db, org); role = _role(db, org)
    _app(db, org, role, name="Alice", score=80, comments=["Yes — strong hire, move forward"])
    # 'yesterday' must NOT match a whole-word 'yes' filter.
    _app(db, org, role, name="Bob", score=75, comments=["Spoke yesterday, needs a follow-up"])
    _app(db, org, role, name="Carol", score=70, comments=None)
    # right comment, wrong Workable stage → excluded by the stage filter.
    _app(db, org, role, name="Dave", score=90, workable_stage="Phone Screen", comments=["Yes, advance"])

    res = tools.dispatch_tool(
        "list_candidates",
        {"workable_stage": "technical interview", "comment_contains": "yes", "limit": 5},
        db=db, role=role, user=user,
    )
    assert res["comment_filter"] == "yes"
    assert [c["name"] for c in res["candidates"]] == ["Alice"]
    assert res["count"] == 1
    # the matched comment travels back so the agent can quote the recruiter's note
    assert "strong hire" in res["candidates"][0]["comments"][0]["body"]


def test_list_candidates_include_comments_without_filter(db):
    org = _org(db); user = _user(db, org); role = _role(db, org)
    _app(db, org, role, name="Alice", score=80, comments=["Yes — strong hire"])
    _app(db, org, role, name="Carol", score=70, comments=None)

    res = tools.dispatch_tool(
        "list_candidates", {"include_comments": True}, db=db, role=role, user=user
    )
    by = {c["name"]: c for c in res["candidates"]}
    assert by["Alice"]["comments"][0]["body"].startswith("Yes")
    assert by["Alice"]["comments"][0]["author"] == "Recruiter"
    assert by["Carol"]["comments"] == []  # key present, just empty


def test_list_candidates_omits_comments_by_default(db):
    """Lean default: no filter, no include → no comment read, no `comments` key."""
    org = _org(db); user = _user(db, org); role = _role(db, org)
    _app(db, org, role, name="Alice", score=80, comments=["Yes"])

    res = tools.dispatch_tool("list_candidates", {"bucket": "all"}, db=db, role=role, user=user)
    assert "comments" not in res["candidates"][0]
    assert res["comment_filter"] is None
