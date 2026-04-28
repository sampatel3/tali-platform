import React, { useEffect, useState } from 'react';
import { Loader2, CheckCircle2, X } from 'lucide-react';

import * as apiClient from '../../shared/api';

/**
 * BackgroundJobsToaster
 *
 * Persistent floating panel in the bottom-right that tracks long-running
 * fetch / re-score jobs across pages. Auto-polls every 4s while a job
 * is active. Stays visible until the recruiter dismisses it (or until
 * the role context changes).
 *
 * Usage: render once at app level, pass the active roleId. The toaster
 * renders nothing when no role is active or when no job is running.
 *
 * Why not a regular toast: regular toasts auto-dismiss after 5s. Bulk
 * scoring runs for 10-90+ minutes; the recruiter wanders off and comes
 * back. They need a persistent surface.
 */
export const BackgroundJobsToaster = ({ roleId }) => {
  const rolesApi = 'roles' in apiClient ? apiClient.roles : null;
  const [batchProgress, setBatchProgress] = useState(null);
  const [fetchProgress, setFetchProgress] = useState(null);
  const [dismissedKey, setDismissedKey] = useState(null);

  useEffect(() => {
    if (!roleId || !rolesApi?.batchScoreStatus || !rolesApi?.fetchCvsStatus) {
      setBatchProgress(null);
      setFetchProgress(null);
      return undefined;
    }

    let cancelled = false;
    let timer = null;

    const poll = async () => {
      try {
        const [batchRes, fetchRes] = await Promise.all([
          rolesApi.batchScoreStatus(roleId),
          rolesApi.fetchCvsStatus(roleId),
        ]);
        if (cancelled) return;
        setBatchProgress(batchRes?.data || null);
        setFetchProgress(fetchRes?.data || null);
      } catch (err) {
        // Silent — the toaster is non-critical UI; don't spam the user.
      }
      if (!cancelled) {
        timer = setTimeout(poll, 4000);
      }
    };

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [roleId, rolesApi]);

  const batchStatus = String(batchProgress?.status || '').toLowerCase();
  const fetchStatus = String(fetchProgress?.status || '').toLowerCase();
  const batchActive = batchStatus === 'running';
  const fetchActive = fetchStatus === 'running';
  const batchTotal = Number(batchProgress?.total || 0);
  const batchScored = Number(batchProgress?.scored || 0);
  const batchErrors = Number(batchProgress?.errors || 0);
  const batchPreScreenedOut = Number(batchProgress?.pre_screened_out || 0);
  const batchPreScreenEnabled = Boolean(batchProgress?.pre_screen_enabled);
  const fetchTotal = Number(fetchProgress?.total || 0);
  const fetchDone = Number(fetchProgress?.fetched || 0);

  // States to keep the row visible:
  //  - running:          show live progress
  //  - cancelling:       worker hasn't acked the cancel yet — keep visible
  //                      so the user sees "Cancelling…" feedback
  //  - cancelled:        terminal state, surface so they know it stopped
  //  - completed:        all done, visible until dismissed
  const showBatch = (
    batchActive
    || batchStatus === 'cancelling'
    || batchStatus === 'cancelled'
    || (batchTotal > 0 && (batchScored + batchErrors) >= batchTotal && batchStatus !== 'idle')
  );
  const showFetch = (
    fetchActive
    || fetchStatus === 'cancelling'
    || fetchStatus === 'cancelled'
    || (fetchTotal > 0 && fetchDone >= fetchTotal && fetchStatus !== 'idle')
  );

  // Dismiss key — refreshes when the job restarts so a new run re-shows
  // the toaster even if the previous one was dismissed.
  const currentKey = (() => {
    const parts = [];
    if (showBatch) parts.push(`batch:${batchProgress?.status}:${batchTotal}:${batchScored}:${batchErrors}`);
    if (showFetch) parts.push(`fetch:${fetchProgress?.status}:${fetchTotal}:${fetchDone}`);
    return parts.join('|');
  })();
  // If user dismisses while running, keep dismissed only until terminal
  // state changes. After completion, dismiss is permanent (until next run).
  const isDismissed = dismissedKey === currentKey;

  if (!showBatch && !showFetch) return null;
  if (isDismissed) return null;

  const handleDismiss = () => setDismissedKey(currentKey);

  const handleCancel = async (job) => {
    if (!rolesApi) return;
    try {
      if (job === 'batch' && rolesApi.cancelBatchScore) {
        await rolesApi.cancelBatchScore(roleId);
        // Optimistic UI flip — next poll will confirm.
        setBatchProgress((prev) => prev ? { ...prev, status: 'cancelling' } : prev);
      } else if (job === 'fetch' && rolesApi.cancelFetchCvs) {
        await rolesApi.cancelFetchCvs(roleId);
        setFetchProgress((prev) => prev ? { ...prev, status: 'cancelling' } : prev);
      }
    } catch (err) {
      // Silent — toaster is non-critical UI; user can click again.
    }
  };

  const items = [];
  if (showBatch) {
    const pct = batchTotal > 0 ? Math.round(((batchScored + batchErrors) / batchTotal) * 100) : 0;
    const cancelling = String(batchProgress?.status || '').toLowerCase() === 'cancelling';
    const cancelled = String(batchProgress?.status || '').toLowerCase() === 'cancelled';
    const processed = batchScored + batchErrors + batchPreScreenedOut;
    const remaining = Math.max(0, batchTotal - processed);
    const activeTitle = batchPreScreenEnabled
      ? (processed === 0 ? 'Pre-screening CVs…' : 'Pre-screening + scoring CVs')
      : 'Re-scoring CVs';
    items.push({
      key: 'batch',
      title: cancelled
        ? 'Re-scoring cancelled'
        : cancelling
          ? 'Cancelling re-score…'
          : batchActive ? activeTitle : 'Re-scoring complete',
      complete: !batchActive,
      cancelling,
      cancelled,
      detail: batchTotal > 0
        ? (() => {
            const parts = [];
            if (processed === 0 && batchPreScreenEnabled) {
              parts.push(`Pre-screening ${batchTotal} candidates…`);
            } else {
              parts.push(`${processed}/${batchTotal} processed`);
              if (batchPreScreenedOut) parts.push(`${batchPreScreenedOut} filtered by pre-screen`);
              if (batchScored) parts.push(`${batchScored} fully scored`);
              if (batchErrors) parts.push(`${batchErrors} error(s)`);
              if (remaining && batchActive) parts.push(`${remaining} remaining`);
            }
            return parts.join(' · ');
          })()
        : 'starting…',
      pct,
    });
  }
  if (showFetch) {
    const pct = fetchTotal > 0 ? Math.round((fetchDone / fetchTotal) * 100) : 0;
    const cancelling = String(fetchProgress?.status || '').toLowerCase() === 'cancelling';
    const cancelled = String(fetchProgress?.status || '').toLowerCase() === 'cancelled';
    items.push({
      key: 'fetch',
      title: cancelled
        ? 'Fetch cancelled'
        : cancelling
          ? 'Cancelling fetch…'
          : fetchActive ? 'Fetching CVs from Workable' : 'CV fetch complete',
      complete: !fetchActive,
      cancelling,
      cancelled,
      detail: fetchTotal > 0 ? `${fetchDone}/${fetchTotal} fetched` : 'starting…',
      pct,
    });
  }

  return (
    <div className="bg-jobs-toaster">
      <button type="button" className="bg-jobs-dismiss" onClick={handleDismiss} aria-label="Dismiss">
        <X size={14} />
      </button>
      {items.map((item) => (
        <div key={item.key} className="bg-jobs-row">
          <div className="bg-jobs-icon">
            {item.complete ? <CheckCircle2 size={18} /> : <Loader2 size={18} className="animate-spin" />}
          </div>
          <div className="bg-jobs-body">
            <div className="bg-jobs-title">{item.title}</div>
            <div className="bg-jobs-detail">{item.detail}</div>
            <div className="bg-jobs-bar" aria-hidden="true">
              <div className="bg-jobs-bar-fill" style={{ width: `${Math.max(0, Math.min(100, item.pct))}%` }} />
            </div>
            {!item.complete && !item.cancelled ? (
              <button
                type="button"
                className="bg-jobs-cancel"
                onClick={() => handleCancel(item.key)}
                disabled={item.cancelling}
                aria-label={`Cancel ${item.key === 'batch' ? 're-scoring' : 'CV fetch'}`}
              >
                {item.cancelling ? 'Cancelling…' : 'Cancel'}
              </button>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
};

export default BackgroundJobsToaster;
