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

import copy
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text as _sql_text
from sqlalchemy.orm import Session

from ..models.role import Role, role_tasks
from ..models.task import Task

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
_FLAG_KEYS = {
    "generated",
    "needs_review",
    "approved_by_user_id",
    "last_revision",
    "repository_ready",
    "battle_test",
    "battle_test_history",
    "battle_test_provisioning",
}
_VERSIONED_TASK_REQUIRED = (
    "This task already has assessment history and cannot be revised in place. "
    "Duplicate it as a new task version, then assign that version going forward."
)
_SHARED_DRAFT_REVISION_BLOCKED = (
    "This draft is assigned to more than one active role. Duplicate it for "
    "this role before changing another job's assessment."
)


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
        "role_version": int(role.version or 1),
        "drafts": [draft_summary(t) for t in drafts],
        "reject_questions": REJECT_QUESTIONS,
        "automatic_activation": automatic_activation,
        "activation_status": activation_status or None,
    }


def count_role_drafts(db: Session, role: Role) -> int:
    return len(_role_draft_tasks(db, role))


def _has_assessment_history(db: Session, *, task_id: int) -> bool:
    from ..domains.tasks_repository.task_reference_guard import (
        task_content_reference_kinds,
    )

    return bool(task_content_reference_kinds(db, task_id=int(task_id)))


def _live_linked_role_ids(db: Session, *, task_id: int) -> tuple[int, ...]:
    return tuple(
        int(row[0])
        for row in db.query(role_tasks.c.role_id)
        .join(Role, Role.id == role_tasks.c.role_id)
        .filter(
            role_tasks.c.task_id == int(task_id),
            Role.deleted_at.is_(None),
        )
        .order_by(role_tasks.c.role_id.asc())
        .all()
    )


def _is_exclusive_live_role_draft(
    db: Session,
    *,
    role: Role,
    task_id: int,
) -> bool:
    return _live_linked_role_ids(db, task_id=task_id) == (int(role.id),)


# --- Mutations --------------------------------------------------------------
def _fetch_role_draft(
    db: Session,
    role: Role,
    task_id: int,
    *,
    lock_for_update: bool = False,
) -> Task | None:
    query = (
        db.query(Task)
        .join(role_tasks, role_tasks.c.task_id == Task.id)
        .filter(
            role_tasks.c.role_id == int(role.id),
            Task.id == int(task_id),
            Task.organization_id == int(role.organization_id),
            Task.is_active == False,  # noqa: E712
        )
    )
    if lock_for_update:
        query = query.with_for_update(of=Task)
    task = query.first()
    if task is None:
        return None
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    return task if extra.get("generated") else None


def capture_draft_approval(
    db: Session,
    role: Role,
    task_id: int,
) -> dict[str, Any]:
    """Capture exact approval inputs under a short Role→Task preflight."""
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
    task = _fetch_role_draft(db, role, task_id, lock_for_update=True)
    if task is None:
        return {"ok": False, "error": "Draft not found or already approved."}
    if not _is_exclusive_live_role_draft(db, role=role, task_id=int(task.id)):
        return {"ok": False, "error": _SHARED_DRAFT_REVISION_BLOCKED}

    from ..services.task_approval_service import capture_task_approval

    return {"ok": True, "captured": capture_task_approval(task)}


def apply_prepared_draft_approval(
    db: Session,
    role: Role,
    task_id: int,
    prepared: Any,
    *,
    user_id: int,
) -> dict[str, Any]:
    """Revalidate and apply detached repository work under Role→Task locks."""
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
    task = _fetch_role_draft(db, role, task_id, lock_for_update=True)
    if task is None:
        return {"ok": False, "error": "Draft not found or already approved."}
    if not _is_exclusive_live_role_draft(db, role=role, task_id=int(task.id)):
        return {"ok": False, "error": _SHARED_DRAFT_REVISION_BLOCKED}

    from ..services.task_approval_service import apply_prepared_task_approval

    apply_prepared_task_approval(
        db,
        task,
        prepared,
        user_id=int(user_id),
        approval_role_id=int(role.id),
    )
    from ..services.agent_activation_checklist import (
        resolve_satisfied_activation_questions,
    )

    resolve_satisfied_activation_questions(db, role=role)
    return {"ok": True, "summary": draft_summary(task)}


