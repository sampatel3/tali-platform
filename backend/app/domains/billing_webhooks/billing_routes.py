"""Billing: usage history, cost observability, and Lemon credit checkout."""
from datetime import datetime, timedelta, timezone
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel

from ...components.integrations.lemon.service import LemonService
from ...platform.database import get_db
from ...deps import get_current_user
from ...platform.config import settings
from ...models.billing_credit_ledger import BillingCreditLedger
from ...models.user import User
from ...models.organization import Organization
from ...models.assessment import Assessment, AssessmentStatus
from ...services.credit_ledger_service import lemon_pack_catalog, resolve_pack

router = APIRouter(prefix="/billing", tags=["Billing"])


class CheckoutSessionCreate(BaseModel):
    success_url: str
    cancel_url: str
    pack_id: str = "starter_5"


def _safe_json_size_bytes(payload) -> int:
    if payload is None:
        return 0
    try:
        return len(json.dumps(payload, default=str).encode("utf-8"))
    except Exception:
        return 0


def _assessment_currency_code() -> str:
    return (settings.ASSESSMENT_PRICE_CURRENCY or "aed").upper()


def _duration_hours(assessment: Assessment) -> float:
    if assessment.total_duration_seconds:
        return max(assessment.total_duration_seconds / 3600.0, 0.0)
    if assessment.started_at and assessment.completed_at:
        delta = assessment.completed_at - assessment.started_at
        return max(delta.total_seconds() / 3600.0, 0.0)
    if assessment.started_at and assessment.status == AssessmentStatus.IN_PROGRESS:
        delta = datetime.now(timezone.utc) - assessment.started_at
        return max(delta.total_seconds() / 3600.0, 0.0)
    return 0.0


def _compute_assessment_cost_usd(assessment: Assessment) -> dict:
    input_tokens = int(assessment.total_input_tokens or 0)
    output_tokens = int(assessment.total_output_tokens or 0)

    claude_input_cost = (
        input_tokens / 1_000_000.0
    ) * settings.CLAUDE_INPUT_COST_PER_MILLION_USD
    claude_output_cost = (
        output_tokens / 1_000_000.0
    ) * settings.CLAUDE_OUTPUT_COST_PER_MILLION_USD

    e2b_cost = _duration_hours(assessment) * settings.E2B_COST_PER_HOUR_USD

    # One invite email and one completion/results email for completed assessments.
    email_events = 2 if assessment.status == AssessmentStatus.COMPLETED else 1
    email_cost = email_events * settings.EMAIL_COST_PER_SEND_USD

    stored_bytes = (
        _safe_json_size_bytes(assessment.test_results)
        + _safe_json_size_bytes(assessment.ai_prompts)
        + _safe_json_size_bytes(assessment.code_snapshots)
        + _safe_json_size_bytes(assessment.timeline)
        + _safe_json_size_bytes(assessment.prompt_analytics)
        + _safe_json_size_bytes(assessment.score_breakdown)
    )
    if assessment.cv_file_url:
        # Unknown file size in DB: use small fallback estimate if CV exists.
        stored_bytes += 250_000

    storage_gb_month = (stored_bytes / (1024 ** 3)) * (
        settings.STORAGE_RETENTION_DAYS_DEFAULT / 30.0
    )
    storage_cost = storage_gb_month * settings.STORAGE_COST_PER_GB_MONTH_USD

    total = claude_input_cost + claude_output_cost + e2b_cost + email_cost + storage_cost

    return {
        "claude": round(claude_input_cost + claude_output_cost, 6),
        "claude_input": round(claude_input_cost, 6),
        "claude_output": round(claude_output_cost, 6),
        "e2b": round(e2b_cost, 6),
        "email": round(email_cost, 6),
        "storage": round(storage_cost, 6),
        "total": round(total, 6),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_storage_bytes": stored_bytes,
    }


