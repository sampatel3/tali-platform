// MONITORING — the single "Analytics" surface for the Hub. Consolidates what
// used to be four sections (History & analytics, Decision & backlog trend, By
// role, Signal) into one: a summary band (KPIs + anomalies) over a shared
// Role + Window filter, then tabbed lenses:
//   Activity  — daily decisions + backlog trend + Workable errors + by-role
//   Outcomes  — funnel + advance→hire conversion + score distribution
//   Quality   — override/teach + realised outcomes + the teach loop
//   History   — the resolved-decision audit log
// Role + window are owned here and threaded into every tab so the whole
// section reads one window/scope (fixes the old 30d/all-time/last-100 mix).

import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronUp } from 'lucide-react';

import { agent as agentApi, analytics as analyticsApi } from '../../shared/api';
import { Select } from '../../shared/ui/TaaliPrimitives';
import { formatUsd } from './atoms';
import { HomeActivityTrends } from './HomeActivityTrends';
import { HomeSignal } from './HomeSignal';
import { HomeRoles } from './HomeRoles';
import { HomeExperiments } from './HomeExperiments';
import { AnalyticsDrillIns, HistoryTable } from './HomeEverything';

const WINDOWS = [
  { key: '7d', label: '7d', days: 7 },
  { key: '30d', label: '30d', days: 30 },
  { key: '90d', label: '90d', days: 90 },
  { key: 'all', label: 'All', days: 120 },
];

const TABS = [
  { key: 'activity', label: 'Activity' },
  { key: 'outcomes', label: 'Outcomes' },
  { key: 'quality', label: 'Quality' },
  { key: 'experiments', label: 'A/B' },
  { key: 'history', label: 'History' },
];

const safeNum = (v, fb = 0) => (Number.isFinite(Number(v)) ? Number(v) : fb);
const pct = (part, whole) => (safeNum(whole) > 0 ? Math.round((safeNum(part) / safeNum(whole)) * 100) : 0);

const Stat = ({ label, value, sub }) => (
  <div className="hm-stat">
    <div className="hm-stat-label">{label}</div>
    <div className="hm-stat-value">{value}</div>
    {sub ? <div className="hm-stat-sub">{sub}</div> : null}
  </div>
);

