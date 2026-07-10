#!/usr/bin/env python3
"""Adverse-impact (4/5ths rule) report — EEOC disparate-impact screen.

Taali **deliberately does not store protected attributes** (see
``config/blocked_edge_attributes.yaml``): there is no gender/race/age column
anywhere in the schema, and there never will be. So this report cannot segment
from app data. Instead — mirroring the compliance-curated
``config/bias_audit_examples/`` holdout pattern — an operator supplies a
**labels CSV** out of band (from an offline HR system, a voluntary
self-ID survey, etc.). The CSV maps candidate/application ids to segment
values; this script joins those labels onto the agent's decisions and outcomes
and computes selection rates + 4/5ths impact ratios per segment.

Labels CSV format (header row required):

    application_id,gender,race,age_band
    1024,F,white,30-39
    1025,M,black,40-49
    ...

or keyed by ``candidate_id`` instead of ``application_id``. Any segment column
is allowed; missing/blank cells become ``"unknown"``. Ids not present in the DB
(or in this org / window) are ignored.

For each segment column — and for the gender×race and gender×age_band
intersections when those columns exist — the report computes, per segment
value, four selection rates:

  (a) agent advance recommendations   — decision_type advance_to_interview
  (b) approved advances               — those with status approved
  (c) non-rejections                  — favorable complement of reject /
                                        skip_assessment_reject (a raw rejection
                                        rate would invert the 4/5ths lens)
  (d) hires                           — outcome transitions to 'hired'

The 4/5ths (0.80) impact ratio for each group is its selection rate divided by
the **highest** group's rate; a ratio below the threshold is flagged. The
threshold is read from ``config/bias_audit_thresholds.yaml`` (not hardcoded).
Cells with n < 5 are suppressed ("insufficient n") to avoid false precision on
tiny samples. Pure stdlib, read-only DB.

Run locally against the public DB URL:
    DATABASE_URL="$PUBLIC_PG_URL" \
      python scripts/adverse_impact_report.py --org-id 2 --labels-csv labels.csv

Options:
    --org-id N          organization_id to report on (required)
    --labels-csv PATH   operator-supplied segment labels (required)
    --from / --to       ISO date window on decision/event created_at (optional)
    --database-url      explicit DB URL (else DATABASE_PUBLIC_URL / DATABASE_URL)
    --min-cell-n        suppression floor (default 5)
"""
from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import create_engine, text


UNKNOWN = "unknown"
DEFAULT_MIN_CELL_N = 5
FOUR_FIFTHS_FALLBACK = 0.80
THRESHOLDS_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "bias_audit_thresholds.yaml"
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_impact_ratio_threshold(path: os.PathLike | str | None = None) -> float:
    """Read ``disparate_impact_ratio_min`` from the bias-audit thresholds YAML.

    Falls back to 0.80 (the EEOC 4/5ths rule) if the file or PyYAML is
    unavailable — the same tolerant load the promotion gate uses.
    """
    target = Path(path) if path else THRESHOLDS_PATH
    if not target.exists():
        return FOUR_FIFTHS_FALLBACK
    try:
        import yaml  # type: ignore[import-not-found]

        with target.open("r") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception:
        return FOUR_FIFTHS_FALLBACK
    return float(raw.get("disparate_impact_ratio_min", FOUR_FIFTHS_FALLBACK))


# ---------------------------------------------------------------------------
# Labels CSV parsing (pure — unit tested)
# ---------------------------------------------------------------------------
@dataclass
class Labels:
    """Parsed operator labels.

    ``by_application`` / ``by_candidate`` map the respective id -> {segment: value}.
    Exactly one is populated per row depending on the CSV's key column.
    ``segment_columns`` is the ordered list of non-key columns.
    """

    key_kind: str  # "application_id" | "candidate_id"
    segment_columns: list[str]
    by_id: dict[int, dict[str, str]] = field(default_factory=dict)


