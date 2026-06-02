"""Auto-provision an assessment task for a role from its JD.

Closes the "new job → no task → assessment send is misconfigured" gap.
When a role has no linked task, this generates one (via the JD→spec
generator), persists it as an org-owned DRAFT (is_active=False, flagged
for human review), provisions its template repo, and links it to the
role. A recruiter approves the draft before it goes live to candidates —
auto-creation is automatic; going-live is gated.

Cost-safety: generation is a multi-call Sonnet operation (~$0.10-0.30).
This service is invoked behind the ``AUTO_GENERATE_ASSESSMENT_TASKS``
flag (default off) or on explicit recruiter request, never unbounded.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.task import Task
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


def _role_jd_text(role: Role) -> str:
    parts = [
        str(getattr(role, "job_spec_text", "") or ""),
        str(getattr(role, "description", "") or ""),
    ]
    text_blob = "\n\n".join(p for p in parts if p.strip())
    return text_blob.strip()


def role_has_active_task(db: Session, role: Role) -> bool:
    """True if the role already links at least one active task."""
    try:
        tasks = list(getattr(role, "tasks", None) or [])
    except Exception:
        tasks = []
    return any(getattr(t, "is_active", False) for t in tasks)


def generate_and_link_task_for_role(
    db: Session,
    role: Role,
    *,
    api_key: str,
    organization_id: int,
    create_repo: bool = True,
) -> Optional[Task]:
    """Generate, persist (as a draft), and link an assessment task for ``role``.

    Returns the created Task, or None if the role already has a task, the
    JD is too thin, or generation failed. The Task is created
    ``is_active=False`` with ``extra_data.generated=True`` and
    ``needs_review=True`` so it does not go live until a recruiter approves.

    Never raises — provisioning failures are logged and return None.
    """
    if role_has_active_task(db, role):
        logger.info("role %s already has an active task; skipping generation", role.id)
        return None

    jd_text = _role_jd_text(role)
    if len(jd_text) < 120:
        logger.info("role %s JD too thin (%d chars) to generate a task", role.id, len(jd_text))
        return None

    role_name = str(getattr(role, "name", "") or "Role")
    role_slug = _slugify(role_name)

    try:
        result = generate_task_spec(
            role_name=role_name,
            role_slug=role_slug,
            jd_text=jd_text,
            api_key=api_key,
            organization_id=int(organization_id),
            deliverable_kind_hint=_deliverable_kind_hint(role_name, jd_text),
        )
    except Exception:
        logger.exception("task generation raised for role %s", role.id)
        return None

    if not result.valid or not result.spec:
        logger.warning(
            "task generation invalid for role %s after %d attempt(s): %s",
            role.id, result.attempts, "; ".join(result.errors[:3]),
        )
        return None

    spec = result.spec
    task = _persist_generated_task(db, spec, organization_id=int(organization_id))
    if task is None:
        return None

    if create_repo:
        _provision_repo_best_effort(task)

    _link_role_task(db, role_id=int(role.id), task_id=int(task.id))
    logger.info(
        "generated draft task %s (task_key=%s) for role %s; needs review",
        task.id, task.task_key, role.id,
    )
    return task


def _persist_generated_task(
    db: Session, spec: Dict[str, Any], *, organization_id: int
) -> Optional[Task]:
    """Build + persist an org-owned DRAFT Task from a validated spec."""
    from .task_catalog import PERSISTED_TASK_SPEC_KEYS

    task_key = str(spec.get("task_id") or "").strip()
    if not task_key:
        return None
    # Avoid collision with an existing key for this org.
    existing = (
        db.query(Task)
        .filter(Task.task_key == task_key, Task.organization_id == organization_id)
        .first()
    )
    if existing is not None:
        task_key = f"{task_key}_{int(organization_id)}"

    extra_data = {k: v for k, v in spec.items() if k not in PERSISTED_TASK_SPEC_KEYS}
    extra_data["generated"] = True
    extra_data["needs_review"] = True

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
    try:
        db.add(task)
        db.flush()
        db.refresh(task)
        return task
    except Exception:
        db.rollback()
        logger.exception("failed to persist generated task key=%s", task_key)
        return None


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
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("failed to link role %s ↔ task %s", role_id, task_id)


__all__ = [
    "generate_and_link_task_for_role",
    "role_has_active_task",
]
