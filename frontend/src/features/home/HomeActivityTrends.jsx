// Home "Decisions & review queue" — a single quick-summary section for the
// review queue. A "Pending now · by type" strip shows the current backlog
// split (what's awaiting review right now); stacked bars show the decisions
// the agent created each day by type; a line tracks the pending backlog (the
// same count as the Home tab badge). A callout flags decisions that bounced
// back into the queue after a Workable writeback failed. Everything here —
// the strip, the chart, the callout — honours the role filter.

import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { analytics as analyticsApi } from '../../shared/api';

const safeNumber = (v, fb = 0) => (Number.isFinite(Number(v)) ? Number(v) : fb);

// Stacking buckets mirror the Home "Pending by type" vocabulary + colours so
// the chart reads as the same decision language as the queue below it.
const TYPE_BUCKETS = [
  { key: 'advance', label: 'Advance', color: 'var(--green)', types: ['advance_to_interview'] },
  { key: 'send_assessment', label: 'Send assessment', color: 'var(--purple)', types: ['send_assessment', 'resend_assessment_invite'] },
  { key: 'reject', label: 'Reject', color: 'var(--red)', types: ['reject'] },
  { key: 'skip_assessment_reject', label: 'Reject (pre-screen)', color: 'var(--red-deep)', types: ['skip_assessment_reject'] },
  { key: 'escalate', label: 'Escalate', color: 'var(--amber)', types: ['escalate_low_confidence'] },
];

const fmtDay = (iso) => {
  const [y, m, d] = String(iso || '').split('-').map(Number);
  if (!y || !m || !d) return String(iso || '');
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  });
};