def parse_labels_csv(rows: Iterable[dict[str, str]], *, fieldnames: list[str]) -> Labels:
    """Parse pre-read CSV rows into a Labels object.

    The key column is whichever of ``application_id`` / ``candidate_id`` is
    present (application_id wins if both). Every other column is a segment.
    Missing/blank cells normalise to ``"unknown"``. Rows with a non-integer or
    missing key are skipped.
    """
    if "application_id" in fieldnames:
        key = "application_id"
    elif "candidate_id" in fieldnames:
        key = "candidate_id"
    else:
        raise ValueError(
            "labels CSV must have an 'application_id' or 'candidate_id' column; "
            f"got {fieldnames!r}"
        )
    segment_columns = [c for c in fieldnames if c not in ("application_id", "candidate_id")]
    labels = Labels(key_kind=key, segment_columns=segment_columns)
    for row in rows:
        raw_id = (row.get(key) or "").strip()
        if not raw_id:
            continue
        try:
            id_int = int(raw_id)
        except ValueError:
            continue
        segs = {}
        for col in segment_columns:
            val = (row.get(col) or "").strip()
            segs[col] = val if val else UNKNOWN
        labels.by_id[id_int] = segs
    return labels


def load_labels_file(path: os.PathLike | str) -> Labels:
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        return parse_labels_csv(reader, fieldnames=fieldnames)


# ---------------------------------------------------------------------------
# 4/5ths math (pure — unit tested)
# ---------------------------------------------------------------------------
@dataclass
class RateCell:
    group: str
    n: int
    selected: int
    rate: Optional[float]  # None => suppressed (n < min_n)
    ratio: Optional[float]  # vs highest-rate group; None if suppressed/no ref
    flagged: bool
    suppressed: bool


def compute_impact_ratios(
    counts: dict[str, tuple[int, int]],
    *,
    threshold: float,
    min_n: int = DEFAULT_MIN_CELL_N,
) -> list[RateCell]:
    """Compute per-group selection rate + 4/5ths impact ratio.

    ``counts`` maps group value -> (n_total, n_selected). The reference is the
    highest selection rate among the **non-suppressed** groups. A group's ratio
    is rate / reference_rate; flagged when ratio < threshold. Groups with
    n < ``min_n`` are suppressed (rate/ratio None, never flagged, never the
    reference). Returns cells sorted by group name for stable output.
    """
    cells: dict[str, RateCell] = {}
    for group, (n, selected) in counts.items():
        if n < min_n:
            cells[group] = RateCell(group, n, selected, None, None, False, True)
        else:
            cells[group] = RateCell(group, n, selected, selected / n, None, False, False)

    live_rates = [c.rate for c in cells.values() if c.rate is not None]
    reference = max(live_rates) if live_rates else None

    for cell in cells.values():
        if cell.suppressed or cell.rate is None or reference is None:
            continue
        if reference == 0:
            # No group selects anyone — impact ratio is undefined; treat as
            # parity (ratio 1.0, unflagged) rather than a spurious divide.
            cell.ratio = 1.0
            cell.flagged = False
        else:
            cell.ratio = cell.rate / reference
            cell.flagged = cell.ratio < threshold
    return [cells[k] for k in sorted(cells)]


# ---------------------------------------------------------------------------
# DB access (read-only)
# ---------------------------------------------------------------------------
def _org_window_clause(from_dt, to_dt) -> str:
    clause = ""
    if from_dt is not None:
        clause += " AND created_at >= :from_dt"
    if to_dt is not None:
        clause += " AND created_at <= :to_dt"
    return clause


def fetch_decision_map(conn, org_id: int, from_dt, to_dt) -> dict[int, dict]:
    """Per application_id: recommendation/approval/reject flags from decisions.

    A single application may carry multiple decisions across cycles; we OR the
    flags so an application that was ever recommended-for-advance counts once.
    """
    sql = text(
        f"""
        SELECT application_id, candidate_id,
               decision_type, status
        FROM agent_decisions ad
        JOIN candidate_applications ca ON ca.id = ad.application_id
        WHERE ad.organization_id = :org
        {_org_window_clause(from_dt, to_dt).replace('created_at', 'ad.created_at')}
        """
    )
    params = {"org": org_id}
    if from_dt is not None:
        params["from_dt"] = from_dt
    if to_dt is not None:
        params["to_dt"] = to_dt

    by_app: dict[int, dict] = {}
    for r in conn.execute(sql, params):
        m = r._mapping
        app_id = int(m["application_id"])
        entry = by_app.setdefault(
            app_id,
            {
                "candidate_id": int(m["candidate_id"]),
                "advance_reco": False,
                "advance_approved": False,
                "reject": False,
            },
        )
        dtype = m["decision_type"]
        status = m["status"]
        if dtype == "advance_to_interview":
            entry["advance_reco"] = True
            if status == "approved":
                entry["advance_approved"] = True
        elif dtype in ("reject", "skip_assessment_reject"):
            entry["reject"] = True
    return by_app


