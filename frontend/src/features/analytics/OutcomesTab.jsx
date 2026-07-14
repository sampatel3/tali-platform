// OUTCOMES — funnel conversion, advance→hire, override-rate-over-time bars,
// and the by-role table. Real sources:
//   funnel + narrator       → /analytics/reporting-summary (summary)
//   advance→hire + by-role  → /analytics/decisions-breakdown (breakdown)
//   override-rate bars      → /analytics/decision-trend (trend)
//   per-role override/spend → /agent/roles/breakdown (rolesBreakdown)
// Every number rounded; missing data renders a proper empty state.

import React, { useMemo } from 'react';

import { safeNum, pct, fmtUsd, fmtUsdFine, monthShort } from './analyticsFormat';
import { PIPELINE_FUNNEL_STAGES } from '../../shared/metrics';
import { MotionProgress, cappedStaggerDelay } from '../../shared/motion';

// Cost-per-outcome: BILLED spend per funnel unit (never raw model cost). Four
// tiles — direct unit cost (per pre-screen, per score) and fully-loaded cost
// per outcome (per advanced, per hire). A unit is "—" (proper empty state) when
// no candidates hit that stage in the window, so we never divide by zero.
const CPO_TILES = [
  { key: 'pre_screen', label: 'Per pre-screen', noun: 'pre-screened' },
  { key: 'score', label: 'Per score', noun: 'scored' },
  { key: 'advanced', label: 'Per advanced', noun: 'advanced' },
  { key: 'hired', label: 'Per hire', noun: 'hired' },
];

const CostPerOutcome = ({ cost }) => {
  const per = cost?.per_outcome || {};
  const billed = safeNum(cost?.billed_spend_cents);
  const anyData = CPO_TILES.some((t) => safeNum(per[t.key]?.count) > 0) || billed > 0;
  return (
    <div className="an-card">
      <div className="ch">
        <div>
          <div className="ct2">Cost per outcome</div>
          <div className="cd">What your billed agent spend buys, per funnel unit — over this window</div>
        </div>
      </div>
      {anyData ? (
        <>
          <div className="an-cpo">
            {CPO_TILES.map((t) => {
              const cell = per[t.key] || {};
              const count = safeNum(cell.count);
              const hasUnit = cell.cost_cents != null && count > 0;
              return (
                <div className="an-cpo-cell" key={t.key}>
                  <div className="k">{t.label}</div>
                  <div className={`v${hasUnit ? '' : ' muted'}`}>
                    {hasUnit ? fmtUsdFine(cell.cost_cents) : '—'}
                  </div>
                  <div className="s">{count.toLocaleString()} {t.noun}</div>
                </div>
              );
            })}
          </div>
          <div className="an-cpo-foot">
            {fmtUsd(billed)} billed this window. Per pre-screen and per score are direct
            unit costs; per advanced and per hire load all spend over the outcome.
          </div>
        </>
      ) : (
        <div className="an-empty">
          No billed agent spend in this window yet — cost-per-outcome appears once the agent scores candidates.
        </div>
      )}
    </div>
  );
};

const FunnelRow = ({ stage, prev, isLast, index }) => {
  const ofApplied = Math.max(0, Math.min(100, safeNum(stage.percentage)));
  const prevCount = prev ? safeNum(prev.count) : 0;
  const stepPct = !prev
    ? '100%'
    : isLast
      ? 'to recruiter'
      : prevCount > 0
        ? `${Math.round((safeNum(stage.count) / prevCount) * 100)}% of ${String(prev.label).toLowerCase()}`
        : '—';
  // Bar fills to share-of-applied, with a small floor so a nonzero stage is
  // always visible. Label uses the stage's own count.
  const width = Math.max(ofApplied, safeNum(stage.count) > 0 ? 8 : 0);
  return (
    <div className="an-convrow">
      <span className="cl">{titleCase(stage.label)}</span>
      <span className="ctrack">
        <MotionProgress
          as="span"
          className="cfill"
          delay={cappedStaggerDelay(index, 'dense')}
          style={{ width: `${width}%` }}
        >
          {safeNum(stage.count).toLocaleString()}
        </MotionProgress>
      </span>
      <span className="cpct">{stepPct}</span>
    </div>
  );
};

const titleCase = (s) => {
  const str = String(s || '');
  return str ? str.charAt(0).toUpperCase() + str.slice(1).toLowerCase() : str;
};

// Monthly override-rate bars from /analytics/decision-trend. The most recent
// month is highlighted; months with no resolved decisions render a flat empty
// bar (never a fabricated value).
const TrendBars = ({ months, valueKey, height = 150 }) => {
  const rows = Array.isArray(months) ? months : [];
  const max = Math.max(1, ...rows.map((m) => safeNum(m[valueKey])));
  if (rows.length === 0) {
    return <div className="an-empty">No monthly history yet.</div>;
  }
  return (
    <div className="an-bars" style={{ height }}>
      {rows.map((m, i) => {
        const v = safeNum(m[valueKey]);
        const has = safeNum(m.decisions) > 0;
        const isLast = i === rows.length - 1;
        const h = has ? Math.max(4, Math.round((v / max) * 100)) : 2;
        return (
          <div className="an-bar" key={m.month}>
            <div className={`bv${has ? '' : ' muted'}`}>{has ? `${v}%` : '—'}</div>
            <MotionProgress
              axis="y"
              delay={cappedStaggerDelay(i, 'dense')}
              className={`bk${!has ? ' empty' : isLast ? ' hl' : ''}`}
              style={{ height: `${h}%` }}
              title={`${monthShort(m.month)} · ${safeNum(m.decisions)} resolved`}
            />
            <div className="bl">{monthShort(m.month)}</div>
          </div>
        );
      })}
    </div>
  );
};

