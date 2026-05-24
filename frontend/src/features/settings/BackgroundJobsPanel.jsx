import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, X } from 'lucide-react';

import { useJobStatus } from '../../contexts/JobStatusContext';
import { roles as rolesApi } from '../../shared/api';
import { formatRelativeDateTime } from '../../shared/ui/RecruiterDesignPrimitives';
import AgentsOverviewPanel from './AgentsOverviewPanel';

const HISTORY_POLL_MS = 5000;
// Hide terminal-state history rows older than this so the panel stays focused
// on what actually needs attention. Toggle "Show all history" to override.
const HISTORY_RECENT_WINDOW_MS = 30 * 60 * 1000;

// Color tokens, keyed by the verbatim backend status string. Anything not
// listed falls through to the neutral grey dot (treated as terminal/unknown).
const STATUS_TONE = {
  running: 'running',
  started: 'running',
  queued: 'queued',
  cancelling: 'cancelling',
  cancelled: 'cancelled',
  completed: 'completed',
  success: 'completed',
  failed: 'failed',
  completed_with_errors: 'warning',
  partial: 'warning',
  nothing_to_score: 'noop',
  nothing_to_sync: 'noop',
  already_running: 'warning',
};

// Plain-English label for each backend status string. Anything not listed
// falls back to a humanized form (underscores → spaces, first letter capped)
// so we never surface a raw code like ``completed_with_errors`` to a recruiter.
const STATUS_LABEL = {
  running: 'Running',
  started: 'Running',
  queued: 'Queued',
  cancelling: 'Cancelling',
  cancelled: 'Cancelled',
  completed: 'Completed',
  success: 'Completed',
  completed_with_errors: 'Completed with errors',
  partial: 'Completed with errors',
  failed: 'Failed',
  nothing_to_score: 'Nothing to score',
  nothing_to_sync: 'Nothing to sync',
  already_running: 'Already running',
  lock_timeout: 'Workable was busy',
  idle: 'Idle',
};

const humanize = (value) => {
  const s = String(value ?? '').trim();
  if (!s) return '';
  return s.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
};

const statusLabel = (status) => {
  const s = String(status ?? '').toLowerCase();
  return STATUS_LABEL[s] || humanize(s) || 'Idle';
};

// Finished states. ``completed_with_errors`` / ``partial`` are terminal too —
// the job is done, some items just need follow-up — so they show a Finished
// time and auto-clear after the window like ``failed`` (the real signal lives
// on the requeued decision / its event, and "Show all history" still has them).
const TERMINAL_STATUSES = new Set([
  'completed', 'success', 'cancelled', 'failed',
  'completed_with_errors', 'partial',
  'nothing_to_score', 'nothing_to_sync', 'already_running',
]);

const ACTIVE_STATUSES = new Set(['running', 'started', 'queued', 'cancelling']);

const isRecentTerminal = (status, finishedAt, showAll) => {
  if (showAll) return true;
  const s = String(status ?? '').toLowerCase();
  if (!TERMINAL_STATUSES.has(s)) return true;
  if (!finishedAt) return false;
  const ts = typeof finishedAt === 'string'
    ? Date.parse(finishedAt)
    : Number(finishedAt?.getTime?.() ?? finishedAt);
  if (!Number.isFinite(ts)) return false;
  return Date.now() - ts <= HISTORY_RECENT_WINDOW_MS;
};

const isVisible = (status) => {
  const s = String(status ?? '').toLowerCase();
  return s !== 'idle' && s !== '';
};

const StatusDot = ({ status }) => {
  const s = String(status ?? '').toLowerCase();
  const tone = STATUS_TONE[s] || 'noop';
  return (
    <span className="bg-jobs-panel-status">
      <span className={`bg-jobs-panel-dot tone-${tone}`} aria-hidden="true" />
      <span className="bg-jobs-panel-status-label">{statusLabel(s)}</span>
    </span>
  );
};

