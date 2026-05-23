import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { billing as billingApi } from '../../shared/api';
import { Spinner } from '../../shared/ui/TaaliPrimitives';

// Three customer-facing surfaces in stacking order from bottom. We
// renamed "In-IDE assistance" → "Workspace AI" because the original
// label implied only the candidate IDE — the bucket actually rolls in
// the recruiter chat, the autonomous agent, and interview-prep
// generation, none of which run inside the candidate's IDE.
const SURFACES = [
  { id: 'workspace', label: 'Workspace AI', color: '#7e6dff' },
  { id: 'scoring', label: 'Scoring & matching', color: '#bcb1f0' },
  { id: 'prescreen', label: 'Pre-screening', color: '#d8c8b0' },
];

// Backend feature codes → one of the three surfaces. Anything that
// produces a numeric ranking against the role goes to "Scoring &
// matching"; pre-screen sits on its own; everything else (assessment
// IDE, recruiter chat, autonomous agent, interview prep) lands in
// "Workspace AI".
const FEATURE_TO_SURFACE = {
  prescreen: 'prescreen',

  score: 'scoring',
  cv_parse: 'scoring',
  cv_rerank: 'scoring',
  search_parse: 'scoring',
  archetype_synthesis: 'scoring',
  pairwise_judge: 'scoring',
  fit_matching: 'scoring',

  assessment: 'workspace',
  taali_chat: 'workspace',
  agent_autonomous: 'workspace',
  interview_focus: 'workspace',
  interview_tech: 'workspace',
  other: 'workspace',
};

const surfaceFor = (featureKey) =>
  FEATURE_TO_SURFACE[String(featureKey || '').toLowerCase()] || 'workspace';

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
      byDay.set(day, { day, total: 0, calls: 0, surfaces: { workspace: 0, scoring: 0, prescreen: 0 } });
    }
    const cell = byDay.get(day);
    const surface = surfaceFor(b.group_key);
    // credits_charged_usd is the customer-facing dollar amount (raw Anthropic
    // cost × per-feature markup). Same unit as the Jobs page budget card and
    // per-role $X/$50 indicators, so all three displays reconcile.
    const dollars = Number(b.credits_charged_usd || 0);
    cell.surfaces[surface] += dollars;
    cell.total += dollars;
    cell.calls += Number(b.event_count || 0);
  }
  return [...byDay.values()].sort((a, b) => (a.day > b.day ? 1 : a.day < b.day ? -1 : 0));
};

const sumBySurface = (buckets) => {
  const totals = { workspace: { cost_usd: 0, event_count: 0 }, scoring: { cost_usd: 0, event_count: 0 }, prescreen: { cost_usd: 0, event_count: 0 } };
  for (const b of buckets) {
    const surface = surfaceFor(b.group_key);
    totals[surface].cost_usd += Number(b.credits_charged_usd || 0);
    totals[surface].event_count += Number(b.event_count || 0);
  }
  return SURFACES.map((s) => ({ id: s.id, label: s.label, color: s.color, ...totals[s.id] }));
};

// Pick a "nice" axis ceiling and tick step for a given max value, so the
// Y-axis lands on round dollar amounts ($0, $5, $10, …) instead of the
// raw data max. Targets ~4–5 ticks; falls back to a tiny step when the
// window has near-zero spend.
const niceAxis = (maxValue) => {
  const target = Math.max(maxValue, 0.0001);
  const rough = target / 4;
  const pow10 = Math.pow(10, Math.floor(Math.log10(rough)));
  const candidates = [1, 2, 2.5, 5, 10].map((m) => m * pow10);
  const step = candidates.find((c) => c >= rough) || 10 * pow10;
  const ceiling = Math.ceil(target / step) * step;
  const ticks = [];
  for (let v = 0; v <= ceiling + step / 2; v += step) ticks.push(v);
  return { ceiling, step, ticks };
};

const formatTick = (v) => {
  if (v >= 1) return `$${Math.round(v)}`;
  if (v >= 0.01) return `$${v.toFixed(2)}`;
  return `$${v.toFixed(4)}`;
};

