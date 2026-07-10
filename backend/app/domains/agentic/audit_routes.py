"""Compliance / audit read surfaces for the autonomous agent.

  GET /api/v1/agent-decisions/export   full decision audit-log export (CSV|JSON)
  GET /api/v1/bias-audit/results       promotion-gate holdout bias-audit results

Both are org-scoped via ``get_current_user`` (recruiter auth, same dependency
as the agent-decisions list route). Read-only. No LLM calls, no demographic
data — the export never carries protected attributes because Taali never
stores them (see ``config/blocked_edge_attributes.yaml``); adverse-impact
segmentation is an out-of-band operator workflow (``scripts/adverse_impact_report.py``).
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, time, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.agent_decision import AgentDecision
from ...models.decision_feedback import DecisionFeedback
from ...models.policy_version import PolicyVersion
from ...models.promotion_gate import BiasAuditResult
from ...models.user import User
from ...platform.database import get_db


router = APIRouter(tags=["agentic-audit"])

logger = logging.getLogger("taali.agentic.audit_routes")

# Hard cap on export rows. An unbounded dump could stream millions of rows and
# starve the web worker; 50k covers any realistic single-org audit window while
# keeping the response bounded. When hit, the response advertises truncation
# (``X-Export-Truncated`` header for CSV; ``truncated`` field for JSON).
EXPORT_ROW_CAP = 50_000

# Column order for both CSV and JSON rows. One flat dict per decision.
EXPORT_COLUMNS = [
    "id",
    "created_at",
    "resolved_at",
    "decision_type",
    "recommendation",
    "status",
    "human_disposition",
    "confidence",
    "model_version",
    "prompt_version",
    "application_id",
    "role_id",
    "resolved_by_email",
    "resolution_note",
    "reasoning",
    "evidence",
    "input_fingerprint",
    "criteria_fingerprint",
    "cv_fingerprint",
    "feedback_failure_mode",
    "feedback_attributed_to",
    "feedback_correction_text",
    "feedback_cosigned",
]


def _parse_date_bound(value: Optional[str], *, end: bool) -> Optional[datetime]:
    """Parse a ``YYYY-MM-DD`` bound into a tz-aware datetime.

    ``from`` snaps to 00:00:00 (inclusive lower bound); ``to`` snaps to
    23:59:59.999999 (inclusive upper bound) so a single-day ``from==to``
    window returns that whole day.
    """
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value).date()
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid date {value!r}; expected YYYY-MM-DD")
    t = time.max if end else time.min
    return datetime.combine(d, t, tzinfo=timezone.utc)


def _json_or_none(value: Any) -> Optional[str]:
    """Serialise a JSON column to a compact string, or None if empty."""
    if value is None:
        return None
    try:
        return json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _build_export_rows(
    db: Session, org_id: int, *, from_dt, to_dt, role_id, decision_type
) -> tuple[list[dict[str, Any]], bool]:
    """Return (rows, truncated). Each row is a flat dict keyed by EXPORT_COLUMNS."""
    query = (
        db.query(AgentDecision, User.email, DecisionFeedback)
        .outerjoin(User, User.id == AgentDecision.resolved_by_user_id)
        # Latest feedback for the decision — ``feedback_id`` points at it when
        # the disposition was ``taught``. Left join so decisions without
        # feedback still export.
        .outerjoin(DecisionFeedback, DecisionFeedback.id == AgentDecision.feedback_id)
        .filter(AgentDecision.organization_id == org_id)
    )
    if from_dt is not None:
        query = query.filter(AgentDecision.created_at >= from_dt)
    if to_dt is not None:
        query = query.filter(AgentDecision.created_at <= to_dt)
    if role_id is not None:
        query = query.filter(AgentDecision.role_id == int(role_id))
    if decision_type:
        query = query.filter(AgentDecision.decision_type == decision_type)

    query = query.order_by(AgentDecision.created_at.asc(), AgentDecision.id.asc())
    # Fetch one past the cap so we can detect (and flag) truncation.
    fetched = query.limit(EXPORT_ROW_CAP + 1).all()
    truncated = len(fetched) > EXPORT_ROW_CAP
    fetched = fetched[:EXPORT_ROW_CAP]

    rows: list[dict[str, Any]] = []
    for decision, resolved_by_email, feedback in fetched:
        rows.append(
            {
                "id": decision.id,
                "created_at": decision.created_at.isoformat() if decision.created_at else None,
                "resolved_at": decision.resolved_at.isoformat() if decision.resolved_at else None,
                "decision_type": decision.decision_type,
                "recommendation": decision.recommendation,
                "status": decision.status,
                "human_disposition": decision.human_disposition,
                "confidence": float(decision.confidence) if decision.confidence is not None else None,
                "model_version": decision.model_version,
                "prompt_version": decision.prompt_version,
                "application_id": decision.application_id,
                "role_id": decision.role_id,
                "resolved_by_email": resolved_by_email,
                "resolution_note": decision.resolution_note,
                "reasoning": decision.reasoning,
                "evidence": _json_or_none(decision.evidence),
                "input_fingerprint": _json_or_none(decision.input_fingerprint),
                "criteria_fingerprint": decision.criteria_fingerprint,
                "cv_fingerprint": decision.cv_fingerprint,
                "feedback_failure_mode": feedback.failure_mode if feedback else None,
                "feedback_attributed_to": feedback.attributed_to if feedback else None,
                "feedback_correction_text": feedback.correction_text if feedback else None,
                "feedback_cosigned": (feedback.cosigned_at is not None) if feedback else None,
            }
        )
    return rows, truncated


def _csv_stream(rows: list[dict[str, Any]]):
    """Yield CSV text, header first, one row at a time via stdlib csv."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    yield buf.getvalue()
    buf.seek(0)
    buf.truncate(0)
    for row in rows:
        writer.writerow(row)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)


