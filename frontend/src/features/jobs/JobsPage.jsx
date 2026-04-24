import React, { useEffect, useMemo, useState } from 'react';
import { BriefcaseBusiness, Search } from 'lucide-react';

import {
  canManageWorkable,
  deriveNextPullAt,
  deriveSyncHealth,
  deriveWorkableSummary,
  formatRelativeTime,
} from '../../lib/workableUi';
import { organizations as organizationsApi, roles as rolesApi } from '../../shared/api';
import { AppShell } from '../../shared/layout/TaaliLayout';
import { useAuth } from '../../context/AuthContext';
import {
  WorkableLogo,
  WorkableSyncIndicator,
  WorkableTag,
} from '../../components/integrations/workable/WorkablePrimitives';

const STAGES = [
  { key: 'applied', label: 'Applied' },
  { key: 'invited', label: 'Invited' },
  { key: 'in_assessment', label: 'In assessment' },
  { key: 'review', label: 'Review' },
];

const FILTER_OPTIONS = [
  { key: 'all', label: 'All roles' },
  { key: 'workable', label: 'From Workable' },
  { key: 'manual', label: 'Created in Taali' },
  { key: 'active', label: 'Active' },
  { key: 'draft', label: 'Draft' },
];

const stageCount = (role, key) => Number(role?.stage_counts?.[key] || 0);

const matchesSourceFilter = (role, sourceFilter) => {
  if (sourceFilter === 'workable') return role?.source === 'workable';
  if (sourceFilter === 'manual') return role?.source !== 'workable';
  if (sourceFilter === 'active') return role?.is_active !== false;
  if (sourceFilter === 'draft') return role?.is_active === false || String(role?.status || '').toLowerCase() === 'draft';
  return true;
};

