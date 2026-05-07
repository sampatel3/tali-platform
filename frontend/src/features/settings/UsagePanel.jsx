import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { billing as billingApi } from '../../shared/api';
import { Spinner } from '../../shared/ui/TaaliPrimitives';

// HANDOFF settings.md — Usage tab spec:
// "Stacked daily bar chart is purely client-side from the
//  /billing/usage-timeseries?period_days=30 payload. Three buckets, in
//  stacking order from bottom: In-IDE assistance (purple), Scoring
//  (lavender), Pre-screen summaries (sand)."
const SURFACES = [
  { id: 'in_ide', label: 'In-IDE assistance', color: '#7e6dff' },
  { id: 'scoring', label: 'Scoring', color: '#bcb1f0' },
  { id: 'prescreen', label: 'Pre-screen summaries', color: '#d8c8b0' },
];

// Backend feature codes → one of the three customer-facing surfaces. Codes
// not listed roll into "In-IDE assistance" (the broadest catch-all per the
// design — it covers both the candidate IDE and the recruiter-side agent).
const FEATURE_TO_SURFACE = {
  prescreen: 'prescreen',

  score: 'scoring',
  cv_parse: 'scoring',
  cv_rerank: 'scoring',
  search_parse: 'scoring',
  archetype_synthesis: 'scoring',
  pairwise_judge: 'scoring',
  fit_matching: 'scoring',

  assessment: 'in_ide',
  taali_chat: 'in_ide',
  agent_autonomous: 'in_ide',
  interview_focus: 'in_ide',
  interview_tech: 'in_ide',
  other: 'in_ide',
};

const surfaceFor = (featureKey) =>
  FEATURE_TO_SURFACE[String(featureKey || '').toLowerCase()] || 'in_ide';

const formatUsd = (n) => `$${Number(n || 0).toFixed(2)}`;
const formatUsd4 = (n) => `$${Number(n || 0).toFixed(4)}`;
const formatNumber = (n) => Number(n || 0).toLocaleString();

const surfaceById = (id) => SURFACES.find((s) => s.id === id);

const PERIOD_DAYS = 30;

// Pivot the per-feature timeseries into per-day, three-surface buckets
// matching the SURFACES order so the stacked bar chart renders bottom-up
// in the order the handoff specifies.
const pivotByDay = (buckets) => {
  const byDay = new Map();
  for (const b of buckets) {
    const day = b.day || 'unknown';
    if (!byDay.has(day)) {
      byDay.set(day, { day, total: 0, calls: 0, surfaces: { in_ide: 0, scoring: 0, prescreen: 0 } });
    }
    const cell = byDay.get(day);
    const surface = surfaceFor(b.group_key);
    const dollars = Number(b.cost_usd || 0);
    cell.surfaces[surface] += dollars;
    cell.total += dollars;
    cell.calls += Number(b.event_count || 0);
  }
  return [...byDay.values()].sort((a, b) => (a.day > b.day ? 1 : a.day < b.day ? -1 : 0));
};

const sumBySurface = (buckets) => {
  const totals = { in_ide: { cost_usd: 0, event_count: 0 }, scoring: { cost_usd: 0, event_count: 0 }, prescreen: { cost_usd: 0, event_count: 0 } };
  for (const b of buckets) {
    const surface = surfaceFor(b.group_key);
    totals[surface].cost_usd += Number(b.cost_usd || 0);
    totals[surface].event_count += Number(b.event_count || 0);
  }
  return SURFACES.map((s) => ({ id: s.id, label: s.label, color: s.color, ...totals[s.id] }));
};

