#!/usr/bin/env python3
"""AEDT audit-pack generator — a factual compliance dossier for an org+window.

Produces a Markdown pack documenting how the automated employment-decision
tool (AEDT) behaved over a window: which model/prompt/engine versions actually
ran, decision volumes, human-oversight metrics, integrity-flag rates, the
latest bias-audit holdout result, and pointers to the adverse-impact workflow.

It is deliberately plain and factual — no marketing language, no claims the
data doesn't support. It reports what the audit tables contain; where a signal
isn't configured (e.g. no bias-audit has run) it says so rather than inventing
one.

Read-only. Pure stdlib + SQLAlchemy Core (same as cost_per_outcome.py).

Run locally against the public DB URL:
    DATABASE_URL="$PUBLIC_PG_URL" \
      python scripts/aedt_audit_pack.py --org-id 2 --from 2026-06-01 --out pack.md

Options:
    --org-id N          organization_id (required)
    --from / --to       ISO date window on created_at (optional)
    --out PATH          write markdown to PATH (default: stdout)
    --database-url      explicit DB URL (else DATABASE_PUBLIC_URL / DATABASE_URL)
"""
from __future__ import annotations

import argparse
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text


BLOCKED_ATTRS_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "blocked_edge_attributes.yaml"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value) -> Optional[datetime]:
    """Coerce a DB timestamp to a datetime.

    Postgres (prod) returns ``datetime`` objects; SQLite returns ISO strings
    for TIMESTAMP columns queried via raw SQL. Tolerate both so latency math
    never crashes on the driver's representation.
    """
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# DB fetch (read-only) — thin, so section builders can be unit-tested on dicts
# ---------------------------------------------------------------------------
def _window_clause(alias: str, from_dt, to_dt) -> str:
    clause = ""
    if from_dt is not None:
        clause += f" AND {alias}.created_at >= :from_dt"
    if to_dt is not None:
        clause += f" AND {alias}.created_at <= :to_dt"
    return clause


def _params(org_id: int, from_dt, to_dt) -> dict:
    p = {"org": org_id}
    if from_dt is not None:
        p["from_dt"] = from_dt
    if to_dt is not None:
        p["to_dt"] = to_dt
    return p


def fetch_versions(conn, org_id: int, from_dt, to_dt) -> dict:
    """Distinct model/prompt versions observed on decisions in-window, plus
    distinct scoring engine/rubric versions from cv_match_details."""
    dec = conn.execute(
        text(
            f"""
            SELECT DISTINCT model_version, prompt_version
            FROM agent_decisions ad
            WHERE ad.organization_id = :org
            {_window_clause('ad', from_dt, to_dt)}
            ORDER BY model_version, prompt_version
            """
        ),
        _params(org_id, from_dt, to_dt),
    )
    decision_versions = [
        {"model_version": r._mapping["model_version"], "prompt_version": r._mapping["prompt_version"]}
        for r in dec
    ]
    # Scoring engine versions live inside the cv_match_details JSON blob. Pull
    # the blobs and extract distinct (scoring_version, score_rubric_version) in
    # Python — portable across SQLite (tests) and Postgres (prod).
    scored = conn.execute(
        text(
            f"""
            SELECT cv_match_details
            FROM candidate_applications ca
            WHERE ca.organization_id = :org
              AND ca.cv_match_details IS NOT NULL
              AND ca.cv_match_scored_at IS NOT NULL
              {(' AND ca.cv_match_scored_at >= :from_dt' if from_dt is not None else '')}
              {(' AND ca.cv_match_scored_at <= :to_dt' if to_dt is not None else '')}
            """
        ),
        _params(org_id, from_dt, to_dt),
    )
    engine_versions: set[tuple] = set()
    for r in scored:
        details = r._mapping["cv_match_details"] or {}
        if isinstance(details, dict):
            engine_versions.add(
                (details.get("scoring_version"), details.get("score_rubric_version"))
            )
    engine_list = [
        {"scoring_version": s, "score_rubric_version": rub}
        for (s, rub) in sorted(
            (v for v in engine_versions if v[0] or v[1]),
            key=lambda v: (str(v[0]), str(v[1])),
        )
    ]
    return {
        "decision_versions": decision_versions,
        "engine_versions": engine_list,
    }


def fetch_volume(conn, org_id: int, from_dt, to_dt) -> list[dict]:
    rows = conn.execute(
        text(
            f"""
            SELECT decision_type, status, COUNT(*) AS n
            FROM agent_decisions ad
            WHERE ad.organization_id = :org
            {_window_clause('ad', from_dt, to_dt)}
            GROUP BY decision_type, status
            ORDER BY decision_type, status
            """
        ),
        _params(org_id, from_dt, to_dt),
    )
    return [dict(r._mapping) for r in rows]