const Timestamp = ({ value }) => {
  if (!value) return <span className="bg-jobs-panel-muted">—</span>;
  const iso = typeof value === 'string' ? value : (value?.toISOString?.() || String(value));
  return (
    <time dateTime={iso} title={iso}>{formatRelativeDateTime(iso)}</time>
  );
};

function ScoreCounters({ data }) {
  const total = Number(data?.total ?? data?.counters?.total ?? 0);
  const scored = Number(data?.scored ?? data?.counters?.scored ?? 0);
  const errors = Number(data?.errors ?? data?.counters?.errors ?? 0);
  const filtered = Number(data?.pre_screened_out ?? data?.counters?.pre_screened_out ?? 0);
  const processed = scored + errors + filtered;
  return (
    <div className="bg-jobs-panel-counters">
      <strong>{processed}</strong> / {total}
      <div className="bg-jobs-panel-breakdown">
        {scored} scored · {filtered} filtered · {errors} errors
      </div>
    </div>
  );
}

function FetchCounters({ data }) {
  const total = Number(data?.total ?? data?.counters?.total ?? 0);
  const fetched = Number(data?.fetched ?? data?.counters?.fetched ?? 0);
  const errors = Number(data?.errors ?? data?.counters?.errors ?? 0);
  return (
    <div className="bg-jobs-panel-counters">
      <strong>{fetched}</strong> / {total}
      <div className="bg-jobs-panel-breakdown">{errors} errors</div>
    </div>
  );
}

function ProcessCounters({ data }) {
  const fetchTotal = Number(data?.fetch?.total ?? 0);
  const fetchAttempted = Number(data?.fetch?.attempted ?? 0);
  const fetchFetched = Number(data?.fetch?.fetched ?? 0);
  const fetchUnavailable = Number(data?.fetch?.unavailable ?? 0);
  const fetchErrors = Number(data?.fetch?.errors ?? 0);
  const preTotal = Number(data?.pre_screen?.total ?? 0);
  const prePros = Number(data?.pre_screen?.processed ?? 0);
  const preErrors = Number(data?.pre_screen?.errors ?? 0);
  const scoreTotal = Number(data?.score?.total ?? 0);
  const scorePros = Number(data?.score?.scored ?? 0);
  const scoreErrors = Number(data?.score?.errors ?? 0);
  const scoreFiltered = Number(data?.score?.filtered ?? 0);
  const total = fetchTotal + preTotal + scoreTotal;
  const processed = fetchAttempted + prePros + scorePros;
  const lines = [];
  if (fetchTotal > 0) {
    const annot = [];
    if (fetchFetched) annot.push(`${fetchFetched} got CV`);
    if (fetchUnavailable) annot.push(`${fetchUnavailable} unavailable`);
    if (fetchErrors) annot.push(`${fetchErrors} err`);
    lines.push(`Fetch ${fetchAttempted}/${fetchTotal}${annot.length ? ` (${annot.join(', ')})` : ''}`);
  }
  if (preTotal > 0) {
    lines.push(`Pre-screen ${prePros}/${preTotal}${preErrors ? ` (${preErrors} err)` : ''}`);
  }
  if (scoreTotal > 0) {
    const annot = [];
    if (scoreFiltered) annot.push(`${scoreFiltered} filtered`);
    if (scoreErrors) annot.push(`${scoreErrors} err`);
    lines.push(`Score ${scorePros}/${scoreTotal}${annot.length ? ` (${annot.join(', ')})` : ''}`);
  }
  return (
    <div className="bg-jobs-panel-counters">
      <strong>{processed}</strong> / {total}
      {lines.map((l) => (
        <div key={l} className="bg-jobs-panel-breakdown">{l}</div>
      ))}
      {data?.current_step ? (
        <div className="bg-jobs-panel-phase">step: <code>{data.current_step}</code></div>
      ) : null}
    </div>
  );
}

