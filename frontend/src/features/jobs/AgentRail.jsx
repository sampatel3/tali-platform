import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Bot } from 'lucide-react';

import { agent as agentApi } from '../../shared/api';
import { AgentLoop } from '../../shared/motion';

const fmtUsd = (cents) => {
  if (cents == null) return '$0';
  const dollars = Number(cents) / 100;
  return dollars >= 100 ? `$${Math.round(dollars)}` : `$${dollars.toFixed(2)}`;
};

const useStatus = (roleId, fallback = null) => {
  const [status, setStatus] = useState(fallback);
  const cancelled = useRef(false);

  useEffect(() => {
    if (!roleId) return undefined;
    cancelled.current = false;
    const fetchOnce = async () => {
      try {
        const res = await agentApi.status(roleId);
        if (!cancelled.current) setStatus(res?.data || null);
      } catch {
        // swallow — keep prior snapshot
      }
    };
    fetchOnce();
    const timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      fetchOnce();
    }, 30_000);
    return () => {
      cancelled.current = true;
      clearInterval(timer);
    };
  }, [roleId]);

  return status;
};

// Sparkline of daily spend over the last 18 days. Falls back to a flat
// placeholder track when the backend doesn't yet return a series.
const Sparkline = ({ series }) => {
  const data = Array.isArray(series) && series.length >= 2 ? series : null;
  if (!data) {
    return (
      <svg viewBox="0 0 200 36" className="mc-rail-spark" preserveAspectRatio="none">
        <path d="M0 30 L200 30" fill="none" stroke="var(--line)" strokeWidth="1.5" strokeDasharray="3 4" />
      </svg>
    );
  }
  const max = Math.max(...data, 1);
  const w = 200;
  const h = 36;
  const step = w / (data.length - 1);
  const points = data.map((v, i) => `${i * step},${h - (v / max) * (h - 4) - 2}`).join(' L ');
  const closed = `M ${points} L ${w},${h} L 0,${h} Z`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="mc-rail-spark" preserveAspectRatio="none">
      <path d={closed} fill="var(--purple)" opacity="0.10" />
      <path d={`M ${points}`} fill="none" stroke="var(--purple)" strokeWidth="1.5" />
    </svg>
  );
};

// AgentRail — sticky left rail on the role detail. Aggregates the agent
// status, monthly spend with EOM projection, "this week" decision counts,
// and a sparkline of daily spend.
export const AgentRail = ({ roleId, onOpenSettings, onOpenFeed, onPending, fallback = null }) => {
  const status = useStatus(roleId, fallback);

  const dayOfMonth = new Date().getDate();
  const daysInMonth = new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0).getDate();
  const spentCents = status?.monthly_spent_cents ?? 0;
  const budgetCents = status?.monthly_budget_cents ?? 5000;
  const pending = status?.pending_decisions ?? 0;
  const decisionsWeek = status?.decisions_week ?? Math.max(pending, 0);
  const autoAdvanced = status?.auto_advanced_week ?? 0;
  const autoRejected = status?.auto_rejected_week ?? 0;
  const flagged = status?.flagged_week ?? pending;
  const lastActivity = status?.last_activity?.summary || status?.last_activity || 'Idle · waiting for new candidates.';
  const lastRunRel = status?.last_activity?.relative_time || status?.last_run_rel || 'just now';

  const pct = budgetCents > 0 ? Math.min(100, Math.round((spentCents / budgetCents) * 100)) : 0;
  const projectedCents = useMemo(() => {
    if (!dayOfMonth) return spentCents;
    return Math.round((spentCents * daysInMonth) / dayOfMonth);
  }, [spentCents, dayOfMonth, daysInMonth]);
  const projectedPct = budgetCents > 0 ? Math.min(120, Math.round((projectedCents / budgetCents) * 100)) : 0;
  const tone = pct >= 95 ? 'red' : pct >= 80 ? 'amber' : 'ok';
  const costPerDecision = decisionsWeek > 0 ? Math.round(spentCents / decisionsWeek) : null;

  return (
    <aside className={`mc-rail tone-${tone}`} aria-label="Agent status">
      <header className="mc-rail-head">
        <div className="mc-rail-orb">
          <AgentLoop kind="ring" className="mc-rail-orb-pulse" />
          <Bot size={16} strokeWidth={2} style={{ color: '#fff' }} />
        </div>
        <div>
          <div className="mc-rail-title">Agent</div>
          <div className="mc-rail-state">
            <span className="mc-rail-dot" />
            Working — {lastRunRel}
          </div>
        </div>
      </header>

      <div className="mc-rail-tick">
        <AgentLoop kind="pulse" className="mc-rail-tick-pulse" />
        {lastActivity}
      </div>

      {pending > 0 ? (
        <button
          type="button"
          className="mc-rail-pending"
          onClick={onPending}
          aria-label={`${pending} decisions await your review`}
        >
          <div className="mc-rail-pending-num">{pending}</div>
          <div className="mc-rail-pending-label">
            decisions await<br />
            <strong>your review →</strong>
          </div>
        </button>
      ) : null}

      <section className="mc-rail-budget">
        <div className="mc-rail-budget-head">
          <span className="mc-kicker is-mute">Monthly budget</span>
          <button type="button" className="taali-text-btn mc-rail-budget-edit" onClick={onOpenSettings}>
            Edit
          </button>
        </div>
        <div className="mc-rail-budget-amount">
          <span className="mc-rail-budget-spent">{fmtUsd(spentCents)}</span>
          <span className="mc-rail-budget-of">of {fmtUsd(budgetCents)}</span>
        </div>
        <div className="mc-rail-budget-meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div className="mc-rail-budget-fill" style={{ width: `${pct}%` }} />
          <div className="mc-rail-budget-projection" style={{ width: `${Math.min(100, projectedPct)}%` }} />
          <div className="mc-rail-budget-mark" style={{ left: '80%' }}>
            <span>80%</span>
          </div>
        </div>
        <div className="mc-rail-budget-stats">
          <div>
            <div className="mc-rail-stat-num">{pct}%</div>
            <div className="mc-rail-stat-lab">used · day {dayOfMonth}</div>
          </div>
          <div>
            <div
              className="mc-rail-stat-num"
              style={{ color: projectedPct > 100 ? 'var(--red)' : projectedPct > 90 ? 'var(--amber)' : 'var(--ink)' }}
            >
              {projectedPct}%
            </div>
            <div className="mc-rail-stat-lab">projected · EOM</div>
          </div>
          <div>
            <div className="mc-rail-stat-num">{costPerDecision != null ? fmtUsd(costPerDecision) : '—'}</div>
            <div className="mc-rail-stat-lab">/ decision</div>
          </div>
        </div>
        <Sparkline series={status?.daily_spend_cents_18d} />
        <div className="mc-rail-spark-cap">Daily spend · last 18 days</div>
      </section>

      <section className="mc-rail-week">
        <div className="mc-kicker is-mute">This week</div>
        <div className="mc-rail-week-grid">
          <div>
            <span className="num">{decisionsWeek}</span>
            <span className="lab">decisions</span>
          </div>
          <div>
            <span className="num">{autoAdvanced}</span>
            <span className="lab">advanced</span>
          </div>
          <div>
            <span className="num">{autoRejected}</span>
            <span className="lab">rejected</span>
          </div>
          <div>
            <span className="num">{flagged}</span>
            <span className="lab">flagged</span>
          </div>
        </div>
      </section>

      {onOpenFeed ? (
        <button type="button" className="mc-rail-feed" onClick={onOpenFeed}>
          View full activity feed →
        </button>
      ) : null}
    </aside>
  );
};

export default AgentRail;
