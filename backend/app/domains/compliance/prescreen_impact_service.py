"""Rolling adverse-impact audit over actual pre-screen outcomes.

This is intentionally a compliance-only join between the segregated,
voluntary EEO table and application outcomes.  No per-candidate record leaves
this module.  Persisted metrics suppress every cell below ``min_cell_n`` and
contain only aggregate counts/rates.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session, load_only

from ...models.candidate_application import CandidateApplication
from ...models.eeo_response import EEOResponse
from ...models.prescreen_adverse_impact_audit import PrescreenAdverseImpactAudit


logger = logging.getLogger("taali.compliance.prescreen_adverse_impact")

SEGMENT_FIELDS = (
    "gender",
    "race_ethnicity",
    "veteran_status",
    "disability_status",
)
METRIC_GATE_PASS = "pre_screen_gate_pass"
METRIC_FRAUD_CAP_PASS = "fraud_cap_pass"
METRIC_AUTO_REJECT_SURVIVAL = "automated_reject_survival"
METRIC_NAMES = (
    METRIC_GATE_PASS,
    METRIC_FRAUD_CAP_PASS,
    METRIC_AUTO_REJECT_SURVIVAL,
)


def prescreen_audit_organization_ids(db: Session) -> list[int]:
    """Return org ids with voluntary self-ID rows, inside compliance only."""

    return [
        int(value)
        for (value,) in db.query(EEOResponse.organization_id).distinct().all()
    ]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _gate_passed(app: CandidateApplication) -> bool | None:
    """Return the verdict that actually ran, or ``None`` when legacy data
    lacks enough provenance to reconstruct it safely.

    New scores stamp the enforced cut in ``pre_screen_evidence``.  Older
    filtered rows retain ``pre_screen_decision`` in ``cv_match_details``;
    successful full scores prove the candidate survived the gate.  We do not
    compare old scores with today's threshold because that would rewrite
    history after a policy change.
    """

    evidence = _as_dict(app.pre_screen_evidence)
    details = _as_dict(app.cv_match_details)
    legacy_decision = str(details.get("pre_screen_decision") or "").strip().lower()
    if legacy_decision in {"no", "reject", "rejected", "filtered"}:
        return False
    if legacy_decision in {"yes", "pass", "passed", "keep"}:
        return True
    threshold = evidence.get("gate_threshold_enforced")
    genuine = getattr(app, "genuine_pre_screen_score_100", None)
    if threshold is not None and genuine is not None:
        try:
            return float(genuine) >= float(threshold)
        except (TypeError, ValueError):
            return None
    if app.cv_match_score is not None:
        return True
    return None


def _outcomes(app: CandidateApplication) -> dict[str, bool | None]:
    evidence = _as_dict(app.pre_screen_evidence)
    fraud_pass: bool | None = None
    if "fraud_capped" in evidence:
        fraud_pass = not bool(evidence.get("fraud_capped"))
    return {
        METRIC_GATE_PASS: _gate_passed(app),
        METRIC_FRAUD_CAP_PASS: fraud_pass,
        # A HITL card is not an automated rejection.  Only the persisted
        # terminal state counts against this favorable-outcome lens.
        METRIC_AUTO_REJECT_SURVIVAL: str(app.auto_reject_state or "") != "rejected",
    }


def _segments(row: EEOResponse) -> dict[str, str]:
    values = {
        field: str(getattr(row, field) or "").strip()
        for field in SEGMENT_FIELDS
    }
    values = {key: value for key, value in values.items() if value}
    if values.get("gender") and values.get("race_ethnicity"):
        values["gender_x_race_ethnicity"] = (
            f"{values['gender']} × {values['race_ethnicity']}"
        )
    return values


def compute_aggregate_metrics(
    records: Iterable[tuple[dict[str, str], dict[str, bool | None]]],
    *,
    impact_ratio_min: float,
    min_cell_n: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Compute small-cell-suppressed selection rates and impact ratios.

    ``records`` is accepted as an iterable to keep the math independently
    testable.  Segment labels for cells below ``min_cell_n`` are never emitted;
    they are rolled into one anonymous ``suppressed_n`` count.
    """

    materialized = list(records)
    metrics: dict[str, Any] = {}
    violations: list[dict[str, Any]] = []
    comparisons = 0

    attributes = (*SEGMENT_FIELDS, "gender_x_race_ethnicity")
    for metric_name in METRIC_NAMES:
        metric_payload: dict[str, Any] = {}
        for attribute in attributes:
            counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
            for segments, outcomes in materialized:
                selected = outcomes.get(metric_name)
                segment = segments.get(attribute)
                if selected is None or not segment:
                    continue
                counts[segment][0] += 1
                counts[segment][1] += int(bool(selected))

            visible = {
                segment: values
                for segment, values in counts.items()
                if values[0] >= min_cell_n
            }
            suppressed_n = sum(
                values[0]
                for segment, values in counts.items()
                if segment not in visible
            )
            cells: list[dict[str, Any]] = []
            reference_rate = max(
                (selected / total for total, selected in visible.values()),
                default=None,
            )
            for segment in sorted(visible):
                total, selected = visible[segment]
                rate = selected / total
                ratio = (
                    1.0
                    if reference_rate == 0
                    else (rate / reference_rate if reference_rate is not None else None)
                )
                flagged = bool(
                    ratio is not None
                    and len(visible) >= 2
                    and ratio < impact_ratio_min
                )
                cell = {
                    "segment": segment,
                    "n": total,
                    "selected": selected,
                    "selection_rate": round(rate, 6),
                    "impact_ratio": round(ratio, 6) if ratio is not None else None,
                    "flagged": flagged,
                }
                cells.append(cell)
                if flagged:
                    violations.append(
                        {
                            "metric": metric_name,
                            "attribute": attribute,
                            **cell,
                            "minimum_ratio": impact_ratio_min,
                        }
                    )
            if len(visible) >= 2:
                comparisons += len(visible) - 1
            if cells or suppressed_n:
                metric_payload[attribute] = {
                    "cells": cells,
                    "suppressed_n": suppressed_n,
                    "evaluable_n": sum(v[0] for v in counts.values()),
                }
        metrics[metric_name] = metric_payload

    return metrics, violations, comparisons


