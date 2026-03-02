import React, { useEffect, useMemo, useState } from 'react';
import { CheckCircle, ClipboardList, Eye, Link2, Timer, TriangleAlert } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { getDocumentTitle } from '../../config/brand';
import * as apiClient from '../../shared/api';
import { Button, PageContainer, PageHeader, Panel, TableShell } from '../../shared/ui/TaaliPrimitives';
import { StatCardSkeleton, TableRowSkeleton } from '../../shared/ui/Skeletons';

const PAGE_SIZE = 10;
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

const formatScore100 = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  const rounded = Math.round(numeric * 10) / 10;
  const display = Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1);
  return `${display}/100`;
};

const formatDate = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString();
};

const mapAssessmentForDetail = (assessment) => ({
  id: assessment.id,
  name: (assessment.candidate_name || assessment.candidate?.full_name || assessment.candidate_email || '').trim() || 'Unknown',
  email: assessment.candidate_email || assessment.candidate?.email || '',
  task: assessment.task_name || assessment.task?.name || 'Assessment',
  status: assessment.status || 'pending',
  score: assessment.score ?? assessment.overall_score ?? null,
  time: assessment.duration_taken ? `${Math.round(assessment.duration_taken / 60)}m` : '—',
  position: assessment.role_name || assessment.candidate?.position || '',
  completedDate: assessment.completed_at ? new Date(assessment.completed_at).toLocaleDateString() : null,
  breakdown: assessment.breakdown || null,
  prompts: assessment.prompt_count ?? 0,
  promptsList: assessment.prompts_list || [],
  timeline: assessment.timeline || [],
  results: assessment.results || [],
  token: assessment.token,
  _raw: assessment,
});

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
    tasksApi.list().then((res) => {
      if (!cancelled) setTasksForFilter(Array.isArray(res.data) ? res.data : []);
    }).catch(() => {});
    if (rolesApi?.list) {
      rolesApi.list().then((res) => {
        if (cancelled) return;
        const roles = Array.isArray(res.data) ? res.data : [];
        setRolesForFilter(roles);
        setRolesCount(roles.length);
      }).catch(() => {});
    }
    if (candidatesApi?.list) {
      const request = candidatesApi.list({ limit: 1, offset: 0 });
      if (request && typeof request.then === 'function') {
        request.then((res) => {
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
    return () => {
      cancelled = true;
    };
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
        const items = Array.isArray(data) ? data : (data.items || []);
        setAssessmentsList(items);
        setTotalAssessmentsCount(typeof data.total === 'number' ? data.total : items.length);
      })
      .catch((err) => {
        console.warn('Failed to fetch assessments:', err?.message || err);
        if (!cancelled) {
          setAssessmentsList([]);
          setTotalAssessmentsCount(0);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [assessmentsApi, page, roleFilter, statusFilter, taskFilter]);

  const userName = user?.full_name?.split(' ')[0] || 'there';

  const displayAssessments = useMemo(() => assessmentsList.map((assessment) => ({
    id: assessment.id,
    candidateName: (assessment.candidate_name || assessment.candidate?.full_name || assessment.candidate_email || '').trim() || 'Unknown',
    candidateEmail: assessment.candidate_email || assessment.candidate?.email || '',
    roleName: assessment.role_name || assessment.task?.role || 'Unassigned role',
    taskName: assessment.task?.name || assessment.task_name || 'Assessment',
    status: normalizeAssessmentStatus(assessment.status),
    taaliScore: assessment.taali_score ?? assessment.final_score ?? (assessment.score != null ? Number(assessment.score) * 10 : null),
    assessmentScore: assessment.assessment_score ?? assessment.final_score ?? (assessment.score != null ? Number(assessment.score) * 10 : null),
    inviteSentAt: assessment.invite_sent_at || assessment.created_at || null,
    completedAt: assessment.completed_at || null,
    expiresAt: assessment.expires_at || null,
    token: assessment.token || '',
    _raw: assessment,
  })), [assessmentsList]);

  const invitedCount = displayAssessments.filter((item) => item.status === 'pending').length;
  const inProgressCount = displayAssessments.filter((item) => item.status === 'in_progress').length;
  const completedCount = displayAssessments.filter((item) => isCompletedStatus(item.status)).length;
  const expiringSoonCount = displayAssessments.filter((item) => {
    const expiryDays = daysUntil(item.expiresAt);
    return item.status === 'pending' && expiryDays != null && expiryDays > 0 && expiryDays <= 3;
  }).length;
  const totalPages = Math.max(1, Math.ceil(totalAssessmentsCount / PAGE_SIZE));
  const startRow = totalAssessmentsCount === 0 ? 0 : page * PAGE_SIZE + 1;
  const endRow = totalAssessmentsCount === 0 ? 0 : Math.min((page + 1) * PAGE_SIZE, totalAssessmentsCount);

  const getAssessmentLink = (token) => {
    const origin = typeof window !== 'undefined' ? window.location.origin : '';
    return `${origin}/assess/${token || ''}`;
  };

  const copyAssessmentLink = async (assessment) => {
    if (!assessment?.token) return;
    const link = getAssessmentLink(assessment.token);
    try {
      await navigator.clipboard.writeText(link);
      showToast('Assessment link copied.', 'success');
    } catch {
      showToast('Failed to copy assessment link.', 'error');
    }
  };

  const handleViewResults = async (assessment) => {
    if (!assessment?.id) return;
    setLoadingViewId(assessment.id);
    try {
      const res = await assessmentsApi.get(assessment.id);
      onViewCandidate(mapAssessmentForDetail(res.data || assessment._raw));
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to load assessment results.', 'error');
    } finally {
      setLoadingViewId(null);
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
      <NavComponent currentPage="assessments" onNavigate={onNavigate} />
      <PageContainer density="compact" width="wide">
        <PageHeader
          density="compact"
          className="mb-5"
          title="Assessments"
          subtitle={`Welcome back, ${userName}`}
        />

        {totalAssessmentsCount === 0 && !onboardingDismissed ? (
          <div className="mb-5 border-2 border-[var(--taali-purple)] bg-[var(--taali-surface)] p-4">
            <div className="mb-3 flex items-start justify-between gap-4">
              <h2 className="text-base font-bold text-[var(--taali-text)]">Get started with TAALI</h2>
              <Button variant="ghost" size="sm" onClick={dismissOnboarding}>Dismiss</Button>
            </div>
            <ol className="space-y-1.5 font-mono text-sm text-[var(--taali-text)]">
              <li>{hasRoles ? '✓' : '○'} Create a role</li>
              <li>{hasCandidates ? '✓' : '○'} Add a candidate with their CV</li>
              <li>{hasSentAssessment ? '✓' : '○'} Send them an assessment link</li>
              <li>○ Manage setup in Candidates, then review completed attempts here.</li>
            </ol>
            <div className="mt-3">
              <Button variant="secondary" size="sm" onClick={() => onNavigate('candidates')}>Go to Candidates</Button>
            </div>
          </div>
        ) : null}

        {loading ? (
          <div className="mb-5 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <StatCardSkeleton />
            <StatCardSkeleton />
            <StatCardSkeleton />
            <StatCardSkeleton />
          </div>
        ) : (
          <div className="mb-5 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <StatsCardComponent
              icon={ClipboardList}
              label="Invited"
              value={String(invitedCount)}
              change="Assessment links sent"
              onClick={() => {
                setStatusFilter('pending');
                setPage(0);
              }}
            />
            <StatsCardComponent
              icon={Timer}
              label="In Progress"
              value={String(inProgressCount)}
              change="Candidates currently working"
              onClick={() => {
                setStatusFilter('in_progress');
                setPage(0);
              }}
            />
            <StatsCardComponent
              icon={CheckCircle}
              label="Completed Awaiting Review"
              value={String(completedCount)}
              change="Open results and review"
              onClick={() => {
                setStatusFilter('completed');
                setPage(0);
              }}
            />
            <StatsCardComponent
              icon={TriangleAlert}
              label="Expiring Soon"
              value={String(expiringSoonCount)}
              change="Pending invites expiring in 3 days"
              onClick={() => {
                setStatusFilter('pending');
                setPage(0);
              }}
            />
          </div>
        )}

        <Panel className="mb-4 p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-xs font-bold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Filters:</span>
            {(roleFilter || taskFilter || statusFilter) ? (
              <Button
                type="button"
                variant="ghost"
                size="xs"
                onClick={() => {
                  setRoleFilter('');
                  setTaskFilter('');
                  setStatusFilter('');
                  setPage(0);
                }}
              >
                Reset
              </Button>
            ) : null}
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            <label className="block">
              <span className="mb-1 block font-mono text-xs uppercase tracking-[0.08em] text-[var(--taali-muted)]">Role</span>
              <select
                className="taali-select min-h-[2.35rem] text-xs"
                value={roleFilter}
                onChange={(event) => {
                  setRoleFilter(event.target.value);
                  setPage(0);
                }}
              >
                <option value="">All roles</option>
                {rolesForFilter.map((role) => (
                  <option key={role.id} value={role.id}>{role.name}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs uppercase tracking-[0.08em] text-[var(--taali-muted)]">Task</span>
              <select
                className="taali-select min-h-[2.35rem] text-xs"
                value={taskFilter}
                onChange={(event) => {
                  setTaskFilter(event.target.value);
                  setPage(0);
                }}
              >
                <option value="">All tasks</option>
                {tasksForFilter.map((task) => (
                  <option key={task.id} value={task.id}>{task.name}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs uppercase tracking-[0.08em] text-[var(--taali-muted)]">Status</span>
              <select
                className="taali-select min-h-[2.35rem] text-xs"
                value={statusFilter}
                onChange={(event) => {
                  setStatusFilter(event.target.value);
                  setPage(0);
                }}
              >
                <option value="">All statuses</option>
                <option value="pending">Invited</option>
                <option value="in_progress">In progress</option>
                <option value="completed">Completed</option>
                <option value="completed_due_to_timeout">Timed out</option>
                <option value="expired">Expired</option>
              </select>
            </label>
          </div>
        </Panel>

        <TableShell>
          <div className="flex items-center justify-between border-b-2 border-[var(--taali-border)] bg-[var(--taali-border)] px-4 py-3 text-white">
            <h2 className="text-base font-bold">Assessment Inbox</h2>
            {totalAssessmentsCount > 0 ? (
              <span className="font-mono text-xs text-white/80">
                Showing {startRow}–{endRow} of {totalAssessmentsCount}
              </span>
            ) : null}
          </div>

          {loading ? (
            <table className="w-full">
              <thead>
                <tr className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-purple-soft)]">
                  {['Candidate', 'Role', 'Task', 'Status', 'TAALI Score', 'Assessment Score', 'Sent', 'Completed', 'Actions'].map((label) => (
                    <th key={label} className="px-4 py-2.5 text-left font-mono text-[11px] font-bold uppercase tracking-[0.08em]">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: 8 }).map((_, index) => (
                  <TableRowSkeleton key={`dashboard-skeleton-${index}`} cols={9} />
                ))}
              </tbody>
            </table>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-purple-soft)]">
                  {['Candidate', 'Role', 'Task', 'Status', 'TAALI Score', 'Assessment Score', 'Sent', 'Completed', 'Actions'].map((label) => (
                    <th key={label} className="px-4 py-2.5 text-left font-mono text-[11px] font-bold uppercase tracking-[0.08em]">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {displayAssessments.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-4 py-10 text-center font-mono text-sm text-[var(--taali-muted)]">
                      No assessments yet. Create an assessment from the Candidates page.
                    </td>
                  </tr>
                ) : (
                  displayAssessments.map((assessment) => {
                    const expiryDays = daysUntil(assessment.expiresAt);
                    return (
                      <tr
                        key={assessment.id}
                        className="border-b border-[var(--taali-border-muted)] transition-colors hover:bg-[var(--taali-surface-hover,rgba(0,0,0,0.04))]"
                      >
                        <td className="px-4 py-3">
                          <div className="font-semibold text-[var(--taali-text)]">{assessment.candidateName}</div>
                          <div className="font-mono text-xs text-[var(--taali-muted)]">{assessment.candidateEmail || '—'}</div>
                        </td>
                        <td className="px-4 py-3 text-sm text-[var(--taali-text)]">{assessment.roleName}</td>
                        <td className="px-4 py-3 text-sm text-[var(--taali-text)]">{assessment.taskName}</td>
                        <td className="px-4 py-3">
                          <StatusBadgeComponent status={assessment.status} />
                        </td>
                        <td className="px-4 py-3 font-mono text-sm text-[var(--taali-text)]">
                          {formatScore100(assessment.taaliScore)}
                        </td>
                        <td className="px-4 py-3 font-mono text-sm text-[var(--taali-text)]">
                          {formatScore100(assessment.assessmentScore)}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-[var(--taali-muted)]">
                          {formatDate(assessment.inviteSentAt)}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-[var(--taali-muted)]">
                          {formatDate(assessment.completedAt)}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap items-center gap-2">
                            {isCompletedStatus(assessment.status) ? (
                              <Button
                                type="button"
                                variant="primary"
                                size="xs"
                                disabled={loadingViewId === assessment.id}
                                onClick={() => handleViewResults(assessment)}
                              >
                                <Eye size={14} />
                                {loadingViewId === assessment.id ? 'Loading...' : 'View results'}
                              </Button>
                            ) : null}
                            {(assessment.status === 'pending' || assessment.status === 'expired') && assessment.token ? (
                              <>
                                <Button
                                  type="button"
                                  variant="secondary"
                                  size="xs"
                                  onClick={() => copyAssessmentLink(assessment)}
                                >
                                  <Link2 size={14} />
                                  Copy link
                                </Button>
                                <Button
                                  type="button"
                                  variant="secondary"
                                  size="xs"
                                  disabled={loadingResendId === assessment.id}
                                  onClick={() => handleResend(assessment.id)}
                                >
                                  {loadingResendId === assessment.id ? 'Resending...' : 'Resend'}
                                </Button>
                              </>
                            ) : null}
                            {assessment.status === 'in_progress' ? (
                              <span className="font-mono text-xs text-[var(--taali-muted)]">In progress</span>
                            ) : null}
                            {assessment.status === 'pending' && expiryDays != null && expiryDays > 0 && expiryDays <= 3 ? (
                              <span className="font-mono text-xs text-amber-700">{expiryDays}d left</span>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          )}
        </TableShell>

        {totalAssessmentsCount > PAGE_SIZE ? (
          <div className="mt-3 flex items-center justify-between font-mono text-xs text-[var(--taali-muted)]">
            <span>
              Showing {startRow}–{endRow} of {totalAssessmentsCount}
            </span>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="ghost"
                disabled={page === 0}
                onClick={() => setPage((current) => Math.max(0, current - 1))}
              >
                Previous
              </Button>
              <span>Page {page + 1} of {totalPages}</span>
              <Button
                size="sm"
                variant="ghost"
                disabled={page >= totalPages - 1}
                onClick={() => setPage((current) => Math.min(totalPages - 1, current + 1))}
              >
                Next
              </Button>
            </div>
          </div>
        ) : null}
      </PageContainer>
    </div>
  );
};
