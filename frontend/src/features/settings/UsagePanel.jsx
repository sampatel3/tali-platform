import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { billing as billingApi } from '../../shared/api';
import { Spinner } from '../../shared/ui/TaaliPrimitives';

const PERIOD_OPTIONS = [
  { value: 7, label: '7 days' },
  { value: 14, label: '14 days' },
  { value: 30, label: '30 days' },
  { value: 60, label: '60 days' },
  { value: 90, label: '90 days' },
];

const GROUP_BY_OPTIONS = [
  { value: 'model', label: 'Model' },
  { value: 'feature', label: 'Feature' },
  { value: 'user', label: 'User' },
];

const FEATURE_LABELS = {
  prescreen: 'Pre-screening',
  score: 'CV scoring',
  assessment: 'Assessment workspace',
  taali_chat: 'Taali Chat',
  agent_autonomous: 'Autonomous agent',
  cv_parse: 'CV parsing',
  cv_rerank: 'Search rerank',
  search_parse: 'Search query parsing',
  archetype_synthesis: 'Archetype synthesis',
  pairwise_judge: 'Pairwise calibration',
  interview_focus: 'Interview focus',
  interview_tech: 'Tech interview prompts',
  fit_matching: 'Fit matching',
  other: 'Other / unattributed',
};

const formatUsd = (n) => `$${Number(n || 0).toFixed(2)}`;
const formatUsd4 = (n) => `$${Number(n || 0).toFixed(4)}`;
const formatPct = (n) => (n === null || n === undefined ? '—' : `${Number(n).toFixed(2)}%`);
const formatNumber = (n) => Number(n || 0).toLocaleString();

const sortKeyAsc = (a, b) => (a > b ? 1 : a < b ? -1 : 0);

// Shared deterministic palette so the same model/feature gets the same
// colour across renders. Palette mirrors the Claude Console: warm tints
// for Haiku, neutral for Sonnet, accent for Opus.
const PALETTE = [
  '#d8c8b0', '#a4b4a8', '#bfa890', '#7e8e98', '#c79b76',
  '#8b9aa3', '#b08e72', '#9aa5a0', '#cdb696', '#7d6e5b',
];

const colorFor = (key, index) => PALETTE[index % PALETTE.length];

// Bucket → wide chart shape: stacks per day. We pivot the timeseries
// (one row per day per group) into ``{ day: { [group]: dollars, total } }``.
const pivotByDay = (buckets) => {
  const byDay = new Map();
  const groupKeys = new Set();
  for (const b of buckets) {
    const day = b.day || 'unknown';
    if (!byDay.has(day)) {
      byDay.set(day, { day, total: 0, groups: {}, calls: 0 });
    }
    const cell = byDay.get(day);
    const dollars = Number(b.cost_usd || 0);
    cell.groups[b.group_key] = (cell.groups[b.group_key] || 0) + dollars;
    cell.total += dollars;
    cell.calls += Number(b.event_count || 0);
    groupKeys.add(b.group_key);
  }
  const days = [...byDay.values()].sort((a, b) => sortKeyAsc(a.day, b.day));
  const orderedGroups = [...groupKeys].sort(sortKeyAsc);
  return { days, groupKeys: orderedGroups };
};

const sumByGroup = (buckets) => {
  const map = new Map();
  for (const b of buckets) {
    const key = b.group_key;
    const prev = map.get(key) || {
      cost_usd: 0,
      event_count: 0,
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_creation_tokens: 0,
    };
    prev.cost_usd += Number(b.cost_usd || 0);
    prev.event_count += Number(b.event_count || 0);
    prev.input_tokens += Number(b.input_tokens || 0);
    prev.output_tokens += Number(b.output_tokens || 0);
    prev.cache_read_tokens += Number(b.cache_read_tokens || 0);
    prev.cache_creation_tokens += Number(b.cache_creation_tokens || 0);
    map.set(key, prev);
  }
  return [...map.entries()]
    .map(([key, value]) => ({ key, ...value }))
    .sort((a, b) => b.cost_usd - a.cost_usd);
};

const labelForGroup = (groupBy, key) => {
  if (groupBy === 'feature') return FEATURE_LABELS[key] || key;
  if (groupBy === 'user') return key === 'unattributed' ? 'Unattributed' : `User #${key}`;
  return key;
};

