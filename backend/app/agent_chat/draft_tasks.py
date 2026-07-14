"""Draft-task review for the role agent chat.

The JD→spec generator authors assessment tasks as org-owned, inactive
``Task`` rows (``extra_data.generated``) tagged to a role. Historically the
recruiter reviewed them only on the Tasks page, where *reject* hard-deleted
the draft. This module brings that review into the role agent's world:

  * ``draft_review_card`` — the agent surfaces the role's pending drafts as a
    ``draft_task_review`` card (read-only summary + the reject question set).
    While a durable Turn-on intent owns a draft, the card is progress-only and
    explicitly says no second approval click is needed.
  * ``approve_draft`` — activate a draft (deterministic; reuses the repo
    provisioning the Tasks-page approve does).
  * ``revise_draft`` — the structured-reject path: instead of deleting, take
    the recruiter's multiple-choice feedback, re-author the spec in place via
    ``revise_task_spec`` (one metered call, opt-in), and re-present.

``REJECT_QUESTIONS`` is the single source of truth for the structured-feedback
card — the frontend renders exactly what the backend will interpret, so the
two never drift. The same shape is intended to power decision overrides later
(the structured version of "Send back & teach").
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as _sql_text
from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.task import Task
from ..platform.config import settings

logger = logging.getLogger("taali.agent_chat.draft_tasks")


# --- The structured reject questionnaire (Claude-Code-style) ----------------
# One multi-select "what's wrong", one single-select "what to do", plus an
# optional free-text note. Rendered verbatim by the dock; interpreted verbatim
# by ``_build_feedback``.
REJECT_QUESTIONS: list[dict[str, Any]] = [
    {
        "key": "issues",
        "prompt": "What's off about this draft?",
        "multi": True,
        "options": [
            {"value": "scenario", "label": "Scenario unrealistic / off-role"},
            {"value": "difficulty", "label": "Wrong difficulty"},
            {"value": "rubric", "label": "Rubric weights off"},
            {"value": "decisions", "label": "Decisions weak or unclear"},
            {"value": "repo", "label": "Repo / files inadequate"},
            {"value": "scope", "label": "Too long / too short"},
        ],
    },
    {
        "key": "direction",
        "prompt": "What should the revision do?",
        "multi": False,
        "options": [
            {"value": "targeted", "label": "Targeted fix — keep the structure"},
            {"value": "harder", "label": "Make it harder"},
            {"value": "easier", "label": "Make it easier"},
            {"value": "reweight", "label": "Reweight toward decisions"},
            {"value": "regenerate", "label": "Regenerate from scratch"},
        ],
    },
]

_ISSUE_PHRASES = {
    "scenario": "the scenario is unrealistic or not aligned to the role",
    "difficulty": "the difficulty is wrong",
    "rubric": "the rubric weights are off",
    "decisions": "the decision points are weak or unclear",
    "repo": "the starter repo / files are inadequate",
    "scope": "the scope is wrong (too long or too short)",
}

_DIRECTION_PHRASES = {
    "targeted": "Make a targeted fix — keep the overall structure and change only what's flagged.",
    "harder": "Make the task harder and more senior.",
    "easier": "Make the task easier and more approachable.",
    "reweight": "Reweight the rubric to put more emphasis on the decision lens.",
    "regenerate": "Re-author the task substantially — the current direction isn't right.",
}

# extra_data keys that are bookkeeping, not part of the task spec.
_FLAG_KEYS = {"generated", "needs_review", "approved_by_user_id", "last_revision"}


# --- Queries / summaries ----------------------------------------------------
def _role_draft_tasks(db: Session, role: Role) -> list[Task]:
    """Generated, not-yet-active tasks linked to THIS role, newest first."""
    rows = db.execute(
        _sql_text("SELECT task_id FROM role_tasks WHERE role_id = :r"),
        {"r": int(role.id)},
    ).fetchall()
    task_ids = [int(r[0]) for r in rows]
    if not task_ids:
        return []
    tasks = (
        db.query(Task)
        .filter(
            Task.id.in_(task_ids),
            Task.organization_id == int(role.organization_id),
            Task.is_active == False,  # noqa: E712
        )
        .order_by(Task.id.desc())
        .all()
    )
    return [t for t in tasks if isinstance(t.extra_data, dict) and t.extra_data.get("generated")]


def _rubric_dims(rubric: Any) -> list[dict[str, Any]]:
    """Normalize the rubric (dict-keyed or list-of-dims) to {name, weight}."""
    out: list[dict[str, Any]] = []
    if isinstance(rubric, dict):
        for name, body in rubric.items():
            w = body.get("weight") if isinstance(body, dict) else None
            out.append({"name": str(name), "weight": w})
    elif isinstance(rubric, list):
        for dim in rubric:
            if isinstance(dim, dict):
                out.append({
                    "name": str(dim.get("name") or dim.get("dimension") or "dimension"),
                    "weight": dim.get("weight"),
                })
    return out


def draft_summary(task: Task) -> dict[str, Any]:
    """Compact, read-only view of a draft for the review card."""
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    deliverable = extra.get("deliverable") if isinstance(extra.get("deliverable"), dict) else {}
    decisions = [
        {"headline": str(d.get("headline") or "").strip()}
        for d in (extra.get("decision_points") or [])
        if isinstance(d, dict) and d.get("headline")
    ]
    files = (task.repo_structure or {}).get("files") if isinstance(task.repo_structure, dict) else {}
    from ..services.task_battle_test import battle_test_summary

    return {
        "task_id": int(task.id),
        "task_key": task.task_key,
        "name": task.name,
        "role": task.role,
        "deliverable_kind": deliverable.get("kind"),
        "decisions": decisions,
        "rubric": _rubric_dims(task.evaluation_rubric),
        "repo_file_count": len(files) if isinstance(files, dict) else 0,
        # Report card from the automated E2B battle-test (None = not yet run):
        # verdict, baseline pass/fail counts, failed checks — what turns draft
        # approval into a 2-minute read instead of an 800-line-JSON audit.
        "battle_test": battle_test_summary(task),
    }


def draft_review_card(db: Session, role: Role) -> dict[str, Any]:
    """``draft_task_review`` card payload for the agent to surface. Carries the
    drafts + the reject question set so the dock can render review + structured
    reject without a second round-trip."""
    drafts = _role_draft_tasks(db, role)
    from ..services.role_activation_intent import (
        ACTIVATION_ACTIVE_STATUSES,
        activation_intent_state,
    )

    activation = activation_intent_state(role)
    activation_status = str(activation.get("status") or "")
    automatic_activation = activation_status in ACTIVATION_ACTIVE_STATUSES
    return {
        "type": "draft_task_review",
        "role_id": int(role.id),
        "drafts": [draft_summary(t) for t in drafts],
        "reject_questions": REJECT_QUESTIONS,
        "automatic_activation": automatic_activation,
        "activation_status": activation_status or None,
    }


def count_role_drafts(db: Session, role: Role) -> int:
    return len(_role_draft_tasks(db, role))


# --- Mutations --------------------------------------------------------------
def _fetch_role_draft(db: Session, role: Role, task_id: int) -> Task | None:
    task = (
        db.query(Task)
        .filter(
            Task.id == int(task_id),
            Task.organization_id == int(role.organization_id),
            Task.is_active == False,  # noqa: E712
        )
        .first()
    )
    if task is None:
        return None
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    return task if extra.get("generated") else None


def approve_draft(db: Session, role: Role, task_id: int, *, user_id: int) -> dict[str, Any]:
    """Activate a draft only after its candidate repo is proven usable."""
    from ..services.role_activation_intent import (
        ACTIVATION_ACTIVE_STATUSES,
        activation_intent_state,
    )

    if str(activation_intent_state(role).get("status") or "") in ACTIVATION_ACTIVE_STATUSES:
        return {
            "ok": False,
            "error": (
                "Turn on is already validating and approving this task automatically; "
                "no separate approval is needed."
            ),
        }
    task = _fetch_role_draft(db, role, task_id)
    if task is None:
        return {"ok": False, "error": "Draft not found or already approved."}
    try:
        from ..services.task_approval_service import approve_task_for_use

        approve_task_for_use(db, task, user_id=int(user_id))
        db.commit()
        db.refresh(task)
    except Exception as exc:
        db.rollback()
        logger.warning(
            "draft approval blocked task_id=%s: %s", task.id, exc, exc_info=True
        )
        return {
            "ok": False,
            "error": (
                "The task repository could not be provisioned and verified, so "
                "the draft remains inactive. Retry after repository access recovers."
            ),
        }
    return {"ok": True, "summary": draft_summary(task)}


def _build_feedback(answers: dict[str, Any], note: str | None) -> str:
    """Turn the structured answers into a concise revision instruction."""
    issues = answers.get("issues") or []
    if isinstance(issues, str):
        issues = [issues]
    issue_clause = ", ".join(_ISSUE_PHRASES.get(i, str(i)) for i in issues if i)
    direction = answers.get("direction")
    if isinstance(direction, list):
        direction = direction[0] if direction else None
    direction_clause = _DIRECTION_PHRASES.get(str(direction or ""), "")
    parts: list[str] = []
    if issue_clause:
        parts.append(f"The recruiter flagged that {issue_clause}.")
    if direction_clause:
        parts.append(direction_clause)
    note = (note or "").strip()
    if note:
        parts.append(f"Specific note from the recruiter: {note}")
    return " ".join(parts) or "The recruiter wants this draft revised; improve its realism, decisions, and rubric."


def _reconstruct_spec(task: Task) -> dict[str, Any]:
    """Rebuild a best-effort spec dict from the stored columns + extra_data.
    Need not be byte-perfect — the generator re-emits a fully contract-valid
    spec; this is just seed context."""
    extra = dict(task.extra_data) if isinstance(task.extra_data, dict) else {}
    for k in _FLAG_KEYS:
        extra.pop(k, None)
    extra.update({
        "task_id": task.task_key,
        "name": task.name,
        "role": task.role,
        "duration_minutes": task.duration_minutes or 30,
        "calibration_prompt": task.calibration_prompt,
        "scenario": task.scenario,
        "repo_structure": task.repo_structure,
        "evaluation_rubric": task.evaluation_rubric,
    })
    return extra


def _apply_spec(task: Task, spec: dict[str, Any], *, feedback: str) -> None:
    """Write a revised spec back onto the draft in place — same id + task_key
    (so the role link + repo name stay stable), still a draft pending review."""
    from ..services.task_catalog import PERSISTED_TASK_SPEC_KEYS

    scenario = spec.get("scenario")
    task.name = spec.get("name", task.name)
    if isinstance(scenario, str):
        task.description = scenario[:500]
        task.scenario = scenario
    task.calibration_prompt = spec.get("calibration_prompt")
    task.role = spec.get("role") or task.role
    task.duration_minutes = spec.get("duration_minutes", 30)
    task.repo_structure = spec.get("repo_structure")
    task.evaluation_rubric = spec.get("evaluation_rubric")
    extra = {k: v for k, v in spec.items() if k not in PERSISTED_TASK_SPEC_KEYS}
    extra["generated"] = True
    extra["needs_review"] = True
    extra["last_revision"] = {"feedback": feedback}
    task.extra_data = extra


def revise_draft(
    db: Session,
    role: Role,
    task_id: int,
    *,
    answers: dict[str, Any],
    note: str | None,
    api_key: str,
) -> dict[str, Any]:
    """Structured-reject → revise: re-author the draft from the recruiter's
    feedback instead of deleting it. Returns {ok, summary, feedback} or
    {ok: False, error, errors}."""
    from ..services.role_activation_intent import (
        ACTIVATION_ACTIVE_STATUSES,
        activation_intent_state,
    )

    if str(activation_intent_state(role).get("status") or "") in ACTIVATION_ACTIVE_STATUSES:
        return {
            "ok": False,
            "error": (
                "Turn on is already validating this task automatically. Wait for it "
                "to finish, or update and re-publish the requisition to replace it."
            ),
        }
    task = _fetch_role_draft(db, role, task_id)
    if task is None:
        return {"ok": False, "error": "Draft not found or already approved."}
    if not api_key:
        return {"ok": False, "error": "No API key configured for revision."}

    from ..services.task_provisioning_service import _role_jd_text, _slugify
    from ..services.task_spec_generator import revise_task_spec

    feedback = _build_feedback(answers, note)
    role_name = str(getattr(role, "name", "") or "Role")
    prior_spec = _reconstruct_spec(task)
    try:
        result = revise_task_spec(
            prior_spec=prior_spec,
            feedback=feedback,
            role_name=role_name,
            role_slug=_slugify(role_name),
            jd_text=_role_jd_text(role),
            api_key=api_key,
            organization_id=int(role.organization_id),
            role_id=int(role.id),
        )
    except Exception:
        logger.exception("revise_task_spec raised for task %s", task_id)
        return {"ok": False, "error": "The revision couldn't run. Try again.", "feedback": feedback}

    if not result.valid or not result.spec:
        return {
            "ok": False,
            "error": "The revision didn't produce a valid task — kept the original.",
            "errors": result.errors[:5],
            "feedback": feedback,
        }

    _apply_spec(task, result.spec, feedback=feedback)
    try:
        db.commit()
        db.refresh(task)
    except Exception:
        db.rollback()
        logger.exception("failed to persist revised draft %s", task_id)
        return {"ok": False, "error": "Couldn't save the revision."}
    return {"ok": True, "summary": draft_summary(task), "feedback": feedback}


__all__ = [
    "REJECT_QUESTIONS",
    "draft_review_card",
    "draft_summary",
    "count_role_drafts",
    "approve_draft",
    "revise_draft",
]
