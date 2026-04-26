import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ArrowRight,
  Filter,
  RefreshCw,
  Search,
} from 'lucide-react';

import * as apiClient from '../../shared/api';
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

const STAGES = [
  { key: 'applied', label: 'Applied' },
  { key: 'invited', label: 'Invited' },
  { key: 'in_assessment', label: 'In assessment' },
  { key: 'review', label: 'Review' },
];

const SOURCE_FILTERS = [
  { key: 'all', label: 'All roles' },
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

const filterRoleBySource = (role, sourceFilter) => {
  if (sourceFilter === 'workable') return String(role?.source || '').toLowerCase() === 'workable';
  if (sourceFilter === 'manual') return String(role?.source || '').toLowerCase() !== 'workable';
  if (sourceFilter === 'active') return Number(role?.active_candidates_count || 0) > 0;
  if (sourceFilter === 'draft') return isRoleDraft(role);
  return true;
};

const buildSourceCounts = (roles) => roles.reduce((acc, role) => {
  acc.all += 1;
  if (String(role?.source || '').toLowerCase() === 'workable') acc.workable += 1;
  if (String(role?.source || '').toLowerCase() !== 'workable') acc.manual += 1;
  if (Number(role?.active_candidates_count || 0) > 0) acc.active += 1;
  if (isRoleDraft(role)) acc.draft += 1;
  return acc;
}, {
  all: 0,
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

export const JobsPage = ({ onNavigate, NavComponent = null }) => {
  const rolesApi = apiClient.roles;
  const orgApi = apiClient.organizations;
  const tasksApi = 'tasks' in apiClient ? apiClient.tasks : null;

  const [roles, setRoles] = useState([]);
  const [orgData, setOrgData] = useState(null);
  const [allTasks, setAllTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncRunId, setSyncRunId] = useState(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [roleSheetOpen, setRoleSheetOpen] = useState(false);
  const [savingRole, setSavingRole] = useState(false);
  const [roleSheetError, setRoleSheetError] = useState('');

  const loadJobsHub = useCallback(async () => {
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
  }, [orgApi, rolesApi]);

  useEffect(() => {
    void loadJobsHub();
  }, [loadJobsHub]);

  useEffect(() => {
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
  }, [tasksApi]);

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
    const lastSyncAt = orgData?.workable_last_sync_at;
    const intervalMinutes = Number(orgData?.workable_config?.sync_interval_minutes || 0);
    if (!lastSyncAt || !Number.isFinite(intervalMinutes) || intervalMinutes <= 0) return null;
    const parsed = new Date(lastSyncAt);
    if (Number.isNaN(parsed.getTime())) return null;
    return new Date(parsed.getTime() + (intervalMinutes * 60000));
  }, [orgData?.workable_config?.sync_interval_minutes, orgData?.workable_last_sync_at]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return roles
      .filter((role) => filterRoleBySource(role, sourceFilter))
      .filter((role) => {
        if (!needle) return true;
        return [
          role?.name,
          role?.description,
          role?.job_spec_filename,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
          .includes(needle);
      });
  }, [query, roles, sourceFilter]);

  const handleRoleSubmit = async ({
    name,
    description,
    additionalRequirements,
    autoRejectEnabled,
    autoRejectThreshold100,
    autoRejectNoteTemplate,
    jobSpecFile,
    taskIds,
  }) => {
    setSavingRole(true);
    setRoleSheetError('');
    try {
      const createRes = await rolesApi.create({
        name,
        description: trimOrUndefined(description),
        additional_requirements: trimOrUndefined(additionalRequirements),
        auto_reject_enabled: autoRejectEnabled || undefined,
        auto_reject_threshold_100: autoRejectEnabled ? autoRejectThreshold100 : undefined,
        auto_reject_note_template: autoRejectEnabled ? trimOrUndefined(autoRejectNoteTemplate) : undefined,
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

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
      <div className="page">
        <div className="page-head">
          <div className="tally-bg" />
          <div>
            <div className="kicker">01 · RECRUITER WORKSPACE</div>
            <h1>Jobs<em>.</em></h1>
            <p className="sub">
              Manage the recruiter workflow from role-level pipeline views. Every candidate, scored and sorted.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-outline"
            onClick={() => onNavigate('candidates')}
          >
            Open candidates
          </button>
        </div>

        <div className="jobs-search">
          <div className="relative grow">
            <Search size={15} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[var(--mute)]" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search jobs…"
              aria-label="Search jobs"
              className="pl-11"
            />
          </div>
          <button
            type="button"
            className="btn btn-outline"
            onClick={() => document.getElementById('jobs-source-filters')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}
          >
            <Filter size={14} />
            Filters
          </button>
          <button
            type="button"
            className="btn btn-purple"
            onClick={() => {
              setRoleSheetError('');
              setRoleSheetOpen(true);
            }}
          >
            + New role
          </button>
        </div>

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
              const lastRoleActivity = role?.last_candidate_activity_at || orgData?.workable_last_sync_at || null;
              const roleBadgeLabel = getRoleBadgeLabel(role);
              return (
                <div
                  key={role.id}
                  className={`job-card ${workableRole ? 'from-wk' : ''}`}
                >
                  <div className="job-head">
                    <div>
                      <h3>{role.name}</h3>
                      <div className="sub">
                        {Number(role.active_candidates_count || role.applications_count || 0)} active candidates
                        {role?.tasks_count ? ` · ${role.tasks_count} task${role.tasks_count === 1 ? '' : 's'}` : ''}
                      </div>
                    </div>
                    {workableRole ? (
                      <WorkableTag label="WORKABLE" size="sm" className="wk-tag !border-0 !px-2 !py-1 !text-[9.5px]" />
                    ) : (
                      <span className={`chip ${isRoleDraft(role) ? '' : 'purple'}`}>
                        {roleBadgeLabel}
                      </span>
                    )}
                  </div>

                  <div className="job-stats">
                    {STAGES.map((stage) => (
                      <div key={stage.key} className="js-cell">
                        <div className="k">{stage.label}</div>
                        <div className="v">{Number(stageCounts?.[stage.key] || 0)}</div>
                      </div>
                    ))}
                  </div>

                  <div className="job-foot">
                    <div>
                      {workableRole ? (
                        <span className="wk-sync">
                          <span className="pulse" />
                          Synced {formatRelativeDateTime(lastRoleActivity)}
                        </span>
                      ) : null}
                    </div>
                    <button
                      type="button"
                      className="btn btn-outline btn-sm"
                      onClick={() => onNavigate('job-pipeline', { roleId: role.id })}
                    >
                      Open pipeline <span className="arrow">→</span>
                    </button>
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