// Tiny SVG stacked bar chart — no external deps. Each bar = one day,
// segments stacked from bottom by group_key. Hovering shows totals.
const StackedBarChart = ({ buckets, groupBy }) => {
  const { days, groupKeys } = useMemo(() => pivotByDay(buckets), [buckets]);
  if (days.length === 0) {
    return (
      <div className="settings-billing-card" style={{ padding: 24, textAlign: 'center' }}>
        <div className="settings-summary-note">
          No billable Claude calls in this window. Run a scoring or chat
          session and check back here.
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

  const colorByGroup = new Map();
  groupKeys.forEach((g, i) => colorByGroup.set(g, colorFor(g, i)));

  return (
    <div style={{ overflowX: 'auto' }}>
      <svg
        width={WIDTH}
        height={HEIGHT}
        role="img"
        aria-label={`Daily Claude spend stacked by ${groupBy}`}
      >
        {days.map((day, idx) => {
          const x = idx * (BAR_WIDTH + BAR_GAP);
          let yCursor = HEIGHT - PAD_Y;
          const segments = groupKeys
            .map((g) => ({ group: g, value: day.groups[g] || 0 }))
            .filter((s) => s.value > 0);
          return (
            <g key={day.day}>
              {segments.map((seg) => {
                const segH = (seg.value / maxTotal) * (HEIGHT - PAD_Y * 2);
                yCursor -= segH;
                return (
                  <rect
                    key={`${day.day}:${seg.group}`}
                    x={x}
                    y={yCursor}
                    width={BAR_WIDTH}
                    height={Math.max(1, segH)}
                    fill={colorByGroup.get(seg.group)}
                  >
                    <title>{`${day.day} · ${labelForGroup(groupBy, seg.group)}: ${formatUsd4(seg.value)}`}</title>
                  </rect>
                );
              })}
              <title>{`${day.day} · ${formatUsd4(day.total)} · ${day.calls} calls`}</title>
            </g>
          );
        })}
      </svg>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginTop: 8 }}>
        {groupKeys.map((g, i) => (
          <div key={g} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
            <span
              style={{
                width: 10,
                height: 10,
                background: colorFor(g, i),
                borderRadius: 2,
                display: 'inline-block',
              }}
            />
            <span>{labelForGroup(groupBy, g)}</span>
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

const ReconciliationPanel = ({ data, loading }) => {
  if (loading) {
    return (
      <div className="settings-loading-inline">
        <Spinner size={18} />
        Loading reconciliation...
      </div>
    );
  }
  if (!data) return null;
  const totals = data.totals || {};
  const rows = data.rows || [];

  const driftBadgeStyle = (drift) => {
    if (drift === null || drift === undefined) {
      return { color: 'var(--taali-warning, #b9762a)' };
    }
    const abs = Math.abs(drift);
    if (abs > 5) return { color: 'var(--taali-danger)' };
    if (abs > 1) return { color: 'var(--taali-warning, #b9762a)' };
    return { color: 'var(--taali-muted)' };
  };

  return (
    <div>
      <div className="settings-billing-summary">
        <div className="settings-billing-card">
          <div className="settings-summary-label">Anthropic billed</div>
          <div className="settings-summary-value">{formatUsd(totals.anthropic_cost_usd || 0)}</div>
          <div className="settings-summary-note">
            Authoritative number from Anthropic's cost report
          </div>
        </div>
        <div className="settings-billing-card">
          <div className="settings-summary-label">Tali attributed</div>
          <div className="settings-summary-value">{formatUsd(totals.internal_cost_usd || 0)}</div>
          <div className="settings-summary-note">
            Sum of usage_events for the same window
          </div>
        </div>
        <div className="settings-billing-card">
          <div className="settings-summary-label">Drift</div>
          <div className="settings-summary-value" style={driftBadgeStyle(totals.cost_drift_pct)}>
            {formatPct(totals.cost_drift_pct)}
          </div>
          <div className="settings-summary-note">
            {totals.cost_drift_pct === null
              ? 'No Anthropic data yet — reconciliation pending'
              : Math.abs(totals.cost_drift_pct || 0) <= 1
                ? 'Within 1% — attribution healthy'
                : 'Drift exceeds 1% — investigate'}
          </div>
        </div>
      </div>

      <div className="settings-usage-table" style={{ marginTop: 16 }}>
        <div className="settings-usage-head">
          <h3>Daily reconciliation</h3>
        </div>
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Model</th>
              <th>Anthropic cost</th>
              <th>Tali cost</th>
              <th>Drift</th>
              <th>Events</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty">
                  No reconciliation rows yet. The daily Celery task populates
                  this table once Anthropic Admin API access is configured
                  (ANTHROPIC_ADMIN_API_KEY).
                </td>
              </tr>
            ) : rows.map((r) => (
              <tr key={`${r.usage_date}:${r.model}`}>
                <td>{r.usage_date || '—'}</td>
                <td style={{ fontSize: 12 }}>{r.model || '—'}</td>
                <td>{formatUsd4(r.anthropic_cost_usd)}</td>
                <td>{formatUsd4(r.internal_cost_usd)}</td>
                <td style={driftBadgeStyle(r.cost_drift_pct)}>
                  {formatPct(r.cost_drift_pct)}
                </td>
                <td>{formatNumber(r.internal_event_count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default function UsagePanel() {
  const [periodDays, setPeriodDays] = useState(30);
  const [groupBy, setGroupBy] = useState('model');
  const [timeseries, setTimeseries] = useState(null);
  const [recon, setRecon] = useState(null);
  const [loadingSeries, setLoadingSeries] = useState(false);
  const [loadingRecon, setLoadingRecon] = useState(false);

  const loadTimeseries = useCallback(async (days, gb) => {
    setLoadingSeries(true);
    try {
      const res = await billingApi.usageTimeseries(days, gb);
      setTimeseries(res?.data || null);
    } catch {
      setTimeseries(null);
    } finally {
      setLoadingSeries(false);
    }
  }, []);

  const loadRecon = useCallback(async (days) => {
    setLoadingRecon(true);
    try {
      const res = await billingApi.usageReconciliation(Math.min(days, 30));
      setRecon(res?.data || null);
    } catch {
      setRecon(null);
    } finally {
      setLoadingRecon(false);
    }
  }, []);

  useEffect(() => {
    void loadTimeseries(periodDays, groupBy);
  }, [periodDays, groupBy, loadTimeseries]);

  useEffect(() => {
    void loadRecon(periodDays);
  }, [periodDays, loadRecon]);

  const buckets = timeseries?.buckets || [];
  const totalUsd = useMemo(
    () => buckets.reduce((sum, b) => sum + Number(b.cost_usd || 0), 0),
    [buckets],
  );
  const totalCalls = useMemo(
    () => buckets.reduce((sum, b) => sum + Number(b.event_count || 0), 0),
    [buckets],
  );
  const groupSummary = useMemo(() => sumByGroup(buckets), [buckets]);

  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', marginBottom: 16 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          <span>Period:</span>
          <select
            value={periodDays}
            onChange={(e) => setPeriodDays(Number(e.target.value))}
          >
            {PERIOD_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          <span>Group by:</span>
          <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
            {GROUP_BY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>
        {(loadingSeries || loadingRecon) ? <Spinner size={14} /> : null}
      </div>

      <div className="settings-billing-summary">
        <div className="settings-billing-card">
          <div className="settings-summary-label">Total spend</div>
          <div className="settings-summary-value">{formatUsd(totalUsd)}</div>
          <div className="settings-summary-note">
            Raw Claude cost — markup applied per feature in the breakdown below
          </div>
        </div>
        <div className="settings-billing-card">
          <div className="settings-summary-label">Billable calls</div>
          <div className="settings-summary-value">{formatNumber(totalCalls)}</div>
          <div className="settings-summary-note">
            {periodDays}-day window · grouped by {groupBy}
          </div>
        </div>
        <div className="settings-billing-card">
          <div className="settings-summary-label">Top driver</div>
          <div className="settings-summary-value">
            {groupSummary[0] ? labelForGroup(groupBy, groupSummary[0].key) : '—'}
          </div>
          <div className="settings-summary-note">
            {groupSummary[0]
              ? `${formatUsd(groupSummary[0].cost_usd)} · ${formatNumber(groupSummary[0].event_count)} calls`
              : 'No spend yet'}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 24 }}>
        <h3 style={{ fontSize: 14, marginBottom: 8 }}>Daily spend</h3>
        <StackedBarChart buckets={buckets} groupBy={groupBy} />
      </div>

      {groupSummary.length > 0 ? (
        <div className="settings-usage-table" style={{ marginTop: 24 }}>
          <div className="settings-usage-head">
            <h3>Breakdown by {groupBy}</h3>
          </div>
          <table>
            <thead>
              <tr>
                <th>{groupBy === 'model' ? 'Model' : groupBy === 'feature' ? 'Feature' : 'User'}</th>
                <th>Calls</th>
                <th>Input tokens</th>
                <th>Output tokens</th>
                <th>Cache (read / create)</th>
                <th>Cost</th>
              </tr>
            </thead>
            <tbody>
              {groupSummary.map((row) => (
                <tr key={row.key}>
                  <td>{labelForGroup(groupBy, row.key)}</td>
                  <td>{formatNumber(row.event_count)}</td>
                  <td>{formatNumber(row.input_tokens)}</td>
                  <td>{formatNumber(row.output_tokens)}</td>
                  <td>
                    {formatNumber(row.cache_read_tokens)}
                    {' / '}
                    {formatNumber(row.cache_creation_tokens)}
                  </td>
                  <td>{formatUsd4(row.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <div style={{ marginTop: 32 }}>
        <h3 style={{ fontSize: 14, marginBottom: 8 }}>
          Anthropic reconciliation
        </h3>
        <p className="settings-summary-note" style={{ marginBottom: 12 }}>
          Cross-checks every dollar Anthropic billed against what Tali attributed
          to a feature. A daily background task reads the Anthropic Admin API
          and writes one row per day / model. Drift {'>'} 1% means spend isn't
          being attributed correctly.
        </p>
        <ReconciliationPanel data={recon} loading={loadingRecon} />
      </div>
    </div>
  );
}
