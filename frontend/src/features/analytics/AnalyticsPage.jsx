// AnalyticsPage — the dedicated /analytics route. Outcomes owns the scoped
// reporting pulse; Agents is a live workspace view; the remaining tabs
// keep their focused teaching, experiment, and decision-log workflows.
//
// EVERY value is real. The page owns the role + window scope and threads it
// into the windowed feeds:
//   pulse + funnel + narrator      → GET /analytics/reporting-summary
//   advance→hire + by-role         → GET /analytics/decisions-breakdown
//   override / agreement bars      → GET /analytics/decision-trend (new)
//   per-role override + spend      → GET /agent/roles/breakdown
//   teach feed                     → GET /agent/feedback
// The Fleet, A/B, Teaching-timeline and Decision-log tabs self-fetch their own
// live feeds (panel / experiments / threshold-history / decisions). Where real
// data is absent every surface renders a proper empty state — nothing is faked.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import '../../styles/25-analytics.css';
import { Download } from 'lucide-react';

import { agent as agentApi, analytics as analyticsApi } from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import {
  MotionNumber,
  MotionStagger,
  MotionTab,
  MotionTabs,
  PresenceSwap,
} from '../../shared/motion';
import { AgentHeader } from '../../shared/layout/AgentHeader';
import { Select, PageLoader } from '../../shared/ui/TaaliPrimitives';
import {
  safeNum,
  pct,
  fmtUsd,
  decisionTypeLabel,
} from './analyticsFormat';
import { OutcomesTab } from './OutcomesTab';
import { FleetTab } from './FleetTab';
import { TeachingTab } from './TeachingTab';
import { ExperimentsTab } from './ExperimentsTab';
import { DecisionLogTab, outcomeOf } from './DecisionLogTab';
import { ANALYTICS_TABS } from './analyticsTabs';

const WINDOWS = [
  { key: '7d', label: '7d', days: 7 },
  { key: '30d', label: '30d', days: 30 },
  { key: '90d', label: '90d', days: 90 },
  { key: 'all', label: 'All', days: null },
];

const windowLabel = (key) => {
  const w = WINDOWS.find((x) => x.key === key);
  if (!w) return 'Last 30 days';
  return w.key === 'all' ? 'All time' : `Last ${w.days} days`;
};

