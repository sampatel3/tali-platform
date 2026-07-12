import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  ArrowRight,
  Building2,
  Filter,
  Inbox,
  Pause,
  RefreshCw,
  Sparkles,
  Star,
  Zap,
} from 'lucide-react';

import * as apiClient from '../../shared/api';
import {
  PIPELINE_FUNNEL_STAGES,
  invitedStageValue,
  funnelStageTone,
  formatCount,
  budgetTile,
  inPipelineFromStageCounts,
} from '../../shared/metrics';
import { KpiStrip } from '../../shared/ui/KpiStrip';
import { AgentHeader, buildAgentPropFromStatus } from '../../shared/layout/AgentHeader';
import { useAgentStatusOrg } from '../../shared/layout/AgentBar';
import { RoleSheet } from '../candidates/RoleSheet';
import { getErrorMessage, trimOrUndefined } from '../candidates/candidatesUiUtils';
import {
  EmptyState,
  Select,
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
import { useCountUp, useReducedMotionSync } from '../../shared/motion/useCountUp';
import '../../shared/motion/reveal.css';

// Canonical funnel for the role-card stat row — shared with the home
// "Pipeline" strip and the job-detail funnel via src/shared/metrics.
const STAGES = PIPELINE_FUNNEL_STAGES;

// One per-stage count on a role card. A tiny component so the count-up hook
// runs per cell (hooks can't live inside the STAGES.map body). Reduced motion —
// or a zero count — renders the final value immediately with no tween.
const StageCount = ({ value, reduced }) => (
  useCountUp(value, {
    reduced: reduced || value === 0,
    duration: 900,
    format: (n) => formatCount(n),
  })
);

// Card stagger is capped so a long role list can't push the last card's delay
// out to several seconds (0.06s × index).
const STAGGER_CAP = 12;

// Progressive load: paint this many roles first (the active / starred /
// recently-synced head of the list, per the backend's sort), then fetch the
// full list in the background. Sized to comfortably cover a recruiter's live +
// recently-touched roles on first paint without waiting on the long tail of
// old / filled postings.
const JOBS_FIRST_PAGE = 24;

const SOURCE_FILTERS = [
  { key: 'all', label: 'All roles' },
  { key: 'live', label: 'Live' },
  { key: 'workable', label: 'From Workable' },
  { key: 'manual', label: 'Created in Taali' },
  { key: 'active', label: 'With open candidates' },
  { key: 'draft', label: 'Draft' },
];

// Requisition->Workable job lifecycle (role.job_status). Null for legacy /
// Workable-only roles (their state reads off the Workable pill). The badge shows
// only when a status is set.
const JOB_STATUS_META = {
  draft: { label: 'Draft', tone: 'draft' },
  open: { label: 'Open', tone: 'open' },
  filled: { label: 'Filled', tone: 'filled' },
  filled_external: { label: 'Filled · external', tone: 'ext' },
  cancelled: { label: 'Cancelled', tone: 'cancelled' },
};

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
  const { status, refetch } = useAgentStatusOrg();
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
  return { agent, refetch };
};

