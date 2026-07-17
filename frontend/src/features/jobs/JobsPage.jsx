import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowRight,
  Globe,
  RefreshCw,
} from 'lucide-react';

import * as apiClient from '../../shared/api';
import { useAuth } from '../../context/AuthContext';
import { useJobStatus } from '../../contexts/JobStatusContext';
import {
  formatCount,
  budgetTile,
  inPipelineFromStageCounts,
} from '../../shared/metrics';
import { KpiStrip } from '../../shared/ui/KpiStrip';
import { AgentHeader, buildAgentPropFromStatus } from '../../shared/layout/AgentHeader';
import { useAgentStatusOrg } from '../../shared/layout/AgentBar';
import {
  EmptyState,
  Select,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import {
  BullhornLogo,
  SyncPulse,
  WorkableLogo,
  formatRelativeDateTime,
  resolveSyncHealth,
} from '../../shared/ui/RecruiterDesignPrimitives';
import {
  atsProviderLabel,
  organizationAtsProvider,
  roleAtsProvider,
} from './atsType';
import {
  MOTION_DURATION,
  MotionLoop,
  Reveal,
  cappedStaggerDelay,
  useReducedMotionSync,
} from '../../shared/motion';
import {
  isRoleDraft,
  isRoleLive,
} from './JobsRoleGrid';
import { JobsRoleCatalogue } from './JobsRoleCatalogue';
import { useJobsBulkAgentControls } from './useJobsBulkAgentControls';

// Paint a bounded first page; keep every additional page explicitly requested.
const JOBS_FIRST_PAGE = 24;

const SOURCE_FILTERS = [
  { key: 'all', label: 'All roles' },
  { key: 'live', label: 'Live' },
  { key: 'workable', label: 'From Workable' },
  { key: 'bullhorn', label: 'From Bullhorn' },
  { key: 'full_ats', label: 'Created in Taali' },
  { key: 'active', label: 'With open candidates' },
  { key: 'draft', label: 'Draft' },
];

// Roll a set of roles up by job_status for the per-client summary strip:
// active (draft+open / "waiting to fill"), filled (us), filled (external).
const rollupRolesByStatus = (rolesForClient) => rolesForClient.reduce((acc, role) => {
  const status = role?.job_status;
  if (status === 'filled') acc.filled += 1;
  else if (status === 'filled_external') acc.filled_external += 1;
  else if (status === 'cancelled') acc.cancelled += 1;
  else if (status === 'draft' || status === 'open') acc.active += 1;
  acc.total += 1;
  return acc;
}, { active: 0, filled: 0, filled_external: 0, cancelled: 0, total: 0 });

const filterRoleBySource = (role, sourceFilter) => {
  if (sourceFilter === 'live') return isRoleLive(role);
  if (sourceFilter === 'workable' || sourceFilter === 'bullhorn') return roleAtsProvider(role) === sourceFilter;
  if (sourceFilter === 'full_ats') return roleAtsProvider(role) == null;
  if (sourceFilter === 'active') return Number(role?.active_candidates_count || 0) > 0;
  if (sourceFilter === 'draft') return isRoleDraft(role);
  return true;
};

const buildSourceCounts = (roles) => roles.reduce((acc, role) => {
  acc.all += 1;
  if (isRoleLive(role)) acc.live += 1;
  const provider = roleAtsProvider(role);
  if (provider === 'workable') acc.workable += 1;
  else if (provider === 'bullhorn') acc.bullhorn += 1;
  else acc.full_ats += 1;
  if (Number(role?.active_candidates_count || 0) > 0) acc.active += 1;
  if (isRoleDraft(role)) acc.draft += 1;
  return acc;
}, {
  all: 0,
  live: 0,
  workable: 0,
  bullhorn: 0,
  full_ats: 0,
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

const mergeSyncStatusIntoOrg = (org, payload = {}, provider = 'workable') => {
  if (!org) return org;
  if (provider === 'bullhorn') {
    return {
      ...org,
      bullhorn_last_sync_at: payload.last_sync_at ?? payload.bullhorn_last_sync_at ?? org.bullhorn_last_sync_at,
      bullhorn_last_sync_status: payload.last_sync_status ?? payload.bullhorn_last_sync_status ?? org.bullhorn_last_sync_status,
      bullhorn_last_sync_summary: payload.last_sync_summary ?? payload.bullhorn_last_sync_summary ?? org.bullhorn_last_sync_summary,
      bullhorn_sync_progress: payload.sync_progress ?? payload.bullhorn_sync_progress ?? org.bullhorn_sync_progress,
    };
  }
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

// Maps the org-aggregate /agent/status payload (or the showcase fixture) into
// the shape AgentHeader's right-side panel expects. Activation on the Jobs
// list is intentionally per-role (each role has its own budget cap), so the
// OFF state on this page guides the user to open a role. Pause/Resume here is
// a workspace override: it gates every enabled role without rewriting any
// role's own ON/PAUSED/OFF choice.
const useJobsHeaderAgent = (roles, isShowcase, orgStatusResult) => {
  const { status, refetch } = orgStatusResult;
  const agent = useMemo(() => {
    if (isShowcase) {
      return {
        on: true,
        paused: false,
        pending: 2,
        spentCents: 1820,
        budgetCents: 5000,
        tick: 'Scoring 14 new candidates · just now',
        inFlight: true,
        controlScope: 'workspace',
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
        controlScope: 'workspace',
      };
    }
    return buildAgentPropFromStatus(status, {
      isEnabled: status.active_role_count > 0,
      controlScope: 'workspace',
    });
  }, [status, roles, isShowcase]);
  return { agent, refetch };
};

export const JobsPage = ({ onNavigate: rawOnNavigate, NavComponent = null, showcase = false }) => {
  const { user } = useAuth();
  const rolesApi = apiClient.roles;
  const orgApi = apiClient.organizations;
  const isShowcase = showcase;
  const isOwner = String(user?.role || '') === 'owner';
  const canControlWorkspaceAgent = isOwner;
  const onNavigate = isShowcase ? () => {} : rawOnNavigate;
  const orgStatusResult = useAgentStatusOrg(!isShowcase);
  const {
    workableSyncJob,
    bullhornSyncJob,
    trackWorkableSync,
    trackBullhornSync,
  } = useJobStatus() ?? {};

  const [roles, setRoles] = useState([]);
  // True when another explicit page of roles is available.
  const [rolesPartial, setRolesPartial] = useState(false);
  const [loadingMoreRoles, setLoadingMoreRoles] = useState(false);
  const [orgData, setOrgData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState('');
  // HANDOFF v2 §4 — Live agent spend across roles for the BUDGET USED tile.
  // Fan-out to /roles/{id}/agent/status for every agent-enabled role. Capped
  // at AGENT_SPEND_FANOUT_LIMIT to keep the request count bounded; orgs with
  // more agentic roles fall back to the cap-only display.
  const [agentSpendByRole, setAgentSpendByRole] = useState({});
  // Org-level KPIs from /agent/org-status — the SAME source the Home hub reads,
  // so "Org budget · MTD" is the canonical org figure (total credits this month
  // vs sum of ALL role caps), not a truncated per-role sum.
  const [orgKpis, setOrgKpis] = useState(null);
  const [sourceFilter, setSourceFilter] = useState('all');
  // Consultancy: filter the grid to one client (mirrors the source filter). A
  // role's client rides on its requisition (role.client_id/client_name).
  const [clientFilter, setClientFilter] = useState('all');
  const reduced = useReducedMotionSync();
  // Motion.dev staggers the first visible grid, then later filters/layout
  // changes settle immediately instead of replaying a page entrance.
  const [gridStaggerDone, setGridStaggerDone] = useState(false);
  const gridRevealArmedRef = useRef(false);
  const gridRevealTimerRef = useRef(null);

  const loadJobsHub = useCallback(async () => {
    if (isShowcase) {
      const { JOBS_SHOWCASE, JOBS_SHOWCASE_ORG } = await import('../demo/productWalkthroughModels');
      setRoles(JOBS_SHOWCASE);
      setOrgData(JOBS_SHOWCASE_ORG);
      // Mirror the Home showcase org budget ($18 / $50) so the demo surfaces match.
      setOrgKpis({ org_budget_spent_cents: 1800, org_budget_cap_cents: 5000 });
      // Show a brief "Syncing now" pulse on first load, then settle into the
      // static "Synced X min ago" state. Pure visual — no API calls fire.
      setSyncing(true);
      setError('');
      setLoading(false);
      window.setTimeout(() => setSyncing(false), 2500);
      return;
    }
    setLoading(true);
    setError('');
    try {
      // Phase 1 — paint fast. Fetch only the first page of roles (the active /
      // recently-synced head) alongside the org. The shared org-status store
      // loads the KPI/header payload in parallel. On a large org the
      // full /roles pass aggregates over tens of thousands of applications and
      // serialises ~100 roles; scoping to a page makes first paint cheap.
      const [rolesRes, orgRes] = await Promise.all([
        rolesApi.list({ include_pipeline_stats: true, sort_by: 'name', limit: JOBS_FIRST_PAGE }),
        orgApi.get(),
      ]);
      const firstRoles = Array.isArray(rolesRes?.data) ? rolesRes.data : [];
      const nextOrgData = orgRes?.data || null;
      // Render the hub immediately from the first page + org. The
      // Workable sync badge ("Syncing now" / "Synced X ago") is secondary
      // chrome — read it below WITHOUT awaiting so it can't gate the spinner.
      setRoles(firstRoles);
      setOrgData(nextOrgData);
      setLoading(false);

      // Keep the long tail out of the critical path. Explicit pagination still
      // gives recruiters access to every role without duplicate aggregate work.
      setRolesPartial(firstRoles.length >= JOBS_FIRST_PAGE);
    } catch {
      setRoles([]);
      setRolesPartial(false);
      setOrgData(null);
      setOrgKpis(null);
      setError('Failed to load jobs.');
    } finally {
      setLoading(false);
    }
  }, [isShowcase, orgApi, rolesApi]);

  const loadMoreRoles = useCallback(async () => {
    if (isShowcase || loadingMoreRoles || !rolesPartial) return;
    setLoadingMoreRoles(true);
    try {
      const offset = roles.length;
      const res = await rolesApi.list({
        include_pipeline_stats: true,
        sort_by: 'name',
        limit: JOBS_FIRST_PAGE,
        offset,
      });
      const page = Array.isArray(res?.data) ? res.data : [];
      setRoles((current) => {
        const seen = new Set(current.map((role) => Number(role?.id)));
        return [...current, ...page.filter((role) => !seen.has(Number(role?.id)))];
      });
      setRolesPartial(page.length >= JOBS_FIRST_PAGE);
    } catch {
      // Keep the roles already rendered; the button remains available to retry.
    } finally {
      setLoadingMoreRoles(false);
    }
  }, [isShowcase, loadingMoreRoles, roles.length, rolesApi, rolesPartial]);

  useEffect(() => {
    void loadJobsHub();
  }, [loadJobsHub]);

  useEffect(() => {
    if (!isShowcase && orgStatusResult.payload) {
      setOrgKpis(orgStatusResult.payload);
    }
  }, [isShowcase, orgStatusResult.payload]);

  const activeAts = organizationAtsProvider(orgData);
  const atsSyncJob = activeAts === 'bullhorn' ? bullhornSyncJob : workableSyncJob;
  const trackAtsSync = activeAts === 'bullhorn' ? trackBullhornSync : trackWorkableSync;

  // JobStatusContext is the single ATS sync-status owner. Entering Jobs asks it
  // to discover once; it keeps polling only while a sync is actually live.
  useEffect(() => {
    if (!isShowcase && activeAts) trackAtsSync?.();
  }, [activeAts, isShowcase, trackAtsSync]);

  const atsWasActiveRef = useRef(false);
  useEffect(() => {
    if (!atsSyncJob || !activeAts) return;
    const status = String(
      atsSyncJob.workable_last_sync_status
      || atsSyncJob.bullhorn_last_sync_status
      || atsSyncJob.last_sync_status
      || atsSyncJob.status
      || '',
    ).toLowerCase();
    const inProgress = Boolean(atsSyncJob.sync_in_progress)
      || status === 'running'
      || status === 'cancelling';
    setOrgData((current) => mergeSyncStatusIntoOrg(current, atsSyncJob, activeAts));
    setSyncing(inProgress);
    if (atsWasActiveRef.current && !inProgress) void loadJobsHub();
    atsWasActiveRef.current = inProgress;
  }, [activeAts, atsSyncJob, loadJobsHub]);

  // Per-role agent spend for the BUDGET USED tile. This used to fan out one
  // /roles/{id}/agent/status call per agent-enabled role — up to 20 requests
  // on every Jobs load, each ~190ms, a burst that piled onto the web service.
  // /agent/roles/breakdown returns the same per-role spend / cap / pending in
  // a single batched query, so collapse the fan-out to one call. Polls every
  // 60s and pauses on hidden tabs.
  useEffect(() => {
    if (isShowcase) return undefined;
    const POLL_MS = 60_000;
    const hasAgentRoles = roles.some(
      (role) => role && role.id != null && role.agentic_mode_enabled,
    );
    if (!hasAgentRoles) {
      setAgentSpendByRole({});
      return undefined;
    }
    let cancelled = false;
    const fetchSpend = async () => {
      try {
        const res = await apiClient.agent.rolesBreakdown();
        if (cancelled) return;
        const next = {};
        (res?.data || []).forEach((row) => {
          if (!row || row.role_id == null || !row.agentic_mode_enabled) return;
          // breakdown's budget_cents == /agent/status monthly_spent_cents
          // (both the canonical per-role MTD spend); cap_cents == the budget
          // cap; pending == pending decisions for the role.
          next[row.role_id] = {
            monthly_spent_cents: Number(row.budget_cents || 0),
            monthly_budget_cents: Number(row.cap_cents || 0),
            pending_decisions: Number(row.pending || 0),
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

  const handleSyncNow = async () => {
    if (isShowcase || !activeAts || !isOwner) return;
    setError('');
    setSyncing(true);
    try {
      const res = activeAts === 'bullhorn'
        ? await orgApi.syncBullhorn()
        : await orgApi.syncWorkable();
      const payload = res?.data || {};
      const runId = extractRunId(payload);
      if (payload?.status === 'already_running') {
        trackAtsSync?.();
        setSyncing(true);
        return;
      }
      if (runId || payload?.status === 'started') {
        trackAtsSync?.();
        setSyncing(true);
        return;
      }
      setSyncing(false);
      await loadJobsHub();
    } catch (err) {
      const status = err?.response?.status;
      const runId = extractRunId(err?.response?.data) ?? extractRunId(err?.response?.data?.detail);
      if (status === 409 || runId != null) {
        try {
          const statusRes = activeAts === 'bullhorn'
            ? await orgApi.getBullhornSyncStatus()
            : await orgApi.getWorkableSyncStatus(runId ?? undefined);
          const payload = statusRes?.data || {};
          setOrgData((current) => mergeSyncStatusIntoOrg(current, payload, activeAts));
          const inProgress = Boolean(payload.sync_in_progress);
          setSyncing(inProgress);
          if (inProgress) trackAtsSync?.();
          if (!inProgress) {
            await loadJobsHub();
          }
          return;
        } catch {
          setSyncing(true);
          trackAtsSync?.();
          return;
        }
      }
      setSyncing(false);
      setError(`${atsProviderLabel(activeAts)} sync could not be started.`);
    }
  };

  const sourceCounts = useMemo(() => buildSourceCounts(roles), [roles]);
  const countScopeSuffix = rolesPartial ? ' (loaded)' : '';
  const activeAtsLabel = atsProviderLabel(activeAts);
  const activeAtsRolesCount = activeAts ? sourceCounts[activeAts] : 0;
  const activeAtsLastSyncAt = activeAts === 'bullhorn'
    ? orgData?.bullhorn_last_sync_at
    : orgData?.workable_last_sync_at;
  const activeAtsSummary = activeAts === 'bullhorn'
    ? (orgData?.bullhorn_last_sync_summary || {})
    : (orgData?.workable_last_sync_summary || {});
  const activeAtsHealth = resolveSyncHealth({
    status: activeAts === 'bullhorn'
      ? orgData?.bullhorn_last_sync_status
      : orgData?.workable_last_sync_status,
    lastSyncedAt: activeAtsLastSyncAt,
  });
  const activeAtsHealthLabel = activeAtsHealth === 'error'
    ? 'Attention needed'
    : activeAtsHealth === 'stale'
      ? 'Needs refresh'
      : 'Healthy';
  const nextPullAt = useMemo(() => {
    // Jobs metadata syncs every 15 minutes (sync_workable_jobs Beat task).
    // Candidate cadences vary per role (starred/agent/nightly) — those
    // surface on the role page itself rather than here.
    if (activeAts !== 'workable') return null;
    const lastSyncAt = activeAtsLastSyncAt;
    if (!lastSyncAt) return null;
    const parsed = new Date(lastSyncAt);
    if (Number.isNaN(parsed.getTime())) return null;
    return new Date(parsed.getTime() + (15 * 60000));
  }, [activeAts, activeAtsLastSyncAt]);

  // Distinct clients present across the loaded roles, for the client dropdown.
  const clientOptions = useMemo(() => {
    const byId = new Map();
    roles.forEach((role) => {
      if (role?.client_id && !byId.has(role.client_id)) {
        byId.set(role.client_id, role.client_name || `Client ${role.client_id}`);
      }
    });
    return [...byId.entries()]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [roles]);

  const filtered = useMemo(() => (
    roles
      .filter((role) => filterRoleBySource(role, sourceFilter))
      .filter((role) => clientFilter === 'all' || role?.client_id === clientFilter)
  ), [roles, sourceFilter, clientFilter]);

  useEffect(() => {
    if (gridStaggerDone || loading || error || filtered.length === 0) return;
    if (reduced) {
      setGridStaggerDone(true);
      return;
    }
    if (gridRevealArmedRef.current) return;

    gridRevealArmedRef.current = true;
    const lastStaggeredIndex = Math.min(filtered.length, 8) - 1;
    const revealWindowMs = Math.ceil((
      cappedStaggerDelay(lastStaggeredIndex, 'dense')
      + MOTION_DURATION.reveal
    ) * 1000) + 80;
    gridRevealTimerRef.current = window.setTimeout(() => {
      gridRevealTimerRef.current = null;
      setGridStaggerDone(true);
    }, revealWindowMs);
  }, [error, filtered.length, gridStaggerDone, loading, reduced]);

  useEffect(() => () => {
    if (gridRevealTimerRef.current !== null) {
      window.clearTimeout(gridRevealTimerRef.current);
    }
  }, []);

  // Per-client rollup (open/waiting · filled · external) for the selected client.
  const clientRollup = useMemo(() => (
    clientFilter === 'all'
      ? null
      : rollupRolesByStatus(roles.filter((role) => role?.client_id === clientFilter))
  ), [roles, clientFilter]);
  const selectedClientName = useMemo(() => (
    clientOptions.find((c) => c.id === clientFilter)?.name || null
  ), [clientOptions, clientFilter]);
  const jobBoardHref = useMemo(() => {
    const slug = String(orgData?.slug || '').trim();
    return slug ? `/careers/${encodeURIComponent(slug)}` : null;
  }, [orgData?.slug]);

  const handleToggleStar = useCallback(async (role) => {
    if (!role || isShowcase) return;
    const isStarred = Boolean(role.starred_for_auto_sync);
    // Optimistic flip — reverted on error.
    setRoles((current) => current.map((item) => (
      item.id === role.id ? { ...item, starred_for_auto_sync: !isStarred } : item
    )));
    try {
      let response;
      if (isStarred) {
        response = await rolesApi.unstar(role.id, role.version);
      } else {
        response = await rolesApi.star(role.id, role.version);
      }
      if (response?.data) {
        setRoles((current) => current.map((item) => (
          item.id === role.id ? { ...item, ...response.data } : item
        )));
      }
    } catch {
      setRoles((current) => current.map((item) => (
        item.id === role.id ? { ...item, starred_for_auto_sync: isStarred } : item
      )));
    }
  }, [isShowcase, rolesApi]);

  const { agent: headerAgent, refetch: refetchAgentStatus } = useJobsHeaderAgent(
    roles,
    isShowcase,
    orgStatusResult,
  );

  // These controls bulk-edit role pauses; they do not create a workspace
  // execution overlay. Existing role holds remain untouched by bulk Pause.
  const {
    action: agentBulkAction,
    message: agentControlMessage,
    pause: handlePauseAllAgents,
    resume: handleResumeAllAgents,
    dismissMessage: dismissAgentControlMessage,
  } = useJobsBulkAgentControls({
    isShowcase,
    loadJobsHub,
    refetchAgentStatus,
    workspaceControlVersion: headerAgent?.workspaceControlVersion,
  });
  return (
    <>
      {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
      <main>
      {/* HANDOFF unified-headers.md §2-§4 — single AgentHeader at the top of
          the page. Right-side panel reflects the org-aggregate agent state
          when at least one role has the agent enabled; otherwise the OFF
          panel reserves the same vertical space so the hero stays 280px
          tall. */}
      <AgentHeader
        breadcrumbs={[{ label: 'Jobs' }]}
        kicker={`JOBS · ${sourceCounts.live} LIVE ROLE${sourceCounts.live === 1 ? '' : 'S'}${countScopeSuffix.toUpperCase()}`}
        title={<>{sourceCounts.live} live <em>roles</em>{countScopeSuffix}</>}
        period={false}
        subtitle="You're hiring. Star a role to keep its candidates flowing in automatically."
        actions={(
          <>
            {!isShowcase && jobBoardHref ? (
              <a
                className="btn btn-outline"
                href={jobBoardHref}
                target="_blank"
                rel="noreferrer"
                title="View your public job board"
              >
                <Globe size={13} />
                Job board
              </a>
            ) : (
              <button
                type="button"
                className="btn btn-outline"
                disabled
                aria-disabled="true"
                title={isShowcase ? 'Job board is unavailable in the showcase' : 'Job board URL is unavailable'}
              >
                <Globe size={13} />
                Job board
              </button>
            )}
            <button
              type="button"
              className="btn btn-purple"
              onClick={() => { if (!isShowcase) onNavigate('requisitions'); }}
              disabled={isShowcase}
              aria-disabled={isShowcase || undefined}
              title="Create a job — the agent captures the full brief, then publishes it"
            >
              + Create job
            </button>
          </>
        )}
        agent={headerAgent ? { ...headerAgent, controlAction: agentBulkAction } : headerAgent}
        onPauseAgent={!isShowcase && canControlWorkspaceAgent ? handlePauseAllAgents : undefined}
        onResumeAgent={!isShowcase && canControlWorkspaceAgent ? handleResumeAllAgents : undefined}
        pauseLabel="Pause running agents"
        resumeLabel="Resume eligible paused agents"
        pauseAllCount={headerAgent?.runningRoleCount ?? 0}
        resumeAllCount={headerAgent?.localPausedRoleCount ?? 0}
        controlsDisabledReason={!canControlWorkspaceAgent
          ? 'Only workspace owners can pause running agents or resume eligible paused agents.'
          : null}
        offStateMessage="Open a role and turn on agent mode there — each role has its own monthly cap."
      />
      <div className="mc-page">
        {/* HANDOFF v2 §4 / canvas jobs-list — search lives in the global
            ⌘K palette in Shell. The local "Search jobs by name" input was
            redundant chrome and is gone per the canvas spec. */}

        {agentControlMessage ? (
          <div
            className={`card flat mb-3 flex flex-wrap items-center justify-between gap-3 p-3 text-sm ${agentControlMessage.tone === 'error' ? 'text-[var(--red)]' : 'text-[var(--ink-2)]'}`}
            role={agentControlMessage.tone === 'error' ? 'alert' : 'status'}
          >
            <span>{agentControlMessage.text}</span>
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={dismissAgentControlMessage}
            >
              Dismiss
            </button>
          </div>
        ) : null}

        {activeAts ? (
          <Reveal className="wk-strip">
            <div className="lg">
              {activeAts === 'bullhorn'
                ? <BullhornLogo size={30} className="!rounded-[7px] !shadow-none" />
                : <WorkableLogo size={30} className="!rounded-[7px] !shadow-none" />}
            </div>
            <div>
              <div style={{ fontSize: 'var(--fs-h3)', fontWeight: 600, marginBottom: '2px' }}>
                Synced from {activeAtsLabel} · {activeAtsRolesCount} role{activeAtsRolesCount === 1 ? '' : 's'}{countScopeSuffix}{sourceCounts.full_ats > 0 ? ` · ${sourceCounts.full_ats} created in Taali${countScopeSuffix}` : ''}
              </div>
              <div className="meta">
                <span>
                  <SyncPulse status={syncing ? 'healthy' : activeAtsHealth} className="mr-2 inline-flex" />
                  {syncing ? 'Syncing now' : activeAtsHealthLabel}
                </span>
                <span>Last pull <b>{formatRelativeDateTime(activeAtsLastSyncAt)}</b></span>
                {nextPullAt ? <span>Next in <b>{formatCountdown(nextPullAt)}</b></span> : null}
                <span><b>{getSyncSummaryValue(activeAtsSummary, ['new_candidates', 'candidates_upserted'], 0)}</b> new candidates synced</span>
              </div>
            </div>
            <div className="row">
              {isOwner ? (
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={handleSyncNow}
                  disabled={syncing}
                  aria-label={syncing ? 'Syncing' : 'Sync now'}
                >
                  <MotionLoop kind="spin" active={syncing} className="inline-flex" aria-hidden="true">
                    <RefreshCw size={13} />
                  </MotionLoop>
                  {syncing ? 'Syncing…' : 'Sync now'}
                </button>
              ) : <span className="settings-inline-note">Only owners can start a sync.</span>}
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => onNavigate(`settings-${activeAts}`)}
              >
                Manage <span className="arrow">→</span>
              </button>
            </div>
          </Reveal>
        ) : null}

        {/* Org KPI strip — shares the <KpiStrip> tile with the Home hub so the
            two surfaces look identical. Roles-focused subset:
              In pipeline · Live roles · Awaiting you · Org budget · MTD.
            "Awaiting you" is the pending-decision queue (summed from the
            /roles/{id}/agent/status fan-out), the same metric the Home hub
            surfaces — not the Review funnel stage. */}
        {(() => {
          const liveRoles = sourceCounts.live;
          const starredCount = roles.filter((r) => r.starred_for_auto_sync).length;
          const pipelineCount = roles.reduce(
            (acc, r) => acc + inPipelineFromStageCounts(r?.stage_counts),
            0,
          );
          // Awaiting you — pending agent decisions across roles, from the
          // agent-status fan-out (only agent-enabled roles can have any).
          const awaitingCount = roles.reduce(
            (acc, r) => acc + Number(agentSpendByRole?.[r.id]?.pending_decisions || 0),
            0,
          );
          const awaitingRoleCount = roles.filter(
            (r) => Number(agentSpendByRole?.[r.id]?.pending_decisions || 0) > 0,
          ).length;
          // Org budget · MTD — the canonical org figure from /agent/org-status
          // (total credits charged this month vs sum of ALL role caps), the
          // SAME source the Home hub reads so the two pages always match. The
          // old per-role sum over agent-enabled roles (capped at the fan-out
          // limit) under-counted spend and used sum-of-role-caps, not the org
          // cap — which is why Jobs and Home disagreed.
          const orgBudgetCapCents = Number(orgKpis?.org_budget_cap_cents || 0);
          const budget = budgetTile(Number(orgKpis?.org_budget_spent_cents || 0), orgBudgetCapCents);
          return (
            <Reveal delay={0.08} style={{ marginBottom: 18 }}>
            <KpiStrip
              columns={4}
              tiles={[
                {
                  key: 'pipeline',
                  label: 'In pipeline',
                  value: formatCount(pipelineCount),
                  sub: `across ${formatCount(liveRoles)} live role${liveRoles === 1 ? '' : 's'}${countScopeSuffix}`,
                },
                {
                  key: 'roles',
                  label: `Live roles${countScopeSuffix}`,
                  value: formatCount(liveRoles),
                  sub: starredCount > 0 ? `${formatCount(starredCount)} starred${countScopeSuffix}` : `none starred${countScopeSuffix}`,
                },
                {
                  key: 'awaiting',
                  label: `Awaiting you${countScopeSuffix}`,
                  value: formatCount(awaitingCount),
                  emph: awaitingCount > 0,
                  sub: awaitingCount === 0
                    ? 'queue clear'
                    : `across ${formatCount(awaitingRoleCount)} role${awaitingRoleCount === 1 ? '' : 's'}${countScopeSuffix}`,
                },
                {
                  key: 'budget',
                  label: 'Org budget · MTD',
                  value: budget.value,
                  unit: budget.unit,
                  bar: orgBudgetCapCents > 0 ? budget : null,
                  sub: budget.sub,
                },
              ]}
            />
            </Reveal>
          );
        })()}

        <Reveal
          className="filter-row"
          id="jobs-source-filters"
          role="group"
          aria-label="Filter jobs"
          delay={0.16}
        >
          <span className="filter-row-label">Show{countScopeSuffix}</span>
          {SOURCE_FILTERS.map((filter) => (
            <button
              key={filter.key}
              type="button"
              className={`f-chip ${sourceFilter === filter.key ? 'on' : ''}`}
              aria-pressed={sourceFilter === filter.key}
              onClick={() => setSourceFilter(filter.key)}
            >
              {filter.key === 'workable' || filter.key === 'bullhorn' ? <ArrowRight size={11} /> : null}
              <span>{filter.label}</span>
              <span className="ct">{sourceCounts[filter.key]}</span>
            </button>
          ))}
          {clientOptions.length ? (
            <label className="jobs-client-filter" title="Filter by hiring department">
              <span className="filter-row-label">Department</span>
              <Select
                inline
                aria-label="Filter by hiring department"
                value={clientFilter === 'all' ? 'all' : String(clientFilter)}
                onChange={(event) => {
                  const value = event.target.value;
                  setClientFilter(value === 'all' ? 'all' : Number(value));
                }}
              >
                <option value="all">All departments</option>
                {clientOptions.map((c) => (
                  <option key={c.id} value={String(c.id)}>{c.name}</option>
                ))}
              </Select>
            </label>
          ) : null}
          {rolesPartial ? (
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={loadMoreRoles}
              disabled={loadingMoreRoles}
            >
              {loadingMoreRoles ? <Spinner size={11} /> : null}
              {loadingMoreRoles ? 'Loading more…' : `Load more roles (${roles.length} loaded)`}
            </button>
          ) : null}
        </Reveal>

        {clientRollup ? (
          <div className="client-rollup" role="status">
            <span className="client-rollup-name">{selectedClientName}{countScopeSuffix}</span>
            <span className="client-rollup-stat"><b>{clientRollup.active}</b> open / waiting</span>
            <span className="client-rollup-stat"><b>{clientRollup.filled}</b> filled</span>
            <span className="client-rollup-stat"><b>{clientRollup.filled_external}</b> filled externally</span>
            {clientRollup.cancelled ? (
              <span className="client-rollup-stat is-muted"><b>{clientRollup.cancelled}</b> cancelled</span>
            ) : null}
          </div>
        ) : null}

        {loading ? (
          <div className="flex min-h-[15rem] items-center justify-center">
            <Spinner size={20} />
          </div>
        ) : error ? (
          <div className="card flat flex flex-wrap items-center justify-between gap-3 p-4 text-sm text-[var(--red)]" role="alert">
            <span>{error}</span>
            <button type="button" className="btn btn-outline btn-sm" onClick={loadJobsHub}>
              Retry
            </button>
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No jobs found"
            description="Try a different filter, or create a job — the agent captures the brief and publishes it."
            action={(
              <button
                type="button"
                className="btn btn-outline"
                onClick={() => onNavigate('requisitions')}
              >
                + Create job
              </button>
            )}
          />
        ) : (
          <JobsRoleCatalogue
            activeAts={activeAts}
            activeAtsLastSyncAt={activeAtsLastSyncAt}
            agentSpendByRole={agentSpendByRole}
            autoExpandInactive={sourceFilter === 'draft'}
            gridStaggerDone={gridStaggerDone}
            loadedRoleCount={roles.length}
            onNavigate={onNavigate}
            onRefresh={loadJobsHub}
            onToggleStar={handleToggleStar}
            reduced={reduced}
            refreshDisabled={loading || syncing}
            roles={filtered}
            rolesPartial={rolesPartial}
            sourceFilterLabel={
              sourceFilter === 'all'
                ? ''
                : SOURCE_FILTERS.find((item) => item.key === sourceFilter)?.label || sourceFilter
            }
            workspacePaused={Boolean(headerAgent?.workspacePaused)}
          />
        )}

      </div>
      </main>
    </>
  );
};
export default JobsPage;
