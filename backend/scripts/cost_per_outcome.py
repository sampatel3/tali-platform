#!/usr/bin/env python3
"""Cost-per-outcome report — what Taali's Anthropic spend buys, per funnel unit.

The reconciler (``scripts/reconcile_and_report.py``) answers "is our metering
accurate vs Anthropic's bill?" and "where did the money go, by *operation*?".
This script answers the *business* question instead: **what does it cost per
pre-screen, per full score, per advanced candidate, per offer, per hire** — for
any time window (all-time, a month, the last N days, since the scoring change).

It joins two sides:

  COST  — ``usage_events`` (the metering ledger). Cost basis is the *raw*
          Anthropic cost ``SUM(cost_usd_micro)`` with **cache-hit rows
          excluded**. Cache hits make no Anthropic call (cost == $0 real), and
          pre-#476 cache rows still carry a stale cached cost, so they would
          inflate the total 30–60%. Excluding ``cache_hit=1`` matches the
          reconciler's basis. This is the money Sam pays Anthropic — NOT the
          marked-up ``credits_charged`` customers would be billed.

  FUNNEL — ``candidate_applications`` + ``candidate_application_events``.
          Pre-screen / full-score counts come from distinct ``entity_id``
          (application id) on the matching ``usage_events`` so cost and count
          share one source. Advanced / hire counts come from the timestamped
          transition events. Offers are a Workable-mirrored stage with no
          Taali-native timestamp, so they're reported as a current snapshot.

Two kinds of "cost per X" — kept distinct on purpose, because conflating them
is exactly what makes the answer confusing:

  * DIRECT unit cost (pre-screen, full score) = spend on THAT feature ÷ the
    candidates that feature ran on, in-window. A real per-operation price.

  * FULLY-LOADED cost per outcome (advanced, offer, hire) = TOTAL spend in the
    window ÷ outcomes in the window. Advancing/offering/hiring burn no tokens
    themselves — this amortises ALL AI spend over the funnel result, the
    classic "AI cost per hire" number. Loaded numbers carry a lead-time
    mismatch (spend now vs an outcome seeded weeks ago); the all-time blended
    figures in the footer are the cleanest read of those.

Run on prod (no deploy needed — base64 the file to /tmp on the web service):
    railway ssh --service resourceful-adaptation \
      "cd /app && PYTHONPATH=/app /opt/venv/bin/python /tmp/cost_per_outcome.py"

Run locally against the public DB URL:
    DATABASE_URL="$PUBLIC_PG_URL" python scripts/cost_per_outcome.py

Options:
    --org N            restrict to one organization_id (default: all orgs)
    --since / --until  custom window (ISO date), instead of the standard suite
    --scoring-change   date the scoring model changed (default 2026-06-13)
    --database-url     explicit DB URL (else DATABASE_PUBLIC_URL / DATABASE_URL)
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text


# --- domain constants (mirror app/domains/assessments_runtime/pipeline_service.py) ---
OFFER_STAGES = ("offer", "offer_extended", "offer_accepted")
HIRED_STAGE = "hired"
# Holistic v2 (Sonnet) scoring rolled out ~2026-06-13 → cold re-score spike.
DEFAULT_SCORING_CHANGE = "2026-06-13"
EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Window:
    label: str
    start: datetime
    end: datetime


def standard_windows(now: datetime, scoring_change: datetime) -> list[Window]:
    """The cuts Sam asked for: all-time, May, June, June last 7d, since scoring change."""
    may_start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    jun_start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    jul_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return [
        Window("All time", EPOCH, now),
        Window("May 2026", may_start, jun_start),
        Window("June 2026 (to date)", jun_start, min(jul_start, now)),
        Window("June — last 7 days", now - timedelta(days=7), now),
        Window(f"June — since scoring change ({scoring_change.date()})", scoring_change, now),
    ]


# ---------- query helpers ----------
def _org_clause(org: int | None) -> str:
    return " AND organization_id = :org" if org is not None else ""


def _params(w: Window, org: int | None, **extra) -> dict:
    p = {"start": w.start, "end": w.end, **extra}
    if org is not None:
        p["org"] = org
    return p


def cost_by_feature(conn, w: Window, org: int | None) -> list[dict]:
    """Raw Anthropic cost by feature in-window, cache hits excluded."""
    sql = text(
        f"""
        SELECT feature,
               COUNT(*)                       AS calls,
               COUNT(DISTINCT entity_id)      AS entities,
               COALESCE(SUM(cost_usd_micro),0) AS cost_micro,
               COALESCE(SUM(input_tokens),0)   AS in_tok,
               COALESCE(SUM(output_tokens),0)  AS out_tok
        FROM usage_events
        WHERE cache_hit = 0
          AND created_at >= :start AND created_at < :end
          {_org_clause(org)}
        GROUP BY feature
        ORDER BY cost_micro DESC
        """
    )
    return [dict(r._mapping) for r in conn.execute(sql, _params(w, org))]


def spend_by_org(conn, w: Window) -> list[dict]:
    sql = text(
        """
        SELECT organization_id AS org,
               COALESCE(SUM(cost_usd_micro),0) AS cost_micro,
               COUNT(*) AS calls
        FROM usage_events
        WHERE cache_hit = 0 AND created_at >= :start AND created_at < :end
        GROUP BY organization_id
        ORDER BY cost_micro DESC
        """
    )
    return [dict(r._mapping) for r in conn.execute(sql, {"start": w.start, "end": w.end})]


def feature_entities(conn, w: Window, org: int | None, feature: str) -> int:
    """Distinct candidates (application ids) a feature billed for, in-window."""
    sql = text(
        f"""
        SELECT COUNT(DISTINCT entity_id)
        FROM usage_events
        WHERE cache_hit = 0 AND feature = :feature
          AND created_at >= :start AND created_at < :end
          {_org_clause(org)}
        """
    )
    return int(conn.execute(sql, _params(w, org, feature=feature)).scalar() or 0)


def advanced_in_window(conn, w: Window, org: int | None) -> int:
    """Stage transitions to 'advanced' (Taali decision OR Workable hand-off reflect)."""
    sql = text(
        f"""
        SELECT COUNT(DISTINCT application_id)
        FROM candidate_application_events
        WHERE event_type = 'pipeline_stage_changed' AND to_stage = 'advanced'
          AND created_at >= :start AND created_at < :end
          {_org_clause(org)}
        """
    )
    return int(conn.execute(sql, _params(w, org)).scalar() or 0)


def hires_in_window(conn, w: Window, org: int | None) -> int:
    """Outcome transitions to 'hired' (the only cleanly-timestamped hire signal)."""
    sql = text(
        f"""
        SELECT COUNT(DISTINCT application_id)
        FROM candidate_application_events
        WHERE event_type = 'application_outcome_changed' AND to_outcome = 'hired'
          AND created_at >= :start AND created_at < :end
          {_org_clause(org)}
        """
    )
    return int(conn.execute(sql, _params(w, org)).scalar() or 0)


def offers_windowed_besteffort(conn, w: Window, org: int | None) -> int:
    """Best-effort offers in-window: Workable hand-off advances whose reason names an
    offer stage ('Advanced in Workable (offer)…'). Approximate — Workable stage
    moves aren't individually timestamped in Taali."""
    # Bind the LIKE pattern as a param — a literal '%' in text() SQL collides
    # with SQLAlchemy's parameter parsing.
    sql = text(
        f"""
        SELECT COUNT(DISTINCT application_id)
        FROM candidate_application_events
        WHERE event_type = 'pipeline_stage_changed' AND to_stage = 'advanced'
          AND lower(reason) LIKE :offer_pat
          AND created_at >= :start AND created_at < :end
          {_org_clause(org)}
        """
    )
    return int(conn.execute(sql, _params(w, org, offer_pat="%(offer%")).scalar() or 0)