def fetch_hired_app_ids(conn, org_id: int, from_dt, to_dt) -> set[int]:
    """Applications with an outcome transition to 'hired' in-window."""
    sql = text(
        f"""
        SELECT DISTINCT application_id
        FROM candidate_application_events
        WHERE organization_id = :org
          AND event_type = 'application_outcome_changed'
          AND to_outcome = 'hired'
          {_org_window_clause(from_dt, to_dt)}
        """
    )
    params = {"org": org_id}
    if from_dt is not None:
        params["from_dt"] = from_dt
    if to_dt is not None:
        params["to_dt"] = to_dt
    return {int(r[0]) for r in conn.execute(sql, params)}


# ---------------------------------------------------------------------------
# Aggregation (pure given the joined records — unit tested)
# ---------------------------------------------------------------------------
# The four selection lenses. Each maps to the boolean on a joined record.
# All lenses are FAVORABLE outcomes: the 4/5ths ratio benchmarks each group
# against the highest-rate group, so an unfavorable lens (raw rejection rate)
# would invert the analysis — the most-rejected group would sit at ratio 1.0
# and never be flagged. Rejection is therefore reported as its favorable
# complement, the non-rejection rate.
METRIC_KEYS = [
    ("advance_reco", "agent advance recommendations"),
    ("advance_approved", "approved advances"),
    ("non_reject", "non-rejections (survived screen, incl. skip-assessment)"),
    ("hire", "hires"),
]


def _segment_value(seg_map: dict[str, str], column: str) -> str:
    return seg_map.get(column, UNKNOWN) if seg_map else UNKNOWN


def tally_counts(
    records: list[dict],
    *,
    segment_key: str,
    metric: str,
) -> dict[str, tuple[int, int]]:
    """Group joined records by a single segment column (or an ``a×b`` intersection
    key) and count (n_total, n_selected) for one metric.

    ``segment_key`` names a value already computed onto each record under
    record["segments"][segment_key]. ``metric`` is one of the METRIC_KEYS
    booleans.
    """
    out: dict[str, list[int]] = {}
    for rec in records:
        group = rec["segments"].get(segment_key, UNKNOWN)
        bucket = out.setdefault(group, [0, 0])
        bucket[0] += 1
        if rec.get(metric):
            bucket[1] += 1
    return {g: (n, sel) for g, (n, sel) in out.items()}


def build_records(
    labels: Labels,
    decision_map: dict[int, dict],
    hired_ids: set[int],
) -> list[dict]:
    """Join labels onto per-application decision flags + hire flag.

    Only applications that appear in BOTH the labels file and the decision map
    are included (an application with no agent decision has no selection signal
    to segment). Segment columns absent from the labels row default to unknown.
    Also derives the gender×race and gender×age_band intersection keys when the
    base columns are present.
    """
    records: list[dict] = []
    for app_id, flags in decision_map.items():
        cand_id = flags["candidate_id"]
        if labels.key_kind == "application_id":
            segs = labels.by_id.get(app_id)
        else:
            segs = labels.by_id.get(cand_id)
        if segs is None:
            continue
        seg_values = {col: _segment_value(segs, col) for col in labels.segment_columns}
        # Intersections (only when both base columns exist in the labels file).
        if "gender" in seg_values and "race" in seg_values:
            seg_values["gender×race"] = f"{seg_values['gender']} × {seg_values['race']}"
        if "gender" in seg_values and "age_band" in seg_values:
            seg_values["gender×age_band"] = f"{seg_values['gender']} × {seg_values['age_band']}"
        records.append(
            {
                "application_id": app_id,
                "segments": seg_values,
                "advance_reco": flags["advance_reco"],
                "advance_approved": flags["advance_approved"],
                "non_reject": not flags["reject"],
                "hire": app_id in hired_ids,
            }
        )
    return records


def segment_keys_for(labels: Labels) -> list[str]:
    """Ordered list of segment/intersection keys to report on."""
    keys = list(labels.segment_columns)
    if "gender" in labels.segment_columns and "race" in labels.segment_columns:
        keys.append("gender×race")
    if "gender" in labels.segment_columns and "age_band" in labels.segment_columns:
        keys.append("gender×age_band")
    return keys


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def rule(c: str = "─", n: int = 78) -> str:
    return c * n


def _fmt_rate(cell: RateCell) -> str:
    if cell.suppressed:
        return "insufficient n"
    return f"{cell.rate:.1%} ({cell.selected}/{cell.n})"


