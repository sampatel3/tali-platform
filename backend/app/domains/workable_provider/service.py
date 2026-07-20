"""Workable Assessments-Provider service: provisioning, grade mapping, result sweep.

Self-contained on purpose — reuses the importable building blocks (creation
gate, repository branch, invite dispatch, share links) rather than refactoring
the recruiter ``create_assessment`` hot path, so the add-on is purely additive.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ...components.assessments.repository import utcnow
from ...components.assessments.service import (
    _enforce_artifact_first_task,
    get_assessment_creation_gate,
)
from ...components.assessments.task_snapshot import freeze_assessment_task
from ...domains.integrations_notifications.invite_flow import (
    dispatch_assessment_invite,
)
from ...models.assessment import Assessment
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.share_link import SHARE_LINK_MODE_CLIENT, ShareLink
from ...models.task import Task
from ...platform.config import settings
from ...services.agent_policy_settings import apply_workspace_agent_defaults
from ...services.pre_screening_snapshot import pre_screen_snapshot
from . import outbox
from .schemas import WorkableCandidate

logger = logging.getLogger("taali.workable_provider")


class ProviderError(Exception):
    """A provider-contract error carrying an HTTP-ish code + message."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- Grade mapping (taali_score → Workable grade) -------------------------
# Sam's calls: taali_score is the number; a "maybe" (LEAN_NO) is NOT a pass.
#   >= 85  -> excelled  ("definitely yes")  [STRONG_YES]
#   >= 70  -> passed    ("yes")             [YES]
#   <  70  -> failed    ("no")              [LEAN_NO / NO]
def grade_for_score(score: Optional[float]) -> str:
    if score is None:
        return "failed"
    if score >= 85:
        return "excelled"
    if score >= 70:
        return "passed"
    return "failed"


# ---- Catalog --------------------------------------------------------------
def list_provider_tests(db: Session, organization_id: int) -> list[dict]:
    tasks = (
        db.query(Task)
        .filter(
            Task.is_active.is_(True),
            (
                (Task.organization_id == organization_id)
                | (Task.is_template.is_(True))
            ),
        )
        .order_by(Task.name.asc())
        .all()
    )
    return [{"id": t.task_key or str(t.id), "name": t.name} for t in tasks]


def _resolve_task(db: Session, organization_id: int, test_id: str) -> Task:
    base = db.query(Task).filter(
        Task.is_active.is_(True),
        (
            (Task.organization_id == organization_id)
            | (Task.is_template.is_(True))
        ),
    )
    task = base.filter(Task.task_key == test_id).first()
    if task is None and test_id.isdigit():
        task = base.filter(Task.id == int(test_id)).first()
    if task is None:
        raise ProviderError(422, f"Unknown test_id: {test_id}")
    return task


def _resolve_or_provision_role(
    db: Session,
    organization_id: int,
    *,
    job_shortcode: Optional[str],
    job_title: Optional[str],
) -> Role:
    """Find the Taali role for a Workable job, auto-provisioning one keyed on
    ``workable_job_id`` on first use (Sam's call: a Taali role per Workable job)."""
    role = None
    if job_shortcode:
        role = (
            db.query(Role)
            .filter(
                Role.organization_id == organization_id,
                Role.workable_job_id == job_shortcode,
            )
            .first()
        )
    if role is None:
        org = (
            db.query(Organization)
            .filter(Organization.id == int(organization_id))
            .one_or_none()
        )
        role = Role(
            organization_id=organization_id,
            name=job_title
            or (f"Workable job {job_shortcode}" if job_shortcode else "Workable assessment"),
            source="workable_marketplace",
            workable_job_id=job_shortcode,
        )
        apply_workspace_agent_defaults(role, org)
        db.add(role)
        db.flush()
    return role


def _find_or_create_candidate(
    db: Session, organization_id: int, cand: WorkableCandidate
) -> Candidate:
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.email == cand.email,
            Candidate.organization_id == organization_id,
        )
        .first()
    )
    full_name = (
        " ".join(p for p in [cand.first_name, cand.last_name] if p).strip()
        or None
    )
    if candidate is None:
        candidate = Candidate(
            email=cand.email,
            full_name=full_name,
            organization_id=organization_id,
            phone=cand.phone,
        )
        db.add(candidate)
        db.flush()
    elif full_name and not candidate.full_name:
        candidate.full_name = full_name
    return candidate


def _find_or_create_application(
    db: Session, organization_id: int, candidate_id: int, role_id: int
) -> CandidateApplication:
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.candidate_id == candidate_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.organization_id == organization_id,
        )
        .first()
    )
    if app is None:
        app = CandidateApplication(
            organization_id=organization_id,
            candidate_id=candidate_id,
            role_id=role_id,
            source="workable_marketplace",
        )
        db.add(app)
        db.flush()
    return app