def _serialize_ledger_entry(entry: BillingCreditLedger) -> dict:
    return {
        "id": entry.id,
        "delta": entry.delta,
        "balance_after": entry.balance_after,
        "reason": entry.reason,
        "external_ref": entry.external_ref,
        "assessment_id": entry.assessment_id,
        "metadata": entry.entry_metadata or {},
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.get("/usage")
def get_usage(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return usage history for the current org: completed assessments with date, candidate, task, cost."""
    org_id = current_user.organization_id
    if not org_id:
        return {"usage": [], "total_cost": 0}
    assessments = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(
            Assessment.organization_id == org_id,
            Assessment.status == AssessmentStatus.COMPLETED,
            Assessment.completed_at != None,
        )
        .order_by(Assessment.completed_at.desc())
        .limit(100)
        .all()
    )
    cost_per = int(settings.ASSESSMENT_PRICE_MAJOR or 25)
    currency_code = _assessment_currency_code()
    usage = []
    for a in assessments:
        completed_at = a.completed_at
        date_str = completed_at.strftime("%b %d, %Y") if completed_at else ""
        candidate_name = (a.candidate.full_name or a.candidate.email) if a.candidate else "—"
        task_name = a.task.name if a.task else "—"
        usage.append({
            "date": date_str,
            "candidate": candidate_name,
            "task": task_name,
            "cost": f"{currency_code} {cost_per}",
            "assessment_id": a.id,
        })
    return {"usage": usage, "total_cost": len(usage) * cost_per}


@router.get("/costs")
def get_costs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return estimated per-assessment and per-tenant infrastructure costs for observability."""
    org_id = current_user.organization_id
    if not org_id:
        return {
            "deployment_env": settings.DEPLOYMENT_ENV,
            "model": settings.resolved_claude_model,
            "costs": [],
            "summary": {
                "tenant_total_usd": 0,
                "daily_spend_usd": 0,
                "cost_per_completed_assessment_usd": 0,
                "completed_assessments": 0,
            },
            "thresholds": {
                "daily_spend_usd": settings.COST_ALERT_DAILY_SPEND_USD,
                "cost_per_completed_assessment_usd": settings.COST_ALERT_PER_COMPLETED_ASSESSMENT_USD,
            },
            "alerts": {
                "daily_spend_exceeded": False,
                "cost_per_completed_assessment_exceeded": False,
            },
        }

    assessments = (
        db.query(Assessment)
        .options(joinedload(Assessment.candidate), joinedload(Assessment.task))
        .filter(Assessment.organization_id == org_id)
        .order_by(Assessment.created_at.desc())
        .limit(500)
        .all()
    )

    rows = []
    tenant_total = 0.0
    daily_spend = 0.0
    completed = 0
    now = datetime.now(timezone.utc)

    for a in assessments:
        cost = _compute_assessment_cost_usd(a)
        tenant_total += cost["total"]

        created_at = a.created_at
        if created_at is not None and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at and created_at >= now - timedelta(days=1):
            daily_spend += cost["total"]

        if a.status == AssessmentStatus.COMPLETED:
            completed += 1

        rows.append(
            {
                "assessment_id": a.id,
                "status": getattr(a.status, "value", a.status),
                "candidate": (a.candidate.full_name or a.candidate.email) if a.candidate else "—",
                "task": a.task.name if a.task else "—",
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                "cost_usd": cost,
            }
        )

    cost_per_completed = (tenant_total / completed) if completed else 0.0
    thresholds = {
        "daily_spend_usd": settings.COST_ALERT_DAILY_SPEND_USD,
        "cost_per_completed_assessment_usd": settings.COST_ALERT_PER_COMPLETED_ASSESSMENT_USD,
    }

    return {
        "deployment_env": settings.DEPLOYMENT_ENV,
        "model": settings.resolved_claude_model,
        "costs": rows,
        "summary": {
            "tenant_total_usd": round(tenant_total, 6),
            "daily_spend_usd": round(daily_spend, 6),
            "cost_per_completed_assessment_usd": round(cost_per_completed, 6),
            "completed_assessments": completed,
        },
        "thresholds": thresholds,
        "alerts": {
            "daily_spend_exceeded": daily_spend > thresholds["daily_spend_usd"],
            "cost_per_completed_assessment_exceeded": cost_per_completed > thresholds["cost_per_completed_assessment_usd"],
        },
    }


@router.get("/credits")
def get_credits(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    entries = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.organization_id == org.id)
        .order_by(BillingCreditLedger.created_at.desc(), BillingCreditLedger.id.desc())
        .limit(50)
        .all()
    )
    return {
        "billing_provider": org.billing_provider or "lemon",
        "credits_balance": int(org.credits_balance or 0),
        "packs": lemon_pack_catalog(),
        "entries": [_serialize_ledger_entry(entry) for entry in entries],
    }


@router.post("/checkout-session")
def create_checkout_session(
    body: CheckoutSessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a Lemon checkout session URL for a selected credit pack."""
    if settings.MVP_DISABLE_LEMON:
        raise HTTPException(status_code=503, detail="Billing is disabled for MVP pilot")
    if not settings.LEMON_API_KEY or not settings.LEMON_STORE_ID:
        raise HTTPException(status_code=503, detail="Lemon billing is not configured")
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    pack = resolve_pack(body.pack_id)
    if not pack:
        raise HTTPException(status_code=400, detail="Invalid pack_id")

    try:
        lemon = LemonService(api_key=settings.LEMON_API_KEY, store_id=settings.LEMON_STORE_ID)
        checkout_url = lemon.create_checkout(
            variant_id=str(pack["variant_id"]),
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            email=current_user.email,
            test_mode=bool(settings.LEMON_TEST_MODE),
            custom={
                "org_id": str(org.id),
                "pack_id": body.pack_id,
                "credits": int(pack["credits"]),
                "source": "taali",
            },
        )
        org.billing_provider = "lemon"
        db.commit()
        return {"url": checkout_url}
    except Exception:
        import logging as _logging
        _logging.getLogger("taali.billing").exception("Lemon checkout session error")
        raise HTTPException(status_code=502, detail="Payment service error. Please try again.")
