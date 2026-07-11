import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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

// Polls /roles/{roleId}/agent/status every POLL_INTERVAL_MS and pauses
// when the tab is hidden so we don't burn quota in background tabs.
// Exported so callers (JobPipelinePage budget tile, role detail rail)
// can read the same payload AgentBar consumes — `monthly_spent_cents`,
// `monthly_budget_cents`, `pending_decisions`, `last_activity`, etc.
export const useAgentStatus = (roleId) => {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const cancelledRef = useRef(false);

  // Imperative refetch so callers can reconcile right after a mutation
  // (pause/resume/activate) instead of waiting up to POLL_INTERVAL_MS for the
  // next poll. Stable per roleId.
  const refetch = useCallback(async () => {
    if (!roleId) return;
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
  }, [roleId]);

  useEffect(() => {
    if (!roleId) {
      setStatus(null);
      return undefined;
    }
    cancelledRef.current = false;

    refetch();
    let timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      refetch();
    }, POLL_INTERVAL_MS);

    const onVisibility = () => {
      if (typeof document !== 'undefined' && !document.hidden) refetch();
    };
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
      timer = null;
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [roleId, refetch]);

  // `setStatus` is exposed so callers can optimistically patch the polled
  // payload (e.g. clear `paused_at` the instant the user clicks Resume) — the
  // strip derives on/paused from `paused_at`, so without this the box stays
  // PAUSED until the next poll even though the PATCH already fired.
  return { status, error, setStatus, refetch };
};

// Org rollup for the global header strip. Reads the single purpose-built
// aggregate GET /agent/org-status (counts + sums computed server-side) instead
// of fanning out /roles + up to 25 per-role /agent/status calls
// every 30s on every page — that fan-out issued up to 26 requests/poll against
// a us-east4 API just to derive an on/paused boolean + budget bar.
//
// The returned shape is unchanged for consumers (Shell, JobsPage, AgentHeader):
// `paused`, `pending_decisions`, `monthly_spent_cents`, `monthly_budget_cents`,
// `current_run`, `last_activity` (pre-annotated summary), `paused_reason`,
// `active_role_count`. org-status splits enabled roles into active (running)
// vs paused counts, so `active_role_count` = running + paused (total enabled)
// and `paused` = all-enabled-paused, matching the old fan-out semantics.
export const useAgentStatusOrg = () => {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const cancelledRef = useRef(false);

  const fetchOnce = useCallback(async () => {
    try {
        const res = await agentApi.orgStatus();
        if (cancelledRef.current) return;
        const data = res?.data || {};
        const running = Number(data.active_role_count || 0);
        const pausedRoles = Number(data.paused_role_count || 0);
        const enabledTotal = running + pausedRoles;

        setStatus({
          // All enabled roles paused → bar reads "Agent mode paused".
          paused: enabledTotal > 0 && running === 0,
          any_paused: pausedRoles > 0,
          paused_reason: data.paused_reason || null,
          pending_decisions: Number(data.pending_decisions || 0),
          monthly_spent_cents: Number(data.org_budget_spent_cents || 0),
          monthly_budget_cents: Number(data.org_budget_cap_cents || 0),
          current_run: data.current_run || null,
          last_activity: data.last_activity || null,
          // Total agent-enabled roles (running + paused) — the bar renders
          // whenever any role has the agent on, paused or not.
          active_role_count: enabledTotal,
        });
        setError(null);
      } catch (err) {
        if (!cancelledRef.current) setError(err);
      }
    }, []);

  useEffect(() => {
    cancelledRef.current = false;
    fetchOnce();
    let timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      fetchOnce();
    }, POLL_INTERVAL_MS);

    const onVisibility = () => {
      if (typeof document !== 'undefined' && !document.hidden) fetchOnce();
    };
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibility);
    }

    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
      timer = null;
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibility);
      }
    };
  }, [fetchOnce]);

  return { status, error, refetch: fetchOnce };
};

// AgentBar — purple aurora strip rendered globally inside Shell on
// recruiter routes. Three usage modes:
//   1. `roleId` set → polls /roles/{id}/agent/status (used on the role
//      detail "cockpit" page so role-only metrics replace the org rollup).
//   2. `scope="org"` (default in Shell) → fans out across /roles and
//      aggregates client-side per HANDOFF Phase 1 (no BE aggregate yet).
//   3. Explicit `pending`/`spentCents`/`budgetCents`/`tick`/`paused` →
//      used by the static landing-page mock and tests.
//
// Returns null in scope=org mode if the org has no roles with the agent
// enabled (the bar is the agent's voice — no agent, no bar).
export const AgentBar = ({
  roleId = null,
  scope = roleId ? 'role' : 'org',
  paused: pausedProp = false,
  pending: pendingProp,
  spentCents: spentCentsProp,
  budgetCents: budgetCentsProp,
  tick: tickProp,
  inFlight: inFlightProp = false,
  onRunNow,
  onPause,
  hideWhenOrgIdle = true,
}) => {
  const roleResult = useAgentStatus(roleId);
  const orgResult = useAgentStatusOrg();
  const useOrgScope = !roleId && scope === 'org';
  const status = useOrgScope ? orgResult.status : roleResult.status;

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

  // Don't render in scope=org mode while we're still loading roles, or
  // if there are no agent-enabled roles. Avoids flashing an empty $0 / $0
  // bar on every recruiter page for orgs that haven't turned the agent on.
  // (Early-return must come after every hook to keep hook order stable.)
  if (useOrgScope && hideWhenOrgIdle) {
    if (status == null) return null;
    if (!status.active_role_count) return null;
  }

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
              <span>{paused ? 'Agent mode paused' : 'Agent mode is ON'}</span>
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