const csvEscape = (v) => {
  const s = v == null ? '' : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export const AnalyticsPage = ({ onNavigate, NavComponent }) => {
  const { showToast } = useToast();
  const [roleId, setRoleId] = useState('');
  const [windowKey, setWindowKey] = useState('30d');
  const [tab, setTab] = useState('outcomes');
  const [exporting, setExporting] = useState(false);

  const [summary, setSummary] = useState(null);
  const [breakdown, setBreakdown] = useState(null);
  const [cost, setCost] = useState(null);
  const [trend, setTrend] = useState(null);
  const [rolesBreakdown, setRolesBreakdown] = useState([]);
  const [feedback, setFeedback] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  // True after the first successful load — lets us tell "cold load" (show
  // skeleton) apart from "refetching after a scope change" (dim + keep prior).
  const [hasLoaded, setHasLoaded] = useState(false);

  const days = useMemo(
    () => (WINDOWS.find((w) => w.key === windowKey) || WINDOWS[1]).days,
    [windowKey],
  );
  const dateFrom = useMemo(
    () => (days == null ? null : new Date(Date.now() - days * 86400000).toISOString()),
    [days],
  );

  // Role list for the selector — loaded once (roles/breakdown also feeds the
  // by-role override/spend columns).
  useEffect(() => {
    let cancelled = false;
    agentApi.rolesBreakdown()
      .then((res) => { if (!cancelled) setRolesBreakdown(Array.isArray(res?.data) ? res.data : []); })
      .catch(() => { if (!cancelled) setRolesBreakdown([]); });
    return () => { cancelled = true; };
  }, []);

  // Windowed/scoped feeds — refetched whenever role or window changes.
  //
  // allSettled (not all): one flaky endpoint must not wipe the other three, and
  // must not blank a scope's numbers to fake zeros. Each feed keeps its prior
  // value on failure; we only surface a hard error banner when the primary
  // summary feed fails (there's genuinely nothing real to show then).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const scope = {
      ...(roleId ? { role_id: roleId } : {}),
      ...(dateFrom ? { date_from: dateFrom } : {}),
    };
    Promise.allSettled([
      analyticsApi.reportingSummary(scope),
      analyticsApi.decisionsBreakdown(scope),
      analyticsApi.decisionTrend(roleId ? { role_id: roleId } : {}),
      agentApi.listFeedback({ limit: 30, ...(roleId ? { role_id: roleId } : {}) }),
      analyticsApi.costPerOutcome(scope),
    ])
      .then(([s, b, t, f, c]) => {
        if (cancelled) return;
        const summaryOk = s.status === 'fulfilled';
        if (summaryOk) setSummary(s.value?.data || null);
        if (b.status === 'fulfilled') setBreakdown(b.value?.data || null);
        if (t.status === 'fulfilled') setTrend(t.value?.data || null);
        if (f.status === 'fulfilled') setFeedback(Array.isArray(f.value?.data) ? f.value.data : []);
        if (c.status === 'fulfilled') setCost(c.value?.data || null);
        setLoadError(!summaryOk);
        if (summaryOk) setHasLoaded(true);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [roleId, dateFrom, days, reloadKey]);

  // ── Pulse band values (all real KPIs from reporting-summary +
  //    decisions-breakdown) ──────────────────────────────────────────────
  const k = summary?.kpis || {};
  const hr = k.human_review || {};
  const spend = k.org_spend || {};
  const conv = breakdown?.totals?.advance_conversion || {};
  const decisions = safeNum(k.decisions_made?.current);
  const autoAdvanced = safeNum(k.auto_advanced?.current);
  const autoRejected = safeNum(k.auto_rejected?.current);
  const approved = safeNum(hr.approved);
  const advancedTotal = safeNum(conv.advanced_total);
  const hired = safeNum(conv.hired);
  const advanceHirePct = advancedTotal > 0 ? pct(hired, advancedTotal) : null;
  const overrideRate = safeNum(hr.override_rate_pct);
  const overridden = safeNum(hr.overridden);
  const teachRate = safeNum(hr.teach_rate_pct);
  const taught = safeNum(hr.taught);
  const spentCents = safeNum(spend.spent_cents);
  const budgetCents = safeNum(spend.budget_cents);
  const budgetPctValue = budgetCents > 0 ? Math.round((spentCents / budgetCents) * 100) : null;

  // ── Pulse-band number interpolation. MotionNumber moves from the previous
  //    settled value to the next one, so polling/scope changes never replay a
  //    theatrical zero-to-value count. Reduced motion lands immediately. ────
  const asInt = (n) => Math.round(n).toLocaleString();
  const asPct = (n) => `${Math.round(n)}%`;
  const decisionsTick = <MotionNumber value={decisions} format={asInt} />;
  const autoAdvancedTick = <MotionNumber value={autoAdvanced} format={asInt} />;
  const advanceHireTick = <MotionNumber value={advanceHirePct ?? 0} format={asPct} />;
  const overrideRateTick = <MotionNumber value={overrideRate} format={asPct} />;
  const teachRateTick = <MotionNumber value={teachRate} format={asPct} />;
  const spendTick = <MotionNumber value={spentCents} format={fmtUsd} />;

  const roleName = useMemo(() => {
    if (!roleId) return null;
    const r = rolesBreakdown.find((x) => String(x.role_id) === String(roleId));
    return r?.name || null;
  }, [roleId, rolesBreakdown]);

  // ── Export → CSV of the decision log (fetched on click, scope-aware). ──
  const handleExport = useCallback(async () => {
    setExporting(true);
    try {
      const res = await agentApi.listDecisions({ status: 'all', limit: 500, ...(roleId ? { role_id: roleId } : {}) });
      const rows = Array.isArray(res?.data) ? res.data : [];
      const header = ['Time', 'Actor', 'Role', 'Action', 'Subject', 'Status', 'Override action'];
      const lines = rows.map((r) => [
        (r.resolved_at || r.created_at || '').toString(),
        r.resolved_by_user_id != null ? 'You' : 'Agent',
        r.role_name || `Role #${r.role_id}`,
        decisionTypeLabel(r.decision_type),
        r.candidate_name || `Application #${r.application_id}`,
        outcomeOf(r).text,
        r.override_action ? decisionTypeLabel(r.override_action) : '',
      ].map(csvEscape).join(','));
      const csv = [header.join(','), ...lines].join('\n');
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `taali-decision-log-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch {
      showToast('Export failed — try again.', 'error');
    } finally {
      setExporting(false);
    }
  }, [roleId, showToast]);

  const headerActions = (
    <div className="an-controls">
      <span className="an-sel">
        <Select inline value={roleId} onChange={(e) => setRoleId(e.target.value)} aria-label="Role filter">
          <option value="">All roles</option>
          {rolesBreakdown.map((r) => (
            <option key={r.role_id} value={r.role_id}>{r.name}</option>
          ))}
        </Select>
      </span>
      <span className="an-window" role="group" aria-label="Time window">
        {WINDOWS.map((w) => (
          <button
            key={w.key}
            type="button"
            className={windowKey === w.key ? 'active' : ''}
            onClick={() => setWindowKey(w.key)}
          >
            {w.label}
          </button>
        ))}
      </span>
      <button type="button" className="btn btn-sm" onClick={handleExport} disabled={exporting}>
        <Download size={14} aria-hidden="true" />
        {exporting ? 'Exporting…' : 'Export'}
      </button>
    </div>
  );

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="analytics" onNavigate={onNavigate} /> : null}
      <AgentHeader
        breadcrumbs={[{
          label: tab === 'fleet'
            ? 'Analytics · agents'
            : `Analytics · ${windowLabel(windowKey).toLowerCase()}`,
        }]}
        kicker={tab === 'fleet'
          ? 'ANALYTICS · LIVE WORKSPACE'
          : `ANALYTICS · ${windowLabel(windowKey).toUpperCase()}${roleId ? '' : ' · ALL ROLES'}`}
        title="Analytics"
        subtitle="Outcomes, your agents, and the teaching history that keeps them calibrated."
        actions={tab === 'fleet' ? null : headerActions}
      />
      <div className="an-page">
        {loadError && tab === 'outcomes' ? (
          <div className="an-error" role="alert">
            <p>Couldn&apos;t load analytics. This is usually a temporary connection issue.</p>
            <button type="button" className="btn btn-sm" onClick={() => setReloadKey((k) => k + 1)}>
              Retry
            </button>
          </div>
        ) : null}
        {/* 6-stat pulse band. Dims + shows a spinner while a scope change is
            in-flight so the numbers under the new label aren't read as final. */}
        {tab === 'outcomes' ? (
          <MotionStagger
            className="an-pulse"
            data-motion-stagger="analytics-pulse"
            aria-busy={loading && hasLoaded ? 'true' : undefined}
            style={loading && hasLoaded ? { opacity: 0.5, transition: 'opacity 120ms' } : undefined}
          >
            <div className="an-pcell">
              <div className="k">Decisions</div>
              <div className="v">{decisionsTick}</div>
              <div className="s">{approved.toLocaleString()} approved</div>
            </div>
            <div className="an-pcell">
              <div className="k">Auto-advanced</div>
              <div className="v">{autoAdvancedTick}</div>
              <div className="s">{autoRejected.toLocaleString()} auto-rejected</div>
            </div>
            <div className="an-pcell">
              <div className="k">Advance → hire</div>
              <div className="v attn">{advanceHirePct != null ? advanceHireTick : '—'}</div>
              <div className="s">{hired.toLocaleString()} of {advancedTotal.toLocaleString()} advanced</div>
            </div>
            <div className="an-pcell">
              <div className="k">Override rate</div>
              <div className="v">{overrideRateTick}</div>
              <div className="s">{overridden.toLocaleString()} override{overridden === 1 ? '' : 's'}</div>
            </div>
            <div className="an-pcell">
              <div className="k">Taught</div>
              <div className="v">{teachRateTick}</div>
              <div className="s">{taught.toLocaleString()} teaching event{taught === 1 ? '' : 's'}</div>
            </div>
            <div className="an-pcell">
              <div className="k">Spend · MTD</div>
              <div className="v">
                {spendTick}
                {budgetCents > 0 ? <small> / {fmtUsd(budgetCents)}</small> : null}
              </div>
              <div className="s">{budgetPctValue != null ? `${budgetPctValue}%` : 'no cap set'}</div>
            </div>
          </MotionStagger>
        ) : null}

        {/* The same text-only underline tabs used on Job pages. */}
        <MotionTabs value={tab} onValueChange={setTab} className="vtabs" aria-label="Analytics views">
          {ANALYTICS_TABS.map((t) => (
            <MotionTab
              key={t.key}
              value={t.key}
              id={`analytics-tab-${t.key}`}
              aria-controls={`analytics-panel-${t.key}`}
              className={`vtab${tab === t.key ? ' on' : ''}`}
              indicatorClassName="vtab-motion-indicator"
            >
              {t.label}
            </MotionTab>
          ))}
        </MotionTabs>

        <PresenceSwap
            presenceKey={tab}
            id={`analytics-panel-${tab}`}
            className="an-tabpanel"
            role="tabpanel"
            aria-labelledby={`analytics-tab-${tab}`}
          >
          {tab === 'outcomes' ? (
          loading && !summary
            ? (
              <div className="an-empty" aria-label="Loading outcomes">
                <PageLoader minHeight="16rem" label="Loading outcomes" />
              </div>
            )
            : (
              <OutcomesTab summary={summary} breakdown={breakdown} trend={trend} rolesBreakdown={rolesBreakdown} cost={cost} />
            )
        ) : tab === 'fleet' ? (
          <FleetTab onOpenDecisionLog={() => setTab('log')} />
        ) : tab === 'teaching' ? (
          <TeachingTab feedback={feedback} trend={trend} roleId={roleId} roleName={roleName} />
        ) : tab === 'ab' ? (
          <ExperimentsTab roleId={roleId} />
        ) : (
          <DecisionLogTab roleId={roleId} />
        )}
        </PresenceSwap>
      </div>
    </div>
  );
};

export default AnalyticsPage;
