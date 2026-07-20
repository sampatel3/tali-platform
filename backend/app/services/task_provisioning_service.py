"""Auto-provision an assessment task for a role from its JD.

Closes the "new job → no task → assessment send is misconfigured" gap.
When a role has no linked task, this generates one (via the JD→spec
generator), persists it as an org-owned DRAFT, provisions its template repo,
and links it to the role. The recruiter's single Turn-on command authorizes the
exact draft only after its automated battle test and repository checks pass;
there is no second manual task-approval step.

Cost-safety: generation is a multi-call Sonnet operation (~$0.10-0.30).
For requisitions, publish records a spend-free deferred request and Turn on
moves it into the paid worker outbox. The linked-task and claim-token guards
prevent duplicate or stale-JD authoring.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.task import Task
from .task_provisioning_state import (
    PROVISIONING_AWAITING_ACTIVATION,
    PROVISIONING_BLOCKED,
    PROVISIONING_FAILED,
    PROVISIONING_PENDING,
    PROVISIONING_RECOVERABLE_STATUSES,
    PROVISIONING_RETRY_WAIT,
    PROVISIONING_RUNNING,
    PROVISIONING_STALE_AFTER,
    PROVISIONING_SUCCEEDED,
    TaskProvisioningBlockedError,
    TaskProvisioningClaim,
    TaskProvisioningError,
    TaskProvisioningRetryableError,
    TaskProvisioningSupersededError,
    _linked_task_id,
    authorize_assessment_task_provisioning,
    claim_assessment_task_provisioning,
    finish_assessment_task_provisioning,
    provisioning_state_is_due,
    request_assessment_task_provisioning,
    role_has_active_task,
    role_has_linked_task,
    task_provisioning_state,
)
from .task_spec_generator import generate_task_spec

logger = logging.getLogger("taali.task_provisioning")

# Cheap heuristic: does this role write code, or produce documents/decisions?
# Drives the generator's deliverable.kind hint. Conservative — when unsure,
# the generator still decides; this only nudges.
_CODE_ROLE_HINTS = (
    "engineer", "developer", "data ", "ml ", "ai engineer", "backend",
    "frontend", "full-stack", "fullstack", "devops", "sre", "platform",
    "software", "programmer", "sql", "analytics engineer",
)
_DOC_ROLE_HINTS = (
    "manager", "product", "scrum", "governance", "lead", "director",
    "strategy", "operations", "customer success", "sales", "analyst",
    "specialist", "consultant", "owner", "architect",
)

MIN_ASSESSMENT_INPUT_CHARS = 120


def _slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return s or "role"


def _deliverable_kind_hint(role_name: str, jd_text: str) -> Optional[str]:
    blob = f"{role_name} {jd_text}".lower()
    code_hit = any(h in blob for h in _CODE_ROLE_HINTS)
    doc_hit = any(h in blob for h in _DOC_ROLE_HINTS)
    if code_hit and not doc_hit:
        return "code"
    if doc_hit and not code_hit:
        return "doc"
    return None  # let the generator decide


def role_assessment_input_text(role: Role) -> str:
    """Build the persisted role context used to author an assessment.

    A requisition is structurally rich even when a caller supplies a terse
    rendered public heading. Include its saved criteria and first-class role
    attributes so Turn on does not later reject an otherwise valid published
    job merely because the Markdown snapshot alone is short. Manual roles with
    no meaningful spec/criteria still fail the minimum-input guard below.
    """

    parts = [
        str(getattr(role, "job_spec_text", "") or ""),
        str(getattr(role, "name", "") or ""),
        str(getattr(role, "description", "") or ""),
    ]
    attribute_labels = (
        ("Employment type", "employment_type"),
        ("Workplace type", "workplace_type"),
        ("Department", "department"),
        ("Location city", "location_city"),
        ("Location country", "location_country"),
    )
    for label, field in attribute_labels:
        value = str(getattr(role, field, "") or "").strip()
        if value:
            parts.append(f"{label}: {value}")
    try:
        criteria = sorted(
            (
                criterion
                for criterion in (getattr(role, "criteria", None) or [])
                if getattr(criterion, "deleted_at", None) is None
            ),
            key=lambda criterion: (
                int(getattr(criterion, "ordering", 0) or 0),
                int(getattr(criterion, "id", 0) or 0),
            ),
        )
    except Exception:
        criteria = []
    for criterion in criteria:
        text = str(getattr(criterion, "text", "") or "").strip()
        if text:
            bucket = str(getattr(criterion, "bucket", "criterion") or "criterion")
            parts.append(f"{bucket.title()}: {text}")
    return "\n\n".join(part.strip() for part in parts if part.strip()).strip()


# Compatibility for the draft-revision and automatic-repair paths.  The rich
# requisition-aware input builder replaced the old JD-only helper, but these
# callers still import the private name while they re-author an existing task.
# Keep them on the same canonical input rather than reintroducing a second,
# narrower rendering path.
_role_jd_text = role_assessment_input_text


def generate_and_link_task_for_role(
    db: Session,
    role: Role,
    *,
    api_key: str,
    organization_id: int,
    create_repo: bool = True,
    claim_token: str | None = None,
) -> Optional[Task]:
    """Generate, persist (as a draft), and link an assessment task for ``role``.

    ``None`` has one meaning only: an idempotent delivery found a task already
    linked. Insufficient persisted input raises ``TaskProvisioningBlockedError``;
    generator/database failures raise ``TaskProvisioningRetryableError`` so the
    worker can record and retry them instead of reporting a false no-op.
    """
    if role_has_linked_task(role):
        logger.info("role %s already has a linked task; skipping generation", role.id)
        return None

    jd_text = role_assessment_input_text(role)
    if len(jd_text) < MIN_ASSESSMENT_INPUT_CHARS:
        raise TaskProvisioningBlockedError(
            "role JD is too thin to generate an assessment "
            f"({len(jd_text)} chars; minimum {MIN_ASSESSMENT_INPUT_CHARS})"
        )

    role_name = str(getattr(role, "name", "") or "Role")
    role_slug = _slugify(role_name)

    try:
        result = generate_task_spec(
            role_name=role_name,
            role_slug=role_slug,
            jd_text=jd_text,
            api_key=api_key,
            organization_id=int(organization_id),
            role_id=int(role.id),
            deliverable_kind_hint=_deliverable_kind_hint(role_name, jd_text),
        )
    except Exception as exc:
        logger.exception("task generation raised for role %s", role.id)
        raise TaskProvisioningRetryableError(
            f"task generator raised {type(exc).__name__}: {exc}"
        ) from exc

    if not result.valid or not result.spec:
        logger.warning(
            "task generation invalid for role %s after %d attempt(s): %s",
            role.id, result.attempts, "; ".join(result.errors[:3]),
        )
        detail = "; ".join(result.errors[:3]) or "generator returned no valid spec"
        raise TaskProvisioningRetryableError(
            f"task generation remained invalid after {result.attempts} attempt(s): {detail}"
        )

    spec = result.spec
    # Re-lock immediately before persistence. Generation is deliberately done
    # without holding a row lock; the shared Organization -> Role boundary
    # serializes this new link with assessment sends and recruiter unlinking.
    from .task_mutation_guard import lock_task_mutation_boundary

    boundary = lock_task_mutation_boundary(
        db,
        role_ids=[int(role.id)],
        organization_ids=[int(organization_id)],
    )
    locked_role = boundary.role(int(role.id))
    if locked_role is None:
        db.rollback()
        raise TaskProvisioningSupersededError("role was removed during generation")
    if claim_token:
        state = task_provisioning_state(locked_role)
        if str(state.get("claim_token") or "") != str(claim_token):
            db.rollback()
            raise TaskProvisioningSupersededError(
                "a newer role publish superseded this generation claim"
            )
    if _linked_task_id(db, int(role.id)) is not None:
        db.rollback()
        return None

    try:
        task = _persist_generated_task(
            db, spec, organization_id=int(organization_id)
        )
        _link_role_task(db, role_id=int(role.id), task_id=int(task.id))
        db.commit()
        db.refresh(task)
    except TaskProvisioningRetryableError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("failed to persist/link generated task for role %s", role.id)
        raise TaskProvisioningRetryableError(
            f"failed to persist/link generated assessment: {type(exc).__name__}: {exc}"
        ) from exc

    if create_repo:
        _provision_repo_best_effort(task)
    logger.info(
        "generated draft task %s (task_key=%s) for role %s; needs review",
        task.id, task.task_key, role.id,
    )
    return task


def _persist_generated_task(
    db: Session, spec: Dict[str, Any], *, organization_id: int
) -> Task:
    """Build + persist an org-owned DRAFT Task from a validated spec."""
    from .task_catalog import PERSISTED_TASK_SPEC_KEYS

    task_key = str(spec.get("task_id") or "").strip()
    if not task_key:
        raise TaskProvisioningRetryableError(
            "validated task spec did not include task_id"
        )
    # Avoid collision with an existing key for this org.
    existing = (
        db.query(Task)
        .filter(Task.task_key == task_key, Task.organization_id == organization_id)
        .first()
    )
    if existing is not None:
        # Re-publish archives the old generated draft, so the generator may
        # legitimately emit the same task_id again. Include an entropy suffix
        # instead of reusing ``_<org>`` (which collided on the third revision
        # and could point two drafts at the same template repository).
        task_key = f"{task_key}_{int(organization_id)}_{uuid.uuid4().hex[:8]}"

    extra_data = {k: v for k, v in spec.items() if k not in PERSISTED_TASK_SPEC_KEYS}
    extra_data["generated"] = True
    extra_data["needs_review"] = True
    from .task_battle_test import initialize_battle_test_provisioning

    extra_data = initialize_battle_test_provisioning(extra_data)

    scenario = spec.get("scenario")
    task = Task(
        organization_id=organization_id,
        name=spec.get("name", task_key),
        description=(scenario[:500] if isinstance(scenario, str) else None),
        task_type=spec.get("role") or "general",
        difficulty="medium",
        duration_minutes=spec.get("duration_minutes", 30),
        is_template=False,
        # DRAFT: not live until a recruiter reviews + activates it.
        is_active=False,
        calibration_prompt=spec.get("calibration_prompt"),
        task_key=task_key,
        role=spec.get("role"),
        scenario=scenario,
        repo_structure=spec.get("repo_structure"),
        evaluation_rubric=spec.get("evaluation_rubric"),
        extra_data=extra_data,
    )
    db.add(task)
    try:
        db.flush()
        db.refresh(task)
    except Exception as exc:
        logger.exception("failed to persist generated task key=%s", task_key)
        raise TaskProvisioningRetryableError(
            f"failed to persist generated task key={task_key}: {type(exc).__name__}: {exc}"
        ) from exc
    return task


def _provision_repo_best_effort(task: Task) -> None:
    from ..platform.config import settings
    try:
        from .assessment_repository_service import AssessmentRepositoryService
        from .task_repo_service import recreate_task_main_repo

        recreate_task_main_repo(task)
        repo_service = AssessmentRepositoryService(settings.GITHUB_ORG, settings.GITHUB_TOKEN)
        repo_service.create_template_repo(task)
    except Exception:
        # A generated draft is still useful for review without its repo;
        # the repo can be (re)created when the recruiter activates it.
        logger.warning("template repo provisioning failed for generated task %s", task.id, exc_info=True)


def _link_role_task(db: Session, *, role_id: int, task_id: int) -> None:
    try:
        db.execute(
            text(
                "INSERT INTO role_tasks (role_id, task_id) VALUES (:r, :t) "
                "ON CONFLICT (role_id, task_id) DO NOTHING"
            ),
            {"r": role_id, "t": task_id},
        )
    except Exception as exc:
        logger.exception("failed to link role %s ↔ task %s", role_id, task_id)
        raise TaskProvisioningRetryableError(
            f"failed to link role {role_id} to generated task {task_id}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


__all__ = [
    "PROVISIONING_AWAITING_ACTIVATION",
    "PROVISIONING_BLOCKED",
    "PROVISIONING_FAILED",
    "PROVISIONING_PENDING",
    "PROVISIONING_RECOVERABLE_STATUSES",
    "PROVISIONING_RETRY_WAIT",
    "PROVISIONING_RUNNING",
    "PROVISIONING_STALE_AFTER",
    "PROVISIONING_SUCCEEDED",
    "TaskProvisioningBlockedError",
    "TaskProvisioningClaim",
    "TaskProvisioningError",
    "TaskProvisioningRetryableError",
    "TaskProvisioningSupersededError",
    "claim_assessment_task_provisioning",
    "authorize_assessment_task_provisioning",
    "finish_assessment_task_provisioning",
    "generate_and_link_task_for_role",
    "_role_jd_text",
    "_slugify",
    "provisioning_state_is_due",
    "request_assessment_task_provisioning",
    "role_has_active_task",
    "role_has_linked_task",
    "task_provisioning_state",
]
