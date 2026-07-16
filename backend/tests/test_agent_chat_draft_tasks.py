"""Draft-task review in the role agent chat — list / approve / revise.

Covers draft_tasks.py (the read card + the structured-reject revise) via both
the public dispatch_tool path and the module functions. The repo provisioning
on approve and the LLM call on revise are patched out so the tests don't touch
GitHub or Anthropic.
"""
from __future__ import annotations

from unittest.mock import patch

from app.agent_chat import draft_tasks as dt
from app.agent_chat import tools
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.models.user import User
from app.services.task_spec_generator import GeneratedSpecResult


def _org(db) -> Organization:
    org = Organization(name="Draft Org", slug=f"draft-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _user(db, org) -> User:
    u = User(
        email=f"draft-{id(db)}@x.test", hashed_password="x", full_name="Rec",
        organization_id=org.id, is_active=True, is_verified=True, is_superuser=False,
    )
    db.add(u)
    db.flush()
    return u


def _role(db, org) -> Role:
    role = Role(
        organization_id=org.id, name="Security Engineer", source="manual",
        score_threshold=70, agentic_mode_enabled=True, monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.flush()
    return role


def _draft(db, org, role, *, name="Vendor Risk Task") -> Task:
    task = Task(
        organization_id=org.id, name=name, task_type="security", difficulty="medium",
        duration_minutes=30, is_template=False, is_active=False,
        task_key=f"vr_{id(db)}", role="security_engineer", scenario="A vendor scenario.",
        calibration_prompt="warm up",
        repo_structure={"name": "vr", "files": {"README.md": "x", "tests/test_x.py": "y"}},
        evaluation_rubric={
            "design_decisions_articulated": {"weight": 0.35, "grader": "interrogation_outcome"},
            "ship_quality": {"weight": 0.4, "lens": "deliverable", "criteria": {}},
        },
        extra_data={
            "generated": True, "needs_review": True,
            "battle_test": {"verdict": "pass"},
            "decision_points": [{"id": "d1", "headline": "Classify risk tier"}],
            "deliverable": {"kind": "doc"},
        },
    )
    db.add(task)
    db.flush()
    role.tasks.append(task)
    db.flush()
    return task


# --- read card --------------------------------------------------------------
def test_draft_review_card_lists_role_drafts(db):
    org = _org(db)
    role = _role(db, org)
    _draft(db, org, role)

    card = dt.draft_review_card(db, role)
    assert card["type"] == "draft_task_review"
    assert card["role_id"] == role.id
    assert len(card["drafts"]) == 1
    d = card["drafts"][0]
    assert d["name"] == "Vendor Risk Task"
    assert d["deliverable_kind"] == "doc"
    assert len(d["decisions"]) == 1 and d["decisions"][0]["headline"] == "Classify risk tier"
    assert len(d["rubric"]) == 2
    assert d["repo_file_count"] == 2
    # The card carries the question set so the dock renders what we interpret.
    assert [q["key"] for q in card["reject_questions"]] == ["issues", "direction"]


def test_list_draft_tasks_tool_returns_card(db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org)
    _draft(db, org, role)

    res = tools.dispatch_tool("list_draft_tasks", {}, db=db, role=role, user=user)
    assert res["type"] == "draft_task_review"
    assert len(res["drafts"]) == 1


def test_turn_on_owned_draft_is_progress_only_and_cannot_be_manually_approved(db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org)
    role.agentic_mode_enabled = False
    task = _draft(db, org, role)
    from app.services.role_activation_intent import request_role_activation_intent

    request_role_activation_intent(
        role,
        user_id=int(user.id),
        monthly_budget_cents=5000,
    )
    db.commit()

    card = dt.draft_review_card(db, role)
    approval = dt.approve_draft(db, role, task.id, user_id=int(user.id))

    assert card["automatic_activation"] is True
    assert card["activation_status"] == "pending"
    assert approval["ok"] is False
    assert "no separate approval" in approval["error"].lower()
    assert task.is_active is False


def test_review_card_empty_when_no_drafts(db):
    org = _org(db)
    role = _role(db, org)
    card = dt.draft_review_card(db, role)
    assert card["drafts"] == []


# --- approve ----------------------------------------------------------------
@patch(
    "app.services.task_approval_service.provision_and_validate_task_repository",
    return_value="mock://taali-assessments/vendor-risk",
)
def test_approve_draft_activates(_repo, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org)
    task = _draft(db, org, role)

    res = dt.approve_draft(db, role, task.id, user_id=int(user.id))
    assert res["ok"] is True
    db.refresh(task)
    assert task.is_active is True
    assert task.extra_data["needs_review"] is False
    assert task.extra_data["approved_by_user_id"] == int(user.id)
    assert task.extra_data["repository_ready"]["repo_url"].startswith("mock://")


@patch(
    "app.services.task_approval_service.provision_and_validate_task_repository",
    side_effect=RuntimeError("GitHub unavailable"),
)
def test_approve_draft_repo_failure_leaves_draft_inactive(_repo, db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org)
    task = _draft(db, org, role)
    db.commit()

    res = dt.approve_draft(db, role, task.id, user_id=int(user.id))

    assert res["ok"] is False
    db.refresh(task)
    assert task.is_active is False
    assert task.extra_data["needs_review"] is True
    assert "approved_by_user_id" not in task.extra_data


def test_approve_unknown_draft_fails(db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org)
    res = dt.approve_draft(db, role, 999999, user_id=int(user.id))
    assert res["ok"] is False


# --- structured reject → revise --------------------------------------------
def test_build_feedback_maps_structured_answers():
    fb = dt._build_feedback({"issues": ["scenario", "rubric"], "direction": "harder"}, "make it K8s")
    assert "scenario is unrealistic" in fb
    assert "rubric weights are off" in fb
    assert "harder" in fb.lower()
    assert "make it K8s" in fb


@patch("app.services.task_spec_generator.revise_task_spec")
def test_revise_draft_repersists_in_place(mock_revise, db):
    org = _org(db)
    role = _role(db, org)
    task = _draft(db, org, role)
    original_key = task.task_key

    revised_spec = {
        "task_id": original_key, "name": "Vendor Risk Task (revised)", "role": "security_engineer",
        "duration_minutes": 30, "calibration_prompt": "superseded warmup", "scenario": "A harder scenario.",
        "repo_structure": {"name": "vr", "files": {"README.md": "x"}},
        "evaluation_rubric": {"design_decisions_articulated": {"weight": 0.4, "grader": "interrogation_outcome"}},
        "decision_points": [{"id": "d1", "headline": "Classify risk tier"}, {"id": "d2", "headline": "New call"}],
        "deliverable": {"kind": "doc"},
    }
    mock_revise.return_value = GeneratedSpecResult(spec=revised_spec, valid=True, errors=[], attempts=1)

    res = dt.revise_draft(
        db, role, task.id,
        answers={"issues": ["difficulty"], "direction": "harder"}, note="", api_key="sk-test",
    )
    assert res["ok"] is True
    db.refresh(task)
    # Re-authored in place: same id + task_key, still a draft pending review.
    assert task.task_key == original_key
    assert task.name == "Vendor Risk Task (revised)"
    assert task.is_active is False
    assert task.calibration_prompt == "warm up"
    assert task.extra_data["needs_review"] is True
    assert task.extra_data["last_revision"]["feedback"]
    # The structured answers reached the generator as guidance.
    assert "harder" in mock_revise.call_args.kwargs["feedback"].lower()
    assert mock_revise.call_args.kwargs["role_id"] == role.id


@patch("app.services.task_spec_generator.revise_task_spec")
def test_revise_keeps_original_when_generation_invalid(mock_revise, db):
    org = _org(db)
    role = _role(db, org)
    task = _draft(db, org, role)
    mock_revise.return_value = GeneratedSpecResult(
        spec=None, valid=False, errors=["rubric weights != 1.0"], attempts=3
    )

    res = dt.revise_draft(
        db, role, task.id, answers={"issues": ["rubric"]}, note=None, api_key="sk-test",
    )
    assert res["ok"] is False
    assert res["errors"]
    db.refresh(task)
    assert task.name == "Vendor Risk Task"  # unchanged


def test_revise_without_api_key_is_graceful(db):
    org = _org(db)
    role = _role(db, org)
    task = _draft(db, org, role)
    res = dt.revise_draft(db, role, task.id, answers={"issues": ["rubric"]}, note=None, api_key="")
    assert res["ok"] is False