export const HomeActivityTrends = ({ rolesBreakdown = [] }) => {
  const [roleId, setRoleId] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    analyticsApi.activityTimeseries({ days: 30, ...(roleId ? { role_id: roleId } : {}) })
      .then((res) => {
        if (cancelled) return;
        setData(res?.data || null);
      })
      .catch(() => {
        if (!cancelled) { setData(null); setError(true); }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [roleId]);

  const chartData = useMemo(() => (
    (data?.series || []).map((d) => {
      const row = { date: d.date, label: fmtDay(d.date), backlog: safeNumber(d.backlog) };
      TYPE_BUCKETS.forEach((b) => {
        row[b.key] = b.types.reduce((n, t) => n + safeNumber(d.by_type?.[t]), 0);
      });
      return row;
    })
  ), [data]);

  const activeBuckets = useMemo(
    () => TYPE_BUCKETS.filter((b) => chartData.some((d) => safeNumber(d[b.key]) > 0)),
    [chartData],
  );

  // "Pending now · by type" strip — the current backlog split, role-aware.
  // Core buckets always show (so a 0 reads as "nothing of this kind queued");
  // escalate only appears when there's something to escalate.
  const pendingBuckets = useMemo(() => {
    const counts = data?.pending_now?.by_type || {};
    return TYPE_BUCKETS
      .map((b) => ({ ...b, count: b.types.reduce((n, t) => n + safeNumber(counts[t]), 0) }))
      .filter((b) => b.key !== 'escalate' || b.count > 0);
  }, [data]);
  const pendingTypeTotal = pendingBuckets.reduce((n, b) => n + safeNumber(b.count), 0);

  const tickInterval = chartData.length > 12 ? Math.floor(chartData.length / 6) : 0;
  const pending = data?.pending_now || { decisions: 0, questions: 0, total: 0 };
  const errors = data?.workable_errors || { total: 0, by_role: [] };
  const roleOptions = Array.isArray(rolesBreakdown) ? rolesBreakdown : [];

  return (
    <section className="home-section">
      <div className="home-section-head">
        <div>
          <span className="kicker">REVIEW QUEUE · LAST 30 DAYS</span>
          <h3 className="home-section-title">Decisions &amp; review queue<em>.</em></h3>
          <p className="home-section-sub">
            A quick read on your review queue — what&rsquo;s pending right now by type, plus how the backlog has
            moved. Bars are daily decisions by type; the line is the pending backlog (the same count as the Home
            tab badge).{' '}
            <strong style={{ color: 'var(--ink-2)' }}>
              Now {safeNumber(pending.total).toLocaleString()} awaiting review
            </strong>{' '}
            ({safeNumber(pending.decisions).toLocaleString()} decision{pending.decisions === 1 ? '' : 's'} ·{' '}
            {safeNumber(pending.questions).toLocaleString()} question{pending.questions === 1 ? '' : 's'}).
          </p>
        </div>
        <label className="ht-rolefilter">
          <span className="kicker" style={{ marginBottom: 4, display: 'block' }}>Role</span>
          <select
            className="ht-select"
            value={roleId}
            onChange={(e) => setRoleId(e.target.value)}
          >
            <option value="">All roles</option>
            {roleOptions.map((r) => (
              <option key={r.role_id} value={r.role_id}>{r.name}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="ht-pending">
        <span className="kicker ht-pending-label">Pending now · by type</span>
        {loading ? (
          <span className="ht-pending-empty">Loading…</span>
        ) : pendingTypeTotal === 0 ? (
          <span className="ht-pending-empty">Queue is clear — no pending decisions.</span>
        ) : (
          <div className="ht-pending-items">
            {pendingBuckets.map((b) => (
              <div key={b.key} className="ht-pending-item">
                <span className="ht-pending-dot" style={{ background: b.color }} aria-hidden="true" />
                <span className="ht-pending-num">{safeNumber(b.count).toLocaleString()}</span>
                <span className="ht-pending-cap">{b.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {errors.total > 0 ? (
        <div className="ht-callout" role="alert">
          <div className="ht-callout-head">
            <AlertTriangle size={16} aria-hidden="true" />
            <span>
              <b>{errors.total.toLocaleString()}</b> decision{errors.total === 1 ? '' : 's'} across{' '}
              <b>{errors.by_role.length}</b> role{errors.by_role.length === 1 ? '' : 's'} bounced back to the queue after a
              Workable error. Approve them again once Workable is reachable.
            </span>
          </div>
          <div className="ht-callout-list">
            {errors.by_role.map((r) => (
              <button
                key={r.role_id}
                type="button"
                className="ht-callout-chip"
                title={r.example || 'Returned to queue after a Workable error'}
                onClick={() => setRoleId(String(r.role_id))}
              >
                {r.role_name} <b>{safeNumber(r.count).toLocaleString()}</b>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {loading ? (
        <div className="home-empty">Loading activity…</div>
      ) : error ? (
        <div className="home-empty">Couldn’t load activity.</div>
      ) : (
        <div style={{ height: 300 }}>
          <ResponsiveContainer>
            <ComposedChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fill: 'var(--mute)', fontSize: 11 }}
                interval={tickInterval}
                tickLine={false}
              />
              <YAxis
                yAxisId="left"
                allowDecimals={false}
                tick={{ fill: 'var(--mute)', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                allowDecimals={false}
                tick={{ fill: 'var(--mute)', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
              />
              <Tooltip
                contentStyle={{
                  background: 'var(--bg-2)',
                  border: '1px solid var(--line)',
                  borderRadius: '12px',
                  color: 'var(--ink)',
                  fontSize: 12,
                }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {activeBuckets.map((b) => (
                <Bar
                  key={b.key}
                  yAxisId="left"
                  dataKey={b.key}
                  name={b.label}
                  stackId="decisions"
                  fill={b.color}
                  maxBarSize={26}
                />
              ))}
              <Line
                yAxisId="right"
                type="monotone"
                dataKey="backlog"
                name="Pending backlog"
                stroke="var(--purple-2)"
                strokeWidth={2}
                dot={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
};

export default HomeActivityTrends;