def fetch_oversight(conn, org_id: int, from_dt, to_dt) -> dict:
    """Human-disposition tallies, resolution latencies, top override reasons."""
    disp = conn.execute(
        text(
            f"""
            SELECT human_disposition, status, COUNT(*) AS n
            FROM agent_decisions ad
            WHERE ad.organization_id = :org
            {_window_clause('ad', from_dt, to_dt)}
            GROUP BY human_disposition, status
            """
        ),
        _params(org_id, from_dt, to_dt),
    )
    disposition_rows = [dict(r._mapping) for r in disp]

    # Resolution latency (seconds) for resolved decisions. Compute in Python
    # from timestamps for cross-DB portability (SQLite has no clean interval).
    times = conn.execute(
        text(
            f"""
            SELECT created_at, resolved_at
            FROM agent_decisions ad
            WHERE ad.organization_id = :org
              AND ad.resolved_at IS NOT NULL
            {_window_clause('ad', from_dt, to_dt)}
            """
        ),
        _params(org_id, from_dt, to_dt),
    )
    latencies: list[float] = []
    for r in times:
        c = _as_datetime(r._mapping["created_at"])
        rr = _as_datetime(r._mapping["resolved_at"])
        if c and rr:
            latencies.append((rr - c).total_seconds())

    reasons = conn.execute(
        text(
            f"""
            SELECT resolution_note, COUNT(*) AS n
            FROM agent_decisions ad
            WHERE ad.organization_id = :org
              AND ad.status = 'overridden'
              AND ad.resolution_note IS NOT NULL
              AND ad.resolution_note <> ''
            {_window_clause('ad', from_dt, to_dt)}
            GROUP BY resolution_note
            ORDER BY n DESC
            LIMIT 5
            """
        ),
        _params(org_id, from_dt, to_dt),
    )
    top_override_reasons = [dict(r._mapping) for r in reasons]
    return {
        "dispositions": disposition_rows,
        "latencies_seconds": latencies,
        "top_override_reasons": top_override_reasons,
    }


def fetch_integrity(conn, org_id: int, from_dt, to_dt) -> dict:
    """Assessment integrity/fraud flag rates in-window."""
    total = conn.execute(
        text(
            f"""
            SELECT COUNT(*) FROM assessments a
            WHERE a.organization_id = :org
            {_window_clause('a', from_dt, to_dt)}
            """
        ),
        _params(org_id, from_dt, to_dt),
    ).scalar() or 0
    # prompt_fraud_flags / flags are JSON; count rows where they are present and
    # non-empty. Portable: pull the blobs, test in Python.
    blobs = conn.execute(
        text(
            f"""
            SELECT prompt_fraud_flags, flags FROM assessments a
            WHERE a.organization_id = :org
            {_window_clause('a', from_dt, to_dt)}
            """
        ),
        _params(org_id, from_dt, to_dt),
    )
    fraud_flagged = 0
    integrity_flagged = 0
    for r in blobs:
        pf = r._mapping["prompt_fraud_flags"]
        fl = r._mapping["flags"]
        if pf:
            fraud_flagged += 1
        if fl:
            integrity_flagged += 1
    return {
        "assessments_total": int(total),
        "prompt_fraud_flagged": fraud_flagged,
        "integrity_flagged": integrity_flagged,
    }


def fetch_latest_bias_audit(conn, org_id: int) -> Optional[dict]:
    row = conn.execute(
        text(
            """
            SELECT bar.id, bar.policy_version_id, bar.audited_at, bar.passed,
                   bar.metrics_json, bar.violations_json
            FROM bias_audit_results bar
            JOIN policy_versions pv ON pv.id = bar.policy_version_id
            WHERE pv.organization_id = :org
            ORDER BY bar.audited_at DESC, bar.id DESC
            LIMIT 1
            """
        ),
        {"org": org_id},
    ).first()
    if row is None:
        return None
    m = row._mapping
    return {
        "id": m["id"],
        "policy_version_id": m["policy_version_id"],
        "audited_at": m["audited_at"],
        "passed": bool(m["passed"]),
        "metrics_json": m["metrics_json"],
        "violations_json": m["violations_json"],
    }


# ---------------------------------------------------------------------------
# Section builders (pure — unit tested)
# ---------------------------------------------------------------------------
def _h(title: str) -> str:
    return f"## {title}\n"


