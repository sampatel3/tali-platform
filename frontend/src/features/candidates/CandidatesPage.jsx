import React, { useEffect, useMemo, useState } from 'react';
import { AlertCircle, ArrowRight, Minus, Plus, Search, X } from 'lucide-react';

import { useToast } from '../../context/ToastContext';
import { recommendationFromScore } from './redesignUtils';
import { roles as rolesApi } from '../../shared/api';
import { AppShell } from '../../shared/layout/TaaliLayout';
import { WorkableScorePip, WorkableTagSm } from '../../components/integrations/workable/WorkablePrimitives';

const SEGMENTS = [
  { id: 'all', label: 'All' },
  { id: 'in_assessment', label: 'In assessment' },
  { id: 'review', label: 'Review' },
  { id: 'shortlist', label: 'Shortlist' },
];

const globalApplicationsQuery = {
  application_outcome: 'open',
  limit: 100,
  offset: 0,
  sort_by: 'pipeline_stage_updated_at',
  sort_order: 'desc',
};

const listFallbackApplicationsFromRolePipelines = async (roles) => {
  const results = await Promise.allSettled(
    roles.map((role) => rolesApi.listPipeline(role.id, {
      stage: 'all',
      application_outcome: 'open',
      limit: 100,
      offset: 0,
    })),
  );

  let succeeded = 0;
  const items = results.flatMap((result, index) => {
    if (result.status !== 'fulfilled') return [];
    succeeded += 1;

    const payload = result.value?.data || {};
    const role = roles[index];
    const pipelineItems = Array.isArray(payload?.items) ? payload.items : [];

    return pipelineItems.map((item) => ({
      ...item,
      role_id: item?.role_id ?? role?.id,
      role_name: item?.role_name || role?.name || '',
      role_reject_threshold: item?.role_reject_threshold ?? role?.reject_threshold ?? 60,
    }));
  });

  return { items, succeeded };
};

const initialsFor = (value) => String(value || '')
  .split(/\s+/)
  .filter(Boolean)
  .slice(0, 2)
  .map((part) => part[0])
  .join('')
  .toUpperCase() || 'TA';

const formatRelativeTime = (value) => {
  if (!value) return '—';
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return '—';
  const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60000));
  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
};

const normalizeScore100 = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  if (numeric <= 10) return Math.round(numeric * 10);
  return Math.round(Math.max(0, Math.min(100, numeric)));
};

const cvMatchTone = (score) => {
  if (!Number.isFinite(Number(score))) return 'muted';
  if (score >= 80) return 'hi';
  if (score >= 65) return 'md';
  return 'lo';
};

const aiCollabSummary = (application) => {
  const categoryScores = application?.assessment_preview?.category_scores;
  if (!categoryScores || typeof categoryScores !== 'object') {
    return { label: '—', score: null, tone: 'var(--mute)' };
  }

  const rawValues = Object.values(categoryScores)
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));
  if (!rawValues.length) return { label: '—', score: null, tone: 'var(--mute)' };

  const normalized = rawValues.map((value) => (value > 10 ? value / 10 : value));
  const average = normalized.reduce((sum, value) => sum + value, 0) / normalized.length;
  const rounded = Math.round(average * 10);

  if (average >= 9) return { label: 'A+', score: rounded, tone: 'var(--green)' };
  if (average >= 8) return { label: 'A', score: rounded, tone: 'var(--green)' };
  if (average >= 7) return { label: 'B', score: rounded, tone: 'var(--purple)' };
  if (average >= 6) return { label: 'C', score: rounded, tone: 'var(--amber)' };
  return { label: 'D', score: rounded, tone: 'var(--red)' };
};

