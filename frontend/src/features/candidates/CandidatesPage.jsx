import React, { useEffect, useMemo, useState } from 'react';
import { ChevronRight, Search } from 'lucide-react';

import { roles as rolesApi } from '../../shared/api';
import { AppShell } from '../../shared/layout/TaaliLayout';

const statusLabel = (value) => String(value || '').replace(/_/g, ' ');
const assessmentIdFor = (application) => (
  application?.score_summary?.assessment_id
  || application?.valid_assessment_id
  || null
);

const initialsFor = (value) => String(value || '')
  .split(/\s+/)
  .filter(Boolean)
  .slice(0, 2)
  .map((part) => part[0])
  .join('')
  .toUpperCase() || 'TA';

const scoreTone = (score) => {
  if (!Number.isFinite(Number(score))) return '';
  if (score >= 80) return 'text-[var(--green)]';
  if (score >= 65) return 'text-[var(--purple)]';
  return 'text-[var(--red)]';
};

export const CandidatesPage = ({ onNavigate }) => {
  const [applications, setApplications] = useState([]);
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');
  const [sortValue, setSortValue] = useState('pipeline_stage_updated_at:desc');
  const [minTaaliScore, setMinTaaliScore] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError('');
      try {
        const [rolesRes, applicationsRes] = await Promise.all([
          rolesApi.list(),
          rolesApi.listApplicationsGlobal({
            application_outcome: 'open',
            limit: 100,
            offset: 0,
            sort_by: 'pipeline_stage_updated_at',
            sort_order: 'desc',
          }),
        ]);
        if (cancelled) return;
        setRoles(Array.isArray(rolesRes?.data) ? rolesRes.data : []);
        setApplications(Array.isArray(applicationsRes?.data?.items) ? applicationsRes.data.items : []);
      } catch {
        if (!cancelled) {
          setRoles([]);
          setApplications([]);
          setError('Failed to load candidates.');
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

  const duplicateCounts = useMemo(() => {
    const counts = {};
    applications.forEach((application) => {
      const key = application?.candidate_id || application?.candidate_email;
      if (!key) return;
      counts[key] = Number(counts[key] || 0) + 1;
    });
    return counts;
  }, [applications]);

  const filtered = useMemo(() => {
    const [sortBy, sortOrder] = sortValue.split(':');
    const minScore = Number(minTaaliScore);
    const needle = search.trim().toLowerCase();

    return applications
      .filter((application) => {
        if (roleFilter !== 'all' && String(application?.role_id) !== roleFilter) return false;
        if (statusFilter !== 'all' && String(application?.pipeline_stage || '').toLowerCase() !== statusFilter) return false;
        if (minTaaliScore !== '' && Number.isFinite(minScore) && Number(application?.taali_score || application?.rank_score || 0) < minScore) return false;
        if (!needle) return true;
        const haystack = [
          application?.candidate_name,
          application?.candidate_email,
          application?.role_name,
        ].join(' ').toLowerCase();
        return haystack.includes(needle);
      })
      .sort((left, right) => {
        const leftValue = sortBy === 'taali_score'
          ? Number(left?.taali_score ?? left?.rank_score ?? -1)
          : new Date(left?.updated_at || left?.pipeline_stage_updated_at || 0).getTime();
        const rightValue = sortBy === 'taali_score'
          ? Number(right?.taali_score ?? right?.rank_score ?? -1)
          : new Date(right?.updated_at || right?.pipeline_stage_updated_at || 0).getTime();
        return sortOrder === 'asc' ? leftValue - rightValue : rightValue - leftValue;
      });
  }, [applications, minTaaliScore, roleFilter, search, sortValue, statusFilter]);

  const counts = useMemo(() => ({
    all: applications.length,
    in_assessment: applications.filter((item) => item?.pipeline_stage === 'in_assessment').length,
    review: applications.filter((item) => item?.pipeline_stage === 'review').length,
    shortlist: applications.filter((item) => Number(item?.taali_score ?? item?.rank_score ?? 0) >= 80).length,
  }), [applications]);

  const openApplication = (application) => {
    const assessmentId = assessmentIdFor(application);
    if (assessmentId) {
      onNavigate('candidate-detail', { candidateDetailAssessmentId: assessmentId });
      return;
    }
    onNavigate('candidate-report', { candidateApplicationId: application.id });
  };

  return (
    <AppShell currentPage="candidates" onNavigate={onNavigate}>
      <div className="page">
        <div className="page-head">
          <div className="tally-bg" />
          <div>
            <div className="kicker">02 · RECRUITER WORKSPACE</div>
            <h1>Candidates<em>.</em></h1>
            <p className="sub">Every person across every role, scored and filterable. Open any row to review their standing and assessment evidence.</p>
          </div>
          <div className="row">
            <button type="button" className="btn btn-outline btn-sm">Export CSV</button>
            <button type="button" className="btn btn-purple btn-sm" onClick={() => onNavigate('jobs')}>+ Invite candidate</button>
          </div>
        </div>

        <div className="mb-5 flex flex-wrap items-center gap-3">
          <div className="inline-flex gap-1 rounded-full border border-[var(--line)] bg-[var(--bg-2)] p-1 shadow-[var(--shadow-sm)]">
            <button type="button" className="app-tab active">All · {counts.all}</button>
            <button type="button" className="app-tab">In assessment · {counts.in_assessment}</button>
            <button type="button" className="app-tab">Review · {counts.review}</button>
            <button type="button" className="app-tab">Shortlist · {counts.shortlist}</button>
          </div>

          <label className="grow relative min-w-[280px]">
            <Search size={16} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[var(--mute)]" />
            <input
              className="w-full rounded-full border border-[var(--line)] bg-[var(--bg-2)] py-3 pl-11 pr-4 text-sm"
              placeholder="Search by name, email, or role…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>

          <label className="sr-only" htmlFor="candidate-role-filter">Role</label>
          <select id="candidate-role-filter" className="rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-4 py-3 text-sm" value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}>
            <option value="all">All roles</option>
            {roles.map((role) => <option key={role.id} value={String(role.id)}>{role.name}</option>)}
          </select>

          <label className="sr-only" htmlFor="candidate-sort">Sort</label>
          <select id="candidate-sort" aria-label="Sort" className="rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-4 py-3 text-sm" value={sortValue} onChange={(event) => setSortValue(event.target.value)}>
            <option value="pipeline_stage_updated_at:desc">Recent activity</option>
            <option value="taali_score:desc">TAALI high to low</option>
            <option value="taali_score:asc">TAALI low to high</option>
          </select>

          <label className="sr-only" htmlFor="candidate-status">Status</label>
          <select id="candidate-status" className="rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-4 py-3 text-sm" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">All stages</option>
            <option value="applied">Applied</option>
            <option value="invited">Invited</option>
            <option value="in_assessment">In assessment</option>
            <option value="review">Review</option>
          </select>

          <label className="sr-only" htmlFor="candidate-min-taali">Min TAALI</label>
          <input
            id="candidate-min-taali"
            aria-label="Min TAALI"
            className="w-[120px] rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-4 py-3 text-sm"
            placeholder="Score ≥"
            value={minTaaliScore}
            onChange={(event) => setMinTaaliScore(event.target.value)}
          />
        </div>

        <div className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] shadow-[var(--shadow-sm)]">
          <div className="hidden grid-cols-[2fr_1.2fr_1.1fr_1fr_1fr_1fr_auto] gap-4 border-b border-[var(--line)] bg-[var(--bg)] px-6 py-4 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)] md:grid">
            <div>Candidate</div>
            <div>Role</div>
            <div>Composite</div>
            <div>Role applications</div>
            <div>Status</div>
            <div>Updated</div>
            <div />
          </div>

          {loading ? (
            <div className="space-y-3 p-6">
              {Array.from({ length: 6 }).map((_, index) => (
                <div key={index} className="h-20 animate-pulse rounded-[14px] bg-[var(--bg)]" />
              ))}
            </div>
          ) : error ? (
            <div className="p-6 text-sm text-[var(--taali-danger)]">{error}</div>
          ) : filtered.length === 0 ? (
            <div className="p-10 text-center">
              <h2 className="font-[var(--font-display)] text-[32px] tracking-[-0.03em]">No candidates yet.</h2>
              <p className="mt-3 text-[14px] leading-7 text-[var(--mute)]">Invite your first one to start the pipeline.</p>
            </div>
          ) : (
            filtered.map((application) => {
              const score = application?.taali_score ?? application?.rank_score ?? null;
              const key = application?.candidate_id || application?.candidate_email;
              return (
                <button
                  key={application.id}
                  type="button"
                  className="grid w-full gap-4 border-b border-[var(--line)] px-6 py-4 text-left transition hover:bg-[var(--bg)] md:grid-cols-[2fr_1.2fr_1.1fr_1fr_1fr_1fr_auto] md:items-center"
                  onClick={() => openApplication(application)}
                >
                  <div className="flex items-center gap-3">
                    <div className="grid h-[34px] w-[34px] shrink-0 place-items-center rounded-full bg-[var(--bg-3)] text-xs font-semibold">{initialsFor(application?.candidate_name)}</div>
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">{application?.candidate_name || 'Unknown candidate'}</div>
                      <div className="truncate font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">{application?.candidate_email || 'No email'}</div>
                    </div>
                  </div>
                  <div className="text-[13px] text-[var(--ink-2)]">{application?.role_name || '—'}</div>
                  <div className={`font-[var(--font-mono)] text-[15px] font-semibold ${scoreTone(score)}`}>
                    {score == null ? '—' : `${score}`}<span className="font-normal text-[var(--mute-2)]">/100</span>
                  </div>
                  <div className="text-sm text-[var(--ink-2)]">{duplicateCounts[key] > 1 ? `${duplicateCounts[key]} role applications` : '1 role application'}</div>
                  <div>
                    <span className="chip">
                      <span className="dot" style={{ background: application?.pipeline_stage === 'review' ? 'var(--purple)' : application?.pipeline_stage === 'in_assessment' ? 'var(--green)' : application?.pipeline_stage === 'invited' ? 'var(--amber)' : 'var(--mute)' }} />
                      {statusLabel(application?.pipeline_stage || 'applied')}
                    </span>
                  </div>
                  <div className="font-[var(--font-mono)] text-[12.5px] text-[var(--mute)]">
                    {application?.updated_at ? new Date(application.updated_at).toLocaleDateString() : '—'}
                  </div>
                  <div className="flex justify-end">
                    <span className="grid h-[30px] w-[30px] place-items-center rounded-full border border-[var(--line)] bg-[var(--bg-2)] text-[var(--ink-2)]">
                      <ChevronRight size={13} />
                    </span>
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>
    </AppShell>
  );
};

export default CandidatesPage;
