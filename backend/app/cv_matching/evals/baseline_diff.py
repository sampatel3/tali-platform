"""Autogen markdown baseline reports from JSON snapshots.

Reads ``baseline_results/{prompt_version}_{ts}.json`` and produces a
sibling ``.md`` file with a per-case table, pass/fail summary, score
histogram bucketing, and dimension-score distribution.

No human writes these files. They're generated whenever
``run_evals --baseline-md`` runs.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import mean, pstdev


_BANDS = [(0, 25), (25, 50), (50, 70), (70, 85), (85, 101)]


def _bucket(score: float) -> str:
    for lo, hi in _BANDS:
        if lo <= score < hi:
            return f"[{lo},{hi})"
    return "?"


def write_markdown_report(snapshot_path: str | Path) -> Path:
    """Read a JSON snapshot, write a sibling .md report. Returns the .md path."""
    snapshot_path = Path(snapshot_path)
    blob = json.loads(snapshot_path.read_text(encoding="utf-8"))
    results = blob.get("results", []) or []
    prompt_version = blob.get("prompt_version", "?")
    timestamp = blob.get("timestamp", "?")

    n_total = len(results)
    n_passed = sum(1 for r in results if r.get("passed"))
    scores = [float(r.get("role_fit_score", 0.0)) for r in results]
    rec_counter = Counter(r.get("recommendation", "?") for r in results)
    band_counter = Counter(_bucket(s) for s in scores)

    # Dimension distribution (six dimensions; only counts cases with them).
    dim_names = (
        "skills_coverage",
        "skills_depth",
        "title_trajectory",
        "seniority_alignment",
        "industry_match",
        "tenure_pattern",
    )
    per_dim: dict[str, list[float]] = {d: [] for d in dim_names}
    for r in results:
        ds = (r.get("output") or {}).get("dimension_scores")
        if ds:
            for d in dim_names:
                if ds.get(d) is not None:
                    per_dim[d].append(float(ds[d]))

    lines: list[str] = [
        f"# Baseline report — {prompt_version} ({timestamp})",
        "",
        f"- **Cases:** {n_total}",
        f"- **Passed:** {n_passed} / {n_total}",
        f"- **Score range:** {min(scores) if scores else 0:.1f} – {max(scores) if scores else 0:.1f}",
        f"- **Score median / mean:** "
        f"{(sorted(scores)[len(scores)//2] if scores else 0):.1f} / "
        f"{(mean(scores) if scores else 0):.2f}",
        "",
        "## Recommendation distribution",
        "",
        "| recommendation | count | % |",
        "| --- | ---: | ---: |",
    ]
    for rec, count in sorted(rec_counter.items(), key=lambda t: -t[1]):
        pct = (100.0 * count / n_total) if n_total else 0.0
        lines.append(f"| {rec} | {count} | {pct:.1f}% |")
    lines += [
        "",
        "## Score band distribution",
        "",
        "| band | count | % |",
        "| --- | ---: | ---: |",
    ]
    for band in [f"[{lo},{hi})" for lo, hi in _BANDS]:
        count = band_counter.get(band, 0)
        pct = (100.0 * count / n_total) if n_total else 0.0
        lines.append(f"| {band} | {count} | {pct:.1f}% |")

    if any(per_dim.values()):
        lines += [
            "",
            "## Per-dimension stats",
            "",
            "| dimension | n | mean | std | min | max |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for d in dim_names:
            vals = per_dim[d]
            if not vals:
                lines.append(f"| {d} | 0 | — | — | — | — |")
                continue
            lines.append(
                f"| {d} | {len(vals)} | {mean(vals):.1f} | "
                f"{(pstdev(vals) if len(vals) > 1 else 0.0):.1f} | "
                f"{min(vals):.1f} | {max(vals):.1f} |"
            )

    lines += [
        "",
        "## Per-case results",
        "",
        "| case_id | passed | recommendation | role_fit | failures |",
        "| --- | :---: | --- | ---: | --- |",
    ]
    for r in results:
        marker = "✓" if r.get("passed") else "✗"
        failures = "; ".join(r.get("failures", []) or []) or "—"
        lines.append(
            f"| {r.get('case_id', '?')} | {marker} | "
            f"{r.get('recommendation', '?')} | "
            f"{r.get('role_fit_score', 0.0):.1f} | {failures} |"
        )

    md_path = snapshot_path.with_suffix(".md")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


__all__ = ["write_markdown_report"]
