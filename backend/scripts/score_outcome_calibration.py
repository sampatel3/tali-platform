#!/usr/bin/env python3
"""Score↔outcome calibration — does the Taali score predict the interview?

The cost-per-outcome report answers "what does a hire cost?". This answers the
prior question: **is the score any good?** It joins each application's Taali
score against (a) the human interview recommendation recorded in
``interview_feedback`` and (b) the eventual ``application_outcome``, and reports
predictive validity per role and overall.

Signals reported:
  * n interviews with feedback (per role + overall)
  * mean Taali score within each recommendation band (strong_no … strong_yes)
  * point-biserial correlation between Taali score and a positive
    recommendation (yes/strong_yes = 1, else 0)
  * point-biserial correlation between Taali score and a hire
    (application_outcome == 'hired')
  * count of advance decisions later contradicted by no/strong_no feedback
    (advanced/hired candidates who then got a negative interview verdict)

Pure stdlib math — no scipy. Read-only DB access.

Run locally against the public DB URL:
    DATABASE_URL="$PUBLIC_PG_URL" python scripts/score_outcome_calibration.py

Options:
    --org-id N        restrict to one organization_id (default: all orgs)
    --database-url    explicit DB URL (else DATABASE_PUBLIC_URL / DATABASE_URL)
"""
from __future__ import annotations

import argparse
import math
import os
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import create_engine, text


# recommendation → numeric band (strongest-negative … strongest-positive).
RECOMMENDATION_SCORE = {
    "strong_yes": 2,
    "yes": 1,
    "neutral": 0,
    "no": -1,
    "strong_no": -2,
}
POSITIVE_RECOMMENDATIONS = {"yes", "strong_yes"}
NEGATIVE_RECOMMENDATIONS = {"no", "strong_no"}
# Explicit abstention — carries no lean, so it never enters any aggregate.
ABSTAIN_RECOMMENDATIONS = {"no_decision"}
# recommendation bands rendered top→bottom in the per-band mean table.
BAND_ORDER = ("strong_yes", "yes", "neutral", "no", "strong_no")
ADVANCED_MARKERS = {"advanced"}


@dataclass
class FeedbackRow:
    """One interview_feedback row joined to its application's score + outcome."""

    role_id: Optional[int]
    role_name: Optional[str]
    taali_score: Optional[float]
    recommendation: str
    application_outcome: Optional[str]
    pipeline_stage: Optional[str]


@dataclass
class CalibrationStats:
    n: int = 0
    n_scored: int = 0
    band_scores: dict[str, list[float]] = field(default_factory=dict)
    corr_recommendation: Optional[float] = None
    corr_hired: Optional[float] = None
    contradicted_advances: int = 0

    def band_means(self) -> dict[str, Optional[float]]:
        out: dict[str, Optional[float]] = {}
        for band in BAND_ORDER:
            vals = self.band_scores.get(band) or []
            out[band] = (sum(vals) / len(vals)) if vals else None
        return out


def point_biserial(scores: list[float], flags: list[int]) -> Optional[float]:
    """Point-biserial correlation between a continuous score and a 0/1 flag.

    Equivalent to Pearson r with a binary variable. Returns None when there is
    no variance in either series (fewer than 2 points, all-same score, or a
    single class present).
    """
    n = len(scores)
    if n < 2 or len(flags) != n:
        return None
    mean_s = sum(scores) / n
    mean_f = sum(flags) / n
    if mean_f in (0.0, 1.0):  # only one class present → undefined
        return None
    cov = sum((s - mean_s) * (f - mean_f) for s, f in zip(scores, flags))
    var_s = sum((s - mean_s) ** 2 for s in scores)
    var_f = sum((f - mean_f) ** 2 for f in flags)
    denom = math.sqrt(var_s * var_f)
    if denom == 0:
        return None
    return cov / denom


def compute_calibration(rows: list[FeedbackRow]) -> CalibrationStats:
    """Core computation — pure function over the joined rows (unit-tested)."""
    stats = CalibrationStats(n=len(rows))
    stats.band_scores = {band: [] for band in BAND_ORDER}

    scored_scores: list[float] = []
    rec_flags: list[int] = []
    hired_flags: list[int] = []

    for row in rows:
        # Abstentions carry no lean — they don't count toward n, any band, the
        # correlations, or the contradiction tally. (The SQL already filters
        # these out; this guard keeps the pure function correct on its own.)
        if row.recommendation in ABSTAIN_RECOMMENDATIONS:
            stats.n -= 1
            continue
        score = row.taali_score
        if score is not None:
            stats.n_scored += 1
            if row.recommendation in stats.band_scores:
                stats.band_scores[row.recommendation].append(float(score))
            scored_scores.append(float(score))
            rec_flags.append(1 if row.recommendation in POSITIVE_RECOMMENDATIONS else 0)
            hired_flags.append(1 if (row.application_outcome or "") == "hired" else 0)

        # Advance decisions later contradicted by a negative interview verdict.
        advanced = (
            (row.pipeline_stage or "") in ADVANCED_MARKERS
            or (row.application_outcome or "") == "hired"
        )
        if advanced and row.recommendation in NEGATIVE_RECOMMENDATIONS:
            stats.contradicted_advances += 1

    stats.corr_recommendation = point_biserial(scored_scores, rec_flags)
    stats.corr_hired = point_biserial(scored_scores, hired_flags)
    return stats


