import React, { useState, useEffect, useCallback } from 'react';
import { Clipboard, DollarSign, CheckCircle, Eye, Timer, Star, Users } from 'lucide-react';
import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer, Legend } from 'recharts';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { getDocumentTitle } from '../../config/brand';
import * as apiClient from '../../shared/api';
import { COMPARISON_CATEGORY_CONFIG, getCategoryScoresFromAssessment } from '../../lib/comparisonCategories';
import { ASSESSMENT_PRICE_AED, aedToUsd, formatAed } from '../../lib/currency';
import { Button, Select, Spinner, TableShell } from '../../shared/ui/TaaliPrimitives';
import { StatCardSkeleton, TableRowSkeleton } from '../../shared/ui/Skeletons';

const PAGE_SIZE = 10;
const MAX_COMPARE = 5;
const COMPARE_COLORS = ['var(--taali-purple)', 'var(--taali-text)', 'var(--taali-success)', 'var(--taali-warning)', 'var(--taali-info)'];
const ONBOARDING_DISMISSED_KEY = 'taali_onboarding_dismissed';

const normalizeAssessmentStatus = (status) => {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'submitted' || normalized === 'graded') return 'completed';
  if (['completed', 'completed_due_to_timeout', 'in_progress', 'expired', 'pending'].includes(normalized)) {
    return normalized;
  }
  if (normalized.includes('progress')) return 'in_progress';
  if (normalized.includes('timeout')) return 'completed_due_to_timeout';
  if (normalized.includes('expire')) return 'expired';
  if (normalized.includes('complete')) return 'completed';
  return 'pending';
};

const isCompletedStatus = (status) => {
  const normalized = normalizeAssessmentStatus(status);
  return normalized === 'completed' || normalized === 'completed_due_to_timeout';
};