def snapshot_funnel(conn, org: int | None) -> dict:
    """Current-state funnel totals (all-time), independent of event timestamps."""
    oc = _org_clause(org)
    op = {"org": org} if org is not None else {}
    offer_list = ",".join(f"'{s}'" for s in OFFER_STAGES)
    q = {
        "applications": f"SELECT COUNT(*) FROM candidate_applications WHERE deleted_at IS NULL{oc}",
        "prescreened": f"SELECT COUNT(*) FROM candidate_applications WHERE deleted_at IS NULL AND pre_screen_run_at IS NOT NULL{oc}",
        "scored": f"SELECT COUNT(*) FROM candidate_applications WHERE deleted_at IS NULL AND cv_match_score IS NOT NULL{oc}",
        "advanced": f"SELECT COUNT(*) FROM candidate_applications WHERE deleted_at IS NULL AND pipeline_stage = 'advanced'{oc}",
        "offers": f"SELECT COUNT(*) FROM candidate_applications WHERE deleted_at IS NULL AND lower(workable_stage) IN ({offer_list}){oc}",
        "hired_outcome": f"SELECT COUNT(*) FROM candidate_applications WHERE deleted_at IS NULL AND application_outcome = 'hired'{oc}",
        "hired_workable": f"SELECT COUNT(*) FROM candidate_applications WHERE deleted_at IS NULL AND lower(workable_stage) = '{HIRED_STAGE}'{oc}",
    }
    return {k: int(conn.execute(text(v), op).scalar() or 0) for k, v in q.items()}