@router.get("/agent-decisions/export")
def export_agent_decisions(
    format: str = Query(default="csv"),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    role_id: Optional[int] = Query(default=None),
    decision_type: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full decision audit-log export for the caller's org (CSV or JSON).

    One row per decision with its provenance (model/prompt versions,
    fingerprints, reasoning, evidence) and any linked teach-loop feedback.
    Capped at ``EXPORT_ROW_CAP`` rows; truncation is advertised rather than
    silently dropping data.
    """
    fmt = format.lower()
    if fmt not in ("csv", "json"):
        raise HTTPException(status_code=422, detail=f"unsupported format={format!r}; use csv or json")

    from_dt = _parse_date_bound(from_, end=False)
    to_dt = _parse_date_bound(to, end=True)

    rows, truncated = _build_export_rows(
        db,
        current_user.organization_id,
        from_dt=from_dt,
        to_dt=to_dt,
        role_id=role_id,
        decision_type=decision_type,
    )

    if fmt == "json":
        return {
            "count": len(rows),
            "truncated": truncated,
            "row_cap": EXPORT_ROW_CAP,
            "rows": rows,
        }

    headers = {
        "Content-Disposition": "attachment; filename=agent_decisions_export.csv",
        "X-Export-Count": str(len(rows)),
        "X-Export-Truncated": "true" if truncated else "false",
    }
    return StreamingResponse(_csv_stream(rows), media_type="text/csv", headers=headers)


class BiasAuditResultPayload(BaseModel):
    id: int
    policy_version_id: int
    role_id: Optional[int] = None
    audited_at: datetime
    passed: bool
    metrics: Optional[dict[str, Any]] = None
    violations: Optional[Any] = None
    override_reason: Optional[str] = None


@router.get("/bias-audit/results", response_model=list[BiasAuditResultPayload])
def list_bias_audit_results(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Promotion-gate holdout bias-audit results for the caller's org.

    Read-only. Org scoping is via the joined ``PolicyVersion`` (the audit row
    itself references only a policy version). Per-segment metrics and
    violations are returned verbatim from the stored JSON.
    """
    rows = (
        db.query(BiasAuditResult, PolicyVersion.role_id)
        .join(PolicyVersion, PolicyVersion.id == BiasAuditResult.policy_version_id)
        .filter(PolicyVersion.organization_id == current_user.organization_id)
        .filter(BiasAuditResult.metrics_json.isnot(None))
        .order_by(BiasAuditResult.audited_at.desc(), BiasAuditResult.id.desc())
        .limit(limit)
        .all()
    )
    return [
        BiasAuditResultPayload(
            id=result.id,
            policy_version_id=result.policy_version_id,
            role_id=role_id,
            audited_at=result.audited_at,
            passed=bool(result.passed),
            metrics=result.metrics_json,
            violations=result.violations_json,
            override_reason=result.override_reason,
        )
        for result, role_id in rows
    ]