def _impact_rows_query(
    db: Session,
    *,
    organization_id: int,
    window_start: datetime,
    window_end: datetime,
):
    """Build the narrow compliance join without hydrating CV/JSON payloads."""

    return (
        db.query(EEOResponse, CandidateApplication)
        .options(
            load_only(
                EEOResponse.gender,
                EEOResponse.race_ethnicity,
                EEOResponse.veteran_status,
                EEOResponse.disability_status,
            ),
            load_only(
                CandidateApplication.pre_screen_evidence,
                CandidateApplication.cv_match_details,
                CandidateApplication.genuine_pre_screen_score_100,
                CandidateApplication.cv_match_score,
                CandidateApplication.auto_reject_state,
            ),
        )
        .join(
            CandidateApplication,
            EEOResponse.application_id == CandidateApplication.id,
        )
        .filter(
            EEOResponse.organization_id == int(organization_id),
            EEOResponse.declined_to_answer.is_(False),
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.pre_screen_run_at.isnot(None),
            CandidateApplication.pre_screen_run_at >= window_start,
            CandidateApplication.pre_screen_run_at < window_end,
        )
    )


def run_prescreen_adverse_impact_audit(
    db: Session,
    *,
    organization_id: int,
    window_start: datetime,
    window_end: datetime,
    impact_ratio_min: float = 0.80,
    min_cell_n: int = 5,
) -> PrescreenAdverseImpactAudit:
    """Compute and upsert one org/window audit.

    This writes only the aggregate audit row.  It never mutates applications,
    EEO responses, scores, decisions, or recruiter-visible candidate state.
    """

    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=timezone.utc)
    if window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=timezone.utc)
    if window_start >= window_end:
        raise ValueError("window_start must be earlier than window_end")
    if not 0 < float(impact_ratio_min) <= 1:
        raise ValueError("impact_ratio_min must be in (0, 1]")
    if int(min_cell_n) < 2:
        raise ValueError("min_cell_n must be at least 2")

    joined = _impact_rows_query(
        db,
        organization_id=int(organization_id),
        window_start=window_start,
        window_end=window_end,
    ).all()
    records = [(_segments(eeo), _outcomes(app)) for eeo, app in joined]
    metrics, violations, comparisons = compute_aggregate_metrics(
        records,
        impact_ratio_min=float(impact_ratio_min),
        min_cell_n=int(min_cell_n),
    )
    metrics["summary"] = {
        "labeled_prescreen_runs": len(records),
        "minimum_cell_n": int(min_cell_n),
        "minimum_impact_ratio": float(impact_ratio_min),
        "source": "voluntary_eeo",
    }
    status = (
        "violations"
        if violations
        else ("passed" if comparisons else "insufficient_data")
    )
    audit = (
        db.query(PrescreenAdverseImpactAudit)
        .filter(
            PrescreenAdverseImpactAudit.organization_id == int(organization_id),
            PrescreenAdverseImpactAudit.window_start == window_start,
            PrescreenAdverseImpactAudit.window_end == window_end,
        )
        .one_or_none()
    )
    if audit is None:
        audit = PrescreenAdverseImpactAudit(
            organization_id=int(organization_id),
            window_start=window_start,
            window_end=window_end,
        )
        db.add(audit)
    audit.status = status
    audit.sample_size = len(records)
    audit.comparisons = comparisons
    audit.source = "voluntary_eeo"
    audit.metrics_json = metrics
    audit.violations_json = violations
    db.flush()

    if violations:
        logger.error(
            "prescreen_adverse_impact_violation organization_id=%s audit_id=%s "
            "violations=%s sample_size=%s window_start=%s window_end=%s",
            organization_id,
            audit.id,
            len(violations),
            len(records),
            window_start.isoformat(),
            window_end.isoformat(),
        )
    return audit


__all__ = [
    "METRIC_AUTO_REJECT_SURVIVAL",
    "METRIC_FRAUD_CAP_PASS",
    "METRIC_GATE_PASS",
    "compute_aggregate_metrics",
    "prescreen_audit_organization_ids",
    "run_prescreen_adverse_impact_audit",
]
