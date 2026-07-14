// Agent Fleet is split into a pure, prop-driven view and a small polling
// wrapper. The view deliberately shares the same KPI and role-first language
// as the rest of the product; the wrapper owns all network lifecycle state.

import React, { useEffect, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Circle,
  FilterX,
  Loader2,
  Lock,
  Pause,
  Sparkles,
} from 'lucide-react';

import { agent as agentApi } from '../../shared/api';
import { KpiStrip } from '../../shared/ui/KpiStrip';
import { Spinner } from '../../shared/ui/TaaliPrimitives';
import {
  safeNum,
  fmtUsd,
  fmtUsdFine,
  fmtRelShort,
  fmtRelAgo,
} from './analyticsFormat';

const PANEL_POLL_MS = 5000;
const ACTIVITY_POLL_MS = 15000;
const COHORT_BEAT_SECS = 1800;

const nextCycleLabel = (lastCycleAt) => {
  if (!lastCycleAt) return 'soon';
  const last = new Date(lastCycleAt).getTime();
  if (Number.isNaN(last)) return 'soon';
  const remaining = COHORT_BEAT_SECS - (Date.now() - last) / 1000;
  if (remaining <= 0) return 'due';
  return `${Math.ceil(remaining / 60)}m`;
};

const ActivityGlyph = ({ kind }) => {
  const map = {
    run: <Loader2 size={13} aria-hidden="true" />,
    decision: <ArrowUpRight size={13} aria-hidden="true" />,
    event: <ArrowUpRight size={13} aria-hidden="true" />,
    needs_input: <FilterX size={13} aria-hidden="true" />,
  };
  return <span className="gl">{map[kind] || <ArrowUpRight size={13} aria-hidden="true" />}</span>;
};

const statusFor = (agent) => {
  const activity = agent?.activity || {};
  const label = String(activity.label || '').toUpperCase();
  const detail = String(activity.text || '').trim();

  if (agent?.running === false || label === 'PAUSED') {
    const reason = String(agent?.paused_reason || detail || '').trim().replace(/_/g, ' ');
    return {
      kind: 'paused',
      text: `Paused · ${reason && reason.toLowerCase() !== 'paused' ? reason : 'reason not provided'}`,
    };
  }
  if (label === 'WORKING') {
    return {
      kind: 'work',
      text: `Working${detail && detail.toLowerCase() !== 'working' ? ` · ${detail}` : ''}`,
    };
  }
  return {
    kind: 'idle',
    text: `Idle · next run ${nextCycleLabel(agent?.last_run_at)}`,
  };
};

const StatusGlyph = ({ kind }) => (
  <span className={`an-agent-glyph ${kind}`} aria-hidden="true">
    {kind === 'work' ? <Sparkles size={15} /> : kind === 'paused' ? <Pause size={14} /> : <Circle size={13} />}
  </span>
);

const AgentCard = ({ agent }) => {
  const spent = safeNum(agent.budget_spent_cents);
  const cap = safeNum(agent.budget_cap_cents);
  const rawPct = cap > 0 ? Math.round((100 * spent) / cap) : 0;
  const barPct = Math.min(100, Math.max(0, rawPct));
  const barHi = rawPct >= 90;
  const status = statusFor(agent);
  const name = agent.name || `Role #${agent.role_id}`;

  return (
    <article className="an-agent-card">
      <header className="an-agent-card-head">
        <StatusGlyph kind={status.kind} />
        <div className="an-agent-identity">
          <h3 className="an-agent-name" title={name}>{name}</h3>
          <p className={`an-agent-status ${status.kind}`}>{status.text}</p>
        </div>
      </header>

      <div className="an-agent-budget">
        <div className="an-agent-budget-head">
          <span>Monthly budget</span>
          <strong>{fmtUsdFine(spent)} / {cap > 0 ? fmtUsdFine(cap) : 'No cap'}</strong>
        </div>
        <div
          className="an-agent-budget-track"
          role="progressbar"
          aria-label={`${name} monthly budget used`}
          aria-valuemin="0"
          aria-valuemax="100"
          aria-valuenow={barPct}
          aria-valuetext={cap > 0 ? `${rawPct}% used` : 'No monthly cap set'}
        >
          <span
            className={`an-agent-budget-fill${barHi ? ' hi' : ''}`}
            style={{ width: `${barPct}%` }}
          />
        </div>
      </div>

      <div className="an-agent-meta">
        <div className="an-agent-meta-item"><span>Pending</span><strong>{safeNum(agent.pending)}</strong></div>
        <div className="an-agent-meta-item"><span>Last run</span><strong>{agent.last_run_at ? fmtRelAgo(agent.last_run_at) : '—'}</strong></div>
        <div className="an-agent-meta-item"><span>Cycles · 24h</span><strong>{safeNum(agent.cycles_24h)}</strong></div>
      </div>
    </article>
  );
};