def approve_draft(db: Session, role: Role, task_id: int, *, user_id: int) -> dict[str, Any]:
    """Transactional compatibility wrapper for non-HTTP callers.

    Repository ownership begins before capture and lasts through commit. The
    preflight Task lock is released before provider I/O, then Role→Task state is
    reacquired and revalidated before activation.
    """
    from ..services.task_repository_serialization import (
        task_repository_write_mutex,
    )

    role_id = int(role.id)
    organization_id = int(role.organization_id)
    with task_repository_write_mutex(db, task_id=task_id):
        captured = capture_draft_approval(db, role, task_id)
        if not captured.get("ok"):
            return captured
        db.rollback()
        try:
            from ..services.task_approval_service import prepare_task_approval

            prepared = prepare_task_approval(captured["captured"])
            locked_role = (
                db.query(Role)
                .filter(
                    Role.id == role_id,
                    Role.organization_id == organization_id,
                    Role.deleted_at.is_(None),
                )
                .populate_existing()
                .with_for_update(of=Role)
                .one_or_none()
            )
            if locked_role is None:
                raise RuntimeError("Role no longer exists")
            result = apply_prepared_draft_approval(
                db,
                locked_role,
                task_id,
                prepared,
                user_id=int(user_id),
            )
            if not result.get("ok"):
                db.rollback()
                return result
            db.commit()
            return result
        except Exception as exc:
            db.rollback()
            logger.warning(
                "draft approval blocked task_id=%s: %s", task_id, exc, exc_info=True
            )
            return {
                "ok": False,
                "error": (
                    "The task repository could not be provisioned and verified, so "
                    "the draft remains inactive. Retry after repository access recovers."
                ),
            }


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
        "scenario": task.scenario,
        "repo_structure": task.repo_structure,
        "evaluation_rubric": task.evaluation_rubric,
    })
    return extra


def _apply_spec(task: Task, spec: dict[str, Any], *, feedback: str) -> None:
    """Write a revised spec back onto the draft in place — same id + task_key
    (so the role link + repo name stay stable), still a draft pending review."""
    from ..services.task_catalog import PERSISTED_TASK_SPEC_KEYS

    current_extra = (
        copy.deepcopy(task.extra_data)
        if isinstance(task.extra_data, dict)
        else {}
    )
    scenario = spec.get("scenario")
    task.name = spec.get("name", task.name)
    if isinstance(scenario, str):
        task.description = scenario[:500]
        task.scenario = scenario
    task.role = spec.get("role") or task.role
    task.duration_minutes = spec.get("duration_minutes", 30)
    task.repo_structure = spec.get("repo_structure")
    task.evaluation_rubric = spec.get("evaluation_rubric")
    extra = {k: v for k, v in spec.items() if k not in PERSISTED_TASK_SPEC_KEYS}
    # Revision invalidates approval/repository proof, but provenance and model
    # generation identity remain part of the task's audit trail. Keep the
    # prior execution reports as bounded history rather than silently dropping
    # evidence when a new battle test is requested.
    for key, value in current_extra.items():
        key_name = str(key or "")
        if key_name == "provenance" or key_name.startswith(
            ("provenance_", "generated_", "generation_")
        ):
            extra[key] = copy.deepcopy(value)
    history = [
        copy.deepcopy(item)
        for item in (current_extra.get("battle_test_history") or [])
        if isinstance(item, dict)
    ]
    previous_report = current_extra.get("battle_test")
    if isinstance(previous_report, dict):
        history.append(copy.deepcopy(previous_report))
    if history:
        extra["battle_test_history"] = history[-5:]
    extra["generated"] = True
    extra["needs_review"] = True
    extra["last_revision"] = {"feedback": feedback}
    from ..services.task_battle_test import initialize_battle_test_provisioning

    task.extra_data = initialize_battle_test_provisioning(extra)


@dataclass(frozen=True)
class DraftRevisionPreparation:
    """Detached inputs for the paid revision call.

    No ORM object is retained here.  The route can end its read transaction,
    run the model without a Role lock, then reacquire and compare both the Role
    revision and this task fingerprint before applying the result.
    """

    organization_id: int
    role_id: int
    task_id: int
    feedback: str
    role_name: str
    role_slug: str
    jd_text: str
    prior_spec: dict[str, Any]
    task_fingerprint: str


_DRAFT_MUTABLE_FIELDS = (
    "name",
    "description",
    "scenario",
    "calibration_prompt",
    "role",
    "duration_minutes",
    "repo_structure",
    "evaluation_rubric",
    "extra_data",
)


