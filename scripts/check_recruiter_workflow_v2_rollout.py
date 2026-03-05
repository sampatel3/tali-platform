#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, not_, or_
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from sqlalchemy.exc import OperationalError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.candidate_application import CandidateApplication  # noqa: E402
from app.models.candidate_application_event import CandidateApplicationEvent  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.role import Role  # noqa: E402
from app.platform.database import SessionLocal  # noqa: E402


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stage_counts(db, *, org_id: int) -> dict[str, int]:
    rows = (
        db.query(CandidateApplication.pipeline_stage, func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
        )
        .group_by(CandidateApplication.pipeline_stage)
        .all()
    )
    counts = {"applied": 0, "invited": 0, "in_assessment": 0, "review": 0}
    for stage, total in rows:
        key = str(stage or "").strip().lower()
        if key in counts:
            counts[key] = int(total or 0)
    counts["all"] = int(sum(counts.values()))
    return counts


def _drift_rate(db, *, org_id: int) -> tuple[int, int, float]:
    base = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            or_(
                CandidateApplication.external_stage_normalized.isnot(None),
                CandidateApplication.external_stage_raw.isnot(None),
                CandidateApplication.workable_stage.isnot(None),
            ),
        )
    )
    total = int(base.count())
    if total == 0:
        return 0, 0, 0.0
    drifted = int(
        base.filter(
            not_(
                and_(
                    func.lower(
                        func.replace(
                            func.replace(
                                func.coalesce(
                                    CandidateApplication.external_stage_normalized,
                                    CandidateApplication.external_stage_raw,
                                    CandidateApplication.workable_stage,
                                ),
                                "-",
                                "_",
                            ),
                            " ",
                            "_",
                        )
                    )
                    == func.lower(
                        func.replace(
                            func.replace(CandidateApplication.pipeline_stage, "-", "_"),
                            " ",
                            "_",
                        )
                    )
                )
            )
        ).count()
    )
    return drifted, total, round((drifted / total) * 100.0, 2)


