"""Experimental task-selection capability retained from Amendment A2.

Decides whether to send an assessment task, skip it (existing artifacts
cover the dimensions), or request artifacts (no good template match).

This module is deliberately **not** registered with the production sub-agent
runtime.  No production caller consumes its ``TaskSelection`` result.  The
live assessment-send path instead uses
``services.experiment_assignment.resolve_task_and_variant``, which limits a
choice to active tasks linked to the role, preserves stable experiment arms,
and then passes through the existing HITL, budget, repository-readiness, and
delivery guards.

The deterministic implementation and contracts remain available for offline
evaluation and a future product decision.  Connecting it safely would require
an explicit adapter from all three outcomes below to the current assessment
workflow; the original ``request_artifacts`` action was removed as unused in
cleanup #826.  Treating registration alone as integration would be unsafe.

Decision algorithm (pre-pilot, deterministic):

  1. Fetch the candidate's existing artifact dimensions via the
     ``RoleIntent`` (when available) plus the heuristic candidate-
     profile dimensions (skills/work history). If the intent dimensions
     are already covered by the candidate's CV-evidenced strengths,
     return ``skip_task`` with a structured reason.
  2. Pull eligible templates for the role (Task rows with
     ``is_template=True`` and matching role family).
  3. For each, look up the ``task_calibrations`` row for the role
     family. Score = predictive_quality * sqrt(min(n, 30)/30).
  4. Pick the best-scoring template. If the best score is below
     ``MIN_SELECTION_QUALITY`` (no calibrated template exists yet),
     return ``request_artifacts`` instead of guessing.
  5. Otherwise return ``send_task`` with the chosen template id.

The LLM path is reserved for v10 (``bidirectional_subagents``); this
implementation is rule-driven and inspectable.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..agent_runtime.contracts import TaskSelection
from ..agent_runtime.role_intent import fetch_active_intent
from ..cv_matching.calibrators.extractor import _default_role_family_mapper
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.task import Task
from ..models.task_calibration import TaskCalibration
from ..platform.database import SessionLocal
from .base import SubAgent, SubAgentRequest, SubAgentResult


logger = logging.getLogger("taali.sub_agents.task_selection")


# Minimum (calibrated) template score below which we'd rather ask for
# artifacts than send a task we don't trust. Below this floor the system
# admits it doesn't know rather than guessing — Section A2.5.
MIN_SELECTION_QUALITY = 0.20
# Sample-size at which a template's calibration becomes fully weighted.
# Below this we shrink linearly.
CALIBRATION_SATURATION_N = 30
AGENT_VERSION = "task_selection-v1"
TASK_SELECTION_RUNTIME_STATUS = "experimental_unwired"


def _intent_dimensions(intent) -> set[str]:
    """Flatten the intent's structured fields into a dimension set."""
    if intent is None:
        return set()
    parts: list[str] = []
    parts.extend(intent.structured.soft_signals)
    parts.extend(intent.structured.deal_breakers)
    parts.extend(intent.structured.must_haves_missing_from_spec)
    out: set[str] = set()
    for p in parts:
        out |= {tok.strip().lower() for tok in p.replace(",", " ").split() if tok.strip()}
    return out


def _candidate_evidenced_dimensions(candidate: Candidate) -> set[str]:
    """Cheap heuristic: declared skills + experience industries form the
    candidate's evidenced-dimension set.
    """
    out: set[str] = set()
    for s in candidate.skills or []:
        out.add(str(s).strip().lower())
    for entry in candidate.experience_entries or []:
        if isinstance(entry, dict):
            for key in ("title", "industry"):
                value = entry.get(key)
                if value:
                    out |= {tok.strip().lower() for tok in str(value).split() if tok.strip()}
    return out


def _score_template(
    calibration: TaskCalibration | None,
) -> float:
    """Map a calibration row to a [0, 1] selection score.

    No calibration row → score = 0 (the agent treats it as "no signal").
    """
    if calibration is None or calibration.retired_at is not None:
        return 0.0
    n = max(0, int(calibration.sample_size or 0))
    shrinkage = math.sqrt(min(n, CALIBRATION_SATURATION_N) / float(CALIBRATION_SATURATION_N))
    # predictive_quality is in [-1, 1]; clamp negatives to 0 (a
    # negatively-correlated template is worse than no signal).
    pq = max(0.0, float(calibration.predictive_quality or 0.0))
    return pq * shrinkage