def section_system_description(versions: dict) -> str:
    lines = [_h("1. System description")]
    lines.append(
        "Taali's automated employment-decision tool screens and ranks candidate "
        "applications and emits advance / reject recommendations to a human "
        "recruiter, who approves or overrides each one. No recommendation is "
        "acted on without a human disposition; interviews and final hiring "
        "decisions remain human.\n"
    )
    dvs = versions.get("decision_versions") or []
    if dvs:
        lines.append("**Decision model/prompt versions observed in window:**\n")
        for v in dvs:
            lines.append(f"- model `{v['model_version']}`, prompt `{v['prompt_version']}`")
        lines.append("")
    else:
        lines.append("_No decisions recorded in this window._\n")
    evs = versions.get("engine_versions") or []
    if evs:
        lines.append("**CV-scoring engine/rubric versions observed in window:**\n")
        for v in evs:
            lines.append(
                f"- scoring `{v.get('scoring_version')}`, rubric `{v.get('score_rubric_version')}`"
            )
        lines.append("")
    else:
        lines.append("_No CV-scoring engine versions recorded in this window._\n")
    return "\n".join(lines)


def section_volume(volume: list[dict]) -> str:
    lines = [_h("2. Decision volume by type and status")]
    if not volume:
        lines.append("_No decisions in this window._\n")
        return "\n".join(lines)
    lines.append("| decision_type | status | count |")
    lines.append("| --- | --- | ---: |")
    total = 0
    for row in volume:
        lines.append(f"| {row['decision_type']} | {row['status']} | {row['n']} |")
        total += int(row["n"])
    lines.append(f"| **total** | | **{total}** |")
    lines.append("")
    return "\n".join(lines)


def _median_str(seconds: list[float]) -> str:
    if not seconds:
        return "n/a (no resolved decisions)"
    med = statistics.median(seconds)
    if med < 90:
        return f"{med:.0f}s"
    if med < 5400:
        return f"{med / 60:.1f} min"
    return f"{med / 3600:.1f} h"


def section_oversight(oversight: dict) -> str:
    lines = [_h("3. Human-oversight metrics")]
    dispositions = oversight.get("dispositions") or []
    resolved = sum(
        int(d["n"]) for d in dispositions if d["status"] in ("approved", "overridden", "reverted_for_feedback")
    )
    approved = sum(int(d["n"]) for d in dispositions if d["human_disposition"] == "approved")
    overridden = sum(int(d["n"]) for d in dispositions if d["human_disposition"] == "overridden")
    taught = sum(int(d["n"]) for d in dispositions if d["human_disposition"] == "taught")
    resolved_disp = approved + overridden + taught

    def pct(n: int) -> str:
        return f"{(n / resolved_disp * 100):.1f}%" if resolved_disp else "n/a"

    lines.append(f"- resolved decisions (with a human disposition): **{resolved_disp}**")
    lines.append(f"- approved: {approved} ({pct(approved)})")
    lines.append(f"- overridden: {overridden} ({pct(overridden)})")
    lines.append(f"- taught (send-back & correct): {taught} ({pct(taught)})")
    lines.append(
        f"- median time-to-resolution: {_median_str(oversight.get('latencies_seconds') or [])}"
    )
    lines.append("")
    reasons = oversight.get("top_override_reasons") or []
    if reasons:
        lines.append("**Top override reasons:**\n")
        for r in reasons:
            note = str(r["resolution_note"]).replace("\n", " ").strip()
            if len(note) > 120:
                note = note[:117] + "..."
            lines.append(f"- ({r['n']}×) {note}")
        lines.append("")
    else:
        lines.append("_No override reasons recorded._\n")
    return "\n".join(lines)


def section_integrity(integrity: dict) -> str:
    lines = [_h("4. Integrity / fraud flag rates")]
    total = int(integrity.get("assessments_total") or 0)
    fraud = int(integrity.get("prompt_fraud_flagged") or 0)
    integ = int(integrity.get("integrity_flagged") or 0)
    if total == 0:
        lines.append("_No assessments in this window._\n")
        return "\n".join(lines)

    def rate(n: int) -> str:
        return f"{(n / total * 100):.1f}%"

    lines.append(f"- assessments in window: **{total}**")
    lines.append(f"- prompt-fraud flagged: {fraud} ({rate(fraud)})")
    lines.append(f"- integrity flagged: {integ} ({rate(integ)})")
    lines.append("")
    return "\n".join(lines)


