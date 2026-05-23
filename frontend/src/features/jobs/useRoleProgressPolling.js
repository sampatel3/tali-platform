import { useEffect } from 'react';

export const EMPTY_FETCH_PROGRESS = { status: 'idle', total: 0, fetched: 0, errors: 0 };
export const EMPTY_PRE_SCREEN_PROGRESS = { status: 'idle', total: 0, processed: 0, errors: 0, refresh: false };

const isRunning = (progress) => String(progress?.status || '').toLowerCase() === 'running';

// Polls fetch-CVs + pre-screen progress while either is running. Batch-score
// progress lives in the global BackgroundJobsToaster context, so it's not
// polled here. When a job transitions running→done it reloads the workspace
// so candidate scores refresh. Polling pauses while the tab is backgrounded.
export function useRoleProgressPolling({
  numericRoleId,
  rolesApi,
  fetchCvsProgress,
  preScreenProgress,
  setFetchCvsProgress,
  setPreScreenProgress,
  loadRoleWorkspace,
  bumpRefreshTick,
}) {
  useEffect(() => {
    if (!Number.isFinite(numericRoleId)) return undefined;
    if (!isRunning(fetchCvsProgress) && !isRunning(preScreenProgress)) return undefined;

    let cancelled = false;
    const poll = async () => {
      try {
        const [fetchStatusRes, preScreenStatusRes] = await Promise.all([
          rolesApi.fetchCvsStatus(numericRoleId),
          rolesApi.batchPreScreenStatus(numericRoleId).catch(() => ({ data: EMPTY_PRE_SCREEN_PROGRESS })),
        ]);
        if (cancelled) return;
        const nextFetch = fetchStatusRes?.data || EMPTY_FETCH_PROGRESS;
        const nextPre = preScreenStatusRes?.data || EMPTY_PRE_SCREEN_PROGRESS;
        const fetchFinished = isRunning(fetchCvsProgress) && !isRunning(nextFetch);
        const preFinished = isRunning(preScreenProgress) && !isRunning(nextPre);
        setFetchCvsProgress(nextFetch);
        setPreScreenProgress(nextPre);
        if (fetchFinished || preFinished) {
          await loadRoleWorkspace();
          bumpRefreshTick();
        }
      } catch {
        if (!cancelled) {
          setFetchCvsProgress((current) => ({ ...current, status: current.status || 'failed' }));
        }
      }
    };

    const intervalId = window.setInterval(() => {
      // Don't poll while the tab is backgrounded — it wastes requests and the
      // user can't see progress anyway. The next tick after focus resumes it.
      if (typeof document !== 'undefined' && document.hidden) return;
      void poll();
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [
    fetchCvsProgress,
    preScreenProgress,
    loadRoleWorkspace,
    numericRoleId,
    rolesApi,
    setFetchCvsProgress,
    setPreScreenProgress,
    bumpRefreshTick,
  ]);
}

export default useRoleProgressPolling;