def _spec_fingerprint(spec: dict[str, Any]) -> str:
    encoded = json.dumps(
        spec,
        allow_nan=False,
        default=repr,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _task_revision_fingerprint(task: Task) -> str:
    return _spec_fingerprint(_reconstruct_spec(task))


def _task_mutation_snapshot(task: Task) -> dict[str, Any]:
    return {
        field: copy.deepcopy(getattr(task, field))
        for field in _DRAFT_MUTABLE_FIELDS
    }


def _restore_task_mutation_snapshot(task: Task, snapshot: dict[str, Any]) -> None:
    for field in _DRAFT_MUTABLE_FIELDS:
        setattr(task, field, copy.deepcopy(snapshot[field]))


def prepare_draft_revision(
    db: Session,
    role: Role,
    task_id: int,
    *,
    answers: dict[str, Any],
    note: str | None,
    api_key: str,
) -> dict[str, Any]:
    """Validate and detach revision inputs without calling the model."""
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
    if not _is_exclusive_live_role_draft(db, role=role, task_id=int(task.id)):
        return {"ok": False, "error": _SHARED_DRAFT_REVISION_BLOCKED}
    if _has_assessment_history(db, task_id=int(task.id)):
        return {"ok": False, "error": _VERSIONED_TASK_REQUIRED}
    if not api_key:
        return {"ok": False, "error": "No API key configured for revision."}

    from ..services.task_provisioning_service import _role_jd_text, _slugify

    feedback = _build_feedback(answers, note)
    role_name = str(getattr(role, "name", "") or "Role")
    prior_spec = copy.deepcopy(_reconstruct_spec(task))
    preparation = DraftRevisionPreparation(
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        task_id=int(task.id),
        feedback=feedback,
        role_name=role_name,
        role_slug=_slugify(role_name),
        jd_text=str(_role_jd_text(role) or ""),
        prior_spec=prior_spec,
        task_fingerprint=_spec_fingerprint(prior_spec),
    )
    return {"ok": True, "preparation": preparation, "feedback": feedback}


def generate_prepared_draft_revision(
    preparation: DraftRevisionPreparation,
    *,
    api_key: str,
) -> dict[str, Any]:
    """Run the model from detached inputs; no database lock is required."""
    from ..services.task_spec_generator import revise_task_spec

    try:
        result = revise_task_spec(
            prior_spec=copy.deepcopy(preparation.prior_spec),
            feedback=preparation.feedback,
            role_name=preparation.role_name,
            role_slug=preparation.role_slug,
            jd_text=preparation.jd_text,
            api_key=api_key,
            organization_id=preparation.organization_id,
            role_id=preparation.role_id,
        )
    except Exception:
        logger.exception("revise_task_spec raised for task %s", preparation.task_id)
        return {
            "ok": False,
            "error": "The revision couldn't run. Try again.",
            "feedback": preparation.feedback,
        }

    if not result.valid or not result.spec:
        return {
            "ok": False,
            "error": "The revision didn't produce a valid task — kept the original.",
            "errors": list(result.errors or [])[:5],
            "feedback": preparation.feedback,
        }
    return {
        "ok": True,
        "spec": copy.deepcopy(result.spec),
        "feedback": preparation.feedback,
    }


def apply_prepared_draft_revision(
    db: Session,
    role: Role,
    preparation: DraftRevisionPreparation,
    *,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Recheck the exact linked draft and apply a material prepared result.

    ``conflict`` means the review card changed after preparation.  The HTTP
    boundary translates it to the same ROLE_VERSION_CONFLICT contract used by
    all other shared-job writes.
    """
    if (
        int(role.id) != preparation.role_id
        or int(role.organization_id) != preparation.organization_id
    ):
        return {"ok": False, "conflict": True, "error": "Draft changed during revision."}

    from ..services.role_activation_intent import (
        ACTIVATION_ACTIVE_STATUSES,
        activation_intent_state,
    )

    if str(activation_intent_state(role).get("status") or "") in ACTIVATION_ACTIVE_STATUSES:
        return {"ok": False, "conflict": True, "error": "Draft changed during revision."}
    task = _fetch_role_draft(
        db,
        role,
        preparation.task_id,
        lock_for_update=True,
    )
    if task is None or _task_revision_fingerprint(task) != preparation.task_fingerprint:
        return {"ok": False, "conflict": True, "error": "Draft changed during revision."}
    if not _is_exclusive_live_role_draft(db, role=role, task_id=int(task.id)):
        return {"ok": False, "conflict": True, "error": _SHARED_DRAFT_REVISION_BLOCKED}
    if _has_assessment_history(db, task_id=int(task.id)):
        return {"ok": False, "error": _VERSIONED_TASK_REQUIRED}

    before = _task_mutation_snapshot(task)
    before_fingerprint = preparation.task_fingerprint
    _apply_spec(task, spec, feedback=preparation.feedback)
    if _task_revision_fingerprint(task) == before_fingerprint:
        # The generator can legitimately return the exact current spec.  Do
        # not manufacture a Role revision/audit row for bookkeeping-only
        # ``last_revision`` metadata.
        _restore_task_mutation_snapshot(task, before)
        db.add(task)
        return {
            "ok": True,
            "material": False,
            "summary": draft_summary(task),
            "feedback": preparation.feedback,
        }

    db.add(task)
    db.flush()
    return {
        "ok": True,
        "material": True,
        "summary": draft_summary(task),
        "feedback": preparation.feedback,
    }


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
    prepared = prepare_draft_revision(
        db,
        role,
        task_id,
        answers=answers,
        note=note,
        api_key=api_key,
    )
    if not prepared.get("ok"):
        return prepared
    preparation = prepared["preparation"]
    generated = generate_prepared_draft_revision(preparation, api_key=api_key)
    if not generated.get("ok"):
        return generated
    result = apply_prepared_draft_revision(
        db,
        role,
        preparation,
        spec=generated["spec"],
    )
    if not result.get("ok"):
        return result
    try:
        if result.get("material"):
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("failed to persist revised draft %s", task_id)
        return {"ok": False, "error": "Couldn't save the revision."}
    return result


__all__ = [
    "REJECT_QUESTIONS",
    "draft_review_card",
    "draft_summary",
    "count_role_drafts",
    "capture_draft_approval",
    "apply_prepared_draft_approval",
    "approve_draft",
    "DraftRevisionPreparation",
    "prepare_draft_revision",
    "generate_prepared_draft_revision",
    "apply_prepared_draft_revision",
    "revise_draft",
]
