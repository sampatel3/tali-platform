"""Workable webhook endpoint."""
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from ....platform.database import get_db
from ....platform.config import settings
from ....models.organization import Organization
from ....models.candidate import Candidate
from ....models.assessment import Assessment
from ....models.task import Task

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/workable")
async def workable_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle incoming Workable webhooks."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    if not settings.WORKABLE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Workable webhook secret is not configured")
    signature = request.headers.get("X-Workable-Signature", "")
    body = await request.body()

    expected = hmac.new(
        settings.WORKABLE_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    data = await request.json()
    event_type = data.get("type")

    if event_type == "candidate_stage_changed":
        subdomain = data.get("account", {}).get("subdomain")
        org = db.query(Organization).filter(Organization.workable_subdomain == subdomain).first()
        if org and org.workable_config and org.workable_config.get("auto_send_on_stage"):
            target_stage = org.workable_config.get("auto_send_stage")
            if data.get("stage") == target_stage:
                candidate_payload = data.get("candidate") or {}
                email = candidate_payload.get("email")
                name = candidate_payload.get("name") or candidate_payload.get("firstname")
                if email:
                    candidate = db.query(Candidate).filter(
                        Candidate.organization_id == org.id,
                        Candidate.email == email,
                    ).first()
                    if not candidate:
                        candidate = Candidate(
                            organization_id=org.id,
                            email=email,
                            full_name=name or email,
                            workable_candidate_id=str(candidate_payload.get("id") or ""),
                            workable_data=candidate_payload,
                        )
                        db.add(candidate)
                        db.flush()
                    task = db.query(Task).filter(
                        Task.organization_id == org.id,
                        Task.is_active == True,  # noqa: E712
                    ).first()
                    if task:
                        assessment = Assessment(
                            organization_id=org.id,
                            candidate_id=candidate.id,
                            task_id=task.id,
                            token=secrets.token_urlsafe(32),
                            duration_minutes=task.duration_minutes or 30,
                            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                            workable_candidate_id=str(candidate_payload.get("id") or ""),
                        )
                        db.add(assessment)
                        db.commit()

    return {"status": "received"}