export const JobsPage = ({ onNavigate: rawOnNavigate, NavComponent = null }) => {
  const rolesApi = apiClient.roles;
  const orgApi = apiClient.organizations;
  const tasksApi = 'tasks' in apiClient ? apiClient.tasks : null;
  const [searchParams] = useSearchParams();
  const isShowcase = searchParams.get('demo') === '1' && searchParams.get('showcase') === '1';
  const onNavigate = isShowcase ? () => {} : rawOnNavigate;

  const [roles, setRoles] = useState([]);
  // True while the first page is shown and the full role list is still
  // loading in the background (drives the subtle "loading all roles" hint).
  const [rolesPartial, setRolesPartial] = useState(false);
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
  // Org-level KPIs from /agent/org-status — the SAME source the Home hub reads,
  // so "Org budget · MTD" is the canonical org figure (total credits this month
  // vs sum of ALL role caps), not a truncated per-role sum.
  const [orgKpis, setOrgKpis] = useState(null);
  const [sourceFilter, setSourceFilter] = useState('all');
  // Consultancy: filter the grid to one client (mirrors the source filter). A
  // role's client rides on its requisition (role.client_id/client_name).
  const [clientFilter, setClientFilter] = useState('all');
  const [roleSheetOpen, setRoleSheetOpen] = useState(false);
  const [savingRole, setSavingRole] = useState(false);
  const [roleSheetError, setRoleSheetError] = useState('');
  const reduced = useReducedMotionSync();
  // The card grid staggers in ONCE, on the first render it becomes visible.
  // A ref guards against re-arming; the state flips off the stagger class after
  // the animation window so filter-chip / department / phase-2 re-renders don't
  // re-fire the reveal (the grid re-renders on every filter change).
  const gridRevealArmedRef = useRef(false);
  const [gridStaggerDone, setGridStaggerDone] = useState(false);

  const loadJobsHub = useCallback(async () => {
    if (isShowcase) {
      setRoles(JOBS_SHOWCASE);
      setOrgData(JOBS_SHOWCASE_ORG);
      // Mirror the Home showcase org budget ($18 / $50) so the demo surfaces match.
      setOrgKpis({ org_budget_spent_cents: 1800, org_budget_cap_cents: 5000 });
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
      // Phase 1 — paint fast. Fetch only the first page of roles (the active /
      // recently-synced head) alongside org + org-status. On a large org the
      // full /roles pass aggregates over tens of thousands of applications and
      // serialises ~100 roles; scoping to a page makes first paint cheap.
      const [rolesRes, orgRes, orgStatusRes] = await Promise.all([
        rolesApi.list({ include_pipeline_stats: true, limit: JOBS_FIRST_PAGE }),
        orgApi.get(),
        apiClient.agent.orgStatus().catch(() => null),
      ]);
      setOrgKpis(orgStatusRes?.data || null);
      const firstRoles = Array.isArray(rolesRes?.data) ? rolesRes.data : [];
      const nextOrgData = orgRes?.data || null;
      // Render the hub immediately from the first page + org + org-status. The
      // Workable sync badge ("Syncing now" / "Synced X ago") is secondary
      // chrome — read it below WITHOUT awaiting so it can't gate the spinner.
      setRoles(firstRoles);
      setOrgData(nextOrgData);
      setSyncing(false);
      setSyncRunId(null);
      setLoading(false);

      // Phase 2 — fill in the long tail. If the first page came back full there
      // are likely more roles; fetch the COMPLETE list in the background and
      // swap it in. The page is already interactive, so the recruiter never
      // waits on the full aggregate pass. Role keys (role.id) keep the first
      // page stable as the rest append.
      if (firstRoles.length >= JOBS_FIRST_PAGE) {
        setRolesPartial(true);
        rolesApi
          .list({ include_pipeline_stats: true })
          .then((fullRes) => {
            const allRoles = Array.isArray(fullRes?.data) ? fullRes.data : null;
            if (allRoles && allRoles.length) setRoles(allRoles);
          })
          .catch(() => { /* keep the first page if the full fetch fails */ })
          .finally(() => setRolesPartial(false));
      } else {
        setRolesPartial(false);
      }
      if (nextOrgData?.workable_connected) {
        try {
          const statusRes = await orgApi.getWorkableSyncStatus();
          const statusPayload = statusRes?.data || {};
          const inProgress = Boolean(statusPayload.sync_in_progress);
          setOrgData((cur) => mergeSyncStatusIntoOrg(cur || nextOrgData, statusPayload));
          setSyncing(inProgress);
          setSyncRunId(inProgress ? (statusPayload.run_id ?? null) : null);
        } catch {
          setSyncing(false);
          setSyncRunId(null);
        }
      }
    } catch {
      setRoles([]);
      setRolesPartial(false);
      setOrgData(null);
      setOrgKpis(null);
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

  // Arm the one-shot card stagger the first time the grid is actually shown,
  // then retire the stagger class once its animation window has passed so later
  // filter re-renders paint instantly. Reduced motion skips straight to done.
  useEffect(() => {
    if (gridRevealArmedRef.current) return undefined;
    if (loading || error || filtered.length === 0) return undefined;
    gridRevealArmedRef.current = true;
    if (reduced) {
      setGridStaggerDone(true);
      return undefined;
    }
    // Cover the last (capped) card's delay + the 0.48s reveal duration.
    const windowMs = Math.min(STAGGER_CAP, filtered.length) * 60 + 480 + 120;
    const id = window.setTimeout(() => setGridStaggerDone(true), windowMs);
    return () => window.clearTimeout(id);
  }, [loading, error, filtered.length, reduced]);

  // Per-client rollup (open/waiting · filled · external) for the selected client.
  const clientRollup = useMemo(() => (
    clientFilter === 'all'
      ? null
      : rollupRolesByStatus(roles.filter((role) => role?.client_id === clientFilter))
  ), [roles, clientFilter]);
  const selectedClientName = useMemo(() => (
    clientOptions.find((c) => c.id === clientFilter)?.name || null
  ), [clientOptions, clientFilter]);

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
      setRoleSheetError(getErrorMessage(err, 'Failed to save role.'));
    } finally {
      setSavingRole(false);
    }
  };

  const { agent: headerAgent, refetch: refetchAgentStatus } = useJobsHeaderAgent(roles, isShowcase);

  // Org-wide soft pause / resume driven from the header's Agent panel.
  // Pause flips every agent-enabled role's pause flag (keeping its pending
  // review items); resume clears it for roles back under their cap. A ref
  // guard blocks double-fire while the request is in flight; on success we
  // reload roles + re-poll the org-aggregate so the panel flips Pause⇄Resume
  // immediately instead of waiting for the 30s poll.
  const agentBulkBusyRef = useRef(false);
  const runAgentBulk = useCallback(async (action, failMsg) => {
    if (isShowcase || agentBulkBusyRef.current) return;
    agentBulkBusyRef.current = true;
    setError('');
    try {
      await action();
      await Promise.all([loadJobsHub(), refetchAgentStatus()]);
    } catch {
      setError(failMsg);
    } finally {
      agentBulkBusyRef.current = false;
    }
  }, [isShowcase, loadJobsHub, refetchAgentStatus]);
  const handlePauseAllAgents = useCallback(
    () => runAgentBulk(() => apiClient.agent.pauseAll(), 'Could not pause agents.'),
    [runAgentBulk],
  );
  const handleResumeAllAgents = useCallback(
    () => runAgentBulk(() => apiClient.agent.resumeAll(), 'Could not resume agents.'),
    [runAgentBulk],
  );
  // Running vs paused split across agent-enabled roles. In a mixed org the
  // panel shows BOTH "Pause" and "Resume" (and states the split in its tick);
  // when every agent is on (or every one paused) only the relevant button
  // shows. Derived from the same role list the cards use, so the badges and
  // buttons agree.
  const { agentRunningCount, agentPausedCount } = useMemo(() => {
    let running = 0;
    let pausedCount = 0;
    roles.forEach((role) => {
      if (!role?.agentic_mode_enabled) return;
      if (role?.agent_paused_at) pausedCount += 1;
      else running += 1;
    });
    return { agentRunningCount: running, agentPausedCount: pausedCount };
  }, [roles]);

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
        kicker={`JOBS · ${sourceCounts.live} LIVE ROLE${sourceCounts.live === 1 ? '' : 'S'}`}
        title={<>{sourceCounts.live} live <em>roles</em></>}
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
              onClick={() => { if (!isShowcase) onNavigate('requisitions'); }}
              disabled={isShowcase}
              aria-disabled={isShowcase || undefined}
              title="Start a requisition — the agent captures the full spec, then publishes the job"
            >
              + New requisition
            </button>
          </>
        )}
        agent={headerAgent}
        onPauseAgent={isShowcase ? undefined : handlePauseAllAgents}
        onResumeAgent={isShowcase ? undefined : handleResumeAllAgents}
        pauseAllCount={isShowcase ? null : agentRunningCount}
        resumeAllCount={isShowcase ? null : agentPausedCount}
        offStateMessage="Open a role and turn on agent mode there — each role has its own monthly cap."
      />
      <div className="mc-page">
        {/* HANDOFF v2 §4 / canvas jobs-list — search lives in the global
            ⌘K palette in Shell. The local "Search jobs by name" input was
            redundant chrome and is gone per the canvas spec. */}

        {orgData?.workable_connected ? (
          <div className="wk-strip reveal" style={{ '--reveal-delay': '0s' }}>
            <div className="lg">
              <WorkableLogo size={30} className="!rounded-[7px] !shadow-none" />
            </div>
            <div>
              <div style={{ fontSize: 'var(--fs-h3)', fontWeight: 600, marginBottom: '2px' }}>
                Synced from Workable · {workableRolesCount} role{workableRolesCount === 1 ? '' : 's'}{sourceCounts.manual > 0 ? ` · ${sourceCounts.manual} created in Taali` : ''}
              </div>
              <div className="meta">
                <span>
                  <SyncPulse status={syncing ? 'healthy' : workableHealth} className="mr-2 inline-flex" />
                  {syncing ? 'Syncing now' : workableHealthLabel}
                </span>
                <span>Last pull <b>{formatRelativeDateTime(orgData?.workable_last_sync_at)}</b></span>
                <span>Next in <b>{formatCountdown(nextPullAt)}</b></span>
                <span><b>{getSyncSummaryValue(workableSummary, ['new_candidates', 'candidates_upserted'], 0)}</b> new candidates synced</span>
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
            <div className="reveal" style={{ marginBottom: 18, '--reveal-delay': '0.08s' }}>
            <KpiStrip
              columns={4}
              tiles={[
                {
                  key: 'pipeline',
                  label: 'In pipeline',
                  value: formatCount(pipelineCount),
                  sub: `across ${formatCount(liveRoles)} live role${liveRoles === 1 ? '' : 's'}`,
                },
                {
                  key: 'roles',
                  label: 'Live roles',
                  value: formatCount(liveRoles),
                  sub: starredCount > 0 ? `${formatCount(starredCount)} starred` : 'none starred',
                },
                {
                  key: 'awaiting',
                  label: 'Awaiting you',
                  value: formatCount(awaitingCount),
                  emph: awaitingCount > 0,
                  sub: awaitingCount === 0
                    ? 'queue clear'
                    : `across ${formatCount(awaitingRoleCount)} role${awaitingRoleCount === 1 ? '' : 's'}`,
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
            </div>
          );
        })()}

        <div className="filter-row reveal" id="jobs-source-filters" style={{ '--reveal-delay': '0.16s' }}>
          <span className="filter-row-label">Show</span>
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
            <span
              className="flex items-center gap-1 text-xs text-[var(--mute)]"
              aria-live="polite"
            >
              <Spinner size={11} /> Loading all roles…
            </span>
          ) : null}
        </div>

        {clientRollup ? (
          <div className="client-rollup" role="status">
            <span className="client-rollup-name">{selectedClientName}</span>
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
          <div className="card flat p-4 text-sm text-[var(--red)]">
            {error}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No jobs found"
            description="Try a different filter, or start a new requisition — the agent captures the spec and publishes the job."
            action={(
              <button
                type="button"
                className="btn btn-outline"
                onClick={() => onNavigate('requisitions')}
              >
                + New requisition
              </button>
            )}
          />
        ) : (
          <div className={`jobs-grid${gridStaggerDone ? '' : ' reveal-stagger'}`}>
            {filtered.map((role, roleIndex) => {
              const stageCounts = role?.stage_counts || {};
              const workableRole = String(role?.source || '').toLowerCase() === 'workable';
              const roleLive = isRoleLive(role);
              const roleDimmed = isRoleDimmed(role);
              const lastRoleActivity = role?.last_candidate_activity_at || role?.updated_at || orgData?.workable_last_sync_at || null;
              const roleBadgeLabel = getRoleBadgeLabel(role);
              const agentEnabled = Boolean(role?.agentic_mode_enabled);
              // Soft pause keeps agentic_mode_enabled=true but stamps
              // agent_paused_at, so an enabled-but-paused role must read
              // "AGENT PAUSED", not "AGENT ON".
              const agentPaused = agentEnabled && Boolean(role?.agent_paused_at);
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
              const pendingCount = Number(agentLive?.pending_decisions || 0);
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
                  style={{ cursor: 'pointer', '--i': Math.min(roleIndex, STAGGER_CAP) }}
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
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-body-lg)', color: 'var(--mute)' }}>#{role.id}</span>
                        {workableRole ? (
                          <WorkableTag label="WORKABLE" size="sm" className="wk-tag !border-0 !px-2 !py-1 !text-[0.59375rem]" />
                        ) : (
                          <span className={`chip ${isRoleDraft(role) ? '' : 'purple'}`} style={{ fontSize: 'var(--fs-caption)' }}>
                            {roleBadgeLabel}
                          </span>
                        )}
                        {role?.job_status && JOB_STATUS_META[role.job_status] ? (
                          <span className={`job-status-badge is-${JOB_STATUS_META[role.job_status].tone}`}>
                            {JOB_STATUS_META[role.job_status].label}
                          </span>
                        ) : null}
                        {role?.client_name ? (
                          <span className="job-client-chip" title={`Client · ${role.client_name}`}>
                            <Building2 size={10} strokeWidth={2} /> {role.client_name}
                          </span>
                        ) : null}
                      </div>
                      <div className="role-meta">
                        {[
                          roleDept || null,
                          roleLoc || null,
                          lastRoleActivity ? `updated ${formatRelativeDateTime(lastRoleActivity)}` : null,
                        ].filter(Boolean).join(' · ') || 'No details yet'}
                      </div>
                    </div>
                    {agentPaused ? (
                      <span className="job-agent-pill is-paused" title={agentBudget > 0 ? `Agent paused · cap $${Math.round(agentBudget)}` : 'Agent paused'}>
                        <span className="d"><Pause size={10} strokeWidth={2.4} fill="currentColor" /></span>
                        PAUSED
                      </span>
                    ) : agentEnabled ? (
                      <span
                        className="job-agent-pill is-on"
                        title="Agent on for this role"
                      >
                        <span className="d"><Sparkles size={11} strokeWidth={2.2} /></span>
                        {agentSpent != null && agentBudget > 0
                          ? `ON · $${Math.round(agentSpent)}/$${Math.round(agentBudget)}`
                          : agentBudget > 0
                            ? `ON · cap $${Math.round(agentBudget)}`
                            : 'ON'}
                      </span>
                    ) : (
                      <span className="job-agent-pill is-off" title="Agent off">OFF</span>
                    )}
                  </div>

                  <div className="job-stats">
                    {STAGES.map((stage) => {
                      const value = stage.key === 'invited'
                        ? invitedStageValue(stageCounts)
                        : Number(stageCounts?.[stage.key] || 0);
                      const tone = funnelStageTone(stage.key, value);
                      return (
                        <div key={stage.key} className={`js-cell${tone === 'term' ? ' is-term' : ''}`}>
                          <div className="k">{stage.label}</div>
                          <div
                            className="v"
                            style={tone === 'term' ? { color: 'var(--mute)' } : undefined}
                          >
                            <StageCount value={value} reduced={reduced} />
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <div className="job-foot">
                    {pendingCount > 0 ? (
                      <span className="job-foot-pending"><Inbox size={13} aria-hidden="true" /> {pendingCount} awaiting you</span>
                    ) : agentPaused ? (
                      <span className="job-foot-hint job-foot-paused"><Pause size={13} aria-hidden="true" /> Agent paused</span>
                    ) : !agentEnabled ? (
                      <span className="job-foot-hint"><Zap size={13} aria-hidden="true" /> Turn on agent mode to start screening</span>
                    ) : (
                      <span />
                    )}
                    <span className="job-foot-open">Open pipeline →</span>
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