// SVG stacked bar chart with a Y-axis, gridlines, and an interactive
// hover tooltip showing the per-surface breakdown for the focused day.
// No external charting deps — keeps the settings bundle small.
const StackedBarChart = ({ days }) => {
  const [hoverIdx, setHoverIdx] = useState(null);

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

  const maxTotal = Math.max(...days.map((d) => d.total), 0);
  const axis = niceAxis(maxTotal);

  const HEIGHT = 240;
  const PAD_TOP = 12;
  const PAD_BOTTOM = 28;
  const PAD_LEFT = 56;
  const PAD_RIGHT = 12;
  const BAR_GAP = 4;
  const plotMinWidth = 640;
  const innerWidth = Math.max(plotMinWidth, days.length * 28);
  const BAR_WIDTH = Math.max(8, Math.floor(innerWidth / days.length) - BAR_GAP);
  const plotWidth = days.length * (BAR_WIDTH + BAR_GAP) - BAR_GAP;
  const WIDTH = PAD_LEFT + plotWidth + PAD_RIGHT;
  const plotHeight = HEIGHT - PAD_TOP - PAD_BOTTOM;
  const yFor = (v) => PAD_TOP + plotHeight - (v / axis.ceiling) * plotHeight;

  // Sparse x-axis labels: aim for ~6 labels regardless of window length.
  const labelStride = Math.max(1, Math.ceil(days.length / 6));

  const hovered = hoverIdx != null ? days[hoverIdx] : null;
  const hoveredX = hoverIdx != null ? PAD_LEFT + hoverIdx * (BAR_WIDTH + BAR_GAP) + BAR_WIDTH / 2 : 0;
  // Anchor tooltip to the side of the bar that keeps it on-screen.
  const tooltipOnLeft = hoveredX > PAD_LEFT + plotWidth * 0.65;

  return (
    <div style={{ position: 'relative', overflowX: 'auto' }}>
      <svg
        width={WIDTH}
        height={HEIGHT}
        role="img"
        aria-label="Daily spend stacked by surface"
        onMouseLeave={() => setHoverIdx(null)}
      >
        {/* Y-axis gridlines + tick labels */}
        {axis.ticks.map((tick) => {
          const y = yFor(tick);
          return (
            <g key={`tick-${tick}`}>
              <line
                x1={PAD_LEFT}
                x2={WIDTH - PAD_RIGHT}
                y1={y}
                y2={y}
                stroke="var(--line-2)"
                strokeWidth={1}
                shapeRendering="crispEdges"
              />
              <text
                x={PAD_LEFT - 8}
                y={y}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize={11}
                fill="var(--mute)"
              >
                {formatTick(tick)}
              </text>
            </g>
          );
        })}

        {/* Bars + invisible hit areas (full plot height) for stable hover */}
        {days.map((day, idx) => {
          const x = PAD_LEFT + idx * (BAR_WIDTH + BAR_GAP);
          let yCursor = yFor(0);
          return (
            <g key={day.day}>
              {SURFACES.map((surface) => {
                const value = day.surfaces[surface.id] || 0;
                if (value <= 0) return null;
                const segH = (value / axis.ceiling) * plotHeight;
                yCursor -= segH;
                return (
                  <rect
                    key={`${day.day}:${surface.id}`}
                    x={x}
                    y={yCursor}
                    width={BAR_WIDTH}
                    height={Math.max(1, segH)}
                    fill={surface.color}
                    opacity={hoverIdx == null || hoverIdx === idx ? 1 : 0.55}
                  />
                );
              })}
              <rect
                x={x - BAR_GAP / 2}
                y={PAD_TOP}
                width={BAR_WIDTH + BAR_GAP}
                height={plotHeight}
                fill="transparent"
                onMouseEnter={() => setHoverIdx(idx)}
                style={{ cursor: 'pointer' }}
              />
              {idx % labelStride === 0 || idx === days.length - 1 ? (
                <text
                  x={x + BAR_WIDTH / 2}
                  y={HEIGHT - PAD_BOTTOM + 14}
                  textAnchor="middle"
                  fontSize={11}
                  fill="var(--mute)"
                >
                  {day.day.slice(5)}
                </text>
              ) : null}
            </g>
          );
        })}

        {/* Baseline above the x-axis labels */}
        <line
          x1={PAD_LEFT}
          x2={WIDTH - PAD_RIGHT}
          y1={yFor(0)}
          y2={yFor(0)}
          stroke="var(--line)"
          strokeWidth={1}
          shapeRendering="crispEdges"
        />
      </svg>

      {hovered ? (
        <div
          role="tooltip"
          style={{
            position: 'absolute',
            top: PAD_TOP,
            left: tooltipOnLeft ? undefined : Math.min(hoveredX + 12, WIDTH - 220),
            right: tooltipOnLeft ? Math.max(PAD_RIGHT, WIDTH - hoveredX + 12) : undefined,
            background: 'var(--bg-2)',
            border: '1px solid var(--line)',
            borderRadius: 10,
            boxShadow: 'var(--shadow-sm)',
            padding: '10px 12px',
            fontSize: 12,
            minWidth: 200,
            pointerEvents: 'none',
            zIndex: 2,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 6 }}>{hovered.day}</div>
          {SURFACES.map((surface) => {
            const value = hovered.surfaces[surface.id] || 0;
            return (
              <div
                key={surface.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 12,
                  margin: '2px 0',
                  color: value > 0 ? 'inherit' : 'var(--mute)',
                }}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span
                    aria-hidden="true"
                    style={{
                      width: 8,
                      height: 8,
                      background: surface.color,
                      borderRadius: 2,
                      display: 'inline-block',
                    }}
                  />
                  {surface.label}
                </span>
                <span style={{ fontVariantNumeric: 'tabular-nums' }}>{formatUsd4(value)}</span>
              </div>
            );
          })}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              gap: 12,
              marginTop: 8,
              paddingTop: 6,
              borderTop: '1px solid var(--line-2)',
              fontWeight: 600,
            }}
          >
            <span>Total</span>
            <span style={{ fontVariantNumeric: 'tabular-nums' }}>{formatUsd4(hovered.total)}</span>
          </div>
          <div style={{ marginTop: 2, color: 'var(--mute)' }}>
            {formatNumber(hovered.calls)} requests
          </div>
        </div>
      ) : null}

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
    </div>
  );
};

