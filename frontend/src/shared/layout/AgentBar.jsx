import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play, Sparkles } from 'lucide-react';

import { agent as agentApi } from '../api';

const POLL_INTERVAL_MS = 30_000;
const AMBER_THRESHOLD_PCT = 80;

const formatDollars = (cents) => {
  if (cents == null) return null;
  const dollars = Number(cents) / 100;
  return dollars >= 100 ? `$${Math.round(dollars)}` : `$${dollars.toFixed(2)}`;
};

const formatTick = (status) => {
  if (!status) return null;
  const last = status.last_activity || status.current_run;
  if (!last) return 'Idle · waiting for new candidates.';
  if (typeof last === 'string') return last;
  const summary = last.summary || last.title || last.event || last.kind;
  if (!summary) return null;
  if (last.relative_time || last.ago) {
    return `${summary} · ${last.relative_time || last.ago}`;
  }
  return summary;
};

// Internal hook — polls /roles/{roleId}/agent/status every POLL_INTERVAL_MS
// and pauses when the tab is hidden so we don't burn quota in background tabs.
const useAgentStatus = (roleId) => {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    if (!roleId) {
      setStatus(null);
      return undefined;
    }
    cancelledRef.current = false;

    const fetchOnce = async () => {
      try {
        const res = await agentApi.status(roleId);
        if (!cancelledRef.current) {
          setStatus(res?.data || null);
          setError(null);
        }
      } catch (err) {
        if (!cancelledRef.current) {
          setError(err);
        }
      }
    };

    fetchOnce();
    let timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      fetchOnce();
    }, POLL_INTERVAL_MS);

    const onVisibility = () => {
      if (typeof document !== 'undefined' && !document.hidden) fetchOnce();
    };
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
      timer = null;
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [roleId]);

  return { status, error };
};

// AgentBar — purple aurora strip rendered at the top of recruiter page
// content (NOT inside Shell, so pages can omit it on screens where the
// agent narrative dominates the layout, e.g. Reporting v2).
//
// Pages can either:
//   1. Pass `roleId` and let the bar poll /roles/{roleId}/agent/status, or
//   2. Pass explicit `pending`, `spentCents`, `budgetCents`, `tick`, `paused`
//      (used by org-level pages until an org-aggregate endpoint exists).
export const AgentBar = ({
  roleId = null,
  paused: pausedProp = false,
  pending: pendingProp,
  spentCents: spentCentsProp,
  budgetCents: budgetCentsProp,
  tick: tickProp,
  inFlight: inFlightProp = false,
  onRunNow,
  onPause,
}) => {
  const { status } = useAgentStatus(roleId);

  const paused = status?.paused ?? pausedProp;
  const pending = status?.pending_decisions ?? pendingProp ?? 0;
  const spentCents = status?.monthly_spent_cents ?? spentCentsProp ?? 0;
  const budgetCents = status?.monthly_budget_cents ?? budgetCentsProp ?? 5000;
  const tick = formatTick(status) || tickProp || 'Agent is monitoring.';
  const inFlight = Boolean(status?.current_run) || inFlightProp;

  const pct = useMemo(() => {
    if (!budgetCents || budgetCents <= 0) return 0;
    return Math.min(100, Math.round((spentCents / budgetCents) * 100));
  }, [spentCents, budgetCents]);

  const amber = pct >= AMBER_THRESHOLD_PCT;
  const spentLabel = formatDollars(spentCents);
  const budgetLabel = formatDollars(budgetCents);

  return (
    <div
      className={`mc-agent-bar ${amber ? 'is-amber' : ''}`.trim()}
      role="status"
      aria-live="polite"
    >
      <div className="mc-agent-row">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: '1 1 320px', minWidth: 0 }}>
          <div className="mc-agent-glyph">
            <Sparkles size={18} strokeWidth={2} style={{ color: '#fff' }} />
            {inFlight ? <span className="mc-pulse-ring" aria-hidden="true" /> : null}
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="mc-agent-title">
              <span>{paused ? 'Agentic mode paused' : 'Agentic mode is ON'}</span>
              {pending > 0 ? (
                <span className="mc-agent-pending">{pending} awaiting your review</span>
              ) : null}
            </div>
            <div className="mc-agent-tick">{tick}</div>
          </div>
        </div>
        <div className="mc-agent-budget">
          <div className="mc-agent-budget-row">
            <span>This month</span>
            <span style={{ fontWeight: 600 }}>
              {spentLabel} / {budgetLabel}
            </span>
          </div>
          <div className="mc-agent-budget-bar">
            <i style={{ width: `${pct}%` }} />
          </div>
        </div>
        <div className="mc-agent-actions">
          <button type="button" className="mc-agent-btn" onClick={onRunNow} disabled={!onRunNow}>
            <Play size={12} strokeWidth={2} fill="#fff" />
            Run now
          </button>
          <button type="button" className="mc-agent-btn is-ghost" onClick={onPause} disabled={!onPause}>
            <Pause size={12} strokeWidth={2} />
            {paused ? 'Resume' : 'Pause'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default AgentBar;