export const HomeMonitoring = ({
  rolesBreakdown = [],
  feedback = [],
  outcomes = [],
  loadingSignal = false,
  reload,
  onNavigate,
  onSelect,
}) => {
  const [open, setOpen] = useState(false);
  const [roleId, setRoleId] = useState('');
  const [windowKey, setWindowKey] = useState('30d');
  const [tab, setTab] = useState('activity');

  const [summary, setSummary] = useState(null);
  const [breakdown, setBreakdown] = useState(null);
  const [historyRows, setHistoryRows] = useState([]);
  const [loading, setLoading] = useState(false);

  const days = useMemo(
    () => (WINDOWS.find((w) => w.key === windowKey) || WINDOWS[1]).days,
    [windowKey],
  );
  const dateFrom = useMemo(
    () => (windowKey === 'all' ? null : new Date(Date.now() - days * 86400000).toISOString()),
    [windowKey, days],
  );

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    setLoading(true);
    const scope = {
      ...(roleId ? { role_id: roleId } : {}),
      ...(dateFrom ? { date_from: dateFrom } : {}),
    };
    Promise.all([
      analyticsApi.reportingSummary(scope),
      analyticsApi.decisionsBreakdown(scope),
      agentApi.listDecisions({
        status: 'resolved',
        limit: 100,
        ...(roleId ? { role_id: roleId } : {}),
        ...(dateFrom ? { since: dateFrom } : {}),
      }),
    ])
      .then(([s, b, h]) => {
        if (cancelled) return;
        setSummary(s?.data || null);
        setBreakdown(b?.data || null);
        setHistoryRows(Array.isArray(h?.data) ? h.data : []);
      })
      .catch(() => {
        if (!cancelled) { setSummary(null); setBreakdown(null); setHistoryRows([]); }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [open, roleId, dateFrom]);

  const k = summary?.kpis || {};
  const conv = breakdown?.totals?.advance_conversion || {};
  const hr = k.human_review || {};
  const spend = k.org_spend || {};
  const decisionsDelta = k.decisions_made?.delta_pct;
  const anomalies = Array.isArray(summary?.anomalies) ? summary.anomalies : [];

  return (
    <section className="home-section">
      <div className="home-section-head">
        <div>
          <span className="kicker">ANALYTICS · PLATFORM PULSE</span>
          <h3 className="home-section-title">Analytics<em>.</em></h3>
          <p className="home-section-sub">
            Everything worth watching about the agent in one place — throughput, outcomes, quality, and the
            decision log. Scope it by role and time window.
          </p>
        </div>
        <button
          type="button"
          className="home-section-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          <span>{open ? 'Hide' : 'Show'} analytics</span>
          {open ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
        </button>
      </div>

      {open ? (
        <>
          <div className="hm-controls">
            <label className="hm-rolefilter">
              <span className="kicker">Role</span>
              <Select inline value={roleId} onChange={(e) => setRoleId(e.target.value)}>
                <option value="">All roles</option>
                {rolesBreakdown.map((r) => (
                  <option key={r.role_id} value={r.role_id}>{r.name}</option>
                ))}
              </Select>
            </label>
            <div className="hm-window" role="group" aria-label="Time window">
              {WINDOWS.map((w) => (
                <button
                  key={w.key}
                  type="button"
                  className={`hm-window-btn${windowKey === w.key ? ' active' : ''}`}
                  onClick={() => setWindowKey(w.key)}
                >
                  {w.label}
                </button>
              ))}
            </div>
          </div>

          <div className="hm-summary">
            <Stat
              label="Decisions"
              value={safeNum(k.decisions_made?.current).toLocaleString()}
              sub={`${safeNum(hr.approved).toLocaleString()} approved${decisionsDelta != null ? ` · ${decisionsDelta > 0 ? '+' : ''}${decisionsDelta}% vs prior` : ''}`}
            />
            <Stat
              label="Auto-advanced"
              value={safeNum(k.auto_advanced?.current).toLocaleString()}
              sub={`${safeNum(k.auto_rejected?.current).toLocaleString()} auto-rejected`}
            />
            <Stat
              label="Advance → hire"
              value={`${pct(conv.hired, conv.advanced_total)}%`}
              sub={`${safeNum(conv.hired).toLocaleString()} of ${safeNum(conv.advanced_total).toLocaleString()} advanced`}
            />
            <Stat
              label="Override / teach"
              value={`${safeNum(hr.override_rate_pct)}%`}
              sub={`${safeNum(hr.teach_rate_pct)}% taught`}
            />
            <Stat
              label="Spend · MTD"
              value={formatUsd(spend.spent_cents)}
              sub={safeNum(spend.budget_cents) > 0 ? `of ${formatUsd(spend.budget_cents)}` : 'no cap set'}
            />
          </div>

          <div className="hm-anomalies">
            {loading ? null : anomalies.length === 0 ? (
              <span className="hm-anom-clear">
                <CheckCircle2 size={15} aria-hidden="true" /> All clear — nothing flagged.
              </span>
            ) : (
              anomalies.map((a, i) => (
                <div key={`${a.title}-${i}`} className={`hm-anom tone-${a.tone || 'neutral'}`}>
                  <AlertTriangle size={14} aria-hidden="true" />
                  <span><b>{a.title}</b>{a.body ? ` — ${a.body}` : ''}</span>
                </div>
              ))
            )}
          </div>

          <div className="hm-tabs" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                aria-selected={tab === t.key}
                className={`hm-tab${tab === t.key ? ' active' : ''}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === 'activity' ? (
            <>
              <HomeActivityTrends roleId={roleId} days={days} onRoleChange={setRoleId} />
              {!roleId ? (
                <HomeRoles rows={rolesBreakdown} loading={false} onNavigate={onNavigate} embedded />
              ) : null}
            </>
          ) : tab === 'outcomes' ? (
            loading
              ? <div className="home-empty">Loading…</div>
              : <div className="hm-tabpanel"><AnalyticsDrillIns summary={summary} breakdown={breakdown} /></div>
          ) : tab === 'quality' ? (
            <HomeSignal embedded feedback={feedback} outcomes={outcomes} loading={loadingSignal} reload={reload} />
          ) : tab === 'experiments' ? (
            <HomeExperiments roleId={roleId} dateFrom={dateFrom} />
          ) : (
            loading
              ? <div className="home-empty">Loading…</div>
              : <div className="hm-tabpanel"><HistoryTable rows={historyRows} onSelect={onSelect} onNavigate={onNavigate} /></div>
          )}
        </>
      ) : null}
    </section>
  );
};

export default HomeMonitoring;
