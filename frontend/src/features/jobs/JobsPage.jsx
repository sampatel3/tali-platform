import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  ArrowRight,
  Filter,
  RefreshCw,
  Star,
} from 'lucide-react';

import * as apiClient from '../../shared/api';
import { AgentHeader, buildAgentPropFromStatus } from '../../shared/layout/AgentHeader';
import { useAgentStatusOrg } from '../../shared/layout/AgentBar';
import { RoleSheet } from '../candidates/RoleSheet';
import { trimOrUndefined } from '../candidates/candidatesUiUtils';
import {
  EmptyState,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import {
  SyncPulse,
  WorkableLogo,
  WorkableTag,
  formatRelativeDateTime,
  resolveSyncHealth,
} from '../../shared/ui/RecruiterDesignPrimitives';
import {
  JOBS_SHOWCASE,
  JOBS_SHOWCASE_ORG,
} from '../demo/productWalkthroughModels';

const STAGES = [
  { key: 'applied', label: 'Applied' },
  { key: 'invited', label: 'Invited' },
  { key: 'in_assessment', label: 'Assessing' },
  { key: 'review', label: 'Review' },
  { key: 'advanced', label: 'Advanced' },
  // `rejected` is an application_outcome (not a pipeline_stage), counted
  // separately by the backend across every stage.
  { key: 'rejected', label: 'Rejected' },
];

const SOURCE_FILTERS = [
  { key: 'all', label: 'All roles' },
  { key: 'live', label: 'Live' },
  { key: 'workable', label: 'From Workable' },
  { key: 'manual', label: 'Created in Taali' },
  { key: 'active', label: 'Active' },
  { key: 'draft', label: 'Draft' },
];

const isRoleDraft = (role) => (
  !role?.workable_job_id
  && !role?.job_spec_present
  && Number(role?.applications_count || 0) === 0
);

// Live == the Workable job is published (actively recruiting / posted to job
// boards). Manual/Taali roles have no Workable state and are never "live".
const isRoleLive = (role) => String(role?.workable_job_state || '').toLowerCase() === 'published';

// A Workable role that isn't live is a filled/closed/draft posting — greyed
// out on the grid. Manual roles are never greyed (they aren't Workable-managed).
const isRoleDimmed = (role) => (
  String(role?.source || '').toLowerCase() === 'workable' && !isRoleLive(role)
);

const filterRoleBySource = (role, sourceFilter) => {
  if (sourceFilter === 'live') return isRoleLive(role);
  if (sourceFilter === 'workable') return String(role?.source || '').toLowerCase() === 'workable';
  if (sourceFilter === 'manual') return String(role?.source || '').toLowerCase() !== 'workable';
  if (sourceFilter === 'active') return Number(role?.active_candidates_count || 0) > 0;
  if (sourceFilter === 'draft') return isRoleDraft(role);
  return true;
};

const buildSourceCounts = (roles) => roles.reduce((acc, role) => {
  acc.all += 1;
  if (isRoleLive(role)) acc.live += 1;
  if (String(role?.source || '').toLowerCase() === 'workable') acc.workable += 1;
  if (String(role?.source || '').toLowerCase() !== 'workable') acc.manual += 1;
  if (Number(role?.active_candidates_count || 0) > 0) acc.active += 1;
  if (isRoleDraft(role)) acc.draft += 1;
  return acc;
}, {
  all: 0,
  live: 0,
  workable: 0,
  manual: 0,
  active: 0,
  draft: 0,
});

const extractRunId = (value) => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const match = value.match(/run_id=(\d+)/i);
    return match ? Number(match[1]) : null;
  }
  if (value && typeof value === 'object') {
    if (typeof value.run_id === 'number' && Number.isFinite(value.run_id)) return value.run_id;
    if (typeof value.detail === 'string') return extractRunId(value.detail);
  }
  return null;
};