export const FleetView = ({ panel, activity = [], onOpenDecisionLog }) => {
  const kpis = panel?.kpis || {};
  const pulse = panel?.pulse || {};
  const agents = Array.isArray(panel?.agents) ? panel.agents : [];
  const entries = Array.isArray(activity) ? activity : [];

  const activeCount = kpis.agents_running == null
    ? agents.filter((agent) => agent.running !== false).length
    : safeNum(kpis.agents_running);
  const pausedCount = kpis.agents_paused == null
    ? agents.filter((agent) => agent.running === false).length
    : safeNum(kpis.agents_paused);
  const reviewCount = safeNum(kpis.pending_decisions ?? kpis.pending);
  const oldestHint = kpis.oldest_pending_age_seconds != null
    ? `Oldest waiting ${fmtRelShort(new Date(Date.now() - safeNum(kpis.oldest_pending_age_seconds) * 1000).toISOString())}`
    : (reviewCount > 0 ? 'Ready for your review' : 'Nothing waiting');
  const spent = safeNum(kpis.budget_spent_cents);
  const cap = safeNum(kpis.budget_cap_cents);
  const budgetPct = cap > 0 ? Math.round((100 * spent) / cap) : 0;
  const errors = safeNum(kpis.errors_24h);
  const cycles = safeNum(kpis.cycles_24h);

  const summaryTiles = [
    {
      key: 'active-agents',
      label: 'Active agents',
      value: activeCount,
      sub: `${pausedCount} paused`,
    },
    {
      key: 'needs-review',
      label: 'Needs review',
      value: reviewCount,
      emph: reviewCount > 0,
      sub: oldestHint,
    },
    {
      key: 'workspace-spend',
      label: 'Workspace spend',
      value: fmtUsd(spent),
      unit: cap > 0 ? `/ ${fmtUsd(cap)}` : null,
      bar: { pct: Math.min(100, Math.max(0, budgetPct)), over: budgetPct > 100 },
      sub: cap > 0 ? `${budgetPct}% of monthly budget` : 'No monthly cap set',
    },
    {
      key: 'fleet-health',
      label: 'Fleet health',
      value: errors > 0 ? `${errors} issue${errors === 1 ? '' : 's'}` : 'Healthy',
      sub: `${cycles} cycle${cycles === 1 ? '' : 's'} in 24h`,
    },
  ];

  return (
    <div className="an-tabpanel">
      <div className="an-fleet-status">
        <span className="gp" aria-hidden="true" />
        <span>Review cycle</span>
        <span>Last run <strong>{pulse.last_cycle_at ? fmtRelAgo(pulse.last_cycle_at) : '—'}</strong></span>
        <span>Next <strong>{nextCycleLabel(pulse.last_cycle_at)}</strong></span>
        <span>Last activity <strong>{pulse.last_activity_at ? fmtRelAgo(pulse.last_activity_at) : '—'}</strong></span>
      </div>

      <div className="an-fleet-summary">
        <KpiStrip columns={4} tiles={summaryTiles} />
      </div>

      <section aria-labelledby="fleet-agents-heading">
        <h2 className="an-kicker an-fleet-heading" id="fleet-agents-heading">Agents</h2>
        {agents.length === 0 ? (
          <div className="an-card"><div className="an-empty">No agent-enabled roles yet.</div></div>
        ) : (
          <div className="an-fleet-agents">
            {agents.map((agent) => <AgentCard key={agent.role_id} agent={agent} />)}
          </div>
        )}
      </section>

      <article className="an-card an-fleet-activity">
        <header className="ch">
          <h3 className="ct2">Recent activity</h3>
          <button
            type="button"
            className="an-fleet-log-link"
            onClick={onOpenDecisionLog}
            aria-label="View decision log"
          >
            View decision log <ArrowUpRight size={13} aria-hidden="true" />
          </button>
        </header>
        {entries.length === 0 ? (
          <div className="an-empty">No agent activity yet.</div>
        ) : (
          <div className="an-feed" role="list">
            {entries.map((entry, index) => (
              <div className="fi" role="listitem" key={`${entry.kind || 'activity'}-${entry.id ?? entry.created_at ?? index}`}>
                <ActivityGlyph kind={entry.kind} />
                <span>
                  {entry.role_name ? <span className="rc">{entry.role_name}</span> : null}
                  <span className="fbody">{entry.title || 'Agent activity'}</span>
                  <span className="ft">{fmtRelShort(entry.created_at)}</span>
                  {entry.detail ? <span className="fdetail">{entry.detail}</span> : null}
                </span>
              </div>
            ))}
          </div>
        )}
      </article>

      <div className="an-privacy">
        <Lock size={14} className="ti" aria-hidden="true" />
        <span>Agent activity and billed spend are visible here. Internal system details stay private.</span>
      </div>
    </div>
  );
};

export const FleetTab = ({ onOpenDecisionLog }) => {
  const [panel, setPanel] = useState(null);
  const [activity, setActivity] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    let panelTimer = null;
    let activityTimer = null;

    const loadPanel = async () => {
      try {
        const response = await agentApi.panel();
        if (!cancelledRef.current) {
          setPanel(response?.data || null);
          setError(null);
        }
      } catch {
        if (!cancelledRef.current) setError('Could not load the agent fleet.');
      } finally {
        if (!cancelledRef.current) {
          setLoaded(true);
          panelTimer = setTimeout(loadPanel, PANEL_POLL_MS);
        }
      }
    };

    const loadActivity = async () => {
      try {
        const response = await agentApi.orgActivity({ limit: 12 });
        if (!cancelledRef.current) setActivity(response?.data?.entries || []);
      } catch {
        // Activity is best-effort; the fleet summary remains useful without it.
      } finally {
        if (!cancelledRef.current) activityTimer = setTimeout(loadActivity, ACTIVITY_POLL_MS);
      }
    };

    loadPanel();
    loadActivity();
    return () => {
      cancelledRef.current = true;
      if (panelTimer) clearTimeout(panelTimer);
      if (activityTimer) clearTimeout(activityTimer);
    };
  }, []);

  if (!loaded && !panel) {
    return (
      <div className="an-tabpanel">
        <div className="an-empty"><Spinner size={14} className="!text-current" /> Loading agent fleet…</div>
      </div>
    );
  }
  if (error && !panel) {
    return <div className="an-tabpanel"><div className="an-empty">{error}</div></div>;
  }

  return <FleetView panel={panel} activity={activity} onOpenDecisionLog={onOpenDecisionLog} />;
};

export default FleetTab;