const GAP_PERIOD_DAYS = 7;

export default function UsagePanel() {
  const [timeseries, setTimeseries] = useState(null);
  const [loadingSeries, setLoadingSeries] = useState(false);
  // Admin-only metering-gap summary (claude_call_log). null when the
  // endpoint 403s for non-admins or hasn't loaded — section stays hidden.
  const [meteringGap, setMeteringGap] = useState(null);

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

  const loadMeteringGap = useCallback(async () => {
    try {
      const res = await billingApi.meteringGap(GAP_PERIOD_DAYS);
      setMeteringGap(res?.data || null);
    } catch {
      // 403 for non-admins, or endpoint unavailable — hide the section.
      setMeteringGap(null);
    }
  }, []);

  useEffect(() => {
    void loadTimeseries();
    void loadMeteringGap();
  }, [loadTimeseries, loadMeteringGap]);

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

      <MeteringGapPanel gap={meteringGap} />
    </div>
  );
}

// Admin-only diagnostic. Reads claude_call_log (ground-truth log of every
// Anthropic call). Surfaces the attribution gap — calls that hit Anthropic
// but never got a feature/role record — plus failure rate. Hidden entirely
// for non-admins (the endpoint 403s and ``gap`` stays null).
function MeteringGapPanel({ gap }) {
  if (!gap || !gap.totals) return null;
  const totalCalls = Number(gap.totals.calls || 0);
  const totalUsd = Number(gap.totals.cost_usd || 0);
  const gapCalls = Number(gap.attribution_gap?.calls || 0);
  const gapUsd = Number(gap.attribution_gap?.cost_usd || 0);
  const gapPct = totalCalls > 0 ? (gapCalls / totalCalls) * 100 : 0;
  const byStatus = Array.isArray(gap.by_status) ? gap.by_status : [];
  const errorCalls = byStatus
    .filter((s) => s.status && s.status !== 'ok')
    .reduce((sum, s) => sum + Number(s.calls || 0), 0);
  const errorPct = totalCalls > 0 ? (errorCalls / totalCalls) * 100 : 0;
  const byFeature = Array.isArray(gap.by_feature) ? gap.by_feature : [];

  return (
    <div style={{ marginTop: 32 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
        <h3 style={{ fontSize: 14, margin: 0 }}>Anthropic call log</h3>
        <span style={{ fontSize: 11, color: 'var(--mute)' }}>
          admin · ground truth · trailing {gap.period_days || GAP_PERIOD_DAYS} days
        </span>
      </div>
      <p style={{ fontSize: 12, color: 'var(--mute)', marginTop: 0, marginBottom: 12 }}>
        Every Anthropic call writes a row here regardless of whether the app
        attributed it. The attribution gap is spend with no feature record —
        non-zero means a code path is calling Claude without metering it.
      </p>

      <div className="settings-billing-summary">
        <div className="settings-billing-card">
          <div className="settings-summary-label">Total calls</div>
          <div className="settings-summary-value">{formatNumber(totalCalls)}</div>
          <div className="settings-summary-note">{formatUsd(totalUsd)} raw cost</div>
        </div>
        <div className="settings-billing-card">
          <div className="settings-summary-label">Attribution gap</div>
          <div
            className="settings-summary-value"
            style={{ color: gapUsd > 0 ? 'var(--purple-2)' : 'inherit' }}
          >
            {formatUsd(gapUsd)}
          </div>
          <div className="settings-summary-note">
            {formatNumber(gapCalls)} calls · {gapPct.toFixed(1)}% of total
          </div>
        </div>
        <div className="settings-billing-card">
          <div className="settings-summary-label">Failure rate</div>
          <div
            className="settings-summary-value"
            style={{ color: errorPct > 10 ? 'var(--purple-2)' : 'inherit' }}
          >
            {errorPct.toFixed(1)}%
          </div>
          <div className="settings-summary-note">
            {formatNumber(errorCalls)} non-ok of {formatNumber(totalCalls)}
          </div>
        </div>
      </div>

      {byFeature.length > 0 ? (
        <div className="settings-usage-table" style={{ marginTop: 16 }}>
          <div className="settings-usage-head">
            <h3>Calls by feature</h3>
          </div>
          <table>
            <thead>
              <tr>
                <th>Feature</th>
                <th>Calls</th>
                <th>Raw cost</th>
              </tr>
            </thead>
            <tbody>
              {byFeature.map((row) => (
                <tr key={row.feature || 'unknown'}>
                  <td>{row.feature || '(unattributed)'}</td>
                  <td>{formatNumber(row.calls)}</td>
                  <td>{formatUsd4(row.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

export { SURFACES, surfaceById };