# ---------- DB access ----------
def _org_clause(org: Optional[int]) -> str:
    return " AND ca.organization_id = :org" if org is not None else ""


def load_rows(conn, org: Optional[int]) -> list[FeedbackRow]:
    """Join interview_feedback to its application's score + outcome. Read-only.

    Uses ``taali_score_cache_100`` as the score (the canonical 0–100 the UI
    shows), falling back to ``cv_match_score`` then ``pre_screen_score_100``.
    """
    params: dict = {}
    if org is not None:
        params["org"] = org
    sql = text(
        f"""
        SELECT ifb.role_id                                   AS role_id,
               r.name                                        AS role_name,
               COALESCE(ca.taali_score_cache_100,
                        ca.cv_match_score,
                        ca.pre_screen_score_100)             AS taali_score,
               ifb.overall_recommendation                    AS recommendation,
               ca.application_outcome                         AS application_outcome,
               ca.pipeline_stage                              AS pipeline_stage
        FROM interview_feedback ifb
        JOIN candidate_applications ca ON ca.id = ifb.application_id
        LEFT JOIN roles r ON r.id = ifb.role_id
        WHERE ca.deleted_at IS NULL
          -- Submitted feedback only; drafts (submitted_at NULL) are excluded.
          -- Legacy rows were backfilled to submitted (migration 148), so this
          -- is a no-op at cutover.
          AND ifb.submitted_at IS NOT NULL
          -- Abstentions carry no lean — never enter the calibration.
          AND ifb.overall_recommendation <> 'no_decision'
          {_org_clause(org)}
        """
    )
    rows: list[FeedbackRow] = []
    for rec in conn.execute(sql, params):
        m = rec._mapping
        score = m["taali_score"]
        rows.append(
            FeedbackRow(
                role_id=m["role_id"],
                role_name=m["role_name"],
                taali_score=float(score) if score is not None else None,
                recommendation=m["recommendation"],
                application_outcome=m["application_outcome"],
                pipeline_stage=m["pipeline_stage"],
            )
        )
    return rows


# ---------- formatting ----------
def rule(c: str = "─", n: int = 74) -> str:
    return c * n


def _fmt_corr(v: Optional[float]) -> str:
    return f"{v:+.3f}" if v is not None else "n/a"


def _fmt_mean(v: Optional[float]) -> str:
    return f"{v:6.1f}" if v is not None else "   n/a"


def render_stats(label: str, stats: CalibrationStats) -> None:
    print(f"\n{rule('═')}")
    print(f"  {label}")
    print(rule('═'))
    print(f"    interviews with feedback : {stats.n:,}")
    print(f"    with a Taali score       : {stats.n_scored:,}")

    means = stats.band_means()
    print(f"\n    mean Taali score by recommendation band")
    print(f"    {'band':<12}{'mean':>8}{'n':>7}")
    print(f"    {rule('-', 27)}")
    for band in BAND_ORDER:
        vals = stats.band_scores.get(band) or []
        print(f"    {band:<12}{_fmt_mean(means[band]):>8}{len(vals):>7}")

    print(f"\n    point-biserial correlations (score vs …)")
    print(f"      positive recommendation : {_fmt_corr(stats.corr_recommendation)}")
    print(f"      hired outcome           : {_fmt_corr(stats.corr_hired)}")
    print(f"\n    advances contradicted by no/strong_no feedback : {stats.contradicted_advances:,}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--org-id", type=int, default=None, help="restrict to one organization_id")
    ap.add_argument("--database-url", type=str, default=None)
    args = ap.parse_args()

    url = (
        args.database_url
        or os.environ.get("DATABASE_PUBLIC_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not url:
        raise SystemExit("No DB URL: set DATABASE_URL / DATABASE_PUBLIC_URL or pass --database-url")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    engine = create_engine(url, **({} if "sqlite" in url else {"pool_pre_ping": True}))
    with engine.connect() as conn:
        rows = load_rows(conn, args.org_id)

    print(rule("█"))
    print("  TAALI SCORE↔OUTCOME CALIBRATION")
    print(f"  org={args.org_id if args.org_id is not None else 'ALL'}   basis: interview_feedback ⋈ application score/outcome")
    print(rule("█"))

    render_stats("OVERALL", compute_calibration(rows))

    # Per-role breakdown, largest cohorts first.
    by_role: dict[tuple, list[FeedbackRow]] = {}
    for row in rows:
        key = (row.role_id, row.role_name)
        by_role.setdefault(key, []).append(row)
    for (role_id, role_name), role_rows in sorted(
        by_role.items(), key=lambda kv: len(kv[1]), reverse=True
    ):
        label = f"ROLE {role_id} · {role_name or '(unnamed)'}"
        render_stats(label, compute_calibration(role_rows))

    print(f"\n{rule()}")
    print("  NOTES")
    print("  • Score = taali_score_cache_100 (→ cv_match_score → pre_screen_score_100).")
    print("  • Positive recommendation = yes/strong_yes; correlation is point-biserial.")
    print("  • Contradicted advances = advanced/hired candidates with a no/strong_no verdict.")
    print("  • Read-only: this script never writes.")
    print(rule())


if __name__ == "__main__":
    main()