def _select_template(
    db: Session,
    *,
    organization_id: int,
    role_family: str,
) -> tuple[Task | None, float]:
    """Pick the highest-scoring template for the role family.

    Returns ``(None, 0.0)`` when no eligible template clears the floor.
    """
    candidates = (
        db.query(Task)
        .filter(
            Task.organization_id == organization_id,
            Task.is_template.is_(True),
            Task.is_active.is_(True),
        )
        .all()
    )
    if not candidates:
        return None, 0.0
    best_task: Task | None = None
    best_score = -1.0
    for task in candidates:
        cal = (
            db.query(TaskCalibration)
            .filter(
                TaskCalibration.task_id == task.id,
                TaskCalibration.role_family == role_family,
            )
            .first()
        )
        score = _score_template(cal)
        if score > best_score:
            best_score = score
            best_task = task
    return best_task, max(0.0, best_score)


class TaskSelectionSubAgent:
    name = "task_selection"

    def run(self, req: SubAgentRequest, *, db: Session | None = None) -> SubAgentResult:
        session = db or SessionLocal()
        owns = db is None
        try:
            return self._run(req, session)
        except Exception:  # pragma: no cover — defensive
            logger.exception("task_selection sub-agent crashed")
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error="task_selection_failed",
            )
        finally:
            if owns:
                session.close()

    def _run(self, req: SubAgentRequest, db: Session) -> SubAgentResult:
        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == req.application_id,
                CandidateApplication.organization_id == req.organization_id,
            )
            .one_or_none()
        )
        if app is None:
            return SubAgentResult(
                sub_agent=self.name,
                ok=False,
                error="application_not_found",
            )
        candidate = (
            db.query(Candidate).filter(Candidate.id == app.candidate_id).one_or_none()
        )
        role = db.query(Role).filter(Role.id == req.role_id).one_or_none()
        if candidate is None or role is None:
            return SubAgentResult(
                sub_agent=self.name, ok=False, error="candidate_or_role_not_found"
            )

        role_family = _default_role_family_mapper(role.name)
        intent = fetch_active_intent(db, role_id=int(req.role_id))

        intent_dims = _intent_dimensions(intent)
        cand_dims = _candidate_evidenced_dimensions(candidate)

        # 1. Skip path: candidate's evidenced dimensions cover at least
        # 80% of the intent dimensions (when the intent has any).
        if intent_dims:
            overlap = intent_dims & cand_dims
            coverage = len(overlap) / max(1, len(intent_dims))
            if coverage >= 0.8:
                selection = TaskSelection(
                    application_id=int(req.application_id),
                    decision="skip_task",
                    skip_reason=(
                        f"existing skill/history coverage of {len(overlap)} "
                        f"of {len(intent_dims)} intent dimensions"
                    ),
                    reasoning=(
                        f"Candidate already evidences "
                        f"{sorted(overlap)[:5]} from the role intent — "
                        f"no task needed."
                    ),
                    selected_at=datetime.now(timezone.utc),
                    agent_version=AGENT_VERSION,
                    uncertainty=0.1,
                )
                return _result(selection)

        # 2 + 3. Score eligible templates.
        best_task, best_score = _select_template(
            db, organization_id=int(req.organization_id), role_family=role_family
        )

        # 4. Below floor → request artifacts instead of guessing.
        if best_task is None or best_score < MIN_SELECTION_QUALITY:
            requested = sorted(intent_dims)[:5] or ["work_samples", "code_review"]
            selection = TaskSelection(
                application_id=int(req.application_id),
                decision="request_artifacts",
                requested_artifacts=requested,
                reasoning=(
                    f"No template for role_family={role_family} clears "
                    f"the selection floor ({best_score:.2f} < {MIN_SELECTION_QUALITY}); "
                    f"requesting artifacts instead of sending a "
                    f"low-confidence task."
                ),
                selected_at=datetime.now(timezone.utc),
                agent_version=AGENT_VERSION,
                uncertainty=0.6,
            )
            return _result(selection)

        # 5. Send.
        selection = TaskSelection(
            application_id=int(req.application_id),
            decision="send_task",
            chosen_template_id=int(best_task.id),
            reasoning=(
                f"Template '{best_task.name}' for role_family={role_family} "
                f"scored {best_score:.2f}."
            ),
            selected_at=datetime.now(timezone.utc),
            agent_version=AGENT_VERSION,
            uncertainty=max(0.0, min(1.0, 1.0 - best_score)),
        )
        return _result(selection)


def _result(selection: TaskSelection) -> SubAgentResult:
    payload = selection.model_dump(mode="json")
    return SubAgentResult(
        sub_agent="task_selection",
        ok=True,
        output=payload,
        confidence=1.0 - selection.uncertainty,
        uncertainty=selection.uncertainty,
    )


TASK_SELECTION_SUB_AGENT: SubAgent = TaskSelectionSubAgent()


__all__ = [
    "AGENT_VERSION",
    "CALIBRATION_SATURATION_N",
    "MIN_SELECTION_QUALITY",
    "TASK_SELECTION_SUB_AGENT",
    "TASK_SELECTION_RUNTIME_STATUS",
    "TaskSelectionSubAgent",
]
