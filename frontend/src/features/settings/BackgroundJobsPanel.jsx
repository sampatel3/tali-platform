import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, X } from 'lucide-react';

import { useJobStatus } from '../../contexts/JobStatusContext';
import { roles as rolesApi } from '../../shared/api';
import { formatRelativeDateTime } from '../../shared/ui/RecruiterDesignPrimitives';

const HISTORY_POLL_MS = 5000;

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
  nothing_to_score: 'noop',
  nothing_to_sync: 'noop',
  already_running: 'warning',
};

const TERMINAL_STATUSES = new Set([
  'completed', 'success', 'cancelled', 'failed',
  'nothing_to_score', 'nothing_to_sync', 'already_running',
]);

const ACTIVE_STATUSES = new Set(['running', 'started', 'queued', 'cancelling']);

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
      <code className="bg-jobs-panel-status-label">{s || 'idle'}</code>
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
  status, scope, counters, startedAt, finishedAt,
  onCancel, onDismiss, isLive,
}) {
  const s = String(status ?? '').toLowerCase();
  const isTerminal = TERMINAL_STATUSES.has(s);
  const isActive = ACTIVE_STATUSES.has(s);
  return (
    <div className="bg-jobs-panel-row">
      <div className="bg-jobs-panel-cell"><StatusDot status={status} /></div>
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

function SubTable({ title, headers, rows, emptyText }) {
  return (
    <div className="bg-jobs-panel-subtable">
      <h3 className="bg-jobs-panel-subtitle">{title}</h3>
      <div className="bg-jobs-panel-table">
        <div className="bg-jobs-panel-row bg-jobs-panel-head">
          {headers.map((h) => (
            <div key={h} className="bg-jobs-panel-cell bg-jobs-panel-head-cell">{h}</div>
          ))}
        </div>
        {rows.length === 0 ? (
          <div className="bg-jobs-panel-empty">{emptyText}</div>
        ) : rows}
      </div>
    </div>
  );
}

export default function BackgroundJobsPanel() {
  const ctx = useJobStatus();
  const [history, setHistory] = useState([]);
  const [workableHistory, setWorkableHistory] = useState([]);
  const [lastUpdatedAt, setLastUpdatedAt] = useState(null);
  const [tick, setTick] = useState(0);

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

  const processRows = useMemo(() => {
    const out = [];
    for (const [roleIdRaw, data] of Object.entries(liveProcess)) {
      const roleId = Number(roleIdRaw);
      if (!isVisible(data?.status)) continue;
      const scope = data?.role_name ? `Role: ${data.role_name}` : `Role #${roleId}`;
      out.push(
        <JobRow
          key={`process-live-${roleId}`}
          status={data.status}
          scope={scope}
          counters={<ProcessCounters data={data} />}
          startedAt={data?.started_at}
          finishedAt={data?.finished_at}
          onCancel={() => ctx?.cancelProcessJob?.(roleId)}
          onDismiss={() => ctx?.dismissProcessJob?.(roleId)}
          isLive
        />
      );
    }
    return out;
  }, [liveProcess, ctx]);

  const scoreRows = useMemo(() => {
    // Render live rows first (active + terminal-but-still-tracked), then
    // historic rows from the listing endpoint that we don't already have live.
    const out = [];
    for (const [roleIdRaw, data] of Object.entries(liveJobs)) {
      const roleId = Number(roleIdRaw);
      if (!isVisible(data?.status)) continue;
      const scope = data?.role_name
        ? `Role: ${data.role_name}`
        : `Role #${roleId}`;
      out.push(
        <JobRow
          key={`score-live-${roleId}`}
          status={data.status}
          scope={scope}
          counters={<ScoreCounters data={data} />}
          startedAt={data?.started_at}
          finishedAt={data?.finished_at}
          onCancel={() => ctx?.cancelBatch?.(roleId)}
          onDismiss={() => ctx?.dismissJob?.(roleId)}
          isLive
        />
      );
    }
    for (const r of history) {
      if (r.kind !== 'scoring_batch') continue;
      if (liveScoreRoleIds.has(Number(r.scope_id))) continue;
      const scope = r.role_name ? `Role: ${r.role_name}` : `Role #${r.scope_id}`;
      out.push(
        <JobRow
          key={`score-hist-${r.id}`}
          status={r.status}
          scope={scope}
          counters={<ScoreCounters data={{ ...r.counters }} />}
          startedAt={r.started_at}
          finishedAt={r.finished_at}
        />
      );
    }
    return out;
  }, [liveJobs, history, liveScoreRoleIds, ctx]);

  const fetchRows = useMemo(() => {
    const out = [];
    for (const [roleIdRaw, data] of Object.entries(liveFetch)) {
      const roleId = Number(roleIdRaw);
      if (!isVisible(data?.status)) continue;
      const scope = `Role #${roleId}`;
      out.push(
        <JobRow
          key={`fetch-live-${roleId}`}
          status={data.status}
          scope={scope}
          counters={<FetchCounters data={data} />}
          startedAt={data?.started_at}
          finishedAt={data?.finished_at}
          onCancel={() => ctx?.cancelFetchCvs?.(roleId)}
          onDismiss={() => ctx?.dismissFetchJob?.(roleId)}
          isLive
        />
      );
    }
    for (const r of history) {
      if (r.kind !== 'cv_fetch') continue;
      if (liveFetchRoleIds.has(Number(r.scope_id))) continue;
      const scope = r.role_name ? `Role: ${r.role_name}` : `Role #${r.scope_id}`;
      out.push(
        <JobRow
          key={`fetch-hist-${r.id}`}
          status={r.status}
          scope={scope}
          counters={<FetchCounters data={{ ...r.counters }} />}
          startedAt={r.started_at}
          finishedAt={r.finished_at}
        />
      );
    }
    return out;
  }, [liveFetch, history, liveFetchRoleIds, ctx]);

  const graphRows = useMemo(() => {
    const out = [];
    if (graphSync && isVisible(graphSync.status)) {
      out.push(
        <JobRow
          key="graph-live"
          status={graphSync.status}
          scope="Org-wide"
          counters={<GraphCounters data={graphSync} />}
          startedAt={graphSync?.started_at}
          finishedAt={graphSync?.finished_at}
          onCancel={() => ctx?.cancelGraphSync?.()}
          onDismiss={() => ctx?.dismissGraphSyncJob?.()}
          isLive
        />
      );
    }
    for (const r of history) {
      if (r.kind !== 'graph_sync') continue;
      // Don't double-render the row that's the same as the live graphSync.
      if (graphSync && isVisible(graphSync.status) && r.finished_at == null) continue;
      out.push(
        <JobRow
          key={`graph-hist-${r.id}`}
          status={r.status}
          scope="Org-wide"
          counters={<GraphCounters data={{ ...r.counters }} />}
          startedAt={r.started_at}
          finishedAt={r.finished_at}
        />
      );
    }
    return out;
  }, [graphSync, history, ctx]);

  const workableRows = useMemo(() => {
    const out = [];
    if (workableSync && (workableSync.sync_in_progress || workableSync.status === 'running')) {
      out.push(
        <JobRow
          key="workable-live"
          status={workableSync.status || 'running'}
          scope={`Org-wide · mode: ${workableSync.mode || 'metadata'}`}
          counters={<WorkableCounters data={workableSync} />}
          startedAt={workableSync.started_at}
          finishedAt={workableSync.finished_at}
          onCancel={() => ctx?.cancelWorkableSync?.(workableSync.run_id ?? null)}
          onDismiss={() => ctx?.dismissWorkableSyncJob?.()}
          isLive
        />
      );
    }
    for (const r of workableHistory) {
      // Skip the live row to avoid double-render.
      if (workableSync?.run_id && r.id === workableSync.run_id && (workableSync.sync_in_progress || workableSync.status === 'running')) continue;
      out.push(
        <JobRow
          key={`workable-hist-${r.id}`}
          status={r.status}
          scope={`Org-wide · mode: ${r.mode || 'metadata'}`}
          counters={<WorkableCounters data={r} />}
          startedAt={r.started_at}
          finishedAt={r.finished_at}
        />
      );
    }
    return out;
  }, [workableSync, workableHistory, ctx]);

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

  const headers = ['Status', 'Scope', 'Counters', 'Started', 'Finished', 'Actions'];

  return (
    <div className="bg-jobs-panel">
      <div className="bg-jobs-panel-header" title={lastUpdatedTitle}>
        <Loader2
          size={12}
          className={hasActive ? 'animate-spin' : ''}
          style={{ opacity: hasActive ? 1 : 0.45 }}
        />
        <span className="bg-jobs-panel-header-text">Auto-refreshing every 5s</span>
      </div>

      <SubTable
        title="Process candidates"
        headers={headers}
        rows={processRows}
        emptyText="No active runs."
      />
      <SubTable
        title="Scoring batch"
        headers={headers}
        rows={scoreRows}
        emptyText="No recent runs."
      />
      <SubTable
        title="CV fetch"
        headers={headers}
        rows={fetchRows}
        emptyText="No recent runs."
      />
      <SubTable
        title="Workable sync"
        headers={headers}
        rows={workableRows}
        emptyText="No recent runs."
      />
      <SubTable
        title="Graph sync"
        headers={headers}
        rows={graphRows}
        emptyText="No recent runs."
      />
    </div>
  );
}
