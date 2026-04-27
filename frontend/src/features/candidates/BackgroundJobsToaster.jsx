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

  const batchActive = String(batchProgress?.status || '').toLowerCase() === 'running';
  const fetchActive = String(fetchProgress?.status || '').toLowerCase() === 'running';
  const batchTotal = Number(batchProgress?.total || 0);
  const batchScored = Number(batchProgress?.scored || 0);
  const batchErrors = Number(batchProgress?.errors || 0);
  const fetchTotal = Number(fetchProgress?.total || 0);
  const fetchDone = Number(fetchProgress?.fetched || 0);

  // The toaster has three states it should render:
  // 1. running: active job, show progress
  // 2. recently completed: not running, but scored < total still bears
  //    visibility for a moment (we surface "completed" until dismissed)
  // 3. nothing relevant — render null
  const showBatch = batchActive || (
    !batchActive && batchTotal > 0 && (batchScored + batchErrors) >= batchTotal && batchProgress?.status !== 'idle'
  );
  const showFetch = fetchActive || (
    !fetchActive && fetchTotal > 0 && fetchDone >= fetchTotal && fetchProgress?.status !== 'idle'
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

  const items = [];
  if (showBatch) {
    const pct = batchTotal > 0 ? Math.round(((batchScored + batchErrors) / batchTotal) * 100) : 0;
    items.push({
      key: 'batch',
      title: batchActive ? 'Re-scoring CVs' : 'Re-scoring complete',
      complete: !batchActive,
      detail: batchTotal > 0
        ? `${batchScored}/${batchTotal} scored${batchErrors ? ` · ${batchErrors} error(s)` : ''}`
        : 'starting…',
      pct,
    });
  }
  if (showFetch) {
    const pct = fetchTotal > 0 ? Math.round((fetchDone / fetchTotal) * 100) : 0;
    items.push({
      key: 'fetch',
      title: fetchActive ? 'Fetching CVs from Workable' : 'CV fetch complete',
      complete: !fetchActive,
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
          </div>
        </div>
      ))}
    </div>
  );
};

export default BackgroundJobsToaster;
