import React, { useEffect, useMemo, useState } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { analytics as analyticsApi, assessments as assessmentsApi } from '../../shared/api';
import { AppShell } from '../../shared/layout/TaaliLayout';

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

const scoreFromAssessment = (assessment) => (
  Number(assessment?.final_score)
  || Number(assessment?.taali_score)
  || (Number.isFinite(Number(assessment?.score)) ? Number(assessment.score) * 10 : 0)
);

export const ReportingPage = ({ onNavigate }) => {
  const [dateRange, setDateRange] = useState('30d');
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [data, setData] = useState({
    weekly_completion: [],
    total_assessments: 0,
    completed_count: 0,
    completion_rate: 0,
    avg_score: null,
    avg_time_minutes: null,
    score_buckets: [],
  });
  const [topAssessments, setTopAssessments] = useState([]);

  const queryParams = useMemo(() => getDateRangeParams(dateRange), [dateRange]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const [analyticsRes, assessmentsRes] = await Promise.all([
          analyticsApi.get(queryParams),
          assessmentsApi.list({ limit: 20, offset: 0 }),
        ]);
        if (cancelled) return;
        const analyticsPayload = analyticsRes?.data || {};
        const assessmentPayload = assessmentsRes?.data || {};
        const assessmentItems = Array.isArray(assessmentPayload) ? assessmentPayload : (assessmentPayload.items || []);
        setData({
          weekly_completion: analyticsPayload?.weekly_completion || [],
          total_assessments: safeNumber(analyticsPayload?.total_assessments),
          completed_count: safeNumber(analyticsPayload?.completed_count),
          completion_rate: safeNumber(analyticsPayload?.completion_rate),
          avg_score: analyticsPayload?.avg_score,
          avg_time_minutes: analyticsPayload?.avg_time_minutes,
          score_buckets: Array.isArray(analyticsPayload?.score_buckets) ? analyticsPayload.score_buckets : [],
        });
        setTopAssessments(
          assessmentItems
            .slice()
            .sort((left, right) => scoreFromAssessment(right) - scoreFromAssessment(left))
            .slice(0, 5)
        );
      } catch {
        if (cancelled) return;
        setData({
          weekly_completion: [],
          total_assessments: 0,
          completed_count: 0,
          completion_rate: 0,
          avg_score: null,
          avg_time_minutes: null,
          score_buckets: [],
        });
        setTopAssessments([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [queryParams]);

  const trendData = data.weekly_completion?.length
    ? data.weekly_completion.map((entry, index) => ({
      week: entry.week || `W${index + 1}`,
      rate: safeNumber(entry.rate),
      count: safeNumber(entry.count),
    }))
    : Array.from({ length: 6 }).map((_, index) => ({ week: `W${index + 1}`, rate: 0, count: 0 }));

  const scoreDistribution = data.score_buckets?.length
    ? data.score_buckets
    : [
      { range: '0-20', count: 0 },
      { range: '20-40', count: 0 },
      { range: '40-60', count: 0 },
      { range: '60-80', count: 0 },
      { range: '80-100', count: 0 },
    ];

  const exportCsv = async () => {
    setExporting(true);
    try {
      const res = await assessmentsApi.list({ limit: 200, offset: 0 });
      const payload = res?.data || {};
      const items = Array.isArray(payload) ? payload : (payload.items || []);
      const csv = [
        ['candidate_name', 'candidate_email', 'task_name', 'role_name', 'score_100', 'completed_at'].join(','),
        ...items.map((item) => [
          item?.candidate_name || item?.candidate?.full_name || '',
          item?.candidate_email || item?.candidate?.email || '',
          item?.task_name || item?.task?.name || '',
          item?.role_name || '',
          scoreFromAssessment(item),
          item?.completed_at || '',
        ].map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(',')),
      ].join('\n');
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'taali-reporting.csv';
      anchor.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  return (
    <AppShell currentPage="reporting" onNavigate={onNavigate}>
      <div className="page">
        <div className="page-head">
          <div className="tally-bg" />
          <div>
            <div className="kicker">03 · RECRUITER WORKSPACE</div>
            <h1>Reporting<em>.</em></h1>
            <p className="sub">Pipeline health, AI-collaboration quality over time, and where candidates convert or don’t.</p>
          </div>
          <div className="row">
            <select className="rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-4 py-2 text-sm" value={dateRange} onChange={(event) => setDateRange(event.target.value)}>
              {DATE_RANGE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
            <button type="button" className="btn btn-outline btn-sm" onClick={exportCsv} disabled={exporting || loading}>
              {exporting ? 'Exporting…' : 'Export CSV'}
            </button>
          </div>
        </div>

        <div className="mb-5 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {[
            ['Assessments run', data.total_assessments, 'Across the selected time window'],
            ['Median AI-collab score', data.avg_score == null ? '—' : Number(data.avg_score).toFixed(1), 'Average scored performance'],
            ['Advance rate', `${Math.round(data.completion_rate || 0)}%`, `${data.completed_count} completed assessments`],
            ['Time to decision', data.avg_time_minutes == null ? '—' : `${Math.round(data.avg_time_minutes)}m`, 'Average time spent in assessment'],
          ].map(([label, value, foot]) => (
            <div key={label} className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] px-6 py-6 shadow-[var(--shadow-sm)]">
              <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">{label}</div>
              <div className="mt-3 font-[var(--font-display)] text-[56px] leading-none tracking-[-0.02em]">{value}</div>
              <div className="mt-2 text-[12.5px] text-[var(--mute)]">{foot}</div>
            </div>
          ))}
        </div>

        <div className="grid gap-5 xl:grid-cols-[2fr_1fr]">
          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-7 shadow-[var(--shadow-sm)]">
            <h2 className="font-[var(--font-display)] text-[26px] tracking-[-0.02em]">AI-collab score <em>over time</em>.</h2>
            <p className="mt-1 text-[13px] text-[var(--mute)]">Median completion-rate trend across the selected period.</p>
            <div className="mt-6 h-[260px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={trendData}>
                  <CartesianGrid stroke="var(--line)" strokeDasharray="3 4" vertical={false} />
                  <XAxis dataKey="week" tick={{ fill: 'var(--mute)', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: 'var(--mute)', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip />
                  <Line type="monotone" dataKey="rate" stroke="var(--purple)" strokeWidth={2.5} dot={{ r: 3, fill: 'var(--purple)' }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-7 shadow-[var(--shadow-sm)]">
            <h2 className="font-[var(--font-display)] text-[26px] tracking-[-0.02em]">Score <em>distribution</em>.</h2>
            <p className="mt-1 text-[13px] text-[var(--mute)]">Current scoring spread across the reporting window.</p>
            <div className="mt-6 space-y-3">
              {scoreDistribution.map((bucket) => {
                const count = safeNumber(bucket?.count);
                const max = Math.max(...scoreDistribution.map((entry) => safeNumber(entry?.count)), 1);
                return (
                  <div key={bucket.range} className="grid grid-cols-[60px_1fr_42px] items-center gap-3">
                    <span className="font-[var(--font-mono)] text-[11px] text-[var(--mute)]">{bucket.range}</span>
                    <div className="bar"><i style={{ width: `${(count / max) * 100}%` }} /></div>
                    <span className="text-right font-[var(--font-mono)] text-[12px]">{count}</span>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-7 shadow-[var(--shadow-sm)] xl:col-span-2">
            <h2 className="font-[var(--font-display)] text-[26px] tracking-[-0.02em]">Top <em>candidates this week</em>.</h2>
            <p className="mt-1 text-[13px] text-[var(--mute)]">Sorted by TAALI score from recent assessment activity.</p>
            <div className="mt-5 space-y-3">
              {loading ? (
                Array.from({ length: 5 }).map((_, index) => <div key={index} className="h-16 animate-pulse rounded-[10px] bg-[var(--bg)]" />)
              ) : topAssessments.length === 0 ? (
                <div className="py-8 text-sm text-[var(--mute)]">No completed assessments available yet.</div>
              ) : (
                topAssessments.map((assessment) => (
                  <button
                    key={assessment.id}
                    type="button"
                    className="grid w-full gap-4 rounded-[10px] border border-[var(--line-2)] px-4 py-3 text-left transition hover:border-[var(--purple)] md:grid-cols-[1fr_auto_auto] md:items-center"
                    onClick={() => onNavigate('candidate-detail', { candidateDetailAssessmentId: assessment.id })}
                  >
                    <div>
                      <div className="text-[13.5px] font-medium">{assessment?.candidate_name || assessment?.candidate?.full_name || 'Unknown candidate'}</div>
                      <div className="mt-1 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">
                        {assessment?.role_name || assessment?.task_name || 'Assessment'}
                      </div>
                    </div>
                    <span className="chip green">{assessment?.status || 'completed'}</span>
                    <span className="font-[var(--font-mono)] font-semibold text-[var(--purple)]">{scoreFromAssessment(assessment)} / 100</span>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
};

export default ReportingPage;
