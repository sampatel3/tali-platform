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
import { Button, Panel, Select } from '../../shared/ui/TaaliPrimitives';
import { CardSkeleton, StatCardSkeleton } from '../../shared/ui/Skeletons';

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

export const AnalyticsPage = ({ onNavigate, NavComponent }) => {
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
      <NavComponent currentPage="analytics" onNavigate={onNavigate} />
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold">Analytics</h1>
            <p className="text-sm text-[var(--taali-muted)]">Performance insights across assessments and AI-collaboration dimensions.</p>
          </div>
          <Button type="button" variant="secondary" onClick={handleExportCsv} disabled={loading || exporting}>
            {exporting ? 'Exporting...' : 'Export CSV'}
          </Button>
        </div>

        <div className="mb-6 grid gap-3 md:grid-cols-4">
          <label className="block">
            <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Role</span>
            <Select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}>
              <option value="">All roles</option>
              {roles.map((role) => (
                <option key={role.id} value={role.id}>{role.name}</option>
              ))}
            </Select>
          </label>
          <label className="block">
            <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Task</span>
            <Select value={taskFilter} onChange={(event) => setTaskFilter(event.target.value)}>
              <option value="">All tasks</option>
              {tasks.map((task) => (
                <option key={task.id} value={task.id}>{task.name}</option>
              ))}
            </Select>
          </label>
          <label className="block">
            <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Date range</span>
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

        {loading ? (
          <div className="space-y-6">
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <StatCardSkeleton />
              <StatCardSkeleton />
              <StatCardSkeleton />
              <StatCardSkeleton />
            </div>
            <div className="grid gap-6 lg:grid-cols-2">
              <CardSkeleton lines={8} />
              <CardSkeleton lines={8} />
            </div>
            <CardSkeleton lines={10} />
          </div>
        ) : (
          <div className="space-y-6">
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <Panel className="p-5">
                <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Total Assessments</div>
                <div className="text-3xl font-bold text-[var(--taali-text)]">{data.total_assessments}</div>
              </Panel>
              <Panel className="p-5">
                <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Avg Score</div>
                <div className="text-3xl font-bold text-[var(--taali-purple)]">
                  {data.avg_score != null ? `${Number(data.avg_score).toFixed(1)}/10` : '—'}
                </div>
              </Panel>
              <Panel className="p-5">
                <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Completion Rate</div>
                <div className="text-3xl font-bold text-[var(--taali-text)]">{safeNumber(data.completion_rate).toFixed(1)}%</div>
              </Panel>
              <Panel className="p-5">
                <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Avg Time</div>
                <div className="text-3xl font-bold text-[var(--taali-text)]">
                  {data.avg_time_minutes != null ? `${data.avg_time_minutes}m` : '—'}
                </div>
              </Panel>
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
              <Panel className="p-5">
                <h2 className="mb-4 font-bold text-lg">Completion Rate Trend</h2>
                <div className="h-[280px]">
                  <ResponsiveContainer>
                    <BarChart data={weekly}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--taali-border-muted)" />
                      <XAxis dataKey="week" />
                      <YAxis domain={[0, 100]} />
                      <Tooltip />
                      <Bar dataKey="rate" fill="var(--taali-purple)" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </Panel>

              <Panel className="p-5">
                <h2 className="mb-4 font-bold text-lg">Score Distribution</h2>
                <div className="h-[280px]">
                  <ResponsiveContainer>
                    <BarChart data={histogramData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--taali-border-muted)" />
                      <XAxis dataKey="range" />
                      <YAxis />
                      <Tooltip />
                      <Bar dataKey="count" fill="var(--taali-info)" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </Panel>
            </div>

            <Panel className="p-5">
              <h2 className="mb-4 font-bold text-lg">Per-Dimension Averages</h2>
              <div className="h-[360px]">
                <ResponsiveContainer>
                  <RadarChart data={radarData}>
                    <PolarGrid stroke="var(--taali-border-muted)" />
                    <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 11 }} />
                    <PolarRadiusAxis domain={[0, 10]} />
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