# ---------- formatting ----------
def usd(micro: float) -> str:
    d = micro / 1_000_000
    if abs(d) >= 100:
        return f"${d:,.0f}"
    if abs(d) >= 1:
        return f"${d:,.2f}"
    return f"${d:,.4f}"


def per_unit(cost_micro: float, n: int) -> str:
    return usd(cost_micro / n) if n else "n/a (0)"


def rule(c: str = "─", n: int = 78) -> str:
    return c * n


def render_window(conn, w: Window, org: int | None) -> None:
    feats = cost_by_feature(conn, w, org)
    total = sum(f["cost_micro"] for f in feats)

    print(f"\n{rule('═')}")
    print(f"  {w.label}   [{w.start.date()} → {w.end.date()}]   org={org if org is not None else 'ALL'}")
    print(rule('═'))
    print(f"  RAW ANTHROPIC SPEND (cache-hit calls excluded): {usd(total)}")

    if feats:
        print(f"\n  {'feature':<20}{'$':>11}{'%':>6}{'calls':>9}{'cands':>8}{'$/cand':>11}")
        print(f"  {rule('-', 65)}")
        for f in feats:
            pct = (f["cost_micro"] / total * 100) if total else 0
            print(
                f"  {f['feature']:<20}{usd(f['cost_micro']):>11}{pct:>5.0f}%"
                f"{f['calls']:>9,}{f['entities']:>8,}{per_unit(f['cost_micro'], f['entities']):>11}"
            )

    # by-feature lookup for unit economics
    fmap = {f["feature"]: f for f in feats}
    pre_cost = fmap.get("prescreen", {}).get("cost_micro", 0)
    score_cost = fmap.get("score", {}).get("cost_micro", 0)
    pre_n = feature_entities(conn, w, org, "prescreen")
    score_n = feature_entities(conn, w, org, "score")

    adv = advanced_in_window(conn, w, org)
    hire = hires_in_window(conn, w, org)
    offer_be = offers_windowed_besteffort(conn, w, org)

    print("\n  ACTIVITY IN WINDOW")
    print(f"    pre-screened candidates : {pre_n:,}")
    print(f"    fully-scored candidates : {score_n:,}")
    print(f"    advanced (stage events) : {adv:,}")
    print(f"    hires (outcome events)  : {hire:,}")
    print(f"    offers (≈, handoff events): {offer_be:,}")

    print("\n  DIRECT UNIT COST")
    print(f"    cost per pre-screen     : {per_unit(pre_cost, pre_n)}   ({usd(pre_cost)} / {pre_n:,})")
    print(f"    cost per full score     : {per_unit(score_cost, score_n)}   ({usd(score_cost)} / {score_n:,})")
    if score_n:
        print(f"    cost to screen+score 1  : {usd((pre_cost + score_cost) / max(score_n, 1))}")

    print("\n  FULLY-LOADED COST PER OUTCOME (total window spend ÷ outcomes)")
    print(f"    per advanced candidate  : {per_unit(total, adv)}")
    print(f"    per hire                : {per_unit(total, hire)}")
    print(f"    per offer (≈)           : {per_unit(total, offer_be)}")