function GraphCounters({ data }) {
  const total = Number(data?.total ?? data?.counters?.total ?? 0);
  const synced = Number(data?.synced ?? data?.counters?.synced ?? 0);
  const errors = Number(data?.errors ?? data?.counters?.errors ?? 0);
  return (
    <div className="bg-jobs-panel-counters">
      <strong>{synced}</strong> / {total}
      <div className="bg-jobs-panel-breakdown">{errors} errors</div>
    </div>
  );
}

// Recruiter approve / bulk-approve of Hub decisions — one row drains the
// batch's Workable writebacks sequentially.
function DecisionBatchCounters({ data }) {
  const total = Number(data?.total ?? 0);
  const succeeded = Number(data?.succeeded ?? 0);
  const requeued = Number(data?.requeued ?? 0);
  const failed = Number(data?.failed ?? 0);
  const annot = [];
  if (requeued) annot.push(`${requeued} requeued`);
  if (failed) annot.push(`${failed} failed`);
  return (
    <div className="bg-jobs-panel-counters">
      <strong>{succeeded}</strong> / {total} approved
      {annot.length ? <div className="bg-jobs-panel-breakdown">{annot.join(' · ')}</div> : null}
    </div>
  );
}

// A single Workable write-back op (override, hand-back stage move, manual
// outcome sync, note) run through the generic serialized runner.
const WORKABLE_OP_LABELS = {
  override_decision: 'Override',
  move_stage: 'Stage move',
  manual_outcome: 'Outcome sync',
  post_note: 'Note',
};

// Plain-English explanation for the failure codes the runner can emit, so the
// panel never shows a bare code like ``api_error`` / ``not_writeable``.
const WORKABLE_OP_CODE_LABELS = {
  api_error: 'Workable API error',
  not_writeable: 'No Workable write access',
  lock_timeout: 'Workable was busy',
  rate_limited: 'Workable rate limit hit',
  unexpected: 'Unexpected error',
};

function WorkableOpCounters({ data }) {
  const opType = String(data?.op_type || '');
  const label = WORKABLE_OP_LABELS[opType] || (opType ? humanize(opType) : 'Workable update');
  const code = data?.code ? String(data.code) : null;
  const codeLabel = code ? (WORKABLE_OP_CODE_LABELS[code] || humanize(code)) : null;
  return (
    <div className="bg-jobs-panel-counters">
      <strong>{label}</strong>
      {codeLabel ? <div className="bg-jobs-panel-breakdown">{codeLabel}</div> : null}
    </div>
  );
}

function WorkableCounters({ data }) {
  const jobsTotal = Number(data?.jobs_total ?? 0);
  const jobsProcessed = Number(data?.jobs_processed ?? 0);
  const candidatesSeen = Number(data?.candidates_seen ?? 0);
  const candidatesUpserted = Number(data?.candidates_upserted ?? 0);
  const applicationsUpserted = Number(data?.applications_upserted ?? 0);
  const errCount = Array.isArray(data?.errors) ? data.errors.length : 0;
  return (
    <div className="bg-jobs-panel-counters">
      <strong>{jobsProcessed}</strong> / {jobsTotal} jobs
      <div className="bg-jobs-panel-breakdown">
        {candidatesSeen} seen · {candidatesUpserted} upserted · {applicationsUpserted} apps · {errCount} errors
      </div>
      {data?.phase && data?.status === 'running' ? (
        <div className="bg-jobs-panel-phase">phase: <code>{data.phase}</code></div>
      ) : null}
    </div>
  );
}