export const OutcomesTab = ({ summary, breakdown, trend, rolesBreakdown, cost }) => {
  const funnel = useMemo(() => (
    Array.isArray(summary?.funnel) && summary.funnel.length
      ? summary.funnel
      // Empty-state fallback uses the canonical funnel vocabulary (Applied →
      // Scored → Invited → Completed → Advanced → Rejected) so it matches Home.
      : PIPELINE_FUNNEL_STAGES.map((s) => ({ label: s.label, key: s.key, count: 0, percentage: 0 }))
  ), [summary]);

  const conv = breakdown?.totals?.advance_conversion || {};
  const advancedTotal = safeNum(conv.advanced_total);
  const hiredTotal = safeNum(conv.hired);
  const advanceHirePct = advancedTotal > 0 ? Math.round((hiredTotal / advancedTotal) * 100) : null;

  // Per-role rows: join decisions-breakdown (advance→hire) with rolesBreakdown
  // (override rate + spend, both real per-role server aggregates).
  const roleMeta = useMemo(() => {
    const map = new Map();
    (Array.isArray(rolesBreakdown) ? rolesBreakdown : []).forEach((r) => {
      map.set(Number(r.role_id), r);
    });
    return map;
  }, [rolesBreakdown]);

  const rows = useMemo(() => {
    const bRoles = Array.isArray(breakdown?.roles) ? breakdown.roles : [];
    return bRoles.map((role) => {
      const meta = roleMeta.get(Number(role.role_id)) || {};
      const d = role.decisions || {};
      const c = role.advance_conversion || {};
      const adv = safeNum(c.advanced_total);
      const hired = safeNum(c.hired);
      return {
        roleId: role.role_id,
        name: role.role_name || meta.name || `Role #${role.role_id}`,
        decisions: safeNum(d.total),
        // Per-role auto-resolution rate isn't a real server field — render "—"
        // (proper empty state) rather than fabricate it.
        autoRate: null,
        overridePct: meta.override_rate_pct != null ? safeNum(meta.override_rate_pct) : null,
        advanced: adv,
        hired,
        spentCents: meta.budget_cents != null ? safeNum(meta.budget_cents) : null,
      };
    });
  }, [breakdown, roleMeta]);

  const trendMonths = Array.isArray(trend?.months) ? trend.months : [];
  const hasTrend = trendMonths.some((m) => safeNum(m.decisions) > 0);

  return (
    <div className="an-tabpanel">
      {/* Funnel conversion — full-width card. */}
      <div className="an-card">
        <div className="ch">
          <div>
            <div className="ct2">Funnel conversion · all roles</div>
            <div className="cd">Where candidates move, and where the agent gates them</div>
          </div>
        </div>
        <div className="an-conv">
          {funnel.map((stage, i) => (
            <FunnelRow
              key={stage.label}
              stage={stage}
              prev={i > 0 ? funnel[i - 1] : null}
              isLast={i === funnel.length - 1}
              index={i}
            />
          ))}
        </div>
        {summary?.narrator?.paragraph ? (
          <div className="an-narrator">{summary.narrator.paragraph}</div>
        ) : null}
      </div>

      {/* Cost per outcome — unit economics, the funnel's natural neighbour. */}
      <CostPerOutcome cost={cost} />

      {/* Advance→hire bigstat + override-rate-over-time bars. */}
      <div className="an-grid2">
        <div className="an-card">
          <div className="ch"><div className="ct2">Advance → hire quality</div></div>
          <div className="an-bigstat">
            <div className="n">{advanceHirePct != null ? `${advanceHirePct}%` : '—'}</div>
            <div className="sub">
              {advancedTotal > 0 ? (
                <>
                  <b>{hiredTotal.toLocaleString()}</b> of the {advancedTotal.toLocaleString()} candidate
                  {advancedTotal === 1 ? '' : 's'} the agent advanced {hiredTotal === 1 ? 'was' : 'were'} hired.
                </>
              ) : (
                'No advanced candidates in this window yet — advance→hire appears once the agent hands candidates to the recruiter.'
              )}
            </div>
          </div>
        </div>
        <div className="an-card">
          <div className="ch">
            <div className="ct2">Your override rate over time</div>
            <div className="cd">lower = more agreement</div>
          </div>
          {hasTrend ? (
            <TrendBars months={trendMonths} valueKey="override_rate_pct" />
          ) : (
            <div className="an-empty">
              Override-rate history appears once the agent has resolved decisions across a month or more.
            </div>
          )}
        </div>
      </div>

      {/* By role. */}
      <div className="an-card">
        <div className="ch">
          <div>
            <div className="ct2">By role</div>
            <div className="cd">Decision volume, override rate, and advance→hire per active role</div>
          </div>
        </div>
        {rows.length === 0 ? (
          <div className="an-empty">No decisions recorded yet — per-role outcomes appear once the agent acts on a role.</div>
        ) : (
          <div className="an-table-scroll">
            <table className="an-table">
              <thead>
                <tr>
                  <th>Role</th>
                  <th>Decisions</th>
                  <th>Auto rate</th>
                  <th>Override</th>
                  <th>Advance → hire</th>
                  <th>Spend</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.roleId}>
                    <td><b>{r.name}</b></td>
                    <td>{r.decisions.toLocaleString()}</td>
                    <td>{r.autoRate != null ? `${r.autoRate}%` : '—'}</td>
                    <td>{r.overridePct != null ? `${r.overridePct}%` : '—'}</td>
                    <td>
                      {r.advanced > 0
                        ? `${pct(r.hired, r.advanced)}% (${r.hired}/${r.advanced})`
                        : '—'}
                    </td>
                    <td>{r.spentCents != null ? fmtUsd(r.spentCents) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default OutcomesTab;