def section_bias_audit(latest: Optional[dict]) -> str:
    lines = [_h("5. Latest bias-audit holdout result")]
    if latest is None:
        lines.append(
            "**Not configured.** No promotion-gate bias audit has run for this "
            "organisation. Bias audits run against a compliance-curated holdout "
            "(`config/bias_audit_examples/`) when a fitted policy is promoted; "
            "thresholds live in `config/bias_audit_thresholds.yaml`.\n"
        )
        return "\n".join(lines)
    verdict = "PASS" if latest["passed"] else "FAIL"
    audited = latest["audited_at"]
    audited_str = audited.isoformat() if hasattr(audited, "isoformat") else str(audited)
    lines.append(f"- policy_version_id: {latest['policy_version_id']}")
    lines.append(f"- audited_at: {audited_str}")
    lines.append(f"- verdict: **{verdict}**")
    violations = latest.get("violations_json")
    if violations:
        lines.append(f"- violations: `{violations}`")
    else:
        lines.append("- violations: none recorded")
    lines.append("")
    lines.append("Per-segment metrics (verbatim):\n")
    lines.append(f"```\n{latest.get('metrics_json')}\n```")
    lines.append("")
    return "\n".join(lines)


def section_adverse_impact_stub() -> str:
    lines = [_h("6. Adverse-impact (4/5ths) analysis")]
    lines.append(
        "Taali stores no protected attributes, so adverse-impact segmentation "
        "cannot be produced from application data. Generate it out of band with "
        "`scripts/adverse_impact_report.py`, supplying an operator labels CSV "
        "(candidate_id or application_id → segment columns such as gender, race, "
        "age_band). That script computes per-segment selection rates and 4/5ths "
        "impact ratios (threshold from `config/bias_audit_thresholds.yaml`) with "
        "small-n suppression.\n"
    )
    return "\n".join(lines)


def section_data_minimisation() -> str:
    lines = [_h("7. Data-minimisation statement")]
    lines.append(
        "Protected attributes (gender, race, age, nationality, religion, "
        "disability, marital status, sexual orientation, pregnancy status, "
        "veteran status) are never stored. The graph write-back pipeline blocks "
        "any node label, edge type, or property carrying a protected attribute "
        "at validation time, and this blocklist cannot be overridden by "
        "co-sign. See `config/blocked_edge_attributes.yaml` for the enforced "
        "list.\n"
    )
    return "\n".join(lines)


def build_pack(
    *,
    org_id: int,
    from_dt,
    to_dt,
    versions: dict,
    volume: list[dict],
    oversight: dict,
    integrity: dict,
    latest_bias_audit: Optional[dict],
    generated_at: Optional[datetime] = None,
) -> str:
    """Assemble the full markdown pack from already-fetched section data."""
    generated_at = generated_at or _utcnow()
    win = f"{from_dt.date() if from_dt else 'all'} → {to_dt.date() if to_dt else 'now'}"
    header = [
        "# AEDT audit pack",
        "",
        f"- organisation_id: {org_id}",
        f"- window: {win}",
        f"- generated: {generated_at.isoformat(timespec='seconds')}",
        "",
        "This pack is a factual record drawn from Taali's audit tables. It makes "
        "no claims the underlying data does not support.",
        "",
    ]
    sections = [
        section_system_description(versions),
        section_volume(volume),
        section_oversight(oversight),
        section_integrity(integrity),
        section_bias_audit(latest_bias_audit),
        section_adverse_impact_stub(),
        section_data_minimisation(),
    ]
    return "\n".join(header) + "\n" + "\n".join(sections)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _iso(value: Optional[str], *, end_of_day: bool = False):
    if not value:
        return None
    dt = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    # A date-only upper bound means "through that day": snap to 23:59:59.999999
    # so `<= to_dt` doesn't silently drop everything after midnight.
    if end_of_day and len(value) == 10:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--org-id", type=int, required=True)
    ap.add_argument("--from", dest="from_", type=str, default=None)
    ap.add_argument("--to", type=str, default=None)
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--database-url", type=str, default=None)
    args = ap.parse_args()

    url = args.database_url or os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("No DB URL: set DATABASE_URL / DATABASE_PUBLIC_URL or pass --database-url")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    from_dt = _iso(args.from_)
    to_dt = _iso(args.to, end_of_day=True)

    engine = create_engine(url, **({} if "sqlite" in url else {"pool_pre_ping": True}))
    with engine.connect() as conn:
        versions = fetch_versions(conn, args.org_id, from_dt, to_dt)
        volume = fetch_volume(conn, args.org_id, from_dt, to_dt)
        oversight = fetch_oversight(conn, args.org_id, from_dt, to_dt)
        integrity = fetch_integrity(conn, args.org_id, from_dt, to_dt)
        latest = fetch_latest_bias_audit(conn, args.org_id)

    pack = build_pack(
        org_id=args.org_id,
        from_dt=from_dt,
        to_dt=to_dt,
        versions=versions,
        volume=volume,
        oversight=oversight,
        integrity=integrity,
        latest_bias_audit=latest,
    )

    if args.out:
        Path(args.out).write_text(pack)
        print(f"wrote {args.out} ({len(pack)} bytes)")
    else:
        print(pack)


if __name__ == "__main__":
    main()