const daysUntil = (value) => {
  if (!value) return null;
  const target = new Date(value);
  if (Number.isNaN(target.getTime())) return null;
  const now = new Date();
  return Math.ceil((target.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
};

export const DashboardPage = ({
  onNavigate,
  onViewCandidate,
  NavComponent,
  StatsCardComponent,
  StatusBadgeComponent,
}) => {
  const assessmentsApi = apiClient.assessments;
  const tasksApi = apiClient.tasks;
  const rolesApi = 'roles' in apiClient ? apiClient.roles : null;
  const candidatesApi = 'candidates' in apiClient ? apiClient.candidates : null;
  const { showToast } = useToast();
  const { user } = useAuth();
  const [assessmentsList, setAssessmentsList] = useState([]);
  const [totalAssessmentsCount, setTotalAssessmentsCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingViewId, setLoadingViewId] = useState(null);
  const [loadingResendId, setLoadingResendId] = useState(null);
  const [statusFilter, setStatusFilter] = useState('');
  const [taskFilter, setTaskFilter] = useState('');
  const [tasksForFilter, setTasksForFilter] = useState([]);
  const [rolesForFilter, setRolesForFilter] = useState([]);
  const [roleFilter, setRoleFilter] = useState('');
  const [page, setPage] = useState(0);
  const [compareIds, setCompareIds] = useState([]);
  const [compareAssessments, setCompareAssessments] = useState([]);
  const [compareLoadingId, setCompareLoadingId] = useState(null);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  const [rolesCount, setRolesCount] = useState(0);
  const [candidatesCount, setCandidatesCount] = useState(0);
  const [onboardingDismissed, setOnboardingDismissed] = useState(
    () => (typeof window !== 'undefined' && window.localStorage.getItem(ONBOARDING_DISMISSED_KEY) === 'true')
  );

  useEffect(() => {
    const previousTitle = document.title;
    document.title = getDocumentTitle('Assessments');
    return () => {
      document.title = previousTitle;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    tasksApi.list().then((res) => { if (!cancelled) setTasksForFilter(res.data || []); }).catch(() => {});
    if (rolesApi?.list) {
      rolesApi.list().then((res) => {
        if (cancelled) return;
        const roles = Array.isArray(res.data) ? res.data : [];
        setRolesForFilter(roles);
        setRolesCount(roles.length);
      }).catch(() => {});
    }
    if (candidatesApi?.list) {
      const candidatesReq = candidatesApi.list({ limit: 1, offset: 0 });
      if (candidatesReq && typeof candidatesReq.then === 'function') {
        candidatesReq.then((res) => {
          if (cancelled) return;
          const payload = res.data || {};
          const total = typeof payload.total === 'number'
            ? payload.total
            : Array.isArray(payload.items)
              ? payload.items.length
              : Array.isArray(payload)
                ? payload.length
                : 0;
          setCandidatesCount(total);
        }).catch(() => {});
      }
    }
    return () => { cancelled = true; };
  }, [candidatesApi, rolesApi, tasksApi]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const params = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
    if (statusFilter) params.status = statusFilter;
    if (taskFilter) params.task_id = taskFilter;
    if (roleFilter) params.role_id = roleFilter;
    assessmentsApi.list(params)
      .then((res) => {
        if (cancelled) return;
        const data = res.data || {};
        setAssessmentsList(Array.isArray(data) ? data : (data.items || []));
        setTotalAssessmentsCount(typeof data.total === 'number' ? data.total : (data.items || []).length);
      })
      .catch((err) => {
        console.warn('Failed to fetch assessments:', err.message);
        if (!cancelled) setAssessmentsList([]);
        if (!cancelled) setTotalAssessmentsCount(0);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [page, statusFilter, taskFilter, roleFilter, reloadTick]);

  const getAssessmentLink = (token) =>
    `${typeof window !== 'undefined' ? window.location.origin : ''}/assess/${token || ''}`;

  // Map API assessments to table-friendly shape, falling back to mock data
  const displayCandidates = assessmentsList.length > 0
    ? assessmentsList.map((a) => ({
        id: a.id,
        name: (a.candidate_name || a.candidate?.full_name || a.candidate_email || '').trim() || 'Unknown',
        email: a.candidate_email || a.candidate?.email || '',
        task: a.task?.name || a.task_name || 'Assessment',
        role: a.role_name || a.task?.role || 'Unassigned role',
        status: normalizeAssessmentStatus(a.status),
        score: a.score ?? a.overall_score ?? null,
        time: a.duration_taken ? `${Math.round(a.duration_taken / 60)}m` : '—',
        position: a.candidate?.position || a.task?.name || '',
        completedDate: a.completed_at ? new Date(a.completed_at).toLocaleDateString() : null,
        breakdown: a.breakdown || null,
        prompts: a.prompt_count ?? 0,
        promptsList: a.prompts_list || [],
        timeline: a.timeline || [],
        results: a.results || [],
        token: a.token,
        expiresAt: a.expires_at || null,
        assessmentLink: a.token ? getAssessmentLink(a.token) : '',
        _raw: a,
      }))
    : [];

  const userName = user?.full_name?.split(' ')[0] || 'there';

  // Compute live stats from current page (total count from API)
  const totalAssessments = totalAssessmentsCount;
  const completedCount = displayCandidates.filter((c) => isCompletedStatus(c.status)).length;
  const inProgressCount = displayCandidates.filter((c) => c.status === 'in_progress').length;
  const totalPages = Math.max(1, Math.ceil(totalAssessmentsCount / PAGE_SIZE));
  const startRow = page * PAGE_SIZE + 1;
  const endRow = Math.min((page + 1) * PAGE_SIZE, totalAssessmentsCount);
  const completionRate = totalAssessments > 0 ? ((completedCount / totalAssessments) * 100).toFixed(1) : '0';
  const scores = displayCandidates.filter((c) => c.score !== null).map((c) => c.score);
  const avgScore = scores.length > 0 ? (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1) : '—';
  const monthCostAed = completedCount * ASSESSMENT_PRICE_AED;
  const monthCost = formatAed(monthCostAed);
  const notifications = displayCandidates
    .filter((c) => isCompletedStatus(c.status))
    .slice(0, 5)
    .map((c) => ({
      id: `n-${c.id}`,
      text: `${c.name} completed ${c.task} (${c.score ?? '—'}/10)`,
    }));
  const toggleCompare = useCallback(async (c, checked) => {
    if (checked) {
      if (compareIds.length >= MAX_COMPARE) return;
      const id = c.id;
      setCompareIds((prev) => Array.from(new Set([...prev, id])).slice(-MAX_COMPARE));
      const existing = displayCandidates.find((x) => x.id === id);
      const hasBreakdown = existing?.breakdown?.categoryScores ?? existing?.breakdown?.detailedScores?.category_scores;
      if (existing && hasBreakdown) {
        setCompareAssessments((prev) => {
          const next = prev.filter((a) => a.id !== id);
          next.push({ id: existing.id, name: existing.name, task: existing.task, score: existing.score, breakdown: existing.breakdown, _raw: existing._raw });
          return next;
        });
        return;
      }
      setCompareLoadingId(id);
      try {
        const res = await assessmentsApi.get(id);
        const a = res.data;
        const name = (a.candidate_name || a.candidate_email || '').trim() || `Assessment ${id}`;
        setCompareAssessments((prev) => {
          const next = prev.filter((x) => x.id !== id);
          next.push({ id: a.id, name, task: a.task_name || a.task?.name || '', score: a.score ?? a.final_score, breakdown: a.breakdown || null, _raw: a });
          return next;
        });
      } catch {
        setCompareIds((prev) => prev.filter((x) => x !== id));
      } finally {
        setCompareLoadingId(null);
      }
    } else {
      const id = c.id;
      setCompareIds((prev) => prev.filter((x) => x !== id));
      setCompareAssessments((prev) => prev.filter((a) => a.id !== id));
    }
  }, [compareIds.length, displayCandidates]);

  const compareCandidates = displayCandidates.filter((c) => compareIds.includes(c.id));
  const tableRows = !roleFilter && displayCandidates.length > 0
    ? (() => {
        const byRole = {};
        displayCandidates.forEach((c) => {
          const roleName = c.role || 'Unassigned role';
          if (!byRole[roleName]) byRole[roleName] = [];
          byRole[roleName].push(c);
        });
        const roleOrder = [...new Set(displayCandidates.map((c) => c.role || 'Unassigned role'))].sort();
        return roleOrder.flatMap((roleName) => [{ _group: roleName }, ...byRole[roleName]]);
      })()
    : displayCandidates;

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(displayCandidates, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'assessments.json';
    a.click();
    URL.revokeObjectURL(url);
  };
  const exportCsv = () => {
    const rows = [['Candidate', 'Email', 'Task', 'Status', 'Score']].concat(
      displayCandidates.map((c) => [c.name, c.email, c.task, c.status, c.score ?? ''])
    );
    const csv = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'assessments.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const exportSelectedCsv = () => {
    const selected = displayCandidates.filter((candidate) => compareIds.includes(candidate.id));
    const rows = [['Candidate', 'Email', 'Task', 'Role', 'Status', 'Score', 'Task Completion', 'Prompt Clarity', 'Context Provision', 'Independence & Efficiency', 'Response Utilization', 'Debugging & Design', 'Written Communication', 'Role Fit']]
      .concat(selected.map((candidate) => {
        const categories = getCategoryScoresFromAssessment(candidate);
        return [
          candidate.name,
          candidate.email,
          candidate.task,
          candidate.role || '',
          candidate.status,
          candidate.score ?? '',
          categories.task_completion ?? '',
          categories.prompt_clarity ?? '',
          categories.context_provision ?? '',
          categories.independence_efficiency ?? '',
          categories.response_utilization ?? '',
          categories.debugging_design ?? '',
          categories.written_communication ?? '',
          categories.role_fit ?? '',
        ];
      }));
    const csv = rows.map((row) => row.map((value) => `"${String(value).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'selected-assessments.csv';
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const handleBulkDelete = async () => {
    if (compareIds.length === 0 || bulkDeleting) return;
    if (!window.confirm(`Delete ${compareIds.length} selected assessment(s)? This cannot be undone.`)) return;
    setBulkDeleting(true);
    let deletedCount = 0;
    for (const id of compareIds) {
      try {
        // eslint-disable-next-line no-await-in-loop
        await assessmentsApi.remove(id);
        deletedCount += 1;
      } catch {
        // Continue deleting remaining rows.
      }
    }
    setBulkDeleting(false);
    setCompareIds([]);
    setCompareAssessments([]);
    setReloadTick((tick) => tick + 1);
    if (deletedCount > 0) {
      showToast(`Deleted ${deletedCount} assessment(s).`, 'success');
    } else {
      showToast('No assessments were deleted.', 'error');
    }
  };

  const handleResend = async (assessmentId) => {
    setLoadingResendId(assessmentId);
    try {
      await assessmentsApi.resend(assessmentId);
      showToast('Assessment invite resent.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to resend invite.', 'error');
    } finally {
      setLoadingResendId(null);
    }
  };

  const dismissOnboarding = () => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(ONBOARDING_DISMISSED_KEY, 'true');
    }
    setOnboardingDismissed(true);
  };

  const hasRoles = rolesCount > 0;
  const hasCandidates = candidatesCount > 0;
  const hasSentAssessment = totalAssessmentsCount > 0;

  return (
    <div>
      <NavComponent currentPage="dashboard" onNavigate={onNavigate} />
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-[var(--taali-text)]">Assessments</h1>
            <p className="text-sm text-[var(--taali-muted)] mt-1">Welcome back, {userName}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3 mb-6">
          <Button variant="secondary" size="sm" onClick={exportCsv}>Export CSV</Button>
          <Button variant="secondary" size="sm" onClick={exportJson}>Export JSON</Button>
        </div>
        {notifications.length > 0 && (
          <div className="border-2 border-[var(--taali-border)] p-4 mb-6 bg-[var(--taali-surface)]">
            <div className="font-mono text-xs text-[var(--taali-muted)] mb-2">Recent Notifications</div>
            <div className="space-y-1">
              {notifications.map((n) => (
                <div key={n.id} className="text-sm text-[var(--taali-text)]">• {n.text}</div>
              ))}
            </div>
          </div>
        )}
        {compareAssessments.length >= 2 && (
          <div id="dashboard-compare-overlay" className="border-2 border-[var(--taali-border)] p-6 mb-6 bg-[var(--taali-purple-soft)]">
            <div className="flex items-center gap-2 mb-4">
              <Users size={20} />
              <h3 className="font-bold text-lg">Candidate comparison</h3>
            </div>
            <p className="text-xs text-[var(--taali-muted)] mb-4">Overlay by category and overall score. Compare up to {MAX_COMPARE} candidates.</p>
            <div className="grid md:grid-cols-2 gap-8">
              <div>
                <div className="font-mono text-xs font-bold uppercase text-[var(--taali-muted)] mb-2">Overall score</div>
                <div className="space-y-2">
                  {compareAssessments.map((a, i) => (
                    <div key={a.id} className="flex items-center gap-3">
                      <span className="w-3 h-3 shrink-0" style={{ backgroundColor: COMPARE_COLORS[i % COMPARE_COLORS.length] }} />
                      <span className="font-medium min-w-[120px]">{a.name}</span>
                      <span className="font-mono text-sm">{a.score != null ? `${Number(a.score).toFixed(1)}/10` : '—'}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                {(() => {
                  const candKey = (i) => `_cand_${i}`;
                  const radarData = COMPARISON_CATEGORY_CONFIG.map((cat) => {
                    const point = { dimension: cat.label, fullMark: 10 };
                    compareAssessments.forEach((a, i) => {
                      const scores = getCategoryScoresFromAssessment(a);
                      point[candKey(i)] = scores[cat.key] ?? 0;
                    });
                    return point;
                  });
                  const hasAnyCategoryScore = radarData.some((row) => compareAssessments.some((_, i) => (row[candKey(i)] ?? 0) > 0));
                  if (!hasAnyCategoryScore) return <div className="text-sm text-[var(--taali-muted)]">No category scores available for overlay.</div>;
                  return (
                    <div className="w-full h-[320px]">
                      <ResponsiveContainer>
                        <RadarChart data={radarData}>
                          <PolarGrid />
                          <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 10, fontFamily: 'var(--taali-font)' }} />
                          <PolarRadiusAxis domain={[0, 10]} tick={{ fontSize: 10 }} />
                          {compareAssessments.map((a, i) => (
                            <Radar
                              key={a.id}
                              name={a.name || `Candidate ${i + 1}`}
                              dataKey={candKey(i)}
                              stroke={COMPARE_COLORS[i % COMPARE_COLORS.length]}
                              fill={COMPARE_COLORS[i % COMPARE_COLORS.length]}
                              fillOpacity={0.15}
                              strokeWidth={1.5}
                            />
                          ))}
                          <Legend />
                        </RadarChart>
                      </ResponsiveContainer>
                    </div>
                  );
                })()}
              </div>
            </div>
          </div>
        )}

        {/* Stats Cards */}
        {loading ? (
          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
            <StatCardSkeleton />
            <StatCardSkeleton />
            <StatCardSkeleton />
            <StatCardSkeleton />
          </div>
        ) : (
          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
            <StatsCardComponent
              icon={Clipboard}
              label="Active Assessments"
              value={String(inProgressCount)}
              change={`${completedCount} completed`}
              onClick={() => {
                setStatusFilter('in_progress');
                setPage(0);
              }}
            />
            <StatsCardComponent
              icon={CheckCircle}
              label="Completion Rate"
              value={`${completionRate}%`}
              change={`${completedCount} completed`}
              onClick={() => onNavigate('analytics')}
            />
            <StatsCardComponent
              icon={Star}
              label="Avg Score"
              value={avgScore !== '—' ? `${avgScore}/10` : '—'}
              change="Candidates this month"
              onClick={() => onNavigate('analytics')}
            />
            <StatsCardComponent
              icon={DollarSign}
              label="This Month Cost"
              value={monthCost}
              subValue={`≈ $${aedToUsd(monthCostAed)} USD`}
              change={`${completedCount} assessments`}
              onClick={() => onNavigate('settings-billing')}
            />
          </div>
        )}

        {totalAssessmentsCount === 0 && !onboardingDismissed ? (
          <div className="mb-8 border-2 border-[var(--taali-purple)] bg-[var(--taali-surface)] p-6">
            <div className="mb-4 flex items-start justify-between gap-4">
              <h2 className="text-lg font-bold text-[var(--taali-text)]">Get started with TAALI</h2>
              <Button variant="ghost" size="sm" onClick={dismissOnboarding}>Dismiss</Button>
            </div>
            <ol className="space-y-2 font-mono text-sm text-[var(--taali-text)]">
              <li>{hasRoles ? '✓' : '○'} Create a role</li>
              <li>{hasCandidates ? '✓' : '○'} Add a candidate with their CV</li>
              <li>{hasSentAssessment ? '✓' : '○'} Send them an assessment link</li>
              <li>○ Review results here once they finish</li>
            </ol>
            <div className="mt-4">
              <Button variant="secondary" size="sm" onClick={() => onNavigate('candidates')}>Go to Candidates</Button>
            </div>
          </div>
        ) : null}

        {/* Filters: split by job role (task); recruiters hire for many roles */}
        <div className="flex flex-wrap items-center gap-4 mb-4">
          <span className="font-mono text-sm font-bold text-[var(--taali-text)]">Filters:</span>
          <Select
            className="w-auto min-w-[140px]"
            value={roleFilter}
            onChange={(e) => { setRoleFilter(e.target.value); setPage(0); }}
          >
            <option value="">All roles</option>
            {rolesForFilter.map((role) => (
              <option key={role.id} value={role.id}>{role.name}</option>
            ))}
          </Select>
          <Select
            className="w-auto min-w-[140px]"
            value={taskFilter}
            onChange={(e) => { setTaskFilter(e.target.value); setPage(0); }}
          >
            <option value="">All job roles</option>
            {tasksForFilter.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </Select>
          <Select
            className="w-auto min-w-[140px]"
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(0); }}
          >
            <option value="">All statuses</option>
            <option value="pending">Invited</option>
            <option value="in_progress">In progress</option>
            <option value="completed">Completed</option>
            <option value="completed_due_to_timeout">Timed out</option>
            <option value="expired">Expired</option>
          </Select>
        </div>
        <p className="font-mono text-xs text-[var(--taali-muted)] mb-2">Candidates are grouped by job role. A candidate can appear in multiple roles if they have assessments for different tasks.</p>

        {/* Assessments Table */}
        <TableShell>
          <div className="border-b-2 border-[var(--taali-border)] px-6 py-4 bg-[var(--taali-border)] text-white flex items-center justify-between">
            <h2 className="font-bold text-lg">Recent Assessments</h2>
            {totalAssessmentsCount > 0 && (
              <span className="font-mono text-sm text-white/80">
                Showing {startRow}–{endRow} of {totalAssessmentsCount}
              </span>
            )}
          </div>
          {loading ? (
            <table className="w-full">
              <thead>
                <tr className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-purple-soft)]">
                  <th className="text-left px-2 py-3 font-mono text-xs font-bold uppercase">Compare</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Candidate</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Task</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Status</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Score</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Time</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Assessment link</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Actions</th>
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: 8 }).map((_, index) => (
                  <TableRowSkeleton key={`dashboard-skeleton-${index}`} cols={8} />
                ))}
              </tbody>
            </table>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-purple-soft)]">
                  <th className="text-left px-2 py-3 font-mono text-xs font-bold uppercase">Compare</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Candidate</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Task</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Status</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Score</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Time</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Assessment link</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Actions</th>
                </tr>
              </thead>
              <tbody>
                {displayCandidates.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-6 py-12 text-center font-mono text-sm text-[var(--taali-muted)]">
                      No assessments yet. Create an assessment from the Candidates page.
                    </td>
                  </tr>
                ) : (
                  tableRows.map((row) => {
                    if (row._group) {
                      return (
                        <tr key={`role-${row._group}`} className="bg-[var(--taali-border-muted)]/30 border-b-2 border-[var(--taali-border)]">
                          <td colSpan={8} className="px-6 py-2 font-mono text-sm font-bold uppercase text-[var(--taali-muted)]">
                            — {row._group} —
                          </td>
                        </tr>
                      );
                    }
                    const c = row;
                    const normalizedStatus = normalizeAssessmentStatus(c.status);
                    const canCompare = isCompletedStatus(normalizedStatus) && (compareIds.length < MAX_COMPARE || compareIds.includes(c.id));
                    const expiryDays = daysUntil(c.expiresAt);
                    return (
                      <tr key={c.id} className="border-b border-[var(--taali-border-muted)] hover:bg-[var(--taali-surface-hover,rgba(0,0,0,0.04))] transition-colors">
                        <td className="px-2 py-4">
                          {canCompare ? (
                            <input
                              type="checkbox"
                              className="w-4 h-4 accent-purple-600"
                              checked={compareIds.includes(c.id)}
                              disabled={compareLoadingId === c.id}
                              onChange={(e) => toggleCompare(c, e.target.checked)}
                            />
                          ) : (
                            <span className="text-[var(--taali-border-muted)]">—</span>
                          )}
                        </td>
                        <td className="px-6 py-4">
                          <div className="font-bold">{c.name}</div>
                          <div className="font-mono text-xs text-[var(--taali-muted)]">{c.email}</div>
                          <div className="font-mono text-xs text-[var(--taali-muted)]">Role: {c.role}</div>
                        </td>
                        <td className="px-6 py-4 font-mono text-sm">{c.task}</td>
                        <td className="px-6 py-4">
                          <div className="flex flex-col gap-1">
                            <StatusBadgeComponent status={normalizedStatus} />
                            {normalizedStatus === 'pending' && expiryDays != null && expiryDays > 0 && expiryDays <= 3 ? (
                              <span className="font-mono text-xs text-[var(--taali-warning)]">Expires in {expiryDays}d</span>
                            ) : null}
                          </div>
                        </td>
                        <td className="px-6 py-4 font-bold">{c.score !== null ? `${c.score}/10` : '—'}</td>
                        <td className="px-6 py-4 font-mono text-sm">{c.time}</td>
                        <td className="px-6 py-4">
                          {c.token ? (
                            <Button
                              variant="secondary"
                              size="sm"
                              className="font-mono"
                              onClick={() => {
                                const link = c.assessmentLink || getAssessmentLink(c.token);
                                navigator.clipboard?.writeText(link).then(() => { /* copied */ }).catch(() => {});
                              }}
                              title={c.assessmentLink || getAssessmentLink(c.token)}
                            >
                              <Clipboard size={14} /> Copy link
                            </Button>
                          ) : (
                            <span className="font-mono text-xs text-[var(--taali-muted)]">—</span>
                          )}
                        </td>
                        <td className="px-6 py-4">
                          {isCompletedStatus(normalizedStatus) ? (
                            <Button
                              variant="secondary"
                              size="sm"
                              className="font-mono"
                              disabled={loadingViewId === c.id}
                              onClick={async () => {
                                setLoadingViewId(c.id);
                                try {
                                  const res = await assessmentsApi.get(c.id);
                                  const a = res.data;
                                  const merged = {
                                    ...c,
                                    promptsList: a.prompts_list || [],
                                    timeline: a.timeline || [],
                                    results: a.results || [],
                                    breakdown: a.breakdown || null,
                                    prompts: (a.prompts_list || []).length,
                                  };
                                  onViewCandidate(merged);
                                } catch (err) {
                                  console.warn('Failed to fetch assessment detail, using list data:', err);
                                  onViewCandidate(c);
                                } finally {
                                  setLoadingViewId(null);
                                }
                              }}
                            >
                              {loadingViewId === c.id ? <Spinner size={14} /> : <Eye size={14} />} View
                            </Button>
                          ) : null}
                          {normalizedStatus === 'pending' ? (
                            <div className="flex items-center gap-2">
                              <Button
                                variant="ghost"
                                size="sm"
                                className="font-mono"
                                onClick={() => {
                                  const link = c.assessmentLink || getAssessmentLink(c.token);
                                  if (!link) return;
                                  navigator.clipboard?.writeText(link).then(() => {
                                    showToast('Link copied.', 'success');
                                  }).catch(() => {
                                    showToast('Failed to copy link.', 'error');
                                  });
                                }}
                                disabled={!c.token}
                              >
                                <Clipboard size={14} /> Copy Link
                              </Button>
                              <Button
                                variant="secondary"
                                size="sm"
                                className="font-mono"
                                disabled={loadingResendId === c.id}
                                onClick={() => handleResend(c.id)}
                              >
                                {loadingResendId === c.id ? <Spinner size={14} /> : null}
                                {loadingResendId === c.id ? 'Resending...' : 'Resend'}
                              </Button>
                            </div>
                          ) : null}
                          {normalizedStatus === 'expired' ? (
                            <Button
                              variant="secondary"
                              size="sm"
                              className="font-mono"
                              disabled={loadingResendId === c.id}
                              onClick={() => handleResend(c.id)}
                            >
                              {loadingResendId === c.id ? <Spinner size={14} /> : null}
                              {loadingResendId === c.id ? 'Resending...' : 'Resend'}
                            </Button>
                          ) : null}
                          {normalizedStatus === 'in_progress' ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="opacity-50 cursor-not-allowed"
                              disabled
                            >
                              <Timer size={14} /> In Progress
                            </Button>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          )}
          {!loading && totalAssessmentsCount > PAGE_SIZE && (
            <div className="border-t-2 border-[var(--taali-border)] px-6 py-3 flex items-center justify-between bg-[var(--taali-bg)]">
              <Button
                variant="secondary"
                size="sm"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                Previous
              </Button>
              <span className="font-mono text-sm text-[var(--taali-text)]">Page {page + 1} of {totalPages}</span>
              <Button
                variant="secondary"
                size="sm"
                disabled={page >= totalPages - 1}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          )}
        </TableShell>
      </div>
      {compareIds.length > 0 ? (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 bg-[var(--taali-surface)] border-2 border-[var(--taali-border)] shadow-xl px-6 py-3 flex items-center gap-3 font-mono text-sm">
          <span>{compareIds.length} selected</span>
          <Button size="sm" onClick={exportSelectedCsv}>Export CSV</Button>
          <Button size="sm" variant="danger" onClick={handleBulkDelete} disabled={bulkDeleting}>
            {bulkDeleting ? 'Deleting...' : 'Delete'}
          </Button>
          {compareIds.length >= 2 && compareIds.length <= MAX_COMPARE ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                document.getElementById('dashboard-compare-overlay')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
              }}
            >
              Compare
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setCompareIds([]);
              setCompareAssessments([]);
            }}
          >
            Clear
          </Button>
        </div>
      ) : null}
    </div>
  );
};
