import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';

import * as apiClient from '../shared/api';

// How often to poll each tracked role's status.
const ROLE_POLL_MS = 4000;
// How often to re-discover newly-started batches (catches batches started
// from other pages/tabs, or after a page refresh where local state was lost).
const DISCOVERY_POLL_MS = 10_000;

const STORAGE_KEY = 'tali_tracked_batch_roles';

function loadPersistedRoleIds() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function persistRoleIds(ids) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...ids]));
  } catch {}
}

const JobStatusContext = createContext(null);

export function JobStatusProvider({ children }) {
  const rolesApi = apiClient.roles ?? null;

  // jobs: { [roleId: number]: statusObject from /batch-score/status }
  const [jobs, setJobs] = useState({});
  // tracked: Set<number> — role IDs we are actively polling
  const trackedRef = useRef(new Set(loadPersistedRoleIds()));
  // Force re-render when trackedRef changes (ref mutations are not reactive).
  const [trackedVersion, setTrackedVersion] = useState(0);

  const bumpVersion = useCallback(() => setTrackedVersion((v) => v + 1), []);

  const addTracked = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (!trackedRef.current.has(id)) {
        trackedRef.current = new Set([...trackedRef.current, id]);
        persistRoleIds(trackedRef.current);
        bumpVersion();
      }
    },
    [bumpVersion],
  );

  const removeTracked = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (trackedRef.current.has(id)) {
        const next = new Set(trackedRef.current);
        next.delete(id);
        trackedRef.current = next;
        persistRoleIds(next);
        bumpVersion();
      }
    },
    [bumpVersion],
  );

  // ── Per-role status polling ───────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const poll = async () => {
      if (cancelled) return;
      const ids = [...trackedRef.current];
      if (ids.length > 0) {
        const results = await Promise.allSettled(
          ids.map((roleId) =>
            rolesApi?.batchScoreStatus(roleId).then((r) => ({ roleId, data: r?.data })),
          ),
        );
        if (cancelled) return;
        setJobs((prev) => {
          const next = { ...prev };
          for (const r of results) {
            if (r.status === 'fulfilled' && r.value?.data) {
              next[r.value.roleId] = r.value.data;
            }
          }
          return next;
        });
      }
      if (!cancelled) timer = setTimeout(poll, ROLE_POLL_MS);
    };

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // trackedVersion re-runs this effect when the tracked set changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rolesApi, trackedVersion]);

  // ── Discovery polling — finds batches started from other pages/tabs ───────
  useEffect(() => {
    if (!rolesApi?.activeBatchScores) return undefined;
    let cancelled = false;
    let timer = null;

    const discover = async () => {
      if (cancelled) return;
      try {
        const res = await rolesApi.activeBatchScores();
        const active = res?.data?.active ?? [];
        let changed = false;
        for (const { role_id } of active) {
          if (!trackedRef.current.has(Number(role_id))) {
            trackedRef.current = new Set([...trackedRef.current, Number(role_id)]);
            changed = true;
          }
        }
        if (changed) {
          persistRoleIds(trackedRef.current);
          bumpVersion();
        }
      } catch {}
      if (!cancelled) timer = setTimeout(discover, DISCOVERY_POLL_MS);
    };

    discover();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [rolesApi, bumpVersion]);

  // ── Public API ────────────────────────────────────────────────────────────

  // Call this after triggering a batch so tracking starts immediately
  // (without waiting for the next discovery poll).
  const trackRole = useCallback(
    (roleId) => addTracked(roleId),
    [addTracked],
  );

  // Dismiss a completed/cancelled job and stop tracking it.
  const dismissJob = useCallback(
    (roleId) => {
      removeTracked(roleId);
      setJobs((prev) => {
        const next = { ...prev };
        delete next[Number(roleId)];
        return next;
      });
    },
    [removeTracked],
  );

  // Cancel a running batch. Updates local state optimistically; next poll
  // confirms the server-side status.
  const cancelBatch = useCallback(
    async (roleId) => {
      try {
        await rolesApi?.cancelBatchScore(roleId);
        setJobs((prev) => ({
          ...prev,
          [Number(roleId)]: {
            ...(prev[Number(roleId)] ?? {}),
            status: 'cancelling',
          },
        }));
      } catch {}
    },
    [rolesApi],
  );

  const value = {
    jobs,
    trackRole,
    dismissJob,
    cancelBatch,
    trackedRoleIds: trackedRef.current,
  };

  return (
    <JobStatusContext.Provider value={value}>
      {children}
    </JobStatusContext.Provider>
  );
}

export function useJobStatus() {
  return useContext(JobStatusContext);
}
