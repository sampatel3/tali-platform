import React, { useEffect, useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { assessments as assessmentsApi, analytics as analyticsApi, roles as rolesApi, tasks as tasksApi } from '../../shared/api';
import { getCategoryScoresFromAssessment } from '../../lib/comparisonCategories';
import { dimensionOrder, getDimensionById } from '../../scoring/scoringDimensions';
import { PageHero } from '../../shared/layout/PageHero';
import { Button, Panel, Select, Spinner } from '../../shared/ui/TaaliPrimitives';

const DATE_RANGE_OPTIONS = [
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
  { value: '90d', label: 'Last 90 days' },
  { value: 'all', label: 'All time' },
];

const toIso = (date) => date.toISOString();

const getDateRangeParams = (range) => {
  if (range === 'all') return {};
  const days = Number(String(range || '').replace('d', ''));
  if (!Number.isFinite(days) || days <= 0) return {};
  const now = new Date();
  const from = new Date(now);
  from.setDate(now.getDate() - days);
  return {
    date_from: toIso(from),
    date_to: toIso(now),
  };
};

const safeNumber = (value, fallback = 0) => (Number.isFinite(Number(value)) ? Number(value) : fallback);

const buildNarrative = (data, rangeLabel) => {
  const total = safeNumber(data.total_assessments);
  const completed = safeNumber(data.completed_count);
  const avg = safeNumber(data.avg_score, null);
  if (total === 0) {
    return `No assessments closed in the ${rangeLabel.toLowerCase()}. Once candidates start completing tasks, the agent will narrate what it advanced, rejected, and flagged.`;
  }
  // HANDOFF v2 §6 — recruiter-facing scores rendered as integer nn / 100.
  const avgPart = avg != null ? ` Average composite score sat at ${Math.round(Number(avg) * 10)} / 100.` : '';
  const completionRate = safeNumber(data.completion_rate);
  return `Across the ${rangeLabel.toLowerCase()}, ${completed} of ${total} assessments closed (${completionRate.toFixed(0)}% completion).${avgPart} Use the panels below to see what shifted, where confidence dropped, and how the funnel held up.`;
};

export const ReportingPage = ({ onNavigate, NavComponent }) => {
  const [roles, setRoles] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [roleFilter, setRoleFilter] = useState('');
  const [taskFilter, setTaskFilter] = useState('');
  const [dateRange, setDateRange] = useState('30d');
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [data, setData] = useState({
    weekly_completion: [],
    total_assessments: 0,
    completed_count: 0,
    completion_rate: 0,
    top_score: null,
    avg_score: null,
    avg_time_minutes: null,
    score_buckets: [],
    dimension_averages: {},
  });

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([rolesApi.list(), tasksApi.list()]).then(([rolesRes, tasksRes]) => {
      if (cancelled) return;
      if (rolesRes.status === 'fulfilled') {
        setRoles(Array.isArray(rolesRes.value?.data) ? rolesRes.value.data : []);
      }
      if (tasksRes.status === 'fulfilled') {
        setTasks(Array.isArray(tasksRes.value?.data) ? tasksRes.value.data : []);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const queryParams = useMemo(() => {
    const params = {
      ...getDateRangeParams(dateRange),
    };
    if (roleFilter) params.role_id = roleFilter;
    if (taskFilter) params.task_id = taskFilter;
    return params;
  }, [dateRange, roleFilter, taskFilter]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    analyticsApi.get(queryParams)
      .then((res) => {
        if (!cancelled) {
          setData({
            weekly_completion: res?.data?.weekly_completion || [],
            total_assessments: safeNumber(res?.data?.total_assessments),
            completed_count: safeNumber(res?.data?.completed_count),
            completion_rate: safeNumber(res?.data?.completion_rate),
            top_score: res?.data?.top_score,
            avg_score: res?.data?.avg_score,
            avg_time_minutes: res?.data?.avg_time_minutes,
            score_buckets: Array.isArray(res?.data?.score_buckets) ? res.data.score_buckets : [],
            dimension_averages: res?.data?.dimension_averages || {},
          });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setData({
            weekly_completion: [],
            total_assessments: 0,
            completed_count: 0,
            completion_rate: 0,
            top_score: null,
            avg_score: null,
            avg_time_minutes: null,
            score_buckets: [],
            dimension_averages: {},
          });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [queryParams]);

  const weekly = data.weekly_completion?.length ? data.weekly_completion : [
    { week: 'Week 1', rate: 0, count: 0 },
    { week: 'Week 2', rate: 0, count: 0 },
    { week: 'Week 3', rate: 0, count: 0 },
    { week: 'Week 4', rate: 0, count: 0 },
    { week: 'Week 5', rate: 0, count: 0 },
  ];

  const histogramData = data.score_buckets?.length ? data.score_buckets : [
    { range: '0-20', count: 0, percentage: 0 },
    { range: '20-40', count: 0, percentage: 0 },
    { range: '40-60', count: 0, percentage: 0 },
    { range: '60-80', count: 0, percentage: 0 },
    { range: '80-100', count: 0, percentage: 0 },
  ];

  const radarData = dimensionOrder.map((key) => ({
    dimension: getDimensionById(key).label,
    score: safeNumber(data.dimension_averages?.[key], 0),
    fullMark: 10,
  }));

  const rangeLabel = DATE_RANGE_OPTIONS.find((opt) => opt.value === dateRange)?.label || 'Last 30 days';
  const narrative = buildNarrative(data, rangeLabel);

  const funnelStages = useMemo(() => {
    const total = Math.max(safeNumber(data.total_assessments), 0);
    const completed = Math.max(safeNumber(data.completed_count), 0);
    const inFlight = Math.max(total - completed, 0);
    const highScore = histogramData
      .filter((b) => /80|60-80|80-100/.test(String(b.range)))
      .reduce((acc, b) => acc + safeNumber(b.count), 0);
    const stages = [
      { label: 'Invited', n: total, p: 100 },
      { label: 'In assessment', n: inFlight, p: total ? (inFlight / total) * 100 : 0 },
      { label: 'Completed', n: completed, p: total ? (completed / total) * 100 : 0 },
      { label: 'Score ≥ 60', n: highScore, p: total ? (highScore / total) * 100 : 0 },
    ];
    return stages;
  }, [data.total_assessments, data.completed_count, histogramData]);

  const handleExportCsv = async () => {
    setExporting(true);
    try {
      const allItems = [];
      let offset = 0;
      const limit = 100;
      let total = 0;
      do {
        const res = await assessmentsApi.list({
          limit,
          offset,
          ...(roleFilter ? { role_id: roleFilter } : {}),
          ...(taskFilter ? { task_id: taskFilter } : {}),
        });
        const payload = res?.data || {};
        const items = Array.isArray(payload) ? payload : (payload.items || []);
        total = typeof payload.total === 'number' ? payload.total : items.length;
        allItems.push(...items);
        offset += limit;
      } while (offset < total);

      const { date_from: dateFromRaw, date_to: dateToRaw } = getDateRangeParams(dateRange);
      const dateFrom = dateFromRaw ? new Date(dateFromRaw) : null;
      const dateTo = dateToRaw ? new Date(dateToRaw) : null;
      const filtered = allItems.filter((item) => {
        if (!dateFrom && !dateTo) return true;
        const ts = item?.completed_at || item?.created_at;
        if (!ts) return false;
        const dt = new Date(ts);
        if (Number.isNaN(dt.getTime())) return false;
        if (dateFrom && dt < dateFrom) return false;
        if (dateTo && dt > dateTo) return false;
        return true;
      });

      const rows = filtered.map((item) => {
        const categories = getCategoryScoresFromAssessment(item);
        return {
          candidate_name: item.candidate_name || item.candidate?.full_name || '',
          candidate_email: item.candidate_email || item.candidate?.email || '',
          task: item.task_name || item.task?.name || '',
          role: item.role_name || item.task?.role || '',
          status: item.status || '',
          score_10: item.score ?? '',
          score_100: item.final_score ?? (item.score != null ? Number(item.score) * 10 : ''),
          completed_at: item.completed_at || '',
          task_completion: categories.task_completion ?? '',
          prompt_clarity: categories.prompt_clarity ?? '',
          context_provision: categories.context_provision ?? '',
          independence_efficiency: categories.independence_efficiency ?? '',
          response_utilization: categories.response_utilization ?? '',
          debugging_design: categories.debugging_design ?? '',
          written_communication: categories.written_communication ?? '',
          role_fit: categories.role_fit ?? '',
        };
      });

      const columns = [
        'candidate_name',
        'candidate_email',
        'task',
        'role',
        'status',
        'score_10',
        'score_100',
        'completed_at',
        'task_completion',
        'prompt_clarity',
        'context_provision',
        'independence_efficiency',
        'response_utilization',
        'debugging_design',
        'written_communication',
        'role_fit',
      ];
      const csv = [
        columns.join(','),
        ...rows.map((row) => columns.map((col) => `"${String(row[col] ?? '').replace(/"/g, '""')}"`).join(',')),
      ].join('\n');
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'analytics-assessments.csv';
      anchor.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div>
      <NavComponent currentPage="reporting" onNavigate={onNavigate} />
      <div className="mc-page mc-page-narrow">
        <PageHero
          kicker={`MISSION CONTROL · ${rangeLabel.toUpperCase()}`}
          title={<>Your agent in <em>narrative</em></>}
          subtitle="What Taali did, what it skipped, and where it was unsure. Not a dashboard — a daily standup in retrospect."
          actions={(
            <Button type="button" variant="secondary" size="sm" onClick={handleExportCsv} disabled={loading || exporting}>
              {exporting ? 'Exporting...' : 'Export CSV'}
            </Button>
          )}
        />

        <Panel className="mb-5 p-4">
          <div className="grid gap-3 md:grid-cols-4">
            <label className="block">
              <span className="mb-1 block font-mono text-xs uppercase tracking-[0.08em] text-[var(--taali-muted)]">Role</span>
              <Select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}>
                <option value="">All roles</option>
                {roles.map((role) => (
                  <option key={role.id} value={role.id}>{role.name}</option>
                ))}
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs uppercase tracking-[0.08em] text-[var(--taali-muted)]">Task</span>
              <Select value={taskFilter} onChange={(event) => setTaskFilter(event.target.value)}>
                <option value="">All tasks</option>
                {tasks.map((task) => (
                  <option key={task.id} value={task.id}>{task.name}</option>
                ))}
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs uppercase tracking-[0.08em] text-[var(--taali-muted)]">Date range</span>
              <Select value={dateRange} onChange={(event) => setDateRange(event.target.value)}>
                {DATE_RANGE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </Select>
            </label>
            <div className="flex items-end">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setRoleFilter('');
                  setTaskFilter('');
                  setDateRange('30d');
                }}
                disabled={!roleFilter && !taskFilter && dateRange === '30d'}
              >
                Reset filters
              </Button>
            </div>
          </div>
        </Panel>

        {loading ? (
          <div className="flex min-h-[260px] items-center justify-center">
            <Spinner size={32} />
          </div>
        ) : (
          <div className="space-y-5">
            <section className="mc-narrator">
              <div className="mc-narrator-bg" aria-hidden="true" />
              <div className="mc-kicker" style={{ color: 'rgba(255,255,255,0.7)', marginBottom: 6 }}>
                NARRATOR · WHAT THE AGENT DID
              </div>
              <p>{narrative}</p>
            </section>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <Panel className="p-4">
                <div className="mb-1 font-mono text-xs uppercase tracking-[0.08em] text-[var(--taali-muted)]">Decisions made</div>
                <div className="text-2xl font-bold text-[var(--taali-text)]">{data.total_assessments}</div>
              </Panel>
              <Panel className="p-4">
                <div className="mb-1 font-mono text-xs uppercase tracking-[0.08em] text-[var(--taali-muted)]">Completed</div>
                <div className="text-2xl font-bold text-[var(--taali-purple)]">{data.completed_count}</div>
              </Panel>
              <Panel className="p-4">
                <div className="mb-1 font-mono text-xs uppercase tracking-[0.08em] text-[var(--taali-muted)]">Avg score</div>
                <div className="text-2xl font-bold text-[var(--taali-text)]">
                  {data.avg_score != null
                    ? <>{Math.round(Number(data.avg_score) * 10)} <span className="text-base font-normal text-[var(--taali-muted)]">/ 100</span></>
                    : '—'}
                </div>
              </Panel>
              <Panel className="p-4">
                <div className="mb-1 font-mono text-xs uppercase tracking-[0.08em] text-[var(--taali-muted)]">Completion rate</div>
                <div className="text-2xl font-bold text-[var(--taali-text)]">{safeNumber(data.completion_rate).toFixed(1)}%</div>
              </Panel>
            </div>

            <div className="grid gap-5 lg:grid-cols-[1.3fr_1fr]">
              <Panel className="p-4">
                <h2 className="mb-1 font-bold text-base">Completion rate trend</h2>
                <p className="mb-3 text-[12.5px] text-[var(--mute)]">Weekly close rate across the selected range.</p>
                <div className="h-[260px]">
                  <ResponsiveContainer>
                    <BarChart data={weekly}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--taali-border-muted)" />
                      <XAxis dataKey="week" tick={{ fill: 'var(--taali-muted)', fontSize: 12 }} axisLine={{ stroke: 'var(--taali-border-soft)' }} tickLine={{ stroke: 'var(--taali-border-soft)' }} />
                      <YAxis domain={[0, 100]} tick={{ fill: 'var(--taali-muted)', fontSize: 12 }} axisLine={{ stroke: 'var(--taali-border-soft)' }} tickLine={{ stroke: 'var(--taali-border-soft)' }} />
                      <Tooltip
                        contentStyle={{
                          background: 'var(--taali-surface-elevated)',
                          border: '1px solid var(--taali-border-soft)',
                          borderRadius: '16px',
                          color: 'var(--taali-text)',
                        }}
                      />
                      <Bar dataKey="rate" fill="var(--taali-purple)" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </Panel>

              <div className="flex flex-col gap-4">
                <Panel className="p-4">
                  <h2 className="mb-1 font-bold text-base">Anomalies</h2>
                  <p className="mb-3 text-[12.5px] text-[var(--mute)]">Things worth your attention.</p>
                  {data.total_assessments > 0 ? (
                    <div className="flex flex-col gap-2">
                      {data.completion_rate < 50 ? (
                        <AnomalyRow
                          tone="amber"
                          title="Low completion rate"
                          body={`Only ${safeNumber(data.completion_rate).toFixed(0)}% of invited candidates completed the assessment. Consider revisiting invite copy or task length.`}
                        />
                      ) : null}
                      {data.avg_score != null && data.avg_score < 5 ? (
                        <AnomalyRow
                          tone="amber"
                          title="Average score below mid-band"
                          body={`Mean composite is ${Math.round(Number(data.avg_score) * 10)} / 100. Either the bar is set high or the candidate pool needs sharpening.`}
                        />
                      ) : null}
                      {data.completion_rate >= 50 && (data.avg_score == null || data.avg_score >= 5) ? (
                        <p className="text-[12.5px] text-[var(--mute)]">Nothing flagged in the current window.</p>
                      ) : null}
                    </div>
                  ) : (
                    <p className="text-[12.5px] text-[var(--mute)]">Anomalies appear once the agent has graded enough assessments to spot drift.</p>
                  )}
                </Panel>

                <Panel className="p-4">
                  <h2 className="mb-1 font-bold text-base">Funnel · org-wide</h2>
                  <p className="mb-3 text-[12.5px] text-[var(--mute)]">From invitation through completion to score ≥ 60.</p>
                  <div className="flex flex-col gap-2">
                    {funnelStages.map((stage) => (
                      <div key={stage.label} className="grid grid-cols-[120px_1fr_auto] items-center gap-3">
                        <span className="text-[13px] text-[var(--ink-2)]">{stage.label}</span>
                        <div className="relative h-2.5 rounded-full bg-[var(--bg-3)] overflow-hidden">
                          <div
                            className="absolute inset-y-0 left-0 rounded-full"
                            style={{
                              width: `${Math.max(0, Math.min(100, stage.p))}%`,
                              background: 'linear-gradient(90deg, var(--purple), color-mix(in srgb, var(--purple) 60%, var(--lime)))',
                            }}
                          />
                        </div>
                        <span className="font-[var(--font-mono)] text-[11.5px] text-[var(--ink-2)]">{stage.n}</span>
                      </div>
                    ))}
                  </div>
                </Panel>
              </div>
            </div>

            <Panel className="p-4">
              <h2 className="mb-1 font-bold text-base">Score distribution</h2>
              <p className="mb-3 text-[12.5px] text-[var(--mute)]">Composite scores bucketed into deciles.</p>
              <div className="h-[260px]">
                <ResponsiveContainer>
                  <BarChart data={histogramData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--taali-border-muted)" />
                    <XAxis dataKey="range" tick={{ fill: 'var(--taali-muted)', fontSize: 12 }} axisLine={{ stroke: 'var(--taali-border-soft)' }} tickLine={{ stroke: 'var(--taali-border-soft)' }} />
                    <YAxis tick={{ fill: 'var(--taali-muted)', fontSize: 12 }} axisLine={{ stroke: 'var(--taali-border-soft)' }} tickLine={{ stroke: 'var(--taali-border-soft)' }} />
                    <Tooltip
                      contentStyle={{
                        background: 'var(--taali-surface-elevated)',
                        border: '1px solid var(--taali-border-soft)',
                        borderRadius: '16px',
                        color: 'var(--taali-text)',
                      }}
                    />
                    <Bar dataKey="count" fill="var(--taali-info)" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Panel>

            <Panel className="p-4">
              <h2 className="mb-1 font-bold text-base">Per-dimension averages</h2>
              <p className="mb-3 text-[12.5px] text-[var(--mute)]">How candidates score across the eleven scoring axes.</p>
              <div className="h-[320px]">
                <ResponsiveContainer>
                  <RadarChart data={radarData}>
                    <PolarGrid stroke="var(--taali-border-muted)" />
                    <PolarAngleAxis dataKey="dimension" tick={{ fill: 'var(--taali-muted)', fontSize: 11 }} />
                    <PolarRadiusAxis domain={[0, 10]} tick={{ fill: 'var(--taali-muted)', fontSize: 11 }} axisLine={{ stroke: 'var(--taali-border-soft)' }} />
                    <Radar dataKey="score" stroke="var(--taali-purple)" fill="var(--taali-purple)" fillOpacity={0.2} />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
            </Panel>
          </div>
        )}
      </div>
    </div>
  );
};

const AnomalyRow = ({ tone, title, body }) => (
  <div className="flex items-start gap-3 rounded-[10px] border border-[var(--line)] bg-[var(--bg)] px-3 py-3">
    <span
      className="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full"
      style={{ background: tone === 'red' ? 'var(--red)' : 'var(--amber)' }}
      aria-hidden="true"
    />
    <div>
      <div className="text-[13px] font-medium text-[var(--ink)]">{title}</div>
      <div className="mt-0.5 text-[12px] leading-[1.45] text-[var(--mute)]">{body}</div>
    </div>
  </div>
);

export const AnalyticsPage = ReportingPage;