function JobRow({
  type, status, scope, counters, startedAt, finishedAt,
  onCancel, onDismiss, isLive,
}) {
  const s = String(status ?? '').toLowerCase();
  const isTerminal = TERMINAL_STATUSES.has(s);
  const isActive = ACTIVE_STATUSES.has(s);
  return (
    <div className="bg-jobs-panel-row">
      <div className="bg-jobs-panel-cell"><StatusDot status={status} /></div>
      <div className="bg-jobs-panel-cell">
        <span className="bg-jobs-panel-type">{type}</span>
      </div>
      <div className="bg-jobs-panel-cell">{scope}</div>
      <div className="bg-jobs-panel-cell">{counters}</div>
      <div className="bg-jobs-panel-cell"><Timestamp value={startedAt} /></div>
      <div className="bg-jobs-panel-cell">
        {isTerminal ? <Timestamp value={finishedAt} /> : <span className="bg-jobs-panel-muted">—</span>}
      </div>
      <div className="bg-jobs-panel-cell bg-jobs-panel-actions">
        {isLive && isActive && onCancel ? (
          <button
            type="button"
            className="bg-jobs-panel-btn"
            onClick={onCancel}
            disabled={s === 'cancelling'}
          >
            {s === 'cancelling' ? 'Cancelling…' : 'Cancel'}
          </button>
        ) : null}
        {isLive && isTerminal && onDismiss ? (
          <button type="button" className="bg-jobs-panel-icon-btn" onClick={onDismiss} aria-label="Dismiss">
            <X size={14} />
          </button>
        ) : null}
      </div>
    </div>
  );
}

const tsValue = (value) => {
  if (!value) return 0;
  if (typeof value === 'number') return value;
  if (typeof value === 'string') return Date.parse(value) || 0;
  return value?.getTime?.() ?? 0;
};

