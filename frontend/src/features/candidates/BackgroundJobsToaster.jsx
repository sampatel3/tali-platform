import React from 'react';
import { CheckCircle2, Loader2, X } from 'lucide-react';

import { useJobStatus } from '../../contexts/JobStatusContext';

/**
 * BackgroundJobsToaster
 *
 * Global persistent floating panel (bottom-right) that tracks batch scoring
 * and CV-fetch jobs. Renders one row per active/recently-completed role.
 *
 * State lives in JobStatusContext (App-level), so this component survives
 * navigation and page transitions. Render it once in AppShell — do NOT
 * render it inside page components.
 *
 * Cancellation is a two-layer kill switch:
 *  1. Redis flag — stops the batch loop from dispatching new score tasks.
 *  2. DB marking — all PENDING cv_score_jobs immediately set to error so
 *     Celery workers skip the Claude call when they dequeue them.
 * The "cancelling" state reflects that in-flight Claude calls may still
 * complete (those tasks are already running), but no new API calls start.
 */
export const BackgroundJobsToaster = () => {
  const ctx = useJobStatus();
  if (!ctx) return null;

  const { jobs, dismissJob, cancelBatch } = ctx;

  const entries = Object.entries(jobs)
    .map(([roleId, data]) => ({ roleId: Number(roleId), data }))
    .filter(({ data }) => {
      const s = String(data?.status ?? '').toLowerCase();
      // Show running, cancelling, cancelled, completed — not idle/empty.
      return s === 'running' || s === 'cancelling' || s === 'cancelled' || s === 'completed';
    });

  if (entries.length === 0) return null;

  return (
    <div className="bg-jobs-toaster">
      {entries.map(({ roleId, data }) => (
        <JobRow
          key={roleId}
          roleId={roleId}
          data={data}
          onCancel={() => cancelBatch(roleId)}
          onDismiss={() => dismissJob(roleId)}
        />
      ))}
    </div>
  );
};

function JobRow({ roleId, data, onCancel, onDismiss }) {
  const status = String(data?.status ?? '').toLowerCase();
  const isRunning = status === 'running';
  const isCancelling = status === 'cancelling';
  const isCancelled = status === 'cancelled';
  const isComplete = status === 'completed';
  const isTerminal = isCancelled || isComplete;

  const total = Number(data?.total ?? 0);
  const scored = Number(data?.scored ?? 0);
  const errors = Number(data?.errors ?? 0);
  const preScreenedOut = Number(data?.pre_screened_out ?? 0);
  const preScreenEnabled = Boolean(data?.pre_screen_enabled);
  const queued = data?.queued ?? null;
  const roleName = String(data?.role_name ?? '') || `Role #${roleId}`;

  const processed = scored + errors + preScreenedOut;
  const remaining = Math.max(0, total - processed);
  const pct = total > 0 ? Math.round((processed / total) * 100) : 0;

  const title = (() => {
    if (isCancelled) return `${roleName}: scoring cancelled`;
    if (isCancelling) return `${roleName}: cancelling…`;
    if (isComplete) return `${roleName}: scoring complete`;
    return preScreenEnabled && processed === 0
      ? `${roleName}: pre-screening CVs…`
      : `${roleName}: scoring CVs`;
  })();

  const detail = (() => {
    if (total === 0) return 'starting…';
    const parts = [];
    if (processed === 0 && preScreenEnabled && isRunning) {
      parts.push(`Pre-screening ${total} candidates…`);
    } else {
      parts.push(`${processed}/${total} processed`);
      if (preScreenedOut) parts.push(`${preScreenedOut} filtered`);
      if (scored) parts.push(`${scored} scored`);
      if (errors) parts.push(`${errors} error${errors !== 1 ? 's' : ''}`);
      if (remaining && isRunning) parts.push(`${remaining} remaining`);
    }
    if (queued) parts.push('(next batch queued)');
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
          {!isTerminal && (
            <button
              type="button"
              className="bg-jobs-cancel"
              onClick={onCancel}
              disabled={isCancelling}
              aria-label={`Cancel scoring for ${roleName}`}
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
