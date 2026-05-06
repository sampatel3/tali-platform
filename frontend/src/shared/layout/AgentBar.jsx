import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play, Sparkles } from 'lucide-react';

import { agent as agentApi, roles as rolesApi } from '../api';

const POLL_INTERVAL_MS = 30_000;
const AMBER_THRESHOLD_PCT = 80;
const ROLE_FANOUT_LIMIT = 25;

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

// Fan out across /roles client-side and aggregate into a single status
// object that mimics the per-role response shape. HANDOFF Phase 1 explicitly
// allows this in lieu of a backend org-aggregate endpoint:
//   "Org-level data via client-side fan-out across `/roles` (acceptable for
//   typical org size). cost_per_decision and eom_projection derived
//   client-side from existing monthly_spent_cents + decision count + day-of-
//   month — no backend change required."
//
// We cap the fan-out at ROLE_FANOUT_LIMIT roles (skipping ones the agent is
// not enabled on) so the aggregate stays bounded for huge orgs. If/when the
// backend ships an org-aggregate endpoint, the bar can switch to that without
// changing its render shape.
const useAgentStatusOrg = () => {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;

    const fetchOnce = async () => {
      try {
        const rolesRes = await rolesApi.list();
        const allRoles = Array.isArray(rolesRes?.data) ? rolesRes.data : [];
        // Only roles where the agent is currently enabled count toward the
        // org bar — they're the only ones spending budget or queueing
        // decisions. Cap to bound fan-out cost.
        const activeRoles = allRoles
          .filter((role) => role && role.agentic_mode_enabled)
          .slice(0, ROLE_FANOUT_LIMIT);

        if (activeRoles.length === 0) {
          if (!cancelledRef.current) {
            setStatus({
              paused: false,
              pending_decisions: 0,
              monthly_spent_cents: 0,
              monthly_budget_cents: 0,
              current_run: null,
              last_activity: null,
              active_role_count: 0,
            });
            setError(null);
          }
          return;
        }

        const settled = await Promise.allSettled(
          activeRoles.map((role) => agentApi.status(role.id))
        );

        if (cancelledRef.current) return;

        let monthlySpent = 0;
        let monthlyBudget = 0;
        let pending = 0;
        let allPaused = true;
        let anyPaused = false;
        let currentRun = null;
        let latestActivity = null;
        let latestActivityRole = null;
        let latestActivityTs = -Infinity;

        const tsOf = (activity) => {
          if (!activity || typeof activity !== 'object') return -Infinity;
          const raw = activity.at || activity.timestamp || activity.occurred_at;
          if (!raw) return -Infinity;
          const parsed = Date.parse(String(raw));
          return Number.isNaN(parsed) ? -Infinity : parsed;
        };

        settled.forEach((entry, idx) => {
          if (entry.status !== 'fulfilled') return;
          const data = entry.value?.data || {};
          const role = activeRoles[idx];
          monthlySpent += Number(data.monthly_spent_cents || 0);
          monthlyBudget += Number(data.monthly_budget_cents || 0);
          pending += Number(data.pending_decisions || 0);
          if (data.paused) anyPaused = true;
          else allPaused = false;
          if (!currentRun && data.current_run) currentRun = data.current_run;
          const ts = tsOf(data.last_activity);
          if (ts > latestActivityTs) {
            latestActivityTs = ts;
            latestActivity = data.last_activity;
            latestActivityRole = role?.name || null;
          }
        });

        // Annotate the aggregated tick so it reads "<event> · <role> · <ago>"
        // instead of the per-role bar's "<event> · <ago>".
        let annotatedActivity = latestActivity;
        if (latestActivity && latestActivityRole) {
          annotatedActivity = {
            ...latestActivity,
            summary: latestActivity.summary
              ? `${latestActivity.summary} · ${latestActivityRole}`
              : latestActivityRole,
          };
        }

        if (!cancelledRef.current) {
          setStatus({
            paused: activeRoles.length > 0 && allPaused,
            any_paused: anyPaused,
            pending_decisions: pending,
            monthly_spent_cents: monthlySpent,
            monthly_budget_cents: monthlyBudget,
            current_run: currentRun,
            last_activity: annotatedActivity,
            active_role_count: activeRoles.length,
          });
          setError(null);
        }
      } catch (err) {
        if (!cancelledRef.current) setError(err);
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
  }, []);

  return { status, error };
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
