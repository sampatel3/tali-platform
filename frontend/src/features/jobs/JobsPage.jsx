import React, { useEffect, useMemo, useState } from 'react';
import { BriefcaseBusiness, Search } from 'lucide-react';

import { roles as rolesApi } from '../../shared/api';
import { AppShell } from '../../shared/layout/TaaliLayout';

const STAGES = [
  { key: 'applied', label: 'Applied' },
  { key: 'invited', label: 'Invited' },
  { key: 'in_assessment', label: 'In assessment' },
  { key: 'review', label: 'Review' },
];

const stageCount = (role, key) => Number(role?.stage_counts?.[key] || 0);

export const JobsPage = ({ onNavigate }) => {
  const [roles, setRoles] = useState([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const loadRoles = async () => {
      setLoading(true);
      setError('');
      try {
        const res = await rolesApi.list({ include_pipeline_stats: true });
        if (!cancelled) setRoles(Array.isArray(res?.data) ? res.data : []);
      } catch {
        if (!cancelled) {
          setRoles([]);
          setError('Failed to load jobs.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void loadRoles();
    return () => {
      cancelled = true;
    };
  }, []);

  const filteredRoles = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return roles;
    return roles.filter((role) => String(role?.name || '').toLowerCase().includes(needle));
  }, [query, roles]);

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
            {filteredRoles.map((role) => (
              <div
                key={role.id}
                className="flex flex-col gap-4 rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-[22px] shadow-[var(--shadow-sm)]"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-[18px] font-semibold tracking-[-0.01em]">{role.name}</h3>
                    <div className="mt-1 font-[var(--font-mono)] text-xs text-[var(--mute)]">
                      {Number(role?.active_candidates_count || role?.applications_count || 0)} active candidates
                    </div>
                  </div>
                  <span className={`chip ${role?.source === 'workable' ? '' : 'purple'}`}>
                    <BriefcaseBusiness size={12} />
                    {role?.source === 'workable' ? 'Workable' : 'Role'}
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-2.5">
                  {STAGES.map((stage) => (
                    <div key={stage.key} className="rounded-[10px] border border-[var(--line)] bg-[var(--bg)] px-3 py-2.5">
                      <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">{stage.label}</div>
                      <div className="mt-1 font-[var(--font-display)] text-[24px] leading-none tracking-[-0.02em]">{stageCount(role, stage.key)}</div>
                    </div>
                  ))}
                </div>

                <div className="mt-auto flex justify-end">
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={() => onNavigate('job-pipeline', { roleId: role.id })}
                  >
                    Open pipeline <span className="arrow">→</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
};

export default JobsPage;