const mergeSyncStatusIntoOrg = (org, payload = {}) => {
  if (!org) return org;
  return {
    ...org,
    workable_last_sync_at: payload.workable_last_sync_at ?? org.workable_last_sync_at,
    workable_last_sync_status: payload.workable_last_sync_status ?? org.workable_last_sync_status,
    workable_last_sync_summary: payload.workable_last_sync_summary ?? org.workable_last_sync_summary,
    workable_sync_progress: payload.workable_sync_progress ?? org.workable_sync_progress,
    workable_sync_started_at: payload.started_at ?? org.workable_sync_started_at,
  };
};

const formatCountdown = (value) => {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  const diffMs = parsed.getTime() - Date.now();
  if (diffMs <= 0) return 'Due now';
  const totalMinutes = Math.round(diffMs / 60000);
  if (totalMinutes < 60) return `${totalMinutes} min`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours >= 24) {
    const days = Math.floor(hours / 24);
    return `${days}d ${hours % 24}h`;
  }
  return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
};

const getSyncSummaryValue = (summary, keys, fallback = 0) => {
  for (const key of keys) {
    const value = summary?.[key];
    if (value != null && value !== '') return value;
  }
  return fallback;
};

const getRoleBadgeLabel = (role) => {
  if (String(role?.source || '').toLowerCase() === 'workable') return null;
  if (isRoleDraft(role)) return 'Draft';
  return 'Role';
};

// Maps the org-aggregate /agent/status payload (or the showcase fixture) into
// the shape AgentHeader's right-side panel expects. Activation on the Jobs
// list is intentionally per-role (each role has its own budget cap), so the
// OFF state on this page guides the user to open a role rather than firing
// a single org-wide activate.
const useJobsHeaderAgent = (roles, isShowcase) => {
  const { status } = useAgentStatusOrg();
  return useMemo(() => {
    if (isShowcase) {
      return {
        on: true,
        paused: false,
        pending: 2,
        spentCents: 1820,
        budgetCents: 5000,
        tick: 'Scoring 14 new candidates · just now',
        inFlight: true,
      };
    }
    const anyEnabled = roles.some((role) => role?.agentic_mode_enabled);
    if (!status) {
      // Pre-fetch placeholder. Show OFF until the org-aggregate payload lands.
      return {
        on: false,
        paused: false,
        pending: 0,
        spentCents: 0,
        budgetCents: anyEnabled ? 5000 : 0,
        tick: null,
        inFlight: false,
      };
    }
    return buildAgentPropFromStatus(status, { isEnabled: status.active_role_count > 0 });
  }, [status, roles, isShowcase]);
};