def provision_assessment(
    db: Session,
    *,
    organization_id: int,
    test_id: str,
    callback_url: str,
    candidate: WorkableCandidate,
    job_shortcode: Optional[str] = None,
    job_title: Optional[str] = None,
    duration_minutes: int = 30,
) -> Assessment:
    """Create a Taali assessment from Workable's POST /assessments and email the
    candidate their link. Enqueues a 'pending' callback. Returns the row."""
    task = _resolve_task(db, organization_id, test_id)

    role = _resolve_or_provision_role(
        db, organization_id, job_shortcode=job_shortcode, job_title=job_title
    )
    gate = get_assessment_creation_gate(
        organization_id,
        db,
        role_id=int(role.id),
        lock_organization=True,
    )
    if not gate.get("can_create"):
        raise ProviderError(402, gate.get("message") or "Assessment quota exhausted")
    org = gate.get("organization") or db.query(Organization).filter(
        Organization.id == organization_id
    ).first()

    cand = _find_or_create_candidate(db, organization_id, candidate)
    application = _find_or_create_application(db, organization_id, cand.id, role.id)

    _enforce_artifact_first_task(task)
    assessment = Assessment(
        organization_id=organization_id,
        candidate_id=cand.id,
        task_id=task.id,
        role_id=role.id,
        application_id=application.id,
        token=secrets.token_urlsafe(32),
        duration_minutes=duration_minutes,
        expires_at=utcnow() + timedelta(days=settings.ASSESSMENT_EXPIRY_DAYS),
        workable_candidate_id=getattr(cand, "workable_candidate_id", None),
        workable_job_id=role.workable_job_id,
        workable_callback_url=callback_url,
        invite_channel="workable_marketplace",
    )
    freeze_assessment_task(assessment, task)
    db.add(assessment)
    db.flush()

    # Tell Workable the assessment is pending (durable via the outbox).
    outbox.enqueue(
        db,
        organization_id=organization_id,
        event_kind="pending",
        dedup_key=f"wkb-assessment-{assessment.id}-pending",
        callback_url=callback_url,
        payload={"status": "pending"},
    )
    if org:
        # The Assessment row is also the durable email-invite outbox. Record
        # this before the one producer commit so provisioning can never succeed
        # without a recoverable candidate-delivery intent.
        dispatch_assessment_invite(
            assessment=assessment,
            org=org,
            candidate_email=cand.email,
            candidate_name=cand.full_name or cand.email,
            position=task.name or "Technical assessment",
            reply_to=None,
            pipeline_source="agent",
            pipeline_actor_type="system",
            pipeline_reason="Workable marketplace assessment invite sent",
            pipeline_metadata={"assessment_mode": "workable_marketplace"},
        )
    db.commit()
    db.refresh(assessment)

    return assessment


def candidate_link(assessment: Assessment) -> str:
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/assessment/{assessment.id}?token={assessment.token}"


# ---- Result sweep ---------------------------------------------------------
def _client_share_link(db: Session, assessment: Assessment) -> Optional[str]:
    if not assessment.application_id:
        return None
    link = ShareLink(
        organization_id=assessment.organization_id,
        application_id=assessment.application_id,
        created_by_user_id=None,
        token=f"shr_{secrets.token_urlsafe(24)}",
        mode=SHARE_LINK_MODE_CLIENT,
        expiry_preset="30d",
        expires_at=_now() + timedelta(days=30),
    )
    db.add(link)
    db.flush()
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/share/{link.token}"


def _completed_payload(db: Session, a: Assessment) -> dict:
    score = a.taali_score if a.taali_score is not None else a.final_score
    results_url = _client_share_link(db, a)
    try:
        snap = pre_screen_snapshot(a.application) if a.application else {}
        summary = snap.get("pre_screen_recommendation")
    except Exception:
        summary = None
    payload: dict[str, Any] = {
        "status": "completed",
        "assessment": {
            "score": None if score is None else str(round(float(score))),
            "grade": grade_for_score(score),
            "summary": summary or "Assessment completed.",
        },
    }
    if results_url:
        payload["results_url"] = results_url
    return payload


def enqueue_completed_results(db: Session, *, batch_size: int = 100) -> dict:
    """Enqueue a 'completed' callback for each scored provider assessment not yet
    pushed. Idempotent: marks ``workable_provider_pushed_at``."""
    rows = (
        db.query(Assessment)
        .filter(
            Assessment.workable_callback_url.isnot(None),
            Assessment.scored_at.isnot(None),
            Assessment.workable_provider_pushed_at.is_(None),
        )
        .order_by(Assessment.scored_at.asc())
        .limit(int(batch_size))
        .all()
    )
    enqueued = 0
    for a in rows:
        outbox.enqueue(
            db,
            organization_id=a.organization_id,
            event_kind="completed",
            dedup_key=f"wkb-assessment-{a.id}-completed",
            callback_url=a.workable_callback_url,
            payload=_completed_payload(db, a),
        )
        a.workable_provider_pushed_at = _now()
        enqueued += 1
    db.commit()
    return {"scanned": len(rows), "enqueued": enqueued}
