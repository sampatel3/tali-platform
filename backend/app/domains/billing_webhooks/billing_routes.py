"""Billing: usage history, cost observability, and Stripe credit top-ups."""
from datetime import datetime, timedelta, timezone
import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel

from ...components.integrations.stripe.topup_service import (
    StripeTopupError,
    create_billing_portal_session,
    create_topup_checkout_session,
    stripe_topups_configured,
)
from ...platform.database import get_db
from ...deps import get_current_user
from ...platform.config import settings
from ...models.billing_credit_ledger import BillingCreditLedger
from ...models.user import User
from ...models.organization import Organization
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...models.usage_event import UsageEvent
from ...models.anthropic_usage_reconciliation import AnthropicUsageReconciliation
from ...services.pricing_service import (
    CREDIT_PACKS,
    CREDITS_PER_USD,
    FREE_TIER,
    resolve_pack as _resolve_pack,
)
from ...services.usage_metering_service import usage_summary as _usage_summary
from ...services.anthropic_reconciliation_public import reconcile_recent_public
from sqlalchemy import case, func

router = APIRouter(prefix="/billing", tags=["Billing"])

class CheckoutSessionCreate(BaseModel):
    success_url: str
    cancel_url: str
    pack_id: str = "starter_5"


class TopupCreate(BaseModel):
    success_url: str
    cancel_url: str
    pack_id: str


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
        started_at = assessment.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - started_at
        return max(delta.total_seconds() / 3600.0, 0.0)
    return 0.0


def _assessment_token_totals(assessment: Assessment) -> tuple[int, int]:
    input_tokens = max(0, int(assessment.total_input_tokens or 0))
    output_tokens = max(0, int(assessment.total_output_tokens or 0))
    if input_tokens > 0 or output_tokens > 0:
        return input_tokens, output_tokens

    transcript_input = 0
    transcript_output = 0
    for entry in list(getattr(assessment, "cli_transcript", None) or []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event_type") or "") != "terminal_usage":
            continue
        transcript_input += max(0, int(entry.get("input_tokens") or 0))
        transcript_output += max(0, int(entry.get("output_tokens") or 0))
    return transcript_input, transcript_output


def _compute_assessment_cost_usd(assessment: Assessment) -> dict:
    input_tokens, output_tokens = _assessment_token_totals(assessment)

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


def _extract_claude_usage(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "request_cost_usd": 0.0,
        }
    usage = payload.get("_claude_usage")
    if not isinstance(usage, dict):
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "request_cost_usd": 0.0,
        }
    input_tokens = max(0, int(usage.get("input_tokens") or 0))
    output_tokens = max(0, int(usage.get("output_tokens") or 0))
    request_cost_usd = max(0.0, float(usage.get("request_cost_usd") or 0.0))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "request_cost_usd": request_cost_usd,
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
                "non_assessment_claude_cost_usd": 0,
                "non_assessment_input_tokens": 0,
                "non_assessment_output_tokens": 0,
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
    role_focus_payloads = (
        db.query(Role.interview_focus)
        .filter(Role.organization_id == org_id, Role.interview_focus != None)  # noqa: E711
        .all()
    )
    app_match_payloads = (
        db.query(CandidateApplication.cv_match_details)
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.cv_match_details != None,  # noqa: E711
        )
        .all()
    )

    rows = []
    tenant_total = 0.0
    daily_spend = 0.0
    completed = 0
    now = datetime.now(timezone.utc)
    non_assessment_input_tokens = 0
    non_assessment_output_tokens = 0
    non_assessment_cost_usd = 0.0

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

    for (payload,) in role_focus_payloads:
        usage = _extract_claude_usage(payload)
        non_assessment_input_tokens += usage["input_tokens"]
        non_assessment_output_tokens += usage["output_tokens"]
        non_assessment_cost_usd += usage["request_cost_usd"]

    for (payload,) in app_match_payloads:
        usage = _extract_claude_usage(payload)
        non_assessment_input_tokens += usage["input_tokens"]
        non_assessment_output_tokens += usage["output_tokens"]
        non_assessment_cost_usd += usage["request_cost_usd"]

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
            "non_assessment_claude_cost_usd": round(non_assessment_cost_usd, 6),
            "non_assessment_input_tokens": non_assessment_input_tokens,
            "non_assessment_output_tokens": non_assessment_output_tokens,
        },
        "thresholds": thresholds,
        "alerts": {
            "daily_spend_exceeded": daily_spend > thresholds["daily_spend_usd"],
            "cost_per_completed_assessment_exceeded": cost_per_completed > thresholds["cost_per_completed_assessment_usd"],
        },
    }