// Tiny SVG stacked bar chart — no external deps. Each bar = one day,
// segments stacked from bottom in the SURFACES order so In-IDE sits
// at the base, Scoring above it, Pre-screen on top (per handoff).
const StackedBarChart = ({ days }) => {
  if (days.length === 0) {
    return (
      <div className="settings-billing-card" style={{ padding: 24, textAlign: 'center' }}>
        <div className="settings-summary-note">
          No activity in this window. Pre-screen a candidate or open the
          assessment workspace and check back here.
        </div>
      </div>
    );
  }

  const maxTotal = Math.max(...days.map((d) => d.total), 0.000001);
  const HEIGHT = 220;
  const PAD_Y = 16;
  const BAR_GAP = 4;
  const BAR_WIDTH = Math.max(8, Math.floor(640 / days.length) - BAR_GAP);
  const WIDTH = days.length * (BAR_WIDTH + BAR_GAP);

  return (
    <div style={{ overflowX: 'auto' }}>
      <svg width={WIDTH} height={HEIGHT} role="img" aria-label="Daily spend stacked by surface">
        {days.map((day, idx) => {
          const x = idx * (BAR_WIDTH + BAR_GAP);
          let yCursor = HEIGHT - PAD_Y;
          // Stacking order: In-IDE (bottom), Scoring (middle), Pre-screen (top)
          return (
            <g key={day.day}>
              {SURFACES.map((surface) => {
                const value = day.surfaces[surface.id] || 0;
                if (value <= 0) return null;
                const segH = (value / maxTotal) * (HEIGHT - PAD_Y * 2);
                yCursor -= segH;
                return (
                  <rect
                    key={`${day.day}:${surface.id}`}
                    x={x}
                    y={yCursor}
                    width={BAR_WIDTH}
                    height={Math.max(1, segH)}
                    fill={surface.color}
                  >
                    <title>{`${day.day} · ${surface.label}: ${formatUsd4(value)}`}</title>
                  </rect>
                );
              })}
              <title>{`${day.day} · ${formatUsd4(day.total)} · ${day.calls} requests`}</title>
            </g>
          );
        })}
      </svg>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginTop: 8 }}>
        {SURFACES.map((surface) => (
          <div key={surface.id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
            <span
              style={{
                width: 10,
                height: 10,
                background: surface.color,
                borderRadius: 2,
                display: 'inline-block',
              }}
            />
            <span>{surface.label}</span>
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--taali-muted)', marginTop: 4 }}>
        <span>{days[0]?.day}</span>
        <span>{days[days.length - 1]?.day}</span>
      </div>
    </div>
  );
};

export default function UsagePanel() {
  const [timeseries, setTimeseries] = useState(null);
  const [loadingSeries, setLoadingSeries] = useState(false);

  const loadTimeseries = useCallback(async () => {
    setLoadingSeries(true);
    try {
      // Always feature-grouped — we collapse the per-feature buckets into
      // the three customer-facing surfaces below.
      const res = await billingApi.usageTimeseries(PERIOD_DAYS, 'feature');
      setTimeseries(res?.data || null);
    } catch {
      setTimeseries(null);
    } finally {
      setLoadingSeries(false);
    }
  }, []);

  useEffect(() => {
    void loadTimeseries();
  }, [loadTimeseries]);

  const buckets = timeseries?.buckets || [];
  const days = useMemo(() => pivotByDay(buckets), [buckets]);
  const surfaceSummary = useMemo(() => sumBySurface(buckets), [buckets]);
  const totalUsd = useMemo(
    () => surfaceSummary.reduce((sum, s) => sum + s.cost_usd, 0),
    [surfaceSummary],
  );
  const totalCalls = useMemo(
    () => surfaceSummary.reduce((sum, s) => sum + s.event_count, 0),
    [surfaceSummary],
  );
  const topDriver = useMemo(
    () => [...surfaceSummary].sort((a, b) => b.cost_usd - a.cost_usd)[0] || null,
    [surfaceSummary],
  );

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <span style={{ fontSize: 12, color: 'var(--taali-muted)' }}>
          Trailing {PERIOD_DAYS} days · grouped by surface
        </span>
        {loadingSeries ? <Spinner size={14} /> : null}
      </div>

      <div className="settings-billing-summary">
        <div className="settings-billing-card">
          <div className="settings-summary-label">Total spend</div>
          <div className="settings-summary-value">{formatUsd(totalUsd)}</div>
          <div className="settings-summary-note">{PERIOD_DAYS}-day window</div>
        </div>
        <div className="settings-billing-card">
          <div className="settings-summary-label">Billable AI requests</div>
          <div className="settings-summary-value">{formatNumber(totalCalls)}</div>
          <div className="settings-summary-note">Across all Taali surfaces</div>
        </div>
        <div className="settings-billing-card">
          <div className="settings-summary-label">Top driver</div>
          <div className="settings-summary-value">
            {topDriver ? topDriver.label : '—'}
          </div>
          <div className="settings-summary-note">
            {topDriver
              ? `${formatUsd(topDriver.cost_usd)} · ${formatNumber(topDriver.event_count)} requests`
              : 'No spend yet'}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 24 }}>
        <h3 style={{ fontSize: 14, marginBottom: 8 }}>Daily spend</h3>
        <StackedBarChart days={days} />
      </div>

      <div className="settings-usage-table" style={{ marginTop: 24 }}>
        <div className="settings-usage-head">
          <h3>Breakdown by surface</h3>
        </div>
        <table>
          <thead>
            <tr>
              <th>Surface</th>
              <th>Requests</th>
              <th>Spend</th>
            </tr>
          </thead>
          <tbody>
            {SURFACES.map((surface) => {
              const row = surfaceSummary.find((s) => s.id === surface.id) || { cost_usd: 0, event_count: 0 };
              return (
                <tr key={surface.id}>
                  <td>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                      <span
                        aria-hidden="true"
                        style={{
                          width: 10,
                          height: 10,
                          background: surface.color,
                          borderRadius: 2,
                          display: 'inline-block',
                        }}
                      />
                      {surface.label}
                    </span>
                  </td>
                  <td>{formatNumber(row.event_count)}</td>
                  <td>{formatUsd4(row.cost_usd)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export { SURFACES, surfaceById };
