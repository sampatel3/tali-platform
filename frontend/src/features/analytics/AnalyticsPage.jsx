import React, { useEffect, useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import {
  assessments as assessmentsApi,
  analytics as analyticsApi,
  roles as rolesApi,
  tasks as tasksApi,
} from '../../shared/api';
import { getCategoryScoresFromAssessment } from '../../lib/comparisonCategories';
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

const formatDollars = (cents) => {
  const n = Number(cents || 0) / 100;
  return n >= 100 ? `$${Math.round(n)}` : `$${n.toFixed(0)}`;
};

const formatRelative = (iso) => {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return '';
  const m = Math.round(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
};

const EMPTY_SUMMARY = {
  window: { from: null, to: null, label: 'Last 30 days' },
  kpis: {
    decisions_made: { current: 0, prior: 0, delta_pct: null },
    auto_advanced: { current: 0, borderlines_flagged: 0 },
    auto_rejected: { current: 0, below_threshold: 0 },
    org_spend: { spent_cents: 0, budget_cents: 0, over_pct: null, top_role: null, active_role_count: 0 },
  },
  narrator: { paragraph: '', chips: [] },
  decisions_feed: [],
  anomalies: [],
  funnel: [],
  score_buckets: [],
};

export const ReportingPage = ({ onNavigate, NavComponent }) => {
  const [roles, setRoles] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [roleFilter, setRoleFilter] = useState('');
  const [taskFilter, setTaskFilter] = useState('');
  const [dateRange, setDateRange] = useState('30d');
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [summary, setSummary] = useState(EMPTY_SUMMARY);
  const [activeChip, setActiveChip] = useState(null);

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
    const params = { ...getDateRangeParams(dateRange) };
    if (roleFilter) params.role_id = roleFilter;
    if (taskFilter) params.task_id = taskFilter;
    return params;
  }, [dateRange, roleFilter, taskFilter]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    analyticsApi.reportingSummary(queryParams)
      .then((res) => {
        if (cancelled) return;
        const data = res?.data || EMPTY_SUMMARY;
        setSummary({
          window: data.window || EMPTY_SUMMARY.window,
          kpis: data.kpis || EMPTY_SUMMARY.kpis,
          narrator: data.narrator || EMPTY_SUMMARY.narrator,
          decisions_feed: Array.isArray(data.decisions_feed) ? data.decisions_feed : [],
          anomalies: Array.isArray(data.anomalies) ? data.anomalies : [],
          funnel: Array.isArray(data.funnel) ? data.funnel : [],
          score_buckets: Array.isArray(data.score_buckets) ? data.score_buckets : [],
        });
        setActiveChip(null);
      })
      .catch(() => {
        if (!cancelled) setSummary(EMPTY_SUMMARY);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [queryParams]);

  const rangeLabel = summary.window?.label
    || DATE_RANGE_OPTIONS.find((opt) => opt.value === dateRange)?.label
    || 'Last 30 days';

  const decisionsKpi = summary.kpis.decisions_made;
  const advancedKpi = summary.kpis.auto_advanced;
  const rejectedKpi = summary.kpis.auto_rejected;
  const spendKpi = summary.kpis.org_spend;
  const overBudget = (spendKpi.over_pct || 0) > 0;

  const histogramData = summary.score_buckets?.length ? summary.score_buckets : [
    { range: '0-20', count: 0, percentage: 0 },
    { range: '20-40', count: 0, percentage: 0 },
    { range: '40-60', count: 0, percentage: 0 },
    { range: '60-80', count: 0, percentage: 0 },
    { range: '80-100', count: 0, percentage: 0 },
  ];

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
        'candidate_name', 'candidate_email', 'task', 'role', 'status',
        'score_10', 'score_100', 'completed_at',
        'task_completion', 'prompt_clarity', 'context_provision',
        'independence_efficiency', 'response_utilization',
        'debugging_design', 'written_communication', 'role_fit',
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

  const activeChipBody = activeChip
    ? (summary.narrator.chips || []).find((c) => c.key === activeChip)?.body
    : null;

  const decisionsDelta = decisionsKpi.delta_pct;
  const decisionsDeltaLabel = decisionsDelta == null
    ? `${decisionsKpi.current === 0 ? 'No prior data' : 'No change vs prior period'}`
    : `${decisionsDelta > 0 ? '+' : ''}${decisionsDelta.toFixed(0)}% vs prior ${rangeLabel.toLowerCase()}`;

  return (
    <div>
      <NavComponent currentPage="reporting" onNavigate={onNavigate} />
      <div className="mc-page mc-page-narrow">
        <PageHero
          kicker={`MISSION CONTROL · ${rangeLabel.toUpperCase()}${roleFilter ? '' : ' · ALL ROLES'}`}
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
              <span className="mb-1 block font-mono text-xs uppercase tracking-[0.08em] text-[var(--mute)]">Role</span>
              <Select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}>
                <option value="">All roles</option>
                {roles.map((role) => (
                  <option key={role.id} value={role.id}>{role.name}</option>
                ))}
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs uppercase tracking-[0.08em] text-[var(--mute)]">Task</span>
              <Select value={taskFilter} onChange={(event) => setTaskFilter(event.target.value)}>
                <option value="">All tasks</option>
                {tasks.map((task) => (
                  <option key={task.id} value={task.id}>{task.name}</option>
                ))}
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs uppercase tracking-[0.08em] text-[var(--mute)]">Date range</span>
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
            {/* Narrator — concrete numbers + clickable chip drill-ins. */}
            <section className="mc-narrator">
              <div className="mc-narrator-bg" aria-hidden="true" />
              <div className="mc-kicker" style={{ color: 'rgba(255,255,255,0.7)', marginBottom: 6 }}>
                NARRATOR · WHAT THE AGENT DID
              </div>
              <p>{summary.narrator.paragraph}</p>
              {summary.narrator.chips?.length ? (
                <div className="mc-narrator-chips">
                  {summary.narrator.chips.map((chip) => (
                    <button
                      key={chip.key}
                      type="button"
                      className={`mc-narrator-chip ${activeChip === chip.key ? 'on' : ''}`.trim()}
                      onClick={() => setActiveChip((prev) => (prev === chip.key ? null : chip.key))}
                    >
                      {chip.label}
                    </button>
                  ))}
                </div>
              ) : null}
              {activeChipBody ? (
                <div className="mc-narrator-chip-body">{activeChipBody}</div>
              ) : null}
            </section>

            {/* KPI row matching the canvas: Decisions / Auto-advanced /
                Auto-rejected / Org spend with deltas + drivers. */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <Panel className="p-4">
                <div className="mb-1 font-mono text-xs uppercase tracking-[0.08em] text-[var(--mute)]">Decisions made</div>
                <div className="text-2xl font-bold text-[var(--ink)]">
                  {decisionsKpi.current.toLocaleString()}
                </div>
                <div className="mt-1 text-[12px] text-[var(--mute)]" style={{ color: decisionsDelta != null && decisionsDelta > 0 ? 'var(--purple)' : 'var(--mute)' }}>
                  {decisionsDeltaLabel}
                </div>
              </Panel>
              <Panel className="p-4">
                <div className="mb-1 font-mono text-xs uppercase tracking-[0.08em] text-[var(--mute)]">Auto-advanced</div>
                <div className="text-2xl font-bold text-[var(--purple)]">
                  {advancedKpi.current}
                </div>
                <div className="mt-1 text-[12px] text-[var(--mute)]">
                  {advancedKpi.borderlines_flagged > 0
                    ? `+${advancedKpi.borderlines_flagged} borderline${advancedKpi.borderlines_flagged === 1 ? '' : 's'} flagged`
                    : 'No borderlines flagged'}
                </div>
              </Panel>
              <Panel className="p-4">
                <div className="mb-1 font-mono text-xs uppercase tracking-[0.08em] text-[var(--mute)]">Auto-rejected</div>
                <div className="text-2xl font-bold text-[var(--ink)]">{rejectedKpi.current}</div>
                <div className="mt-1 text-[12px] text-[var(--mute)]">All below role threshold</div>
              </Panel>
              <Panel className="p-4">
                <div className="mb-1 font-mono text-xs uppercase tracking-[0.08em] text-[var(--mute)]">
                  Org spend{spendKpi.budget_cents > 0 ? ` (vs ${formatDollars(spendKpi.budget_cents)} cap)` : ''}
                </div>
                <div className="text-2xl font-bold text-[var(--ink)]">
                  {formatDollars(spendKpi.spent_cents)}
                  {spendKpi.budget_cents > 0 ? (
                    <span className="text-base font-normal text-[var(--mute)]"> / {formatDollars(spendKpi.budget_cents)}</span>
                  ) : null}
                </div>
                <div className="mt-1 text-[12px]" style={{ color: overBudget ? 'var(--amber)' : 'var(--mute)' }}>
                  {spendKpi.active_role_count === 0
                    ? 'No agent-enabled roles yet'
                    : overBudget
                      ? `${spendKpi.over_pct?.toFixed(0)}% over${spendKpi.top_role ? ` · driven by ${spendKpi.top_role}` : ''}`
                      : 'Within budget'}
                </div>
              </Panel>
            </div>

            <div className="grid gap-5 lg:grid-cols-[1.3fr_1fr]">
              {/* Decisions feed (left, larger). */}
              <Panel className="p-4">
                <h2 className="mb-1 font-bold text-base">Decisions feed</h2>
                <p className="mb-3 text-[12.5px] text-[var(--mute)]">
                  A reverse-chronological log of every consequential action.
                </p>
                {summary.decisions_feed.length === 0 ? (
                  <div className="mc-decisions-empty" style={{ padding: '24px 4px', textAlign: 'left', color: 'var(--mute)', fontSize: 13 }}>
                    No agent decisions in this window yet. Once the agent advances, rejects, or
                    flags a candidate, every action lands here with the reasoning attached.
                  </div>
                ) : (
                  <div className="mc-decisions-feed">
                    {summary.decisions_feed.slice(0, 8).map((decision) => {
                      const tone = decision.kind === 'advance' ? 'var(--green, #16a34a)'
                        : decision.kind === 'reject' ? 'var(--red, #dc2626)'
                        : decision.kind === 'flag' ? 'var(--amber, #f59e0b)'
                        : decision.kind === 'pause' ? 'var(--mute)'
                        : 'var(--purple)';
                      const verb = decision.recommendation
                        || (decision.kind === 'advance' ? 'Advanced candidate'
                          : decision.kind === 'reject' ? 'Rejected candidate'
                          : decision.kind === 'flag' ? 'Flagged candidate'
                          : decision.kind === 'pause' ? 'Paused agent'
                          : decision.kind === 'invite' ? 'Sent invitation'
                          : 'Recorded action');
                      const candName = decision.candidate_name || 'Candidate';
                      return (
                        <div key={decision.id} className="mc-decisions-row">
                          <span className="mc-decisions-row-time">{formatRelative(decision.created_at)}</span>
                          <span
                            className="mc-decisions-row-dot"
                            style={{ background: `color-mix(in oklab, ${tone} 22%, transparent)`, color: tone }}
                            aria-hidden="true"
                          />
                          <div>
                            <div className="mc-decisions-row-title">
                              {decision.kind === 'advance' || decision.kind === 'reject'
                                ? `${verb}${candName !== 'Candidate' ? ` · ${candName}` : ''}`
                                : verb}
                            </div>
                            <div className="mc-decisions-row-body">{decision.reasoning || '—'}</div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </Panel>

              <div className="flex flex-col gap-4">
                <Panel className="p-4">
                  <h2 className="mb-1 font-bold text-base">Anomalies</h2>
                  <p className="mb-3 text-[12.5px] text-[var(--mute)]">Things worth your attention.</p>
                  {summary.anomalies.length === 0 ? (
                    <p className="text-[12.5px] text-[var(--mute)]">
                      Anomalies appear once the agent has graded enough assessments to spot drift.
                    </p>
                  ) : (
                    <div className="flex flex-col gap-2">
                      {summary.anomalies.map((anomaly, i) => (
                        <AnomalyRow key={`${anomaly.title}-${i}`} {...anomaly} />
                      ))}
                    </div>
                  )}
                </Panel>

                <Panel className="p-4">
                  <h2 className="mb-1 font-bold text-base">Funnel · org-wide</h2>
                  <p className="mb-3 text-[12.5px] text-[var(--mute)]">From application through review to hire.</p>
                  <div className="flex flex-col gap-2">
                    {(summary.funnel.length ? summary.funnel : [
                      { label: 'APPLIED', count: 0, percentage: 0 },
                      { label: 'INVITED', count: 0, percentage: 0 },
                      { label: 'DONE', count: 0, percentage: 0 },
                      { label: 'REVIEW', count: 0, percentage: 0 },
                      { label: 'HIRED', count: 0, percentage: 0 },
                    ]).map((stage) => (
                      <div key={stage.label} className="grid grid-cols-[110px_1fr_auto] items-center gap-3">
                        <span className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">{stage.label}</span>
                        <div className="relative h-2.5 overflow-hidden rounded-full bg-[var(--bg-3)]">
                          <div
                            className="absolute inset-y-0 left-0 rounded-full"
                            style={{
                              width: `${Math.max(0, Math.min(100, safeNumber(stage.percentage)))}%`,
                              background: 'linear-gradient(90deg, var(--purple), color-mix(in srgb, var(--purple) 60%, var(--lime)))',
                            }}
                          />
                        </div>
                        <span className="font-mono text-[11.5px] text-[var(--ink-2)]">{safeNumber(stage.count).toLocaleString()}</span>
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
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                    <XAxis dataKey="range" tick={{ fill: 'var(--mute)', fontSize: 12 }} axisLine={{ stroke: 'var(--line)' }} tickLine={{ stroke: 'var(--line)' }} />
                    <YAxis tick={{ fill: 'var(--mute)', fontSize: 12 }} axisLine={{ stroke: 'var(--line)' }} tickLine={{ stroke: 'var(--line)' }} />
                    <Tooltip
                      contentStyle={{
                        background: 'var(--bg-2)',
                        border: '1px solid var(--line)',
                        borderRadius: '12px',
                        color: 'var(--ink)',
                      }}
                    />
                    <Bar dataKey="count" fill="var(--purple)" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Panel>
          </div>
        )}
      </div>
    </div>
  );
};

const AnomalyRow = ({ tone, title, body }) => {
  const dotColor = tone === 'red'
    ? 'var(--red)'
    : tone === 'amber'
      ? 'var(--amber)'
      : 'var(--purple)';
  return (
    <div className="flex items-start gap-3 rounded-[10px] border border-[var(--line)] bg-[var(--bg)] px-3 py-3">
      <span
        className="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full"
        style={{ background: dotColor }}
        aria-hidden="true"
      />
      <div>
        <div className="text-[13px] font-medium text-[var(--ink)]">{title}</div>
        <div className="mt-0.5 text-[12px] leading-[1.45] text-[var(--mute)]">{body}</div>
      </div>
    </div>
  );
};

export const AnalyticsPage = ReportingPage;