const hireSignalModel = (application) => {
  const taali = normalizeScore100(application?.taali_score ?? application?.rank_score);
  const cvMatch = normalizeScore100(application?.cv_match_score);
  const model = recommendationFromScore(taali ?? cvMatch);

  if (model.variant === 'success') {
    return { label: 'Strong hire', tone: 'green' };
  }
  if (model.variant === 'info') {
    return { label: 'Advance', tone: 'green' };
  }
  if (model.variant === 'warning') {
    return { label: 'Maybe', tone: 'amber' };
  }
  return { label: 'No hire', tone: 'red' };
};

const roleThresholdFor = (application, rolesById) => {
  const roleThreshold = application?.role_reject_threshold;
  if (Number.isFinite(Number(roleThreshold))) return Number(roleThreshold);
  const role = rolesById[String(application?.role_id)];
  return Number(role?.reject_threshold || 60);
};

const isBelowThreshold = (application, rolesById) => {
  if (application?.below_role_threshold === true) return true;
  const score = normalizeScore100(application?.cv_match_score);
  if (score == null) return false;
  return score < roleThresholdFor(application, rolesById);
};

const matchesSegment = (application, segmentId) => {
  if (segmentId === 'all') return true;
  if (segmentId === 'shortlist') return normalizeScore100(application?.taali_score ?? application?.rank_score) >= 80;
  return String(application?.pipeline_stage || '').toLowerCase() === segmentId;
};

const tableHeaderClass = 'font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]';
const belowThresholdBadgeStyle = {
  borderColor: 'color-mix(in oklab, var(--red) 24%, var(--line))',
  background: 'color-mix(in oklab, var(--red) 8%, transparent)',
};
const strongSignalStyle = {
  borderColor: 'color-mix(in oklab, var(--green) 20%, var(--line))',
  background: 'color-mix(in oklab, var(--green) 10%, transparent)',
};
const maybeSignalStyle = {
  borderColor: 'color-mix(in oklab, var(--amber) 24%, var(--line))',
  background: 'color-mix(in oklab, var(--amber) 10%, transparent)',
};
const noHireSignalStyle = {
  borderColor: 'color-mix(in oklab, var(--red) 24%, var(--line))',
  background: 'color-mix(in oklab, var(--red) 8%, transparent)',
};

