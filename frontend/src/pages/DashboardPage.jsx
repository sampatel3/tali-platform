import React, { useState, useEffect, useCallback } from 'react';
import { Clipboard, DollarSign, CheckCircle, Eye, Loader2, Timer, Star, Users } from 'lucide-react';
import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';
import { useAuth } from '../context/AuthContext';
import { assessments as assessmentsApi, tasks as tasksApi } from '../lib/api';
import { COMPARISON_CATEGORY_CONFIG, getCategoryScoresFromAssessment } from '../lib/comparisonCategories';
import { ASSESSMENT_PRICE_AED, formatAed } from '../lib/currency';

const PAGE_SIZE = 10;
const MAX_COMPARE = 5;
const COMPARE_COLORS = ['#9D00FF', '#111827', '#16a34a', '#d97706', '#2563eb'];

export const DashboardPage = ({
  onNavigate,
  onViewCandidate,
  NavComponent,
  StatsCardComponent,
  StatusBadgeComponent,
}) => {
  const { user } = useAuth();
  const [assessmentsList, setAssessmentsList] = useState([]);
  const [totalAssessmentsCount, setTotalAssessmentsCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingViewId, setLoadingViewId] = useState(null);
  const [statusFilter, setStatusFilter] = useState('');
  const [taskFilter, setTaskFilter] = useState('');
  const [tasksForFilter, setTasksForFilter] = useState([]);
  const [page, setPage] = useState(0);
  const [compareIds, setCompareIds] = useState([]);
  const [compareAssessments, setCompareAssessments] = useState([]);
  const [compareLoadingId, setCompareLoadingId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    tasksApi.list().then((res) => { if (!cancelled) setTasksForFilter(res.data || []); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const params = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
    if (statusFilter) params.status = statusFilter;
    if (taskFilter) params.task_id = taskFilter;
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
  }, [page, statusFilter, taskFilter]);

  const getAssessmentLink = (token) =>
    `${typeof window !== 'undefined' ? window.location.origin : ''}/assess/${token || ''}`;

  // Map API assessments to table-friendly shape, falling back to mock data
  const displayCandidates = assessmentsList.length > 0
    ? assessmentsList.map((a) => ({
        id: a.id,
        name: (a.candidate_name || a.candidate?.full_name || a.candidate_email || '').trim() || 'Unknown',
        email: a.candidate_email || a.candidate?.email || '',
        task: a.task?.name || a.task_name || 'Assessment',
        status: a.status === 'submitted' || a.status === 'graded' ? 'completed' : (a.status || 'in-progress'),
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
        assessmentLink: a.token ? getAssessmentLink(a.token) : '',
        _raw: a,
      }))
    : [];

  const userName = user?.full_name?.split(' ')[0] || 'there';

  // Compute live stats from current page (total count from API)
  const totalAssessments = totalAssessmentsCount;
  const completedCount = displayCandidates.filter((c) => c.status === 'completed' || c.status === 'submitted' || c.status === 'graded').length;
  const totalPages = Math.max(1, Math.ceil(totalAssessmentsCount / PAGE_SIZE));
  const startRow = page * PAGE_SIZE + 1;
  const endRow = Math.min((page + 1) * PAGE_SIZE, totalAssessmentsCount);
  const completionRate = totalAssessments > 0 ? ((completedCount / totalAssessments) * 100).toFixed(1) : '0';
  const scores = displayCandidates.filter((c) => c.score !== null).map((c) => c.score);
  const avgScore = scores.length > 0 ? (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1) : '—';
  const monthCost = formatAed(completedCount * ASSESSMENT_PRICE_AED);
  const notifications = displayCandidates
    .filter((c) => c.status === 'completed')
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
  const tableRows = !taskFilter && displayCandidates.length > 0
    ? (() => {
        const byTask = {};
        displayCandidates.forEach((c) => {
          const t = c.task || 'Other';
          if (!byTask[t]) byTask[t] = [];
          byTask[t].push(c);
        });
        const taskOrder = [...new Set(displayCandidates.map((c) => c.task || 'Other'))].sort();
        return taskOrder.flatMap((task) => [{ _group: task }, ...byTask[task]]);
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

  return (
    <div>
      <NavComponent currentPage="dashboard" onNavigate={onNavigate} />
      <div className="md:hidden p-8 text-center border-b-2 border-black">
        <p className="font-mono text-sm">Desktop browser required for dashboard</p>
      </div>
      <div className="hidden md:block max-w-7xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold">Assessments</h1>
            <p className="font-mono text-sm text-gray-600 mt-1">Welcome back, {userName}</p>
          </div>

        </div>
        <div className="flex flex-wrap items-center gap-3 mb-6">
          <button className="border-2 border-black px-4 py-2 font-mono text-xs font-bold hover:bg-black hover:text-white" onClick={exportCsv}>Export CSV</button>
          <button className="border-2 border-black px-4 py-2 font-mono text-xs font-bold hover:bg-black hover:text-white" onClick={exportJson}>Export JSON</button>
        </div>
        {notifications.length > 0 && (
          <div className="border-2 border-black p-4 mb-6">
            <div className="font-mono text-xs text-gray-500 mb-2">Recent Notifications</div>
            <div className="space-y-1">
              {notifications.map((n) => (
                <div key={n.id} className="font-mono text-sm">• {n.text}</div>
              ))}
            </div>
          </div>
        )}
        {compareAssessments.length >= 2 && (
          <div className="border-2 border-black p-6 mb-6 bg-gray-50">
            <div className="flex items-center gap-2 mb-4">
              <Users size={20} />
              <h3 className="font-bold text-lg">Candidate comparison</h3>
            </div>
            <p className="font-mono text-xs text-gray-600 mb-4">Overlay by category and overall score. Compare up to {MAX_COMPARE} candidates.</p>
            <div className="grid md:grid-cols-2 gap-8">
              <div>
                <div className="font-mono text-xs font-bold uppercase text-gray-500 mb-2">Overall score</div>
                <div className="space-y-2">
                  {compareAssessments.map((a, i) => (
                    <div key={a.id} className="flex items-center gap-3">
                      <span className="w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: COMPARE_COLORS[i % COMPARE_COLORS.length] }} />
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
                    const point = { signal: cat.label.split(' ')[0], fullMark: 10 };
                    compareAssessments.forEach((a, i) => {
                      const scores = getCategoryScoresFromAssessment(a);
                      point[candKey(i)] = scores[cat.key] ?? 0;
                    });
                    return point;
                  }).filter((row) => compareAssessments.some((_, i) => (row[candKey(i)] ?? 0) > 0));
                  if (radarData.length === 0) return <div className="font-mono text-sm text-gray-500">No category scores available for overlay.</div>;
                  return (
                    <div style={{ width: '100%', height: 320 }}>
                      <ResponsiveContainer>
                        <RadarChart data={radarData}>
                          <PolarGrid />
                          <PolarAngleAxis dataKey="signal" tick={{ fontSize: 10, fontFamily: 'monospace' }} />
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
        <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          <StatsCardComponent icon={Clipboard} label="Active Assessments" value={String(totalAssessments)} change={`${completedCount} completed`} />
          <StatsCardComponent icon={CheckCircle} label="Completion Rate" value={`${completionRate}%`} change="Industry avg: 65%" />
          <StatsCardComponent icon={Star} label="Avg Score" value={avgScore !== '—' ? `${avgScore}/10` : '—'} change="Candidates this month" />
          <StatsCardComponent icon={DollarSign} label="This Month Cost" value={monthCost} change={`${completedCount} assessments`} />
        </div>

        {/* Filters: split by job role (task); recruiters hire for many roles */}
        <div className="flex flex-wrap items-center gap-4 mb-4">
          <span className="font-mono text-sm font-bold">Filters:</span>
          <select
            className="border-2 border-black px-3 py-2 font-mono text-sm bg-white"
            value={taskFilter}
            onChange={(e) => { setTaskFilter(e.target.value); setPage(0); }}
          >
            <option value="">All job roles</option>
            {tasksForFilter.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
          <select
            className="border-2 border-black px-3 py-2 font-mono text-sm bg-white"
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(0); }}
          >
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="in_progress">In progress</option>
            <option value="completed">Completed</option>
          </select>
        </div>
        <p className="font-mono text-xs text-gray-500 mb-2">Candidates are grouped by job role. A candidate can appear in multiple roles if they have assessments for different tasks.</p>

        {/* Assessments Table */}
        <div className="border-2 border-black">
          <div className="border-b-2 border-black px-6 py-4 bg-black text-white flex items-center justify-between">
            <h2 className="font-bold text-lg">Recent Assessments</h2>
            {totalAssessmentsCount > 0 && (
              <span className="font-mono text-sm text-gray-300">
                Showing {startRow}–{endRow} of {totalAssessmentsCount}
              </span>
            )}
          </div>
          {loading ? (
            <div className="flex items-center justify-center py-16 gap-3">
              <Loader2 size={24} className="animate-spin" style={{ color: '#9D00FF' }} />
              <span className="font-mono text-sm text-gray-500">Loading assessments...</span>
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b-2 border-black bg-gray-50">
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
                    <td colSpan={8} className="px-6 py-12 text-center font-mono text-sm text-gray-500">
                      No assessments yet. Create an assessment from the Candidates page.
                    </td>
                  </tr>
                ) : (
                  tableRows.map((row) => {
                    if (row._group) {
                      return (
                        <tr key={`role-${row._group}`} className="bg-gray-100 border-b-2 border-black">
                          <td colSpan={8} className="px-6 py-2 font-mono text-sm font-bold uppercase text-gray-700">
                            — {row._group} —
                          </td>
                        </tr>
                      );
                    }
                    const c = row;
                    const canCompare = (c.status === 'completed' || c.status === 'submitted' || c.status === 'graded') && (compareIds.length < MAX_COMPARE || compareIds.includes(c.id));
                    return (
                      <tr key={c.id} className="border-b border-gray-200 hover:bg-gray-50 transition-colors">
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
                            <span className="text-gray-300">—</span>
                          )}
                        </td>
                        <td className="px-6 py-4">
                          <div className="font-bold">{c.name}</div>
                          <div className="font-mono text-xs text-gray-500">{c.email}</div>
                        </td>
                        <td className="px-6 py-4 font-mono text-sm">{c.task}</td>
                        <td className="px-6 py-4"><StatusBadgeComponent status={c.status} /></td>
                        <td className="px-6 py-4 font-bold">{c.score !== null ? `${c.score}/10` : '—'}</td>
                        <td className="px-6 py-4 font-mono text-sm">{c.time}</td>
                        <td className="px-6 py-4">
                          {c.token ? (
                            <button
                              type="button"
                              className="border-2 border-black bg-white px-3 py-1.5 font-mono text-xs font-bold hover:bg-black hover:text-white transition-colors flex items-center gap-1"
                              onClick={() => {
                                const link = c.assessmentLink || getAssessmentLink(c.token);
                                navigator.clipboard?.writeText(link).then(() => { /* copied */ }).catch(() => {});
                              }}
                              title={c.assessmentLink || getAssessmentLink(c.token)}
                            >
                              <Clipboard size={14} /> Copy link
                            </button>
                          ) : (
                            <span className="font-mono text-xs text-gray-400">—</span>
                          )}
                        </td>
                        <td className="px-6 py-4">
                          {c.status === 'completed' || c.status === 'submitted' || c.status === 'graded' ? (
                            <button
                              className="border-2 border-black bg-white px-4 py-2 font-mono text-sm font-bold hover:bg-black hover:text-white transition-colors flex items-center gap-1 disabled:opacity-70"
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
                              {loadingViewId === c.id ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />} View
                            </button>
                          ) : (
                            <button
                              className="border-2 border-gray-300 bg-gray-100 px-4 py-2 font-mono text-sm font-bold text-gray-400 cursor-not-allowed flex items-center gap-1"
                              disabled
                            >
                              <Timer size={14} /> Pending
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          )}
          {!loading && totalAssessmentsCount > PAGE_SIZE && (
            <div className="border-t-2 border-black px-6 py-3 flex items-center justify-between bg-gray-50">
              <button
                type="button"
                className="border-2 border-black px-4 py-2 font-mono text-sm font-bold disabled:opacity-50 disabled:cursor-not-allowed hover:bg-black hover:text-white transition-colors"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                Previous
              </button>
              <span className="font-mono text-sm">Page {page + 1} of {totalPages}</span>
              <button
                type="button"
                className="border-2 border-black px-4 py-2 font-mono text-sm font-bold disabled:opacity-50 disabled:cursor-not-allowed hover:bg-black hover:text-white transition-colors"
                disabled={page >= totalPages - 1}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