export default function BackgroundJobsPanel() {
  const ctx = useJobStatus();
  const [history, setHistory] = useState([]);
  const [workableHistory, setWorkableHistory] = useState([]);
  const [lastUpdatedAt, setLastUpdatedAt] = useState(null);
  const [tick, setTick] = useState(0);
  const [showAllHistory, setShowAllHistory] = useState(false);
  // Default to the consolidated Agents overview; "Job runs" holds the original
  // scoring / CV fetch / Workable / graph sync table.
  const [view, setView] = useState('agents');

  const liveJobs = ctx?.jobs ?? {};
  const liveFetch = ctx?.fetchJobs ?? {};
  const liveProcess = ctx?.processJobs ?? {};
  const graphSync = ctx?.graphSyncJob ?? null;
  const workableSync = ctx?.workableSyncJob ?? null;

  // 5s loop: history endpoints + heartbeat tick (drives "Last updated" tooltip).
  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const load = async () => {
      try {
        const [bg, wk] = await Promise.allSettled([
          rolesApi.backgroundJobsRuns(20),
          rolesApi.workableSyncRuns(10),
        ]);
        if (cancelled) return;
        if (bg.status === 'fulfilled') setHistory(bg.value?.data?.runs ?? []);
        if (wk.status === 'fulfilled') setWorkableHistory(wk.value?.data?.runs ?? []);
        setLastUpdatedAt(new Date());
      } catch {}
      if (!cancelled) timer = setTimeout(load, HISTORY_POLL_MS);
    };
    load();
    const heartbeat = setInterval(() => setTick((t) => t + 1), HISTORY_POLL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      clearInterval(heartbeat);
    };
  }, []);

  // Bookkeeping: history rows whose run_id matches a live job — we'd render
  // them as live (with actions) instead of read-only history. Match by kind
  // + scope_id + active status.
  const liveScoreRoleIds = useMemo(() => new Set(
    Object.keys(liveJobs).map((k) => Number(k)),
  ), [liveJobs]);
  const liveFetchRoleIds = useMemo(() => new Set(
    Object.keys(liveFetch).map((k) => Number(k)),
  ), [liveFetch]);

  // Each "row" is { key, type, status, sortAt, render }. We collect from
  // every job source then sort newest-first so the panel shows one flat
  // chronological list — no per-source grouping. The `type` column tells
  // the recruiter what kind of job each row is.
  const allRows = useMemo(() => {
    const rows = [];

    for (const [roleIdRaw, data] of Object.entries(liveProcess)) {
      const roleId = Number(roleIdRaw);
      if (!isVisible(data?.status)) continue;
      const scope = data?.role_name ? `Role: ${data.role_name}` : `Role #${roleId}`;
      rows.push({
        key: `process-live-${roleId}`,
        type: 'Process candidates',
        status: data.status,
        sortAt: tsValue(data?.started_at),
        node: (
          <JobRow
            key={`process-live-${roleId}`}
            type="Process candidates"
            status={data.status}
            scope={scope}
            counters={<ProcessCounters data={data} />}
            startedAt={data?.started_at}
            finishedAt={data?.finished_at}
            onCancel={() => ctx?.cancelProcessJob?.(roleId)}
            onDismiss={() => ctx?.dismissProcessJob?.(roleId)}
            isLive
          />
        ),
      });
    }

    for (const [roleIdRaw, data] of Object.entries(liveJobs)) {
      const roleId = Number(roleIdRaw);
      if (!isVisible(data?.status)) continue;
      const scope = data?.role_name ? `Role: ${data.role_name}` : `Role #${roleId}`;
      rows.push({
        key: `score-live-${roleId}`,
        type: 'Scoring batch',
        status: data.status,
        sortAt: tsValue(data?.started_at),
        node: (
          <JobRow
            key={`score-live-${roleId}`}
            type="Scoring batch"
            status={data.status}
            scope={scope}
            counters={<ScoreCounters data={data} />}
            startedAt={data?.started_at}
            finishedAt={data?.finished_at}
            onCancel={() => ctx?.cancelBatch?.(roleId)}
            onDismiss={() => ctx?.dismissJob?.(roleId)}
            isLive
          />
        ),
      });
    }
    for (const r of history) {
      if (r.kind !== 'scoring_batch') continue;
      if (liveScoreRoleIds.has(Number(r.scope_id))) continue;
      if (!isRecentTerminal(r.status, r.finished_at, showAllHistory)) continue;
      const scope = r.role_name ? `Role: ${r.role_name}` : `Role #${r.scope_id}`;
      rows.push({
        key: `score-hist-${r.id}`,
        type: 'Scoring batch',
        status: r.status,
        sortAt: tsValue(r.started_at),
        node: (
          <JobRow
            key={`score-hist-${r.id}`}
            type="Scoring batch"
            status={r.status}
            scope={scope}
            counters={<ScoreCounters data={{ ...r.counters }} />}
            startedAt={r.started_at}
            finishedAt={r.finished_at}
          />
        ),
      });
    }

    for (const [roleIdRaw, data] of Object.entries(liveFetch)) {
      const roleId = Number(roleIdRaw);
      if (!isVisible(data?.status)) continue;
      rows.push({
        key: `fetch-live-${roleId}`,
        type: 'CV fetch',
        status: data.status,
        sortAt: tsValue(data?.started_at),
        node: (
          <JobRow
            key={`fetch-live-${roleId}`}
            type="CV fetch"
            status={data.status}
            scope={`Role #${roleId}`}
            counters={<FetchCounters data={data} />}
            startedAt={data?.started_at}
            finishedAt={data?.finished_at}
            onCancel={() => ctx?.cancelFetchCvs?.(roleId)}
            onDismiss={() => ctx?.dismissFetchJob?.(roleId)}
            isLive
          />
        ),
      });
    }
    for (const r of history) {
      if (r.kind !== 'cv_fetch') continue;
      if (liveFetchRoleIds.has(Number(r.scope_id))) continue;
      if (!isRecentTerminal(r.status, r.finished_at, showAllHistory)) continue;
      const scope = r.role_name ? `Role: ${r.role_name}` : `Role #${r.scope_id}`;
      rows.push({
        key: `fetch-hist-${r.id}`,
        type: 'CV fetch',
        status: r.status,
        sortAt: tsValue(r.started_at),
        node: (
          <JobRow
            key={`fetch-hist-${r.id}`}
            type="CV fetch"
            status={r.status}
            scope={scope}
            counters={<FetchCounters data={{ ...r.counters }} />}
            startedAt={r.started_at}
            finishedAt={r.finished_at}
          />
        ),
      });
    }

    if (workableSync && (workableSync.sync_in_progress || workableSync.status === 'running')) {
      rows.push({
        key: 'workable-live',
        type: 'Workable sync',
        status: workableSync.status || 'running',
        sortAt: tsValue(workableSync.started_at),
        node: (
          <JobRow
            key="workable-live"
            type="Workable sync"
            status={workableSync.status || 'running'}
            scope={`Org-wide · mode: ${workableSync.mode || 'metadata'}`}
            counters={<WorkableCounters data={workableSync} />}
            startedAt={workableSync.started_at}
            finishedAt={workableSync.finished_at}
            onCancel={() => ctx?.cancelWorkableSync?.(workableSync.run_id ?? null)}
            onDismiss={() => ctx?.dismissWorkableSyncJob?.()}
            isLive
          />
        ),
      });
    }
    for (const r of workableHistory) {
      if (workableSync?.run_id && r.id === workableSync.run_id && (workableSync.sync_in_progress || workableSync.status === 'running')) continue;
      if (!isRecentTerminal(r.status, r.finished_at, showAllHistory)) continue;
      rows.push({
        key: `workable-hist-${r.id}`,
        type: 'Workable sync',
        status: r.status,
        sortAt: tsValue(r.started_at),
        node: (
          <JobRow
            key={`workable-hist-${r.id}`}
            type="Workable sync"
            status={r.status}
            scope={`Org-wide · mode: ${r.mode || 'metadata'}`}
            counters={<WorkableCounters data={r} />}
            startedAt={r.started_at}
            finishedAt={r.finished_at}
          />
        ),
      });
    }

    if (graphSync && isVisible(graphSync.status)) {
      rows.push({
        key: 'graph-live',
        type: 'Graph sync',
        status: graphSync.status,
        sortAt: tsValue(graphSync?.started_at),
        node: (
          <JobRow
            key="graph-live"
            type="Graph sync"
            status={graphSync.status}
            scope="Org-wide"
            counters={<GraphCounters data={graphSync} />}
            startedAt={graphSync?.started_at}
            finishedAt={graphSync?.finished_at}
            onCancel={() => ctx?.cancelGraphSync?.()}
            onDismiss={() => ctx?.dismissGraphSyncJob?.()}
            isLive
          />
        ),
      });
    }
    for (const r of history) {
      if (r.kind !== 'graph_sync') continue;
      if (graphSync && isVisible(graphSync.status) && r.finished_at == null) continue;
      if (!isRecentTerminal(r.status, r.finished_at, showAllHistory)) continue;
      rows.push({
        key: `graph-hist-${r.id}`,
        type: 'Graph sync',
        status: r.status,
        sortAt: tsValue(r.started_at),
        node: (
          <JobRow
            key={`graph-hist-${r.id}`}
            type="Graph sync"
            status={r.status}
            scope="Org-wide"
            counters={<GraphCounters data={{ ...r.counters }} />}
            startedAt={r.started_at}
            finishedAt={r.finished_at}
          />
        ),
      });
    }

    // Decision approve / bulk-approve batches + single Workable write-back
    // ops. Both run server-side only (no live in-memory source), so they
    // render purely from history. Scope is org-wide.
    for (const r of history) {
      if (r.kind !== 'decision_batch') continue;
      if (!isRecentTerminal(r.status, r.finished_at, showAllHistory)) continue;
      rows.push({
        key: `decision-hist-${r.id}`,
        type: 'Decision approvals',
        status: r.status,
        sortAt: tsValue(r.started_at),
        node: (
          <JobRow
            key={`decision-hist-${r.id}`}
            type="Decision approvals"
            status={r.status}
            scope="Org-wide"
            counters={<DecisionBatchCounters data={{ ...r.counters }} />}
            startedAt={r.started_at}
            finishedAt={r.finished_at}
          />
        ),
      });
    }
    for (const r of history) {
      if (r.kind !== 'workable_op') continue;
      if (!isRecentTerminal(r.status, r.finished_at, showAllHistory)) continue;
      rows.push({
        key: `workable-op-hist-${r.id}`,
        type: 'Workable update',
        status: r.status,
        sortAt: tsValue(r.started_at),
        node: (
          <JobRow
            key={`workable-op-hist-${r.id}`}
            type="Workable update"
            status={r.status}
            scope="Org-wide"
            counters={<WorkableOpCounters data={{ ...r.counters }} />}
            startedAt={r.started_at}
            finishedAt={r.finished_at}
          />
        ),
      });
    }

    rows.sort((a, b) => b.sortAt - a.sortAt);
    return rows;
  }, [
    liveProcess, liveJobs, liveFetch, graphSync, workableSync,
    history, workableHistory, liveScoreRoleIds, liveFetchRoleIds,
    showAllHistory, ctx, tick,
  ]);

  const hasActive = useMemo(() => {
    const liveActive = (m) => Object.values(m).some((d) => ACTIVE_STATUSES.has(String(d?.status ?? '').toLowerCase()));
    if (liveActive(liveJobs)) return true;
    if (liveActive(liveFetch)) return true;
    if (liveActive(liveProcess)) return true;
    if (graphSync && ACTIVE_STATUSES.has(String(graphSync.status ?? '').toLowerCase())) return true;
    if (workableSync && (workableSync.sync_in_progress || ACTIVE_STATUSES.has(String(workableSync.status ?? '').toLowerCase()))) return true;
    return false;
  }, [liveJobs, liveFetch, liveProcess, graphSync, workableSync]);

  // tick is read so React re-renders the "last updated" tooltip text every 5s.
  void tick;
  const lastUpdatedTitle = lastUpdatedAt
    ? `Last updated ${formatRelativeDateTime(lastUpdatedAt.toISOString())}`
    : 'Awaiting first refresh…';

  // HANDOFF settings.md follow-up — one flat list, not five sub-tables.
  // Each row carries its own "Type" column so the recruiter can still
  // tell scoring from sync at a glance, sorted newest-first by start time.
  const headers = ['Status', 'Type', 'Scope', 'Counters', 'Started', 'Finished', 'Actions'];

  return (
    <div className="bg-jobs-panel">
      <div className="agz-toggle" role="tablist" aria-label="Background jobs view">
        <button
          type="button"
          role="tab"
          aria-selected={view === 'agents'}
          className={view === 'agents' ? 'on' : ''}
          onClick={() => setView('agents')}
        >
          Agents
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'jobs'}
          className={view === 'jobs' ? 'on' : ''}
          onClick={() => setView('jobs')}
        >
          Job runs
          {allRows.length > 0 ? <span className="agz-toggle-count">{allRows.length}</span> : null}
        </button>
      </div>

      {view === 'agents' ? (
        <AgentsOverviewPanel />
      ) : (
        <>
          <div className="bg-jobs-panel-header" title={lastUpdatedTitle}>
            <Loader2
              size={12}
              className={hasActive ? 'animate-spin' : ''}
              style={{ opacity: hasActive ? 1 : 0.45 }}
            />
            <span className="bg-jobs-panel-header-text">
              Auto-refreshing every 5s · finished runs auto-clear after 30 min
            </span>
            <button
              type="button"
              className="bg-jobs-panel-btn"
              onClick={() => setShowAllHistory((v) => !v)}
              style={{ marginLeft: 'auto' }}
            >
              {showAllHistory ? 'Hide older runs' : 'Show all history'}
            </button>
          </div>

          <div className="bg-jobs-panel-table">
            <div className="bg-jobs-panel-row bg-jobs-panel-head">
              {headers.map((h) => (
                <div key={h} className="bg-jobs-panel-cell bg-jobs-panel-head-cell">{h}</div>
              ))}
            </div>
            {allRows.length === 0 ? (
              <div className="bg-jobs-panel-empty">No background jobs running.</div>
            ) : allRows.map((row) => row.node)}
          </div>
        </>
      )}
    </div>
  );
}