def render_footer(conn, org: int | None, total_all_micro: float) -> None:
    snap = snapshot_funnel(conn, org)
    hires = max(snap["hired_outcome"], snap["hired_workable"])
    print(f"\n{rule('═')}")
    print("  ALL-TIME FUNNEL SNAPSHOT (current state) + BLENDED COST")
    print(rule('═'))
    print(f"    applications        : {snap['applications']:,}")
    print(f"    pre-screened        : {snap['prescreened']:,}")
    print(f"    scored (cv_match)   : {snap['scored']:,}")
    print(f"    advanced (stage)    : {snap['advanced']:,}")
    print(f"    offers (workable)   : {snap['offers']:,}")
    print(f"    hired (outcome/wkbl): {snap['hired_outcome']:,} / {snap['hired_workable']:,}")
    print(f"\n    all-time raw spend  : {usd(total_all_micro)}")
    print(f"    blended $/advanced  : {per_unit(total_all_micro, snap['advanced'])}")
    print(f"    blended $/offer     : {per_unit(total_all_micro, snap['offers'])}")
    print(f"    blended $/hire      : {per_unit(total_all_micro, hires)}")


def render_org_split(conn, w: Window) -> None:
    rows = spend_by_org(conn, w)
    if len(rows) <= 1:
        return
    total = sum(r["cost_micro"] for r in rows) or 1
    print(f"\n  SPEND BY ORG ({w.label}) — confirms how much is the live org vs test noise")
    for r in rows[:8]:
        pct = r["cost_micro"] / total * 100
        print(f"    org {str(r['org']):<6}{usd(r['cost_micro']):>11}{pct:>6.0f}%   ({r['calls']:,} calls)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org", type=int, default=None, help="restrict to one organization_id")
    ap.add_argument("--since", type=str, default=None, help="custom window start (ISO date)")
    ap.add_argument("--until", type=str, default=None, help="custom window end (ISO date)")
    ap.add_argument("--scoring-change", type=str, default=DEFAULT_SCORING_CHANGE)
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

    now = _utcnow()
    scoring_change = datetime.fromisoformat(args.scoring_change).replace(tzinfo=timezone.utc)

    if args.since:
        start = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc) if args.until else now
        windows = [Window(f"Custom {start.date()}→{end.date()}", start, end)]
    else:
        windows = standard_windows(now, scoring_change)

    engine = create_engine(url, **({} if "sqlite" in url else {"pool_pre_ping": True}))
    with engine.connect() as conn:
        print(rule("█"))
        print("  TAALI COST-PER-OUTCOME REPORT")
        print(f"  generated {now.isoformat(timespec='seconds')}   basis: usage_events raw cost, cache hits excluded")
        print(rule("█"))

        all_time_total = 0.0
        for w in windows:
            render_window(conn, w, args.org)
            if w.label == "All time":
                all_time_total = sum(f["cost_micro"] for f in cost_by_feature(conn, w, args.org))

        # org split + footer only make sense for the standard suite
        if not args.since:
            render_org_split(conn, windows[0])
            render_footer(conn, args.org, all_time_total)

        print(f"\n{rule()}")
        print("  NOTES")
        print("  • Cost = raw Anthropic $ (cost_usd_micro), NOT marked-up credits_charged.")
        print("  • cache_hit rows excluded (no Anthropic call ⇒ $0 real; pre-#476 rows carry stale cost).")
        print("  • DIRECT unit cost (pre-screen, full score) is precise & windowed.")
        print("  • FULLY-LOADED per-outcome amortises ALL spend over funnel results; loaded")
        print("    per-window numbers carry a lead-time mismatch — trust the all-time blended row.")
        print("  • Offers are Workable-mirrored (no Taali timestamp) → snapshot + best-effort only.")
        print("  • Cross-check the totals vs Anthropic's bill: scripts/reconcile_and_report.py --days N")
        print(rule())


if __name__ == "__main__":
    main()