export const JobsPage = ({ onNavigate: rawOnNavigate, NavComponent = null }) => {
  const rolesApi = apiClient.roles;
  const orgApi = apiClient.organizations;
  const tasksApi = 'tasks' in apiClient ? apiClient.tasks : null;
  const [searchParams] = useSearchParams();
  const isShowcase = searchParams.get('demo') === '1' && searchParams.get('showcase') === '1';
  const onNavigate = isShowcase ? () => {} : rawOnNavigate;

  const [roles, setRoles] = useState([]);
  const [orgData, setOrgData] = useState(null);
  const [allTasks, setAllTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncRunId, setSyncRunId] = useState(null);
  const [error, setError] = useState('');
  // HANDOFF v2 §4 — Live agent spend across roles for the BUDGET USED tile.
  // Fan-out to /roles/{id}/agent/status for every agent-enabled role. Capped
  // at AGENT_SPEND_FANOUT_LIMIT to keep the request count bounded; orgs with
  // more agentic roles fall back to the cap-only display.
  const [agentSpendByRole, setAgentSpendByRole] = useState({});
  const [sourceFilter, setSourceFilter] = useState('all');
  const [roleSheetOpen, setRoleSheetOpen] = useState(false);
  const [savingRole, setSavingRole] = useState(false);
  const [roleSheetError, setRoleSheetError] = useState('');

  const loadJobsHub = useCallback(async () => {
    if (isShowcase) {
      setRoles(JOBS_SHOWCASE);
      setOrgData(JOBS_SHOWCASE_ORG);
      // Show a brief "Syncing now" pulse on first load, then settle into the
      // static "Synced X min ago" state. Pure visual — no API calls fire.
      setSyncing(true);
      setSyncRunId(null);
      setError('');
      setLoading(false);
      window.setTimeout(() => setSyncing(false), 2500);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const [rolesRes, orgRes] = await Promise.all([
        rolesApi.list({ include_pipeline_stats: true }),
        orgApi.get(),
      ]);
      const nextRoles = Array.isArray(rolesRes?.data) ? rolesRes.data : [];
      let nextOrgData = orgRes?.data || null;
      let nextSyncing = false;
      let nextRunId = null;
      if (nextOrgData?.workable_connected) {
        try {
          const statusRes = await orgApi.getWorkableSyncStatus();
          const statusPayload = statusRes?.data || {};
          nextOrgData = mergeSyncStatusIntoOrg(nextOrgData, statusPayload);
          nextSyncing = Boolean(statusPayload.sync_in_progress);
          nextRunId = statusPayload.run_id ?? null;
        } catch {
          nextSyncing = false;
          nextRunId = null;
        }
      }
      setRoles(nextRoles);
      setOrgData(nextOrgData);
      setSyncing(nextSyncing);
      setSyncRunId(nextSyncing ? nextRunId : null);
    } catch {
      setRoles([]);
      setOrgData(null);
      setSyncing(false);
      setSyncRunId(null);
      setError('Failed to load jobs.');
    } finally {
      setLoading(false);
    }
  }, [isShowcase, orgApi, rolesApi]);

  useEffect(() => {
    void loadJobsHub();
  }, [loadJobsHub]);

  // Fan-out /roles/{id}/agent/status across agent-enabled roles for the
  // BUDGET USED tile. Bounded to ROLE_FANOUT_LIMIT to keep the request
  // count predictable for orgs with many agentic roles. Polls every 60s
  // and pauses on hidden tabs.
  useEffect(() => {
    if (isShowcase) return undefined;
    const ROLE_FANOUT_LIMIT = 20;
    const POLL_MS = 60_000;
    const targets = roles
      .filter((role) => role && role.id != null && role.agentic_mode_enabled)
      .slice(0, ROLE_FANOUT_LIMIT);
    if (targets.length === 0) {
      setAgentSpendByRole({});
      return undefined;
    }
    let cancelled = false;
    const fetchSpend = async () => {
      try {
        const settled = await Promise.allSettled(
          targets.map((role) => apiClient.agent.status(role.id)),
        );
        if (cancelled) return;
        const next = {};
        settled.forEach((entry, idx) => {
          if (entry.status !== 'fulfilled') return;
          const data = entry.value?.data || {};
          next[targets[idx].id] = {
            monthly_spent_cents: Number(data.monthly_spent_cents || 0),
            monthly_budget_cents: Number(data.monthly_budget_cents || 0),
          };
        });
        setAgentSpendByRole(next);
      } catch {
        // Quiet failure — tile falls back to cap-only.
      }
    };
    void fetchSpend();
    const handle = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      void fetchSpend();
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [isShowcase, roles]);

  useEffect(() => {
    if (isShowcase) {
      setAllTasks([]);
      return undefined;
    }
    if (!tasksApi?.list) {
      setAllTasks([]);
      return undefined;
    }
    let cancelled = false;
    const loadTasks = async () => {
      try {
        const res = await tasksApi.list();
        if (!cancelled) {
          setAllTasks(Array.isArray(res?.data) ? res.data : []);
        }
      } catch {
        if (!cancelled) {
          setAllTasks([]);
        }
      }
    };
    void loadTasks();
    return () => {
      cancelled = true;
    };
  }, [isShowcase, tasksApi]);

  useEffect(() => {
    if (!syncRunId) return undefined;
    let cancelled = false;
    const pollStatus = async () => {
      try {
        const res = await orgApi.getWorkableSyncStatus(syncRunId);
        if (cancelled) return;
        const payload = res?.data || {};
        const inProgress = Boolean(payload.sync_in_progress);
        setOrgData((current) => mergeSyncStatusIntoOrg(current, payload));
        setSyncing(inProgress);
        if (!inProgress) {
          setSyncRunId(null);
          await loadJobsHub();
          return;
        }
        setSyncRunId(payload.run_id ?? syncRunId);
      } catch {
        if (cancelled) return;
        setSyncing(false);
        setSyncRunId(null);
      }
    };
    void pollStatus();
    const intervalId = window.setInterval(() => {
      void pollStatus();
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [loadJobsHub, orgApi, syncRunId]);

  const handleSyncNow = async () => {
    if (isShowcase) return;
    setError('');
    setSyncing(true);
    try {
      const res = await orgApi.syncWorkable();
      const payload = res?.data || {};
      const runId = extractRunId(payload);
      if (payload?.status === 'already_running') {
        if (runId != null) setSyncRunId(runId);
        setSyncing(true);
        return;
      }
      if (runId) {
        setSyncRunId(runId);
        return;
      }
      setSyncing(false);
      await loadJobsHub();
    } catch (err) {
      const status = err?.response?.status;
      const runId = extractRunId(err?.response?.data) ?? extractRunId(err?.response?.data?.detail);
      if (status === 409 || runId != null) {
        try {
          const statusRes = await orgApi.getWorkableSyncStatus(runId ?? undefined);
          const payload = statusRes?.data || {};
          setOrgData((current) => mergeSyncStatusIntoOrg(current, payload));
          const inProgress = Boolean(payload.sync_in_progress);
          setSyncing(inProgress);
          setSyncRunId(inProgress ? (payload.run_id ?? runId ?? null) : null);
          if (!inProgress) {
            await loadJobsHub();
          }
          return;
        } catch {
          setSyncing(true);
          if (runId != null) setSyncRunId(runId);
          return;
        }
      }
      setSyncing(false);
      setError('Workable sync could not be started.');
    }
  };

  const sourceCounts = useMemo(() => buildSourceCounts(roles), [roles]);
  const workableRolesCount = sourceCounts.workable;
  const workableSummary = orgData?.workable_last_sync_summary || {};
  const workableHealth = resolveSyncHealth({
    status: orgData?.workable_last_sync_status,
    lastSyncedAt: orgData?.workable_last_sync_at,
  });
  const workableHealthLabel = workableHealth === 'error'
    ? 'Attention needed'
    : workableHealth === 'stale'
      ? 'Needs refresh'
      : 'Healthy';
  const nextPullAt = useMemo(() => {
    // Jobs metadata syncs every 15 minutes (sync_workable_jobs Beat task).
    // Candidate cadences vary per role (starred/agent/nightly) — those
    // surface on the role page itself rather than here.
    const lastSyncAt = orgData?.workable_last_sync_at;
    if (!lastSyncAt) return null;
    const parsed = new Date(lastSyncAt);
    if (Number.isNaN(parsed.getTime())) return null;
    return new Date(parsed.getTime() + (15 * 60000));
  }, [orgData?.workable_last_sync_at]);

  const filtered = useMemo(() => (
    roles.filter((role) => filterRoleBySource(role, sourceFilter))
  ), [roles, sourceFilter]);

  const handleToggleStar = useCallback(async (role) => {
    if (!role || isShowcase) return;
    const isStarred = Boolean(role.starred_for_auto_sync);
    // Optimistic flip — reverted on error.
    setRoles((current) => current.map((item) => (
      item.id === role.id ? { ...item, starred_for_auto_sync: !isStarred } : item
    )));
    try {
      if (isStarred) {
        await rolesApi.unstar(role.id);
      } else {
        await rolesApi.star(role.id);
      }
    } catch {
      setRoles((current) => current.map((item) => (
        item.id === role.id ? { ...item, starred_for_auto_sync: isStarred } : item
      )));
    }
  }, [isShowcase, rolesApi]);

  const handleRoleSubmit = async ({
    name,
    description,
    jobSpecFile,
    taskIds,
  }) => {
    setSavingRole(true);
    setRoleSheetError('');
    try {
      const createRes = await rolesApi.create({
        name,
        description: trimOrUndefined(description),
      });
      const createdRoleId = createRes?.data?.id;
      if (createdRoleId && jobSpecFile && rolesApi.uploadJobSpec) {
        await rolesApi.uploadJobSpec(createdRoleId, jobSpecFile);
        if (rolesApi.regenerateInterviewFocus) {
          try {
            await rolesApi.regenerateInterviewFocus(createdRoleId);
          } catch {
            // Interview focus generation is best-effort on create.
          }
        }
      }
      if (createdRoleId && rolesApi.addTask) {
        for (const taskId of taskIds || []) {
          await rolesApi.addTask(createdRoleId, taskId);
        }
      }
      setRoleSheetOpen(false);
      await loadJobsHub();
    } catch (err) {
      setRoleSheetError(err?.response?.data?.detail || 'Failed to save role.');
    } finally {
      setSavingRole(false);
    }
  };

  const headerAgent = useJobsHeaderAgent(roles, isShowcase);

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
      {/* HANDOFF unified-headers.md §2-§4 — single AgentHeader at the top of
          the page. Right-side panel reflects the org-aggregate agent state
          when at least one role has the agent enabled; otherwise the OFF
          panel reserves the same vertical space so the hero stays 280px
          tall. */}
      <AgentHeader
        breadcrumbs={[{ label: 'Jobs' }]}
        kicker={`JOBS · ${sourceCounts.live} ACTIVE ROLE${sourceCounts.live === 1 ? '' : 'S'}`}
        title={<>{sourceCounts.live} active <em>roles</em></>}
        period={false}
        subtitle="You're hiring. Star a role to keep its candidates flowing in automatically."
        actions={(
          <>
            <button
              type="button"
              className="btn btn-outline"
              onClick={() => document.getElementById('jobs-source-filters')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}
            >
              <Filter size={13} />
              Filter
            </button>
            <button
              type="button"
              className="btn btn-purple"
              onClick={() => {
                if (isShowcase) return;
                setRoleSheetError('');
                setRoleSheetOpen(true);
              }}
              disabled={isShowcase}
              aria-disabled={isShowcase || undefined}
            >
              + New role
            </button>
          </>
        )}
        agent={headerAgent}
        offStateMessage="Open a role and turn on agent mode there — each role has its own monthly cap."
      />
      <div className="mc-page">
        {/* HANDOFF v2 §4 / canvas jobs-list — search lives in the global
            ⌘K palette in Shell. The local "Search jobs by name" input was
            redundant chrome and is gone per the canvas spec. */}

        {orgData?.workable_connected ? (
          <div className="wk-strip">
            <div className="lg">
              <WorkableLogo size={30} className="!rounded-[7px] !shadow-none" />
            </div>
            <div>
              <div style={{ fontSize: '13.5px', fontWeight: 600, marginBottom: '2px' }}>
                Synced from Workable · {workableRolesCount} of {roles.length} roles
              </div>
              <div className="meta">
                <span>
                  <SyncPulse status={syncing ? 'healthy' : workableHealth} className="mr-2 inline-flex" />
                  {syncing ? 'Syncing now' : workableHealthLabel}
                </span>
                <span>Last pull: <b>{formatRelativeDateTime(orgData?.workable_last_sync_at)}</b></span>
                <span>Next pull in <b>{formatCountdown(nextPullAt)}</b></span>
                <span><b>{getSyncSummaryValue(workableSummary, ['new_candidates', 'candidates_upserted'], 0)}</b> new candidates synced</span>
                <span><b>{getSyncSummaryValue(workableSummary, ['candidates_seen', 'active_candidates'], 0)}</b> active candidates</span>
              </div>
            </div>
            <div className="row">
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={handleSyncNow}
                disabled={syncing}
                aria-label={syncing ? 'Syncing' : 'Sync now'}
              >
                <RefreshCw size={13} className={syncing ? 'animate-spin' : ''} />
                {syncing ? 'Syncing…' : 'Sync now'}
              </button>
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => onNavigate('settings-workable')}
              >
                Manage <span className="arrow">→</span>
              </button>
            </div>
          </div>
        ) : null}

        {/* HANDOFF v2 §4 / canvas jobs-list — exactly 4 KPI tiles:
            ACTIVE ROLES · CANDIDATES IN PIPELINE · YOUR REVIEW QUEUE · BUDGET USED
            (Last tile rolls up monthly_spent + monthly_budget across the
            roles the agent is enabled on; mirrors the AgentBar org rollup.) */}
        <div className="mc-jobs-kpis">
          {(() => {
            const activeRoles = sourceCounts.live;
            const starredCount = roles.filter((r) => r.starred_for_auto_sync).length;
            const pipelineCount = roles.reduce(
              (acc, r) => acc + Number(r.active_candidates_count || r.applications_count || 0),
              0,
            );
            const reviewCount = roles.reduce(
              (acc, r) => acc + Number(r?.stage_counts?.review || 0),
              0,
            );
            const reviewRoleCount = roles.filter((r) => Number(r?.stage_counts?.review || 0) > 0).length;
            const newThisWeek = roles.reduce(
              (acc, r) => acc + Number(r?.new_candidates_this_week || 0),
              0,
            );
            // BUDGET USED — sum live spend + budget across agent-enabled roles.
            // Spend comes from the /roles/{id}/agent/status fan-out kept in
            // `agentSpendByRole`; budget cap falls back to
            // role.monthly_usd_budget_cents when the status hasn't loaded yet.
            const agentEnabledCount = roles.filter((r) => r?.agentic_mode_enabled).length;
            let totalSpentCents = 0;
            let totalBudgetCents = 0;
            roles.forEach((r) => {
              if (!r?.agentic_mode_enabled) return;
              const live = agentSpendByRole?.[r.id];
              totalSpentCents += Number(live?.monthly_spent_cents || 0);
              totalBudgetCents += Number(
                live?.monthly_budget_cents
                ?? r?.monthly_usd_budget_cents
                ?? 0,
              );
            });
            const budgetPct = totalBudgetCents > 0
              ? Math.min(100, Math.round((totalSpentCents / totalBudgetCents) * 100))
              : null;
            const dollars = (cents) => {
              const n = Number(cents) / 100;
              return n >= 100 ? `$${Math.round(n)}` : `$${n.toFixed(0)}`;
            };
            const tiles = [
              { k: 'ACTIVE ROLES', v: activeRoles, d: starredCount > 0 ? `${starredCount} starred` : 'None starred' },
              { k: 'CANDIDATES IN PIPELINE', v: pipelineCount, d: newThisWeek > 0 ? `+${newThisWeek} this week` : 'Across active roles' },
              { k: 'YOUR REVIEW QUEUE', v: reviewCount, d: reviewCount === 0 ? 'All clear' : `Across ${reviewRoleCount} role${reviewRoleCount === 1 ? '' : 's'}` },
              {
                k: 'BUDGET USED',
                v: budgetPct != null ? `${budgetPct}%` : '—',
                d: totalBudgetCents > 0
                  ? `${dollars(totalSpentCents)} of ${dollars(totalBudgetCents)}`
                  : agentEnabledCount > 0
                    ? `${agentEnabledCount} role${agentEnabledCount === 1 ? '' : 's'} with the agent on`
                    : 'No roles using the agent yet',
              },
            ];
            return tiles.map((tile) => (
              <div key={tile.k} className="mc-jobs-kpi">
                <div className="k">{tile.k}</div>
                <div className="v">{tile.v}</div>
                <div className="d">{tile.d}</div>
              </div>
            ));
          })()}
        </div>

        <div className="filter-row" id="jobs-source-filters">
          {SOURCE_FILTERS.map((filter) => (
            <button
              key={filter.key}
              type="button"
              className={`f-chip ${sourceFilter === filter.key ? 'on' : ''}`}
              onClick={() => setSourceFilter(filter.key)}
            >
              {filter.key === 'workable' ? <ArrowRight size={11} /> : null}
              <span>{filter.label}</span>
              <span className="ct">{sourceCounts[filter.key]}</span>
            </button>
          ))}
          <button type="button" className="f-chip" disabled title="Additional recruiter filters are coming next.">
            + Add filter
          </button>
        </div>

        {loading ? (
          <div className="flex min-h-[240px] items-center justify-center">
            <Spinner size={20} />
          </div>
        ) : error ? (
          <div className="card flat p-4 text-sm text-[var(--red)]">
            {error}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No jobs found"
            description="Try a different filter, or create the role from the recruiter workflow."
            action={(
              <button
                type="button"
                className="btn btn-outline"
                onClick={() => {
                  setRoleSheetError('');
                  setRoleSheetOpen(true);
                }}
              >
                Create role
              </button>
            )}
          />
        ) : (
          <div className="jobs-grid">
            {filtered.map((role) => {
              const stageCounts = role?.stage_counts || {};
              const workableRole = String(role?.source || '').toLowerCase() === 'workable';
              const roleLive = isRoleLive(role);
              const roleDimmed = isRoleDimmed(role);
              const lastRoleActivity = role?.last_candidate_activity_at || role?.updated_at || orgData?.workable_last_sync_at || null;
              const roleBadgeLabel = getRoleBadgeLabel(role);
              const agentEnabled = Boolean(role?.agentic_mode_enabled);
              // Live agent status from the /roles/{id}/agent/status fan-out.
              // When loaded, the indicator shows the canvas-spec
              // "AGENT ON · $X/$Y"; otherwise falls back to cap-only.
              const agentLive = agentSpendByRole?.[role.id] || null;
              const agentBudget = Number(
                agentLive?.monthly_budget_cents
                ?? role?.monthly_usd_budget_cents
                ?? 0,
              ) / 100;
              const agentSpent = agentLive
                ? Number(agentLive.monthly_spent_cents || 0) / 100
                : null;
              const roleLoc = String(role?.location || role?.workable_location || '').trim();
              const roleDept = String(role?.department || role?.workable_department || '').trim();
              return (
                <div
                  key={role.id}
                  className={`job-card ${workableRole ? 'from-wk' : ''} ${agentEnabled ? 'agent-on' : ''} ${roleDimmed ? 'not-live' : ''}`}
                  onClick={() => onNavigate('job-pipeline', { roleId: role.id })}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      onNavigate('job-pipeline', { roleId: role.id });
                    }
                  }}
                  style={{ cursor: 'pointer' }}
                >
                  {/* Card header — canvas jobs-list role-card:
                      ⭐ star · role-name + #id + WORKABLE pill   ·   AGENT ON $X/$Y
                      dept · loc · updated ago */}
                  <div className="job-head">
                    {roleLive ? (
                      <span
                        className="job-star is-locked"
                        aria-label="Live role · always in continuous sync"
                        title="Live role · always in continuous sync (auto-starred)"
                        style={{
                          padding: 2,
                          marginTop: 2,
                          flexShrink: 0,
                          color: 'var(--purple)',
                          cursor: 'default',
                          display: 'inline-flex',
                        }}
                      >
                        <Star size={16} strokeWidth={1.5} fill="currentColor" />
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="job-star"
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleToggleStar(role);
                        }}
                        aria-label={role.starred_for_auto_sync ? 'Unstar role (stop auto-sync)' : 'Star role to enable auto-sync and real-time scoring'}
                        aria-pressed={Boolean(role.starred_for_auto_sync)}
                        title={role.starred_for_auto_sync ? 'Auto-sync enabled · click to disable' : 'Star to auto-sync from Workable and score in real-time'}
                        style={{
                          background: 'transparent',
                          border: 'none',
                          padding: 2,
                          marginTop: 2,
                          cursor: 'pointer',
                          flexShrink: 0,
                          color: role.starred_for_auto_sync ? 'var(--purple)' : 'var(--ink-soft)',
                        }}
                      >
                        <Star
                          size={16}
                          strokeWidth={1.5}
                          fill={role.starred_for_auto_sync ? 'currentColor' : 'none'}
                        />
                      </button>
                    )}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3, flexWrap: 'wrap' }}>
                        <h3 className="role-name">{role.name}</h3>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--mute)' }}>#{role.id}</span>
                        {workableRole ? (
                          <WorkableTag label="WORKABLE" size="sm" className="wk-tag !border-0 !px-2 !py-1 !text-[9.5px]" />
                        ) : (
                          <span className={`chip ${isRoleDraft(role) ? '' : 'purple'}`} style={{ fontSize: 10 }}>
                            {roleBadgeLabel}
                          </span>
                        )}
                      </div>
                      <div className="role-meta">
                        {[
                          roleDept || null,
                          roleLoc || null,
                          lastRoleActivity ? `updated ${formatRelativeDateTime(lastRoleActivity)}` : null,
                        ].filter(Boolean).join(' · ') || 'No metadata yet'}
                      </div>
                    </div>
                    {agentEnabled ? (
                      <div style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--purple)', whiteSpace: 'nowrap' }}>
                        {agentSpent != null && agentBudget > 0
                          ? `AGENT ON · $${Math.round(agentSpent)}/$${Math.round(agentBudget)}`
                          : agentBudget > 0
                            ? `AGENT ON · cap $${Math.round(agentBudget)}`
                            : 'AGENT ON'}
                      </div>
                    ) : (
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--mute)', whiteSpace: 'nowrap' }}>
                        AGENT OFF
                      </div>
                    )}
                  </div>

                  <div className="job-stats">
                    {STAGES.map((stage) => {
                      const value = Number(stageCounts?.[stage.key] || 0);
                      const isReviewActive = stage.key === 'review' && value > 0;
                      return (
                        <div key={stage.key} className="js-cell">
                          <div className="k">{stage.label}</div>
                          <div className="v" style={isReviewActive ? { color: 'var(--purple)' } : undefined}>
                            {value}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {!loading && filtered.length > 0 ? (
          <div className="card flat mt-5 flex flex-wrap items-center justify-between gap-3 px-5 py-4 text-xs text-[var(--mute)]">
            <span>
              Showing {filtered.length} of {roles.length} roles
              {sourceFilter !== 'all' ? ` · filtered by ${SOURCE_FILTERS.find((item) => item.key === sourceFilter)?.label || sourceFilter}` : ''}
            </span>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={loadJobsHub}
              disabled={loading || syncing}
            >
              <RefreshCw size={13} className={loading || syncing ? 'animate-spin' : ''} />
              Refresh hub
            </button>
          </div>
        ) : null}

        <RoleSheet
          open={roleSheetOpen}
          mode="create"
          role={null}
          roleTasks={[]}
          allTasks={allTasks}
          saving={savingRole}
          error={roleSheetError}
          onClose={() => setRoleSheetOpen(false)}
          onSubmit={handleRoleSubmit}
        />
      </div>
    </div>
  );
};

export default JobsPage;
