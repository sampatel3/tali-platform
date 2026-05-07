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
  { value: 'feature', label: 'Product' },
  { value: 'user', label: 'Person' },
];

// Customer-facing labels for the internal feature codes the backend records
// against each usage event. Anything missing falls through to a humanised
// version of the raw key so we never leak `agent_autonomous` style strings.
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
  other: 'Other',
};

const formatUsd = (n) => `$${Number(n || 0).toFixed(2)}`;
const formatUsd4 = (n) => `$${Number(n || 0).toFixed(4)}`;
const formatNumber = (n) => Number(n || 0).toLocaleString();

const sortKeyAsc = (a, b) => (a > b ? 1 : a < b ? -1 : 0);

const PALETTE = [
  '#7e6dff', '#a4b4a8', '#bfa890', '#7e8e98', '#c79b76',
  '#8b9aa3', '#b08e72', '#9aa5a0', '#cdb696', '#7d6e5b',
];

const colorFor = (_key, index) => PALETTE[index % PALETTE.length];

const humaniseKey = (key) => String(key || '')
  .replace(/[_-]+/g, ' ')
  .replace(/\b\w/g, (c) => c.toUpperCase());

const labelForGroup = (groupBy, key) => {
  if (groupBy === 'feature') return FEATURE_LABELS[key] || humaniseKey(key);
  if (groupBy === 'user') return key === 'unattributed' ? 'Unattributed' : `Member #${key}`;
  return humaniseKey(key);
};

// Pivot the timeseries (one row per day per group) into a per-day shape so
// the bar chart can stack segments. Days without spend are dropped because
// we render the whole window from the chart's leftmost day to its rightmost.
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
    const prev = map.get(key) || { cost_usd: 0, event_count: 0 };
    prev.cost_usd += Number(b.cost_usd || 0);
    prev.event_count += Number(b.event_count || 0);
    map.set(key, prev);
  }
  return [...map.entries()]
    .map(([key, value]) => ({ key, ...value }))
    .sort((a, b) => b.cost_usd - a.cost_usd);
};

const StackedBarChart = ({ buckets, groupBy }) => {
  const { days, groupKeys } = useMemo(() => pivotByDay(buckets), [buckets]);
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

  const colorByGroup = new Map();
  groupKeys.forEach((g, i) => colorByGroup.set(g, colorFor(g, i)));

  return (
    <div style={{ overflowX: 'auto' }}>
      <svg
        width={WIDTH}
        height={HEIGHT}
        role="img"
        aria-label={`Daily spend stacked by ${groupBy}`}
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
              <title>{`${day.day} · ${formatUsd4(day.total)} · ${day.calls} requests`}</title>
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

export default function UsagePanel() {
  const [periodDays, setPeriodDays] = useState(30);
  const [groupBy, setGroupBy] = useState('feature');
  const [timeseries, setTimeseries] = useState(null);
  const [loadingSeries, setLoadingSeries] = useState(false);

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

  useEffect(() => {
    void loadTimeseries(periodDays, groupBy);
  }, [periodDays, groupBy, loadTimeseries]);

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
  const breakdownLabel = groupBy === 'feature' ? 'Product' : 'Person';

  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', marginBottom: 16 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          <span>Period</span>
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
          <span>Group by</span>
          <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
            {GROUP_BY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>
        {loadingSeries ? <Spinner size={14} /> : null}
      </div>

      <div className="settings-billing-summary">
        <div className="settings-billing-card">
          <div className="settings-summary-label">Total spend</div>
          <div className="settings-summary-value">{formatUsd(totalUsd)}</div>
          <div className="settings-summary-note">
            {periodDays}-day window
          </div>
        </div>
        <div className="settings-billing-card">
          <div className="settings-summary-label">Billable AI requests</div>
          <div className="settings-summary-value">{formatNumber(totalCalls)}</div>
          <div className="settings-summary-note">
            Across all Taali products
          </div>
        </div>
        <div className="settings-billing-card">
          <div className="settings-summary-label">Top driver</div>
          <div className="settings-summary-value">
            {groupSummary[0] ? labelForGroup(groupBy, groupSummary[0].key) : '—'}
          </div>
          <div className="settings-summary-note">
            {groupSummary[0]
              ? `${formatUsd(groupSummary[0].cost_usd)} · ${formatNumber(groupSummary[0].event_count)} requests`
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
            <h3>Breakdown by {breakdownLabel.toLowerCase()}</h3>
          </div>
          <table>
            <thead>
              <tr>
                <th>{breakdownLabel}</th>
                <th>Requests</th>
                <th>Spend</th>
              </tr>
            </thead>
            <tbody>
              {groupSummary.map((row) => (
                <tr key={row.key}>
                  <td>{labelForGroup(groupBy, row.key)}</td>
                  <td>{formatNumber(row.event_count)}</td>
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
