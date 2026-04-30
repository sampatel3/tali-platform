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
    processJobs,
    graphSyncJob,
    dismissJob,
    dismissFetchJob,
    dismissPreScreenJob,
    dismissProcessJob,
    dismissGraphSyncJob,
    cancelBatch,
    cancelFetchCvs,
    cancelProcessJob,
  } = ctx;

  const visible = (status) => {
    const s = String(status ?? '').toLowerCase();
    return s === 'running' || s === 'cancelling' || s === 'cancelled' || s === 'completed' || s === 'failed';
  };

  // Process jobs (cascade) — preferred. Show one row per role.
  const processEntries = Object.entries(processJobs ?? {})
    .map(([roleId, data]) => ({ kind: 'process', roleId: Number(roleId), data }))
    .filter(({ data }) => visible(data?.status));

  // Hide legacy single-action rows for any role that already has a process
  // row visible. This avoids stacking 2-3 rows per role during the transition.
  const processRoleSet = new Set(processEntries.map((e) => e.roleId));

  const scoreEntries = Object.entries(jobs)
    .map(([roleId, data]) => ({ kind: 'score', roleId: Number(roleId), data }))
    .filter(({ data, roleId }) => visible(data?.status) && !processRoleSet.has(roleId));

  const fetchEntries = Object.entries(fetchJobs)
    .map(([roleId, data]) => ({ kind: 'fetch', roleId: Number(roleId), data }))
    .filter(({ data, roleId }) => visible(data?.status) && !processRoleSet.has(roleId));

  const preScreenEntries = Object.entries(preScreenJobs)
    .map(([roleId, data]) => ({ kind: 'pre_screen', roleId: Number(roleId), data }))
    .filter(({ data, roleId }) => visible(data?.status) && !processRoleSet.has(roleId));

  const graphEntries = visible(graphSyncJob?.status)
    ? [{ kind: 'graph', roleId: 0, data: graphSyncJob }]
    : [];

  const entries = [...processEntries, ...scoreEntries, ...fetchEntries, ...preScreenEntries, ...graphEntries];
  if (entries.length === 0) return null;

  return (
    <div className="bg-jobs-toaster">
      {entries.map((entry) => (
        <JobRow
          key={`${entry.kind}-${entry.roleId}`}
          entry={entry}
          onCancel={(() => {
            if (entry.kind === 'process') return () => cancelProcessJob(entry.roleId);
            if (entry.kind === 'score') return () => cancelBatch(entry.roleId);
            if (entry.kind === 'fetch') return () => cancelFetchCvs(entry.roleId);
            return null;  // pre-screen / graph sync don't expose cancel yet
          })()}
          onDismiss={(() => {
            if (entry.kind === 'process') return () => dismissProcessJob(entry.roleId);
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

  const errors = Number(data?.errors ?? 0);
  const roleName = String(data?.role_name ?? '') || (kind === 'graph' ? 'Knowledge graph' : `Role #${roleId}`);

  // Process (cascade) jobs report a multi-step progress structure.
  // We compute total/processed differently for them and surface per-step
  // detail in the subtitle.
  let total = Number(data?.total ?? 0);
  let processed = 0;

  if (kind === 'process') {
    const fetchTotal = Number(data?.fetch?.total ?? 0);
    const fetchAttempted = Number(data?.fetch?.attempted ?? 0);
    const preTotal = Number(data?.pre_screen?.total ?? 0);
    const prePros = Number(data?.pre_screen?.processed ?? 0);
    const scoreTotal = Number(data?.score?.total ?? 0);
    const scorePros = Number(data?.score?.scored ?? 0);
    const graphTotal = Number(data?.graph_sync?.total ?? 0);
    const graphPros = Number(data?.graph_sync?.synced ?? 0);
    total = fetchTotal + preTotal + scoreTotal + graphTotal;
    processed = fetchAttempted + prePros + scorePros + graphPros;
  } else if (kind === 'score') {
    const scored = Number(data?.scored ?? 0);
    const preScreenedOut = Number(data?.pre_screened_out ?? 0);
    processed = scored + errors + preScreenedOut;
  } else if (kind === 'fetch') {
    processed = Number(data?.fetched ?? 0);
  } else if (kind === 'pre_screen') {
    processed = Number(data?.processed ?? 0);
  } else if (kind === 'graph') {
    processed = Number(data?.synced ?? 0);
  }

  const remaining = Math.max(0, total - processed);
  const pct = total > 0 ? Math.round((processed / total) * 100) : 0;

  const title = (() => {
    const verb = (() => {
      if (kind === 'process') {
        const step = String(data?.current_step ?? '').toLowerCase();
        if (step === 'fetch') return 'Fetching CVs';
        if (step === 'pre_screen') return 'Pre-screening';
        if (step === 'score') return 'Scoring';
        if (step === 'graph_sync') return 'Syncing to knowledge graph';
        return 'Processing';
      }
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
    if (kind === 'process') {
      // Render per-step counts so the user sees fetch, pre-screen, and
      // score progress at once. Each step shows "M/N" (processed/total).
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

      const parts = [];
      if (fetchTotal > 0) {
        let f = `Fetch ${fetchAttempted}/${fetchTotal}`;
        if (fetchFetched && fetchFetched < fetchAttempted) f += ` (${fetchFetched} got CV`;
        else if (fetchFetched) f += ` (${fetchFetched} got CV`;
        if (fetchUnavailable) f += `, ${fetchUnavailable} unavailable`;
        if (fetchErrors) f += `, ${fetchErrors} err`;
        if (fetchFetched || fetchUnavailable || fetchErrors) f += ')';
        parts.push(f);
      }
      if (preTotal > 0) {
        let p = `Pre-screen ${prePros}/${preTotal}`;
        if (preErrors) p += ` (${preErrors} err)`;
        parts.push(p);
      }
      if (scoreTotal > 0) {
        let s = `Score ${scorePros}/${scoreTotal}`;
        const annot = [];
        if (scoreFiltered) annot.push(`${scoreFiltered} filtered`);
        if (scoreErrors) annot.push(`${scoreErrors} err`);
        if (annot.length) s += ` (${annot.join(', ')})`;
        parts.push(s);
      }
      const graphTotal = Number(data?.graph_sync?.total ?? 0);
      const graphPros = Number(data?.graph_sync?.synced ?? 0);
      const graphErrors = Number(data?.graph_sync?.errors ?? 0);
      if (graphTotal > 0) {
        let g = `Graph ${graphPros}/${graphTotal}`;
        if (graphErrors) g += ` (${graphErrors} err)`;
        parts.push(g);
      }
      if (parts.length === 0) return 'starting…';
      return parts.join(' · ');
    }

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