const CandidateRow = ({
  application,
  selected,
  rolesById,
  onToggleSelect,
  onOpen,
}) => {
  const cvMatch = normalizeScore100(application?.cv_match_score);
  const taaliScore = normalizeScore100(application?.taali_score ?? application?.rank_score);
  const collab = aiCollabSummary(application);
  const signal = hireSignalModel(application);
  const threshold = roleThresholdFor(application, rolesById);
  const belowThreshold = isBelowThreshold(application, rolesById);
  const statusLabel = String(
    application?.valid_assessment_status
    || application?.pipeline_stage
    || application?.status
    || 'applied',
  ).replace(/_/g, ' ');

  const statusTone = String(statusLabel).toLowerCase().includes('review')
    ? 'var(--purple)'
    : String(statusLabel).toLowerCase().includes('invite')
      ? 'var(--amber)'
      : String(statusLabel).toLowerCase().includes('submit') || String(statusLabel).toLowerCase().includes('complete')
        ? 'var(--green)'
        : 'var(--mute)';
  const rowBackground = selected
    ? 'color-mix(in oklab, var(--purple) 6%, var(--bg-2))'
    : belowThreshold
      ? 'color-mix(in oklab, var(--red) 4%, var(--bg-2))'
      : 'transparent';

  return (
    <button
      type="button"
      className="grid w-full grid-cols-[40px_2.2fr_1.1fr_1fr_1fr_.85fr_1fr_.85fr_.8fr] items-center gap-4 border-t px-6 py-4 text-left transition hover:bg-[var(--bg)]"
      style={{
        borderColor: 'var(--line)',
        background: rowBackground,
      }}
      onClick={() => onOpen(application)}
    >
      <div onClick={(event) => event.stopPropagation()}>
        <input type="checkbox" checked={selected} onChange={() => onToggleSelect(application.id)} />
      </div>

      <div className="min-w-0">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[var(--purple-soft)] text-[11.5px] font-semibold text-[var(--purple)]">
            {initialsFor(application?.candidate_name)}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <div className="truncate text-[14.5px] font-semibold tracking-[-0.01em]">{application?.candidate_name || 'Unknown candidate'}</div>
              {application?.workable_sourced ? <WorkableTagSm /> : null}
            </div>
            <div className="truncate text-[12.5px] text-[var(--mute)]">{application?.candidate_email || 'No email'}</div>
            {belowThreshold ? (
              <div
                className="mt-1 inline-flex items-center gap-1 rounded-full border px-2 py-1 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.06em] text-[var(--red)]"
                style={belowThresholdBadgeStyle}
              >
                <AlertCircle size={10} />
                Below threshold
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <div className="min-w-0">
        <div className="truncate text-[13.5px] font-medium">{application?.role_name || 'Unknown role'}</div>
        <div className="mt-1 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.06em] text-[var(--mute)]">
          Threshold {threshold}%
        </div>
      </div>

      <div>
        <div className={`font-[var(--font-mono)] text-[13.5px] font-semibold ${
          cvMatchTone(cvMatch) === 'hi'
            ? 'text-[var(--green)]'
            : cvMatchTone(cvMatch) === 'md'
              ? 'text-[var(--purple)]'
              : cvMatchTone(cvMatch) === 'lo'
                ? 'text-[var(--red)]'
                : 'text-[var(--mute)]'
        }`.trim()}
        >
          {cvMatch == null ? '—' : `${cvMatch}%`}
        </div>
        <div className="relative mt-2 h-[7px] rounded-full bg-[var(--bg)]">
          <div
            className="h-full rounded-full"
            style={{
              width: `${Math.max(0, Math.min(100, cvMatch || 0))}%`,
              background: cvMatchTone(cvMatch) === 'hi'
                ? 'linear-gradient(90deg, color-mix(in oklab, var(--green) 88%, white), color-mix(in oklab, var(--green) 64%, white))'
                : cvMatchTone(cvMatch) === 'md'
                  ? 'linear-gradient(90deg, color-mix(in oklab, var(--purple) 88%, white), color-mix(in oklab, var(--purple) 64%, white))'
                  : 'linear-gradient(90deg, color-mix(in oklab, var(--red) 88%, white), color-mix(in oklab, var(--red) 64%, white))',
            }}
          />
          <span
            className="absolute -top-[4px] h-[15px] w-px bg-[var(--ink)]/40"
            style={{ left: `calc(${threshold}% - 1px)` }}
          />
        </div>
      </div>

      <div className="font-[var(--font-mono)] text-[13.5px]">
        {taaliScore == null ? (
          <span className="text-[var(--mute)]">—</span>
        ) : (
          <div className="flex items-center gap-2">
            <span className={taaliScore >= 80 ? 'text-[var(--green)]' : taaliScore >= 65 ? 'text-[var(--purple)]' : 'text-[var(--red)]'}>
              {taaliScore}
            </span>
            <span className="text-[11px] text-[var(--mute)]">/100</span>
            {application?.workable_score_raw != null && taaliScore != null ? (
              <WorkableScorePip value={application.workable_score_raw} />
            ) : null}
          </div>
        )}
      </div>

      <div>
        {collab.score == null ? (
          <span className="font-[var(--font-mono)] text-[12px] text-[var(--mute)]">—</span>
        ) : (
          <span className="font-[var(--font-mono)] text-[12.5px] font-semibold" style={{ color: collab.tone }}>
            {collab.label} · {collab.score}
          </span>
        )}
      </div>

      <div>
        <span
          className="inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-[12px] font-medium"
          style={signal.tone === 'green' ? strongSignalStyle : signal.tone === 'amber' ? maybeSignalStyle : noHireSignalStyle}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-current" />
          {signal.label}
        </span>
      </div>

      <div className="font-[var(--font-mono)] text-[11.5px] uppercase tracking-[0.06em]" style={{ color: statusTone }}>
        <span className="inline-flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-current" />
          {statusLabel}
        </span>
      </div>

      <div className="font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">
        {formatRelativeTime(
          application?.assessment_preview?.completed_at
          || application?.updated_at
          || application?.pipeline_stage_updated_at,
        )}
      </div>
    </button>
  );
};

export const CandidatesPage = ({ onNavigate }) => {
  const { showToast } = useToast();
  const [applications, setApplications] = useState([]);
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [segment, setSegment] = useState('all');
  const [roleFilter, setRoleFilter] = useState('all');
  const [sortValue, setSortValue] = useState('recent');
  const [workableOnly, setWorkableOnly] = useState(false);
  const [belowThresholdOnly, setBelowThresholdOnly] = useState(false);
  const [minCvMatch, setMinCvMatch] = useState('');
  const [selectedIds, setSelectedIds] = useState([]);
  const [bulkRejecting, setBulkRejecting] = useState(false);
  const [thresholdSaving, setThresholdSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError('');
      try {
        const [rolesRes, applicationsRes] = await Promise.allSettled([
          rolesApi.list({ include_pipeline_stats: true }),
          rolesApi.listApplicationsGlobal(globalApplicationsQuery),
        ]);
        if (cancelled) return;

        const nextRoles = rolesRes.status === 'fulfilled' && Array.isArray(rolesRes.value?.data)
          ? rolesRes.value.data
          : [];
        setRoles(nextRoles);

        if (applicationsRes.status === 'fulfilled') {
          setApplications(Array.isArray(applicationsRes.value?.data?.items) ? applicationsRes.value.data.items : []);
          return;
        }

        if (nextRoles.length) {
          const fallback = await listFallbackApplicationsFromRolePipelines(nextRoles);
          if (cancelled) return;
          if (fallback.succeeded > 0) {
            setApplications(fallback.items);
            return;
          }
        }

        setApplications([]);
        setError('Failed to load candidates.');
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

  const rolesById = useMemo(
    () => Object.fromEntries(roles.map((role) => [String(role.id), role])),
    [roles],
  );

  const thresholdRole = useMemo(() => {
    if (roleFilter !== 'all') return rolesById[String(roleFilter)] || null;
    const roleIds = Array.from(new Set(applications.map((item) => String(item?.role_id)).filter(Boolean)));
    if (roleIds.length === 1) return rolesById[roleIds[0]] || null;
    return null;
  }, [applications, roleFilter, rolesById]);

  const filtered = useMemo(() => {
    const minimumCv = Number(minCvMatch);
    const needle = search.trim().toLowerCase();

    const next = applications
      .filter((application) => matchesSegment(application, segment))
      .filter((application) => {
        if (roleFilter !== 'all' && String(application?.role_id) !== roleFilter) return false;
        if (workableOnly && application?.workable_sourced !== true) return false;
        if (belowThresholdOnly && !isBelowThreshold(application, rolesById)) return false;
        if (minCvMatch !== '' && Number.isFinite(minimumCv) && (normalizeScore100(application?.cv_match_score) || 0) < minimumCv) return false;
        if (!needle) return true;
        const haystack = [
          application?.candidate_name,
          application?.candidate_email,
          application?.role_name,
        ].join(' ').toLowerCase();
        return haystack.includes(needle);
      });

    next.sort((left, right) => {
      if (sortValue === 'cv-desc') {
        return (normalizeScore100(right?.cv_match_score) || -1) - (normalizeScore100(left?.cv_match_score) || -1);
      }
      if (sortValue === 'taali-desc') {
        return (normalizeScore100(right?.taali_score ?? right?.rank_score) || -1) - (normalizeScore100(left?.taali_score ?? left?.rank_score) || -1);
      }
      return new Date(right?.updated_at || right?.pipeline_stage_updated_at || 0).getTime()
        - new Date(left?.updated_at || left?.pipeline_stage_updated_at || 0).getTime();
    });

    return next;
  }, [applications, belowThresholdOnly, minCvMatch, roleFilter, rolesById, search, segment, sortValue, workableOnly]);

  const counts = useMemo(() => ({
    all: applications.length,
    in_assessment: applications.filter((item) => item?.pipeline_stage === 'in_assessment').length,
    review: applications.filter((item) => item?.pipeline_stage === 'review').length,
    shortlist: applications.filter((item) => normalizeScore100(item?.taali_score ?? item?.rank_score) >= 80).length,
  }), [applications]);

  const thresholdRoleBelowCount = useMemo(() => {
    if (!thresholdRole) return 0;
    return applications.filter((application) => String(application?.role_id) === String(thresholdRole.id))
      .filter((application) => isBelowThreshold(application, rolesById))
      .length;
  }, [applications, rolesById, thresholdRole]);

  const selectedApplications = useMemo(
    () => filtered.filter((application) => selectedIds.includes(application.id)),
    [filtered, selectedIds],
  );

  const toggleSelection = (applicationId) => {
    setSelectedIds((current) => (
      current.includes(applicationId)
        ? current.filter((id) => id !== applicationId)
        : [...current, applicationId]
    ));
  };

  const toggleSelectAllVisible = () => {
    const visibleIds = filtered.map((item) => item.id);
    const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.includes(id));
    setSelectedIds((current) => {
      if (allVisibleSelected) {
        return current.filter((id) => !visibleIds.includes(id));
      }
      return Array.from(new Set([...current, ...visibleIds]));
    });
  };

  const openApplication = (application) => {
    onNavigate('candidate-report', { candidateApplicationId: application.id });
  };

  const handleThresholdChange = async (delta) => {
    if (!thresholdRole || thresholdSaving) return;
    const nextThreshold = Math.max(0, Math.min(100, Number(thresholdRole.reject_threshold || 60) + delta));
    setThresholdSaving(true);
    try {
      await rolesApi.update(thresholdRole.id, { reject_threshold: nextThreshold });
      setRoles((current) => current.map((role) => (
        role.id === thresholdRole.id ? { ...role, reject_threshold: nextThreshold } : role
      )));
      setApplications((current) => current.map((application) => (
        String(application?.role_id) === String(thresholdRole.id)
          ? {
            ...application,
            role_reject_threshold: nextThreshold,
            below_role_threshold: normalizeScore100(application?.cv_match_score) != null
              ? normalizeScore100(application?.cv_match_score) < nextThreshold
              : application?.below_role_threshold,
          }
          : application
      )));
    } catch (requestError) {
      showToast(requestError?.response?.data?.detail || 'Failed to update reject threshold.', 'error');
    } finally {
      setThresholdSaving(false);
    }
  };

  const handleBulkReject = async () => {
    if (!selectedApplications.length || bulkRejecting) return;
    setBulkRejecting(true);
    try {
      await rolesApi.bulkRejectApplications({
        application_ids: selectedApplications.map((item) => item.id),
        reason: 'Below threshold',
      });
      const selectedSet = new Set(selectedApplications.map((item) => item.id));
      setApplications((current) => current.filter((application) => !selectedSet.has(application.id)));
      setSelectedIds([]);
      showToast(`Rejected ${selectedApplications.length} candidate${selectedApplications.length === 1 ? '' : 's'}.`, 'success');
    } catch (requestError) {
      showToast(requestError?.response?.data?.detail || 'Failed to reject selected candidates.', 'error');
    } finally {
      setBulkRejecting(false);
    }
  };

  const allVisibleSelected = filtered.length > 0 && filtered.every((item) => selectedIds.includes(item.id));

  return (
    <AppShell currentPage="candidates" onNavigate={onNavigate}>
      <div className="page">
        <div className="page-head">
          <div className="tally-bg" />
          <div>
            <div className="kicker">02 · RECRUITER WORKSPACE</div>
            <h1>Candidates<em>.</em></h1>
            <p className="sub">Every person across every role, scored and filterable. Click a row to open their assessment report.</p>
          </div>
          <div className="row">
            <button type="button" className="btn btn-outline btn-sm">Export CSV</button>
            <button type="button" className="btn btn-purple btn-sm" onClick={() => onNavigate('jobs')}>+ Invite candidate</button>
          </div>
        </div>

        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-[18px] border border-[var(--line)] bg-[var(--bg-2)] px-4 py-4 shadow-[var(--shadow-sm)]">
          <div className="inline-flex gap-1 rounded-full border border-[var(--line)] bg-[var(--bg)] p-1">
            {SEGMENTS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`app-tab ${segment === item.id ? 'active' : ''}`.trim()}
                onClick={() => setSegment(item.id)}
              >
                {item.label} · {counts[item.id]}
              </button>
            ))}
          </div>

          <label className="relative min-w-[280px] grow">
            <Search size={16} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[var(--mute)]" />
            <input
              className="w-full rounded-full border border-[var(--line)] bg-[var(--bg)] py-3 pl-11 pr-4 text-sm"
              placeholder="Search by name, email, or role…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>

          <select
            className="rounded-full border border-[var(--line)] bg-[var(--bg)] px-4 py-3 text-sm"
            value={roleFilter}
            onChange={(event) => setRoleFilter(event.target.value)}
          >
            <option value="all">All roles</option>
            {roles.map((role) => (
              <option key={role.id} value={String(role.id)}>{role.name}</option>
            ))}
          </select>

          <button
            type="button"
            className={`filter-chip ${belowThresholdOnly ? 'on' : ''}`.trim()}
            style={belowThresholdOnly ? {
              background: 'color-mix(in oklab, var(--red) 10%, transparent)',
              borderColor: 'color-mix(in oklab, var(--red) 30%, var(--line))',
              color: 'var(--red)',
            } : undefined}
            onClick={() => setBelowThresholdOnly((value) => !value)}
          >
            <AlertCircle size={12} />
            Below threshold · {applications.filter((item) => isBelowThreshold(item, rolesById)).length}
          </button>

          <button
            type="button"
            className={`filter-chip ${workableOnly ? 'on' : ''}`.trim()}
            onClick={() => setWorkableOnly((value) => !value)}
          >
            <ArrowRight size={12} />
            From Workable
          </button>

          <label className="filter-chip">
            CV match ≥
            <input
              aria-label="CV match minimum"
              className="ml-2 w-14 border-0 bg-transparent p-0 text-right font-[var(--font-mono)] text-[12px] outline-none"
              placeholder="70"
              value={minCvMatch}
              onChange={(event) => setMinCvMatch(event.target.value)}
            />
          </label>

          <select
            className="rounded-full border border-[var(--line)] bg-[var(--bg)] px-4 py-3 text-sm"
            value={sortValue}
            onChange={(event) => setSortValue(event.target.value)}
          >
            <option value="recent">Recent activity</option>
            <option value="cv-desc">CV match high to low</option>
            <option value="taali-desc">Taali high to low</option>
          </select>

          <div className="basis-full h-0" />

          {thresholdRole ? (
            <div className="inline-flex items-center gap-2 rounded-full border border-[var(--line)] bg-[var(--bg)] px-3 py-2.5 text-[12px]">
              <span className="h-2 w-2 rounded-full bg-[var(--red)]" />
              <span className="font-[var(--font-mono)] uppercase tracking-[0.06em] text-[var(--mute)]">{thresholdRole.name} threshold:</span>
              <b>{thresholdRole.reject_threshold || 60}%</b>
              <button type="button" className="icon-btn !h-7 !w-7" onClick={() => handleThresholdChange(-5)} disabled={thresholdSaving}>
                <Minus size={12} />
              </button>
              <button type="button" className="icon-btn !h-7 !w-7" onClick={() => handleThresholdChange(5)} disabled={thresholdSaving}>
                <Plus size={12} />
              </button>
              <span className="ml-1 text-[var(--mute)]">{thresholdRoleBelowCount} below</span>
            </div>
          ) : (
            <span className="font-[var(--font-mono)] text-[11.5px] uppercase tracking-[0.08em] text-[var(--mute)]">
              Select a single role to adjust its reject threshold
            </span>
          )}

          <span className="font-[var(--font-mono)] text-[11.5px] uppercase tracking-[0.06em] text-[var(--mute)]">
            CV scored against job spec + recruiter requirements
          </span>
        </div>

        {selectedApplications.length ? (
          <div
            className="mb-4 flex flex-wrap items-center gap-3 rounded-[16px] border bg-[var(--bg-2)] px-4 py-3 shadow-[var(--shadow-sm)]"
            style={{ borderColor: 'color-mix(in oklab, var(--red) 18%, var(--line))' }}
          >
            <span className="rounded-full bg-[var(--ink)] px-2.5 py-1 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--bg)]">
              {selectedApplications.length} selected
            </span>
            <span className="flex-1 text-[13.5px] text-[var(--ink-2)]">
              Bulk action — {selectedApplications.filter((item) => isBelowThreshold(item, rolesById)).length} selected candidate{selectedApplications.length === 1 ? '' : 's'} currently score below the role threshold.
            </span>
            <button type="button" className="btn btn-outline btn-sm">Add note</button>
            <button type="button" className="btn btn-outline btn-sm">Move stage</button>
            <button type="button" className="btn btn-outline btn-sm text-[var(--red)]" onClick={handleBulkReject} disabled={bulkRejecting}>
              <X size={14} />
              {bulkRejecting ? 'Rejecting…' : `Reject ${selectedApplications.length}`}
            </button>
            <button type="button" className="icon-btn" onClick={() => setSelectedIds([])} title="Clear selection">
              <X size={14} />
            </button>
          </div>
        ) : null}

        {error ? (
          <div className="mb-4 rounded-[var(--radius-lg)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error}
          </div>
        ) : null}

        <div className="overflow-hidden rounded-[20px] border border-[var(--line)] bg-[var(--bg-2)] shadow-[var(--shadow-sm)]">
          <div className="grid grid-cols-[40px_2.2fr_1.1fr_1fr_1fr_.85fr_1fr_.85fr_.8fr] gap-4 border-b border-[var(--line)] bg-[var(--bg)] px-6 py-4">
            <div className={tableHeaderClass}>
              <input type="checkbox" checked={allVisibleSelected} onChange={toggleSelectAllVisible} />
            </div>
            <div className={tableHeaderClass}>Candidate</div>
            <div className={tableHeaderClass}>Role</div>
            <div className={tableHeaderClass}>CV match</div>
            <div className={tableHeaderClass}>Taali score</div>
            <div className={tableHeaderClass}>AI collab</div>
            <div className={tableHeaderClass}>Hire signal</div>
            <div className={tableHeaderClass}>Status</div>
            <div className={tableHeaderClass}>Submitted</div>
          </div>

          {loading ? (
            <div className="space-y-2 p-6">
              {Array.from({ length: 6 }).map((_, index) => (
                <div key={index} className="h-16 animate-pulse rounded-[12px] bg-[var(--bg)]" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <div className="p-10 text-center">
              <h2 className="font-[var(--font-display)] text-[28px] tracking-[-0.03em]">No candidates match these filters.</h2>
              <p className="mt-2 text-[14px] leading-7 text-[var(--mute)]">Try clearing a filter or syncing another role from Workable.</p>
            </div>
          ) : (
            filtered.map((application) => (
              <CandidateRow
                key={application.id}
                application={application}
                selected={selectedIds.includes(application.id)}
                rolesById={rolesById}
                onToggleSelect={toggleSelection}
                onOpen={openApplication}
              />
            ))
          )}
        </div>
      </div>
    </AppShell>
  );
};

export default CandidatesPage;