export const JobsPage = ({ onNavigate }) => {
  const { user } = useAuth();
  const [roles, setRoles] = useState([]);
  const [orgData, setOrgData] = useState(null);
  const [syncStatus, setSyncStatus] = useState(null);
  const [query, setQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [syncing, setSyncing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError('');
      try {
        const [rolesRes, orgRes, statusRes] = await Promise.allSettled([
          rolesApi.list({ include_pipeline_stats: true }),
          organizationsApi.get(),
          organizationsApi.getWorkableStatus(),
        ]);
        if (cancelled) return;

        setRoles(rolesRes.status === 'fulfilled' && Array.isArray(rolesRes.value?.data) ? rolesRes.value.data : []);
        setOrgData(orgRes.status === 'fulfilled' ? orgRes.value?.data || null : null);
        setSyncStatus(statusRes.status === 'fulfilled' ? statusRes.value?.data || null : null);
      } catch {
        if (!cancelled) {
          setRoles([]);
          setError('Failed to load jobs.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const filterCounts = useMemo(() => ({
    all: roles.length,
    workable: roles.filter((role) => role?.source === 'workable').length,
    manual: roles.filter((role) => role?.source !== 'workable').length,
    active: roles.filter((role) => role?.is_active !== false).length,
    draft: roles.filter((role) => role?.is_active === false || String(role?.status || '').toLowerCase() === 'draft').length,
  }), [roles]);

  const filteredRoles = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return roles.filter((role) => {
      if (!matchesSourceFilter(role, sourceFilter)) return false;
      if (!needle) return true;
      const haystack = [
        role?.name,
        role?.description,
        role?.workable_job_id,
      ].join(' ').toLowerCase();
      return haystack.includes(needle);
    });
  }, [query, roles, sourceFilter]);

  const workableSummary = useMemo(
    () => deriveWorkableSummary({ org: orgData, syncStatus, roles }),
    [orgData, roles, syncStatus],
  );
  const workableRoles = filterCounts.workable;
  const nextPullAt = deriveNextPullAt(orgData?.workable_last_sync_at, orgData?.workable_config?.sync_interval_minutes);
  const canManage = canManageWorkable(user);
  const syncHealth = deriveSyncHealth({
    lastSyncStatus: syncStatus?.workable_last_sync_status || orgData?.workable_last_sync_status,
    syncInProgress: syncStatus?.sync_in_progress,
    lastSyncAt: syncStatus?.workable_last_sync_at || orgData?.workable_last_sync_at,
    errors: syncStatus?.errors || syncStatus?.workable_last_sync_summary?.errors || [],
  });

  const handleCreateRole = async () => {
    const name = window.prompt('Role title');
    if (!name) return;
    const description = window.prompt('Role description (optional)') || '';
    setCreating(true);
    try {
      await rolesApi.create({ name, description });
      const res = await rolesApi.list({ include_pipeline_stats: true });
      setRoles(Array.isArray(res?.data) ? res.data : []);
    } catch {
      setError('Failed to create role.');
    } finally {
      setCreating(false);
    }
  };

  const handleSyncNow = async () => {
    setSyncing(true);
    try {
      await organizationsApi.triggerWorkableSync();
      const statusRes = await organizationsApi.getWorkableStatus();
      setSyncStatus(statusRes?.data || null);
    } catch {
      setError('Failed to start Workable sync.');
    } finally {
      setSyncing(false);
    }
  };

  return (
    <AppShell currentPage="jobs" onNavigate={onNavigate}>
      <div className="page">
        <div className="page-head">
          <div className="tally-bg" />
          <div>
            <div className="kicker">01 · RECRUITER WORKSPACE</div>
            <h1>Jobs<em>.</em></h1>
            <p className="sub">Manage the recruiter workflow from role-level pipeline views. Every candidate, scored and sorted.</p>
          </div>
          <button type="button" className="btn btn-outline" onClick={() => onNavigate('candidates')}>
            Open candidates
          </button>
        </div>

        <div className="mb-5 flex flex-wrap items-center gap-3">
          <label className="grow relative block">
            <Search size={16} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[var(--mute)]" />
            <input
              className="w-full rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] py-3 pl-11 pr-4 text-sm"
              placeholder="Search jobs…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <button type="button" className="btn btn-outline">Filters</button>
          <button type="button" className="btn btn-purple" onClick={handleCreateRole} disabled={creating}>
            {creating ? 'Creating…' : '+ New role'}
          </button>
        </div>

        {orgData?.workable_connected && canManage ? (
          <>
            <div className="mb-4 grid gap-4 rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] px-4 py-3 shadow-[var(--shadow-sm)] md:grid-cols-[auto_1fr_auto] md:items-center">
              <WorkableLogo />
              <div>
                <div className="text-[13.5px] font-semibold">
                  Synced from Workable · {workableRoles} of {roles.length} roles
                </div>
                <div className="mt-1 flex flex-wrap gap-4 font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">
                  <span className="inline-flex items-center gap-2">
                    <span className={`pulse ${syncHealth !== 'healthy' ? '' : ''}`} style={{ background: syncHealth === 'error' ? 'var(--red)' : syncHealth === 'stale' ? 'var(--amber)' : 'var(--green)' }} />
                    {syncHealth === 'error' ? 'Error' : syncHealth === 'stale' ? 'Stale' : 'Healthy'}
                  </span>
                  <span>Last pull: <b className="font-medium text-[var(--ink-2)]">{orgData?.workable_last_sync_at ? formatRelativeTime(orgData.workable_last_sync_at) : 'Never'}</b></span>
                  <span>Next pull {nextPullAt ? formatRelativeTime(nextPullAt) : 'after first sync'}</span>
                  <span><b className="font-medium text-[var(--ink-2)]">+{workableSummary.newCandidates}</b> new candidates synced</span>
                </div>
              </div>
              <div className="row justify-end">
                <button type="button" className="btn btn-outline btn-sm" onClick={handleSyncNow} disabled={syncing}>
                  {syncing ? 'Syncing…' : 'Sync now'}
                </button>
                <button type="button" className="btn btn-outline btn-sm" onClick={() => onNavigate('settings-workable')}>
                  Manage →
                </button>
              </div>
            </div>
          </>
        ) : null}

        <div className="mb-4 flex flex-wrap gap-2">
          {FILTER_OPTIONS.map((option) => (
            <button
              key={option.key}
              type="button"
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 font-[var(--font-mono)] text-[12.5px] transition ${
                sourceFilter === option.key
                  ? 'border-[var(--purple)] bg-[var(--purple-soft)] text-[var(--purple-2)]'
                  : 'border-[var(--line)] bg-[var(--bg-2)] text-[var(--ink-2)] hover:border-[var(--purple)]'
              }`.trim()}
              onClick={() => setSourceFilter(option.key)}
            >
              {option.key === 'workable' ? <BriefcaseBusiness size={12} /> : null}
              {option.label}
              <span className="rounded-[6px] bg-[var(--bg-3)] px-1.5 py-0.5 text-[10.5px] text-[var(--mute)]">
                {filterCounts[option.key]}
              </span>
            </button>
          ))}
        </div>

        {loading ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, index) => (
              <div key={index} className="animate-pulse rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
                <div className="h-4 w-1/2 rounded-full bg-[var(--line)]" />
                <div className="mt-3 h-3 w-1/3 rounded-full bg-[var(--line)]" />
                <div className="mt-5 grid grid-cols-2 gap-3">
                  {Array.from({ length: 4 }).map((__, statIndex) => (
                    <div key={statIndex} className="h-20 rounded-[10px] bg-[var(--bg)]" />
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : error ? (
          <div className="rounded-[var(--radius-lg)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error}
          </div>
        ) : filteredRoles.length === 0 ? (
          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-8 text-center shadow-[var(--shadow-sm)]">
            <h2 className="font-[var(--font-display)] text-[32px] tracking-[-0.03em]">No jobs yet.</h2>
            <p className="mt-3 text-[14px] leading-7 text-[var(--mute)]">Create your first role to start building the pipeline.</p>
            <button type="button" className="btn btn-purple btn-lg mt-5" onClick={handleCreateRole}>Create role <span className="arrow">→</span></button>
          </div>
        ) : (
          <div className="grid gap-[18px] md:grid-cols-2 xl:grid-cols-3">
            {filteredRoles.map((role) => {
              const fromWorkable = role?.source === 'workable';
              return (
                <div
                  key={role.id}
                  className="flex flex-col gap-4 rounded-[var(--radius-lg)] border bg-[var(--bg-2)] p-[22px] shadow-[var(--shadow-sm)]"
                  style={{
                    borderColor: fromWorkable ? 'color-mix(in oklab, var(--workable) 25%, var(--line))' : 'var(--line)',
                  }}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h3 className="text-[18px] font-semibold tracking-[-0.01em]">{role.name}</h3>
                      <div className="mt-1 font-[var(--font-mono)] text-xs text-[var(--mute)]">
                        {Number(role?.active_candidates_count || role?.applications_count || 0)} active candidates
                      </div>
                    </div>
                    {fromWorkable ? (
                      <WorkableTag />
                    ) : (
                      <span className="chip purple">
                        <BriefcaseBusiness size={12} />
                        Role
                      </span>
                    )}
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    {STAGES.map((stage) => (
                      <div key={stage.key} className="rounded-[10px] border border-[var(--line)] bg-[var(--bg)] px-3 py-2.5">
                        <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">{stage.label}</div>
                        <div className="mt-1 font-[var(--font-display)] text-[24px] leading-none tracking-[-0.02em]">{stageCount(role, stage.key)}</div>
                      </div>
                    ))}
                  </div>

                  <div className="mt-auto flex items-center justify-between">
                    {fromWorkable ? (
                      <WorkableSyncIndicator lastSyncedAt={role?.updated_at || role?.last_candidate_activity_at} />
                    ) : (
                      <span className="font-[var(--font-mono)] text-[10.5px] text-[var(--mute)]">
                        {role?.updated_at ? `Updated ${formatRelativeTime(role.updated_at)}` : 'Manual role'}
                      </span>
                    )}
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
      </div>
    </AppShell>
  );
};

export default JobsPage;