def _fmt_ratio(cell: RateCell) -> str:
    if cell.suppressed or cell.ratio is None:
        return "—"
    flag = "  ⚠ FLAG" if cell.flagged else ""
    return f"{cell.ratio:.2f}{flag}"


def render_report(
    records: list[dict],
    labels: Labels,
    *,
    threshold: float,
    min_n: int,
) -> tuple[str, int]:
    """Render the full text report. Returns (text, flag_count)."""
    lines: list[str] = []
    flag_count = 0
    keys = segment_keys_for(labels)

    for seg_key in keys:
        lines.append("")
        lines.append(rule("═"))
        lines.append(f"  SEGMENT: {seg_key}")
        lines.append(rule("═"))
        for metric_key, metric_label in METRIC_KEYS:
            counts = tally_counts(records, segment_key=seg_key, metric=metric_key)
            cells = compute_impact_ratios(counts, threshold=threshold, min_n=min_n)
            lines.append(f"\n  {metric_label}")
            lines.append(f"  {'group':<28}{'selection rate':<22}{'4/5ths ratio':<18}")
            lines.append(f"  {rule('-', 66)}")
            for cell in cells:
                if cell.flagged:
                    flag_count += 1
                lines.append(f"  {cell.group:<28}{_fmt_rate(cell):<22}{_fmt_ratio(cell):<18}")
    return "\n".join(lines), flag_count


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
    ap.add_argument("--labels-csv", type=str, required=True)
    ap.add_argument("--from", dest="from_", type=str, default=None, help="ISO date lower bound")
    ap.add_argument("--to", type=str, default=None, help="ISO date upper bound")
    ap.add_argument("--database-url", type=str, default=None)
    ap.add_argument("--min-cell-n", type=int, default=DEFAULT_MIN_CELL_N)
    args = ap.parse_args()

    url = args.database_url or os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("No DB URL: set DATABASE_URL / DATABASE_PUBLIC_URL or pass --database-url")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    from_dt = _iso(args.from_)
    to_dt = _iso(args.to, end_of_day=True)
    threshold = load_impact_ratio_threshold()
    labels = load_labels_file(args.labels_csv)

    engine = create_engine(url, **({} if "sqlite" in url else {"pool_pre_ping": True}))
    with engine.connect() as conn:
        decision_map = fetch_decision_map(conn, args.org_id, from_dt, to_dt)
        hired_ids = fetch_hired_app_ids(conn, args.org_id, from_dt, to_dt)

    records = build_records(labels, decision_map, hired_ids)

    print(rule("█"))
    print("  TAALI ADVERSE-IMPACT (4/5ths) REPORT")
    print(f"  generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"  org={args.org_id}  impact-ratio threshold={threshold:.2f}  min-cell-n={args.min_cell_n}")
    win = f"{from_dt.date() if from_dt else 'all'} → {to_dt.date() if to_dt else 'now'}"
    print(f"  window={win}  labels-key={labels.key_kind}  segments={labels.segment_columns}")
    print(f"  applications matched (labels ∩ decisions): {len(records)}")
    print(rule("█"))

    if not records:
        print("\n  No labelled applications with agent decisions in this window.")
        print("  Nothing to report. (Check the labels-csv keys match this org's ids.)")
        return

    body, flag_count = render_report(
        records, labels, threshold=threshold, min_n=args.min_cell_n
    )
    print(body)

    print(f"\n{rule('═')}")
    print("  SUMMARY VERDICT")
    print(rule("═"))
    if flag_count == 0:
        print("  No adverse-impact flags: every non-suppressed group's 4/5ths ratio")
        print(f"  is at or above the {threshold:.2f} threshold across all metrics.")
    else:
        print(f"  {flag_count} cell(s) FLAGGED below the {threshold:.2f} impact-ratio threshold.")
        print("  A flag is a screening signal, NOT a legal determination. Review flagged")
        print("  segments with counsel; small samples and confounders can drive ratios.")
    print("\n  NOTES")
    print("  • Segments come from the operator-supplied labels CSV — Taali stores no")
    print("    protected attributes (see config/blocked_edge_attributes.yaml).")
    print(f"  • Cells with n < {args.min_cell_n} are suppressed to avoid false precision.")
    print("  • Ratios are vs the highest-rate group per metric (EEOC 4/5ths rule).")
    print(rule())


if __name__ == "__main__":
    main()