def _events_health(db, *, org_id: int, since: datetime) -> dict[str, Any]:
    total_events = int(
        db.query(func.count(CandidateApplicationEvent.id))
        .filter(
            CandidateApplicationEvent.organization_id == org_id,
            CandidateApplicationEvent.created_at >= since,
        )
        .scalar()
        or 0
    )
    type_rows = (
        db.query(CandidateApplicationEvent.event_type, func.count(CandidateApplicationEvent.id))
        .filter(
            CandidateApplicationEvent.organization_id == org_id,
            CandidateApplicationEvent.created_at >= since,
        )
        .group_by(CandidateApplicationEvent.event_type)
        .all()
    )
    event_type_counts = {str(event_type or "unknown"): int(total or 0) for event_type, total in type_rows}
    initialized_app_ids = {
        int(app_id)
        for (app_id,) in (
            db.query(CandidateApplicationEvent.application_id)
            .filter(
                CandidateApplicationEvent.organization_id == org_id,
                CandidateApplicationEvent.event_type == "pipeline_initialized",
            )
            .distinct()
            .all()
        )
    }
    active_app_ids = {
        int(app_id)
        for (app_id,) in (
            db.query(CandidateApplication.id)
            .filter(
                CandidateApplication.organization_id == org_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .all()
        )
    }
    missing_initialized = int(len(active_app_ids - initialized_app_ids))
    return {
        "window_events_total": total_events,
        "window_event_type_counts": event_type_counts,
        "applications_missing_pipeline_initialized_event": missing_initialized,
    }


def _api_latency_probe(
    *,
    api_base_url: str,
    bearer_token: str,
    role_ids: list[int],
    stage: str | None,
    sample_limit: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {bearer_token}"}
    latencies_ms: list[float] = []
    errors: list[dict[str, Any]] = []
    stage_values = [stage] if stage else [None, "applied", "invited", "in_assessment", "review"]

    for role_id in role_ids[: max(1, sample_limit)]:
        for current_stage in stage_values:
            params = {"limit": 50, "offset": 0}
            if current_stage:
                params["stage"] = current_stage
            query = urlencode(params)
            url = f"{api_base_url.rstrip('/')}/roles/{role_id}/pipeline?{query}"
            started = time.perf_counter()
            try:
                request = Request(url, headers=headers, method="GET")
                with urlopen(request, timeout=timeout_seconds) as response:
                    status_code = int(getattr(response, "status", 200) or 200)
                    body_text = response.read().decode("utf-8", errors="replace")
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                latencies_ms.append(elapsed_ms)
                if status_code >= 400:
                    errors.append(
                        {
                            "role_id": role_id,
                            "stage": current_stage or "all",
                            "status_code": status_code,
                            "body": (body_text or "")[:500],
                        }
                    )
            except HTTPError as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                latencies_ms.append(elapsed_ms)
                error_body = ""
                try:
                    error_body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    error_body = str(exc)
                errors.append(
                    {
                        "role_id": role_id,
                        "stage": current_stage or "all",
                        "status_code": int(exc.code or 500),
                        "body": error_body[:500],
                    }
                )
            except URLError as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                latencies_ms.append(elapsed_ms)
                errors.append(
                    {
                        "role_id": role_id,
                        "stage": current_stage or "all",
                        "status_code": "url_error",
                        "body": str(exc),
                    }
                )
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                latencies_ms.append(elapsed_ms)
                errors.append(
                    {
                        "role_id": role_id,
                        "stage": current_stage or "all",
                        "status_code": "exception",
                        "body": str(exc),
                    }
                )

    if not latencies_ms:
        return {
            "samples": 0,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
            "errors": errors,
        }

    sorted_ms = sorted(latencies_ms)
    p50 = statistics.median(sorted_ms)
    p95_index = max(0, min(len(sorted_ms) - 1, int(round((len(sorted_ms) - 1) * 0.95))))
    p95 = sorted_ms[p95_index]
    return {
        "samples": len(sorted_ms),
        "p50_ms": round(float(p50), 2),
        "p95_ms": round(float(p95), 2),
        "max_ms": round(float(max(sorted_ms)), 2),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Recruiter workflow v2 rollout health check.")
    parser.add_argument("--org-id", type=int, required=True, help="Organization ID to inspect.")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window for event counters.")
    parser.add_argument("--sample-roles", type=int, default=5, help="Number of roles to probe for API latency.")
    parser.add_argument("--stage", type=str, default="", help="Optional fixed stage for API latency probe.")
    parser.add_argument("--timeout-seconds", type=int, default=12, help="Per-request timeout for API probes.")
    parser.add_argument("--skip-api-probe", action="store_true", help="Skip /roles/{id}/pipeline latency probe.")
    args = parser.parse_args()

    cutoff = _utcnow() - timedelta(hours=max(1, int(args.hours or 24)))
    db = SessionLocal()
    try:
        try:
            org = db.query(Organization).filter(Organization.id == int(args.org_id)).first()
        except OperationalError as exc:
            print(
                json.dumps(
                    {
                        "error": "database_schema_unavailable",
                        "detail": str(exc),
                        "hint": "Run migrations on the target database before executing this check.",
                    }
                )
            )
            return 2
        if not org:
            print(json.dumps({"error": f"organization_not_found:{args.org_id}"}))
            return 2

        open_apps = int(
            db.query(func.count(CandidateApplication.id))
            .filter(
                CandidateApplication.organization_id == org.id,
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.application_outcome == "open",
            )
            .scalar()
            or 0
        )
        closed_apps = int(
            db.query(func.count(CandidateApplication.id))
            .filter(
                CandidateApplication.organization_id == org.id,
                CandidateApplication.deleted_at.is_(None),
                CandidateApplication.application_outcome != "open",
            )
            .scalar()
            or 0
        )
        stage_counts = _stage_counts(db, org_id=org.id)
        drifted, drift_total, drift_pct = _drift_rate(db, org_id=org.id)
        events_health = _events_health(db, org_id=org.id, since=cutoff)

        role_ids = [
            int(role_id)
            for (role_id,) in (
                db.query(Role.id)
                .filter(Role.organization_id == org.id, Role.deleted_at.is_(None))
                .order_by(Role.id.desc())
                .limit(max(1, int(args.sample_roles)))
                .all()
            )
        ]

        api_probe = {"skipped": True}
        if not args.skip_api_probe:
            api_base_url = str(os.getenv("TAALI_API_BASE_URL") or "").strip().rstrip("/")
            bearer_token = str(os.getenv("TAALI_BEARER_TOKEN") or "").strip()
            if not api_base_url or not bearer_token:
                api_probe = {
                    "skipped": True,
                    "reason": "set TAALI_API_BASE_URL and TAALI_BEARER_TOKEN to run latency probe",
                }
            else:
                api_probe = _api_latency_probe(
                    api_base_url=api_base_url,
                    bearer_token=bearer_token,
                    role_ids=role_ids,
                    stage=(str(args.stage or "").strip() or None),
                    sample_limit=max(1, int(args.sample_roles)),
                    timeout_seconds=max(2, int(args.timeout_seconds)),
                )
                api_probe["skipped"] = False

        report = {
            "generated_at": _utcnow().isoformat(),
            "organization": {
                "id": org.id,
                "name": org.name,
                "recruiter_workflow_v2_enabled": bool(getattr(org, "recruiter_workflow_v2_enabled", False)),
            },
            "lookback": {"hours": int(args.hours), "since": cutoff.isoformat()},
            "pipeline": {
                "open_applications": open_apps,
                "closed_applications": closed_apps,
                "stage_counts_open": stage_counts,
            },
            "external_drift": {
                "drifted_open_applications": drifted,
                "open_applications_with_external_stage": drift_total,
                "drift_rate_percent": drift_pct,
            },
            "events": events_health,
            "api_probe_roles": role_ids,
            "role_pipeline_latency_probe": api_probe,
        }

        alerts = []
        if events_health["applications_missing_pipeline_initialized_event"] > 0:
            alerts.append("missing_pipeline_initialized_events")
        if drift_total > 0 and drift_pct > 30:
            alerts.append("high_external_drift_rate")
        if not api_probe.get("skipped") and api_probe.get("p95_ms") is not None and float(api_probe["p95_ms"]) > 300.0:
            alerts.append("role_pipeline_p95_above_300ms")
        if not api_probe.get("skipped") and api_probe.get("errors"):
            alerts.append("role_pipeline_probe_errors")
        report["alerts"] = alerts
        report["status"] = "ok" if not alerts else "needs_attention"

        print(json.dumps(report, indent=2))
        return 0 if not alerts else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
