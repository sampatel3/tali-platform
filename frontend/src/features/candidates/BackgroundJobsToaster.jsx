import React from 'react';
import { CheckCircle2, Loader2, X } from 'lucide-react';

import { useJobStatus } from '../../contexts/JobStatusContext';

/**
 * BackgroundJobsToaster
 *
 * Global persistent floating panel (bottom-right) that tracks the four kinds
 * of background jobs the platform runs:
 *   1. Batch scoring         (per role)  — /batch-score/status
 *   2. CV fetching           (per role)  — /fetch-cvs/status
 *   3. Pre-screen processing (per role)  — /batch-pre-screen/status
 *   4. Knowledge-graph sync  (per org)   — /candidates/sync-graph/status
 *
 * State lives in JobStatusContext (App-level), so this component survives
 * navigation and page transitions. Render it once in AppShell — do NOT
 * render it inside page components.
 */
export const BackgroundJobsToaster = () => {
  const ctx = useJobStatus();
  if (!ctx) return null;

  const {
    jobs,
    fetchJobs,
    preScreenJobs,
    graphSyncJob,
    dismissJob,
    dismissFetchJob,
    dismissPreScreenJob,
    dismissGraphSyncJob,
    cancelBatch,
    cancelFetchCvs,
  } = ctx;

  const visible = (status) => {
    const s = String(status ?? '').toLowerCase();
    return s === 'running' || s === 'cancelling' || s === 'cancelled' || s === 'completed' || s === 'failed';
  };

  const scoreEntries = Object.entries(jobs)
    .map(([roleId, data]) => ({ kind: 'score', roleId: Number(roleId), data }))
    .filter(({ data }) => visible(data?.status));

  const fetchEntries = Object.entries(fetchJobs)
    .map(([roleId, data]) => ({ kind: 'fetch', roleId: Number(roleId), data }))
    .filter(({ data }) => visible(data?.status));

  const preScreenEntries = Object.entries(preScreenJobs)
    .map(([roleId, data]) => ({ kind: 'pre_screen', roleId: Number(roleId), data }))
    .filter(({ data }) => visible(data?.status));

  const graphEntries = visible(graphSyncJob?.status)
    ? [{ kind: 'graph', roleId: 0, data: graphSyncJob }]
    : [];

  const entries = [...scoreEntries, ...fetchEntries, ...preScreenEntries, ...graphEntries];
  if (entries.length === 0) return null;

  return (
    <div className="bg-jobs-toaster">
      {entries.map((entry) => (
        <JobRow
          key={`${entry.kind}-${entry.roleId}`}
          entry={entry}
          onCancel={(() => {
            if (entry.kind === 'score') return () => cancelBatch(entry.roleId);
            if (entry.kind === 'fetch') return () => cancelFetchCvs(entry.roleId);
            return null;  // pre-screen / graph sync don't expose cancel yet
          })()}
          onDismiss={(() => {
            if (entry.kind === 'score') return () => dismissJob(entry.roleId);
            if (entry.kind === 'fetch') return () => dismissFetchJob(entry.roleId);
            if (entry.kind === 'pre_screen') return () => dismissPreScreenJob(entry.roleId);
            return () => dismissGraphSyncJob();
          })()}
        />
      ))}
    </div>
  );
};

function JobRow({ entry, onCancel, onDismiss }) {
  const { kind, roleId, data } = entry;
  const status = String(data?.status ?? '').toLowerCase();
  const isRunning = status === 'running';
  const isCancelling = status === 'cancelling';
  const isCancelled = status === 'cancelled';
  const isComplete = status === 'completed';
  const isFailed = status === 'failed';
  const isTerminal = isCancelled || isComplete || isFailed;

  const total = Number(data?.total ?? 0);
  const errors = Number(data?.errors ?? 0);
  const roleName = String(data?.role_name ?? '') || (kind === 'graph' ? 'Knowledge graph' : `Role #${roleId}`);

  // Each kind reports progress under a different field name.
  const processed = (() => {
    if (kind === 'score') {
      const scored = Number(data?.scored ?? 0);
      const preScreenedOut = Number(data?.pre_screened_out ?? 0);
      return scored + errors + preScreenedOut;
    }
    if (kind === 'fetch') return Number(data?.fetched ?? 0);
    if (kind === 'pre_screen') return Number(data?.processed ?? 0);
    if (kind === 'graph') return Number(data?.synced ?? 0);
    return 0;
  })();

  const remaining = Math.max(0, total - processed);
  const pct = total > 0 ? Math.round((processed / total) * 100) : 0;

  const title = (() => {
    const verb = (() => {
      if (kind === 'fetch') return 'Fetching CVs';
      if (kind === 'pre_screen') return data?.refresh ? 'Refreshing pre-screen' : 'Pre-screening';
      if (kind === 'graph') return 'Syncing to graph';
      // score
      const preScreenEnabled = Boolean(data?.pre_screen_enabled);
      return preScreenEnabled && processed === 0 ? 'Pre-screening CVs' : 'Scoring CVs';
    })();
    if (isCancelled) return `${roleName}: ${verb} cancelled`;
    if (isCancelling) return `${roleName}: cancelling…`;
    if (isComplete) return `${roleName}: ${verb} complete`;
    if (isFailed) return `${roleName}: ${verb} failed`;
    return `${roleName}: ${verb}`;
  })();

  const detail = (() => {
    if (total === 0) return 'starting…';
    const parts = [`${processed}/${total} processed`];
    if (kind === 'score') {
      const scored = Number(data?.scored ?? 0);
      const preScreenedOut = Number(data?.pre_screened_out ?? 0);
      if (preScreenedOut) parts.push(`${preScreenedOut} filtered`);
      if (scored) parts.push(`${scored} scored`);
    }
    if (errors) parts.push(`${errors} error${errors !== 1 ? 's' : ''}`);
    if (remaining && isRunning) parts.push(`${remaining} remaining`);
    return parts.join(' · ');
  })();

  return (
    <div className="bg-jobs-row">
      <div className="bg-jobs-icon">
        {isTerminal
          ? <CheckCircle2 size={18} />
          : <Loader2 size={18} className="animate-spin" />}
      </div>
      <div className="bg-jobs-body">
        <div className="bg-jobs-title">{title}</div>
        <div className="bg-jobs-detail">{detail}</div>
        <div className="bg-jobs-bar" aria-hidden="true">
          <div
            className="bg-jobs-bar-fill"
            style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
          />
        </div>
        <div className="bg-jobs-actions">
          {!isTerminal && onCancel && (
            <button
              type="button"
              className="bg-jobs-cancel"
              onClick={onCancel}
              disabled={isCancelling}
              aria-label={`Cancel ${title}`}
            >
              {isCancelling ? 'Cancelling…' : 'Cancel'}
            </button>
          )}
          {isTerminal && (
            <button
              type="button"
              className="bg-jobs-dismiss-row"
              onClick={onDismiss}
              aria-label="Dismiss"
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default BackgroundJobsToaster;