def _serialize_pack(pack) -> dict:
    return {
        "pack_id": pack.pack_id,
        "label": pack.label,
        "price_usd": pack.price_usd,
        "price_usd_cents": pack.price_usd_cents,
        "credits_granted": pack.credits_granted,
        "credits_granted_usd": round(pack.credits_granted / CREDITS_PER_USD, 2),
        "bonus_pct": pack.bonus_pct,
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
    balance = int(org.credits_balance or 0)
    return {
        "billing_provider": "stripe",
        "credits_balance": balance,
        "credits_balance_usd": round(balance / CREDITS_PER_USD, 2),
        "free_tier_credits": FREE_TIER.credits,
        "packs": [_serialize_pack(p) for p in CREDIT_PACKS],
        "entries": [_serialize_ledger_entry(entry) for entry in entries],
    }


@router.get("/usage-breakdown")
def get_usage_breakdown(
    period_days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-feature usage summary for the settings billing tab. Returns
    counts/tokens/credits grouped by feature for the trailing
    ``period_days`` window."""
    org_id = current_user.organization_id
    if not org_id:
        return {"balance_credits": 0, "by_feature": [], "period_days": period_days}
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(period_days)))
    summary = _usage_summary(db, organization_id=int(org_id), since=since)
    summary["period_days"] = period_days
    summary["balance_credits_usd"] = round(
        summary.get("balance_credits", 0) / CREDITS_PER_USD, 2
    )
    return summary


@router.get("/usage-events")
def get_usage_events(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Paginated usage event log for the settings billing tab consumption
    table. Newest first."""
    org_id = current_user.organization_id
    if not org_id:
        return {"events": []}
    rows = (
        db.query(UsageEvent)
        .filter(UsageEvent.organization_id == org_id)
        .order_by(UsageEvent.created_at.desc(), UsageEvent.id.desc())
        .limit(min(int(limit or 50), 200))
        .all()
    )
    return {
        "events": [
            {
                "id": e.id,
                "feature": e.feature,
                "model": e.model,
                "entity_id": e.entity_id,
                "input_tokens": e.input_tokens,
                "output_tokens": e.output_tokens,
                "cost_usd": round(int(e.cost_usd_micro or 0) / 1_000_000, 6),
                "credits_charged": int(e.credits_charged or 0),
                "credits_charged_usd": round(
                    int(e.credits_charged or 0) / CREDITS_PER_USD, 6
                ),
                "cache_hit": bool(e.cache_hit),
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in rows
        ],
    }


@router.get("/usage-timeseries")
def get_usage_timeseries(
    period_days: int = 30,
    group_by: str = "model",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Daily token + cost time series for the settings → usage tab.

    ``group_by`` controls the chart break-down:
    - ``model`` (default) — stacked by Claude model id (matches Console)
    - ``feature`` — stacked by Tali feature (score / prescreen / chat / ...)
    - ``user``   — stacked by user_id (per-recruiter spend)

    Returns daily buckets in the org's UTC timeline. The frontend
    renders these as a stacked bar chart and can flip the grouping
    without re-fetching by re-aggregating client-side, but for data
    over many days the server pre-aggregates to keep payloads small.
    """
    org_id = current_user.organization_id
    if not org_id:
        return {"buckets": [], "period_days": period_days, "group_by": group_by}

    valid_group_by = {"model", "feature", "user"}
    if group_by not in valid_group_by:
        group_by = "model"

    period_days = max(1, min(int(period_days), 90))
    since = datetime.now(timezone.utc) - timedelta(days=period_days)

    day_col = func.date_trunc("day", UsageEvent.created_at).label("bucket_day")
    if group_by == "feature":
        group_col = UsageEvent.feature
    elif group_by == "user":
        group_col = UsageEvent.user_id
    else:
        group_col = UsageEvent.model

    rows = (
        db.query(
            day_col,
            group_col.label("group_key"),
            func.count(UsageEvent.id).label("event_count"),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0).label("output_tokens"),
            func.coalesce(
                func.sum(UsageEvent.cache_read_tokens), 0
            ).label("cache_read_tokens"),
            func.coalesce(
                func.sum(UsageEvent.cache_creation_tokens), 0
            ).label("cache_creation_tokens"),
            # raw Anthropic cost EXCLUDES cache hits (no call ⇒ $0); credits
            # keep the cache fee.
            func.coalesce(
                func.sum(
                    case((UsageEvent.cache_hit == 0, UsageEvent.cost_usd_micro), else_=0)
                ),
                0,
            ).label("cost_usd_micro"),
            func.coalesce(func.sum(UsageEvent.credits_charged), 0).label("credits_charged"),
        )
        .filter(
            UsageEvent.organization_id == org_id,
            UsageEvent.created_at >= since,
        )
        .group_by(day_col, group_col)
        .order_by(day_col.asc())
        .all()
    )

    return {
        "period_days": period_days,
        "group_by": group_by,
        "buckets": [
            {
                "day": (r.bucket_day.date().isoformat() if r.bucket_day else None),
                "group_key": (
                    str(r.group_key) if r.group_key is not None else "unattributed"
                ),
                "event_count": int(r.event_count or 0),
                "input_tokens": int(r.input_tokens or 0),
                "output_tokens": int(r.output_tokens or 0),
                "cache_read_tokens": int(r.cache_read_tokens or 0),
                "cache_creation_tokens": int(r.cache_creation_tokens or 0),
                "cost_usd": round(int(r.cost_usd_micro or 0) / 1_000_000, 6),
                "credits_charged_usd": round(
                    int(r.credits_charged or 0) / CREDITS_PER_USD, 6
                ),
            }
            for r in rows
        ],
    }


@router.get("/usage-reconciliation")
def get_usage_reconciliation(
    period_days: int = 14,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Recent reconciliation rows for this org. Surfaces drift between
    internal ``usage_events`` and Anthropic billing.

    Returns one row per (date, model). Drift is pre-computed by the
    daily Celery task; UI flags rows where ``cost_drift_pct`` is null
    (orphan internal rows) or absolute drift exceeds 1%.
    """
    org_id = current_user.organization_id
    if not org_id:
        return {"rows": [], "period_days": period_days}

    period_days = max(1, min(int(period_days), 90))
    since = (datetime.now(timezone.utc) - timedelta(days=period_days)).date()

    rows = (
        db.query(AnthropicUsageReconciliation)
        .filter(
            AnthropicUsageReconciliation.organization_id == org_id,
            AnthropicUsageReconciliation.usage_date >= since,
        )
        .order_by(
            AnthropicUsageReconciliation.usage_date.desc(),
            AnthropicUsageReconciliation.model.asc(),
        )
        .all()
    )

    def _serialize(r: AnthropicUsageReconciliation) -> dict:
        return {
            "usage_date": r.usage_date.isoformat() if r.usage_date else None,
            "model": r.model,
            "anthropic_input_tokens": int(r.anthropic_input_tokens or 0),
            "anthropic_output_tokens": int(r.anthropic_output_tokens or 0),
            "anthropic_cache_read_tokens": int(r.anthropic_cache_read_tokens or 0),
            "anthropic_cache_creation_tokens": int(
                r.anthropic_cache_creation_tokens or 0
            ),
            "anthropic_cost_usd": round(
                int(r.anthropic_cost_usd_micro or 0) / 1_000_000, 6
            ),
            "internal_input_tokens": int(r.internal_input_tokens or 0),
            "internal_output_tokens": int(r.internal_output_tokens or 0),
            "internal_cache_read_tokens": int(r.internal_cache_read_tokens or 0),
            "internal_cache_creation_tokens": int(
                r.internal_cache_creation_tokens or 0
            ),
            "internal_cost_usd": round(
                int(r.internal_cost_usd_micro or 0) / 1_000_000, 6
            ),
            "internal_event_count": int(r.internal_event_count or 0),
            "tokens_drift_pct": (
                float(r.tokens_drift_pct) if r.tokens_drift_pct is not None else None
            ),
            "cost_drift_pct": (
                float(r.cost_drift_pct) if r.cost_drift_pct is not None else None
            ),
            "reconciled_at": r.reconciled_at.isoformat() if r.reconciled_at else None,
        }

    serialized = [_serialize(r) for r in rows]

    # Top-line summary so the UI can show "Anthropic billed you $X; we
    # attributed $Y; drift Z%".
    totals = {
        "anthropic_cost_usd": round(
            sum(int(r.anthropic_cost_usd_micro or 0) for r in rows) / 1_000_000, 6
        ),
        "internal_cost_usd": round(
            sum(int(r.internal_cost_usd_micro or 0) for r in rows) / 1_000_000, 6
        ),
        "row_count": len(rows),
    }
    if totals["anthropic_cost_usd"] > 0:
        diff = totals["internal_cost_usd"] - totals["anthropic_cost_usd"]
        totals["cost_drift_pct"] = round(
            (diff / totals["anthropic_cost_usd"]) * 100, 3
        )
    else:
        totals["cost_drift_pct"] = None

    return {
        "period_days": period_days,
        "totals": totals,
        "rows": serialized,
    }


# Hard cap chosen to match Anthropic's usage_report retention window. If
# Anthropic ever advertises a different limit we surface their error to
# the caller — the cap here is just a server-side sanity bound.
_RECONCILE_MAX_DAYS = 180


@router.post("/admin/reconcile")
def admin_reconcile(
    days: int = 7,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger an Anthropic-vs-internal reconciliation pass for the trailing
    ``days`` days. Admin-only. Used to seed history after the rolling 48h
    beat task has been in place — or to re-pull when drift is suspected.

    Returns the summary dict from ``reconcile_recent`` (rows upserted,
    skip/error reasons). Idempotent: re-running over the same window
    upserts on (date, workspace, model).
    """
    if not getattr(current_user, "is_superuser", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    if days < 1 or days > _RECONCILE_MAX_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"days must be in [1, {_RECONCILE_MAX_DAYS}]",
        )
    return reconcile_recent_public(db, days=int(days))


@router.get("/admin/metering-gap")
def admin_metering_gap(
    days: int = 7,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Summarise ``claude_call_log`` over the trailing ``days`` so the
    user can see at a glance: total Anthropic calls, total $ recorded,
    per-feature breakdown, attribution gap (calls without a UsageEvent),
    and failure rate. This is the user-facing answer to "where is my
    money going?" — now answerable without a multi-PR investigation.

    The structural invariant from PR #237 (every Claude call writes a
    call_log row, no exceptions) means these numbers are ground truth,
    not best-effort accounting. Drift between the totals here and
    Anthropic's admin API billing is now a metering-rate-table bug
    (unlikely) or a model-pricing-config bug (catchable), not an
    "application code forgot to record" bug (the historical leak).
    """
    if not getattr(current_user, "is_superuser", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    if days < 1 or days > 90:
        raise HTTPException(
            status_code=422,
            detail="days must be in [1, 90]",
        )

    from ...models.claude_call_log import ClaudeCallLog

    org_id = current_user.organization_id
    since = datetime.now(timezone.utc) - timedelta(days=days)

    base = db.query(ClaudeCallLog).filter(
        ClaudeCallLog.created_at >= since,
        ClaudeCallLog.organization_id == org_id,
    )

    # Totals
    total_calls = base.count()
    total_cost_micro = (
        base.with_entities(func.coalesce(func.sum(ClaudeCallLog.cost_usd_micro), 0)).scalar()
        or 0
    )

    # Per-feature breakdown
    feature_rows = (
        db.query(
            ClaudeCallLog.feature_hint,
            func.count(ClaudeCallLog.id).label("calls"),
            func.coalesce(func.sum(ClaudeCallLog.cost_usd_micro), 0).label("cost_micro"),
        )
        .filter(
            ClaudeCallLog.created_at >= since,
            ClaudeCallLog.organization_id == org_id,
        )
        .group_by(ClaudeCallLog.feature_hint)
        .order_by(func.sum(ClaudeCallLog.cost_usd_micro).desc())
        .all()
    )

    # Attribution gap (no usage_event FK — caller didn't enrich)
    gap_rows = (
        db.query(
            ClaudeCallLog.feature_hint,
            func.count(ClaudeCallLog.id).label("calls"),
            func.coalesce(func.sum(ClaudeCallLog.cost_usd_micro), 0).label("cost_micro"),
        )
        .filter(
            ClaudeCallLog.created_at >= since,
            ClaudeCallLog.organization_id == org_id,
            ClaudeCallLog.usage_event_id.is_(None),
        )
        .group_by(ClaudeCallLog.feature_hint)
        .order_by(func.sum(ClaudeCallLog.cost_usd_micro).desc())
        .all()
    )
    gap_total_calls = sum(int(r.calls or 0) for r in gap_rows)
    gap_total_cost_micro = sum(int(r.cost_micro or 0) for r in gap_rows)

    # Failure rate
    status_rows = (
        db.query(
            ClaudeCallLog.status,
            func.count(ClaudeCallLog.id).label("calls"),
        )
        .filter(
            ClaudeCallLog.created_at >= since,
            ClaudeCallLog.organization_id == org_id,
        )
        .group_by(ClaudeCallLog.status)
        .all()
    )

    return {
        "period_days": days,
        "since": since.isoformat(),
        "totals": {
            "calls": int(total_calls),
            "cost_usd": round(int(total_cost_micro) / 1_000_000, 6),
        },
        "by_feature": [
            {
                "feature": (r.feature_hint or "(unattributed)"),
                "calls": int(r.calls or 0),
                "cost_usd": round(int(r.cost_micro or 0) / 1_000_000, 6),
            }
            for r in feature_rows
        ],
        "attribution_gap": {
            "calls": int(gap_total_calls),
            "cost_usd": round(int(gap_total_cost_micro) / 1_000_000, 6),
            "by_feature_hint": [
                {
                    "feature_hint": (r.feature_hint or "(none)"),
                    "calls": int(r.calls or 0),
                    "cost_usd": round(int(r.cost_micro or 0) / 1_000_000, 6),
                }
                for r in gap_rows
            ],
        },
        "by_status": [
            {"status": r.status, "calls": int(r.calls or 0)}
            for r in status_rows
        ],
    }


@router.post("/topup")
def create_topup(
    body: TopupCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a Stripe Checkout session for a credit-pack top-up. Returns
    the URL the frontend should redirect to. Replaces the legacy Lemon
    checkout flow."""
    if not stripe_topups_configured():
        raise HTTPException(status_code=503, detail="Stripe top-ups are unavailable")
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if _resolve_pack(body.pack_id) is None:
        raise HTTPException(status_code=400, detail="Invalid pack_id")
    try:
        url = create_topup_checkout_session(
            org_id=int(org.id),
            customer_email=current_user.email,
            pack_id=body.pack_id,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
        )
    except StripeTopupError as exc:
        import logging as _logging
        _logging.getLogger("taali.billing").exception("Stripe topup error: %s", exc)
        raise HTTPException(status_code=502, detail="Payment service error. Please try again.") from exc

    org.billing_provider = "stripe"
    db.commit()
    return {"url": url}


class PortalSessionCreate(BaseModel):
    return_url: str


@router.post("/portal")
def create_billing_portal(
    body: PortalSessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a Stripe Billing Portal session tied to the org's customer.

    Returns ``{url}`` for the frontend to open. A 409 means the org has no
    Stripe customer yet (nothing has been purchased), so there's no portal to
    open — the UI hides the link in that case.
    """
    if settings.MVP_DISABLE_STRIPE or not settings.STRIPE_API_KEY:
        raise HTTPException(status_code=503, detail="Stripe payments are unavailable")
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not org.stripe_customer_id:
        raise HTTPException(
            status_code=409,
            detail="No billing account yet. Add credits first to set up billing.",
        )
    try:
        url = create_billing_portal_session(
            customer_id=str(org.stripe_customer_id),
            return_url=body.return_url,
        )
    except StripeTopupError as exc:
        import logging as _logging
        _logging.getLogger("taali.billing").exception("Stripe portal error: %s", exc)
        raise HTTPException(status_code=502, detail="Payment service error. Please try again.") from exc
    return {"url": url}
