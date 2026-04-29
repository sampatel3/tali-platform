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

const FETCH_STORAGE_KEY = 'tali_tracked_fetch_roles';
const PRESCREEN_STORAGE_KEY = 'tali_tracked_pre_screen_roles';
const PROCESS_STORAGE_KEY = 'tali_tracked_process_roles';

function loadPersistedFromKey(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function persistToKey(key, ids) {
  try {
    localStorage.setItem(key, JSON.stringify([...ids]));
  } catch {}
}

const JobStatusContext = createContext(null);

export function JobStatusProvider({ children }) {
  const rolesApi = apiClient.roles ?? null;

  // jobs / fetchJobs / preScreenJobs are keyed by role_id.
  // Each job kind has its own polling loop because they have different
  // backend endpoints (different status payload shapes).
  const [jobs, setJobs] = useState({});
  const [fetchJobs, setFetchJobs] = useState({});
  const [preScreenJobs, setPreScreenJobs] = useState({});
  // processJobs replaces fetch + pre-screen + score into a single cascade.
  // Once we migrate all callers to the unified endpoint, we'll remove the
  // three legacy maps above.
  const [processJobs, setProcessJobs] = useState({});
  // Org-wide knowledge-graph sync. There's only ever one active sync per org,
  // so we don't bother keying by org_id — the user is in one org per session.
  const [graphSyncJob, setGraphSyncJob] = useState(null);
  const [graphSyncTracked, setGraphSyncTracked] = useState(false);
  // Org-wide Workable sync — same single-active assumption.
  const [workableSyncJob, setWorkableSyncJob] = useState(null);
  const [workableSyncTracked, setWorkableSyncTracked] = useState(false);

  // tracked sets: role IDs we're actively polling for each job kind
  const trackedRef = useRef(new Set(loadPersistedRoleIds()));
  const trackedFetchRef = useRef(new Set(loadPersistedFromKey(FETCH_STORAGE_KEY)));
  const trackedPreScreenRef = useRef(new Set(loadPersistedFromKey(PRESCREEN_STORAGE_KEY)));
  const trackedProcessRef = useRef(new Set(loadPersistedFromKey(PROCESS_STORAGE_KEY)));
  const [trackedVersion, setTrackedVersion] = useState(0);
  const [fetchVersion, setFetchVersion] = useState(0);
  const [preScreenVersion, setPreScreenVersion] = useState(0);
  const [processVersion, setProcessVersion] = useState(0);

  const bumpVersion = useCallback(() => setTrackedVersion((v) => v + 1), []);
  const bumpFetch = useCallback(() => setFetchVersion((v) => v + 1), []);
  const bumpPreScreen = useCallback(() => setPreScreenVersion((v) => v + 1), []);
  const bumpProcess = useCallback(() => setProcessVersion((v) => v + 1), []);

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

  const addTrackedFetch = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (!trackedFetchRef.current.has(id)) {
        trackedFetchRef.current = new Set([...trackedFetchRef.current, id]);
        persistToKey(FETCH_STORAGE_KEY, trackedFetchRef.current);
        bumpFetch();
      }
    },
    [bumpFetch],
  );

  const removeTrackedFetch = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (trackedFetchRef.current.has(id)) {
        const next = new Set(trackedFetchRef.current);
        next.delete(id);
        trackedFetchRef.current = next;
        persistToKey(FETCH_STORAGE_KEY, next);
        bumpFetch();
      }
    },
    [bumpFetch],
  );

  const addTrackedProcess = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (!trackedProcessRef.current.has(id)) {
        trackedProcessRef.current = new Set([...trackedProcessRef.current, id]);
        persistToKey(PROCESS_STORAGE_KEY, trackedProcessRef.current);
        bumpProcess();
      }
    },
    [bumpProcess],
  );

  const removeTrackedProcess = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (trackedProcessRef.current.has(id)) {
        const next = new Set(trackedProcessRef.current);
        next.delete(id);
        trackedProcessRef.current = next;
        persistToKey(PROCESS_STORAGE_KEY, next);
        bumpProcess();
      }
    },
    [bumpProcess],
  );

  const addTrackedPreScreen = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (!trackedPreScreenRef.current.has(id)) {
        trackedPreScreenRef.current = new Set([...trackedPreScreenRef.current, id]);
        persistToKey(PRESCREEN_STORAGE_KEY, trackedPreScreenRef.current);
        bumpPreScreen();
      }
    },
    [bumpPreScreen],
  );

  const removeTrackedPreScreen = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (trackedPreScreenRef.current.has(id)) {
        const next = new Set(trackedPreScreenRef.current);
        next.delete(id);
        trackedPreScreenRef.current = next;
        persistToKey(PRESCREEN_STORAGE_KEY, next);
        bumpPreScreen();
      }
    },
    [bumpPreScreen],
  );

  // ── Per-role status polling: batch-score ──────────────────────────────────
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

  // ── Per-role status polling: fetch-cvs ────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      if (cancelled) return;
      const ids = [...trackedFetchRef.current];
      if (ids.length > 0) {
        const results = await Promise.allSettled(
          ids.map((roleId) =>
            rolesApi?.fetchCvsStatus(roleId).then((r) => ({ roleId, data: r?.data })),
          ),
        );
        if (cancelled) return;
        setFetchJobs((prev) => {
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
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rolesApi, fetchVersion]);

  // ── Per-role status polling: pre-screen ───────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      if (cancelled) return;
      const ids = [...trackedPreScreenRef.current];
      if (ids.length > 0) {
        const results = await Promise.allSettled(
          ids.map((roleId) =>
            rolesApi?.batchPreScreenStatus(roleId).then((r) => ({ roleId, data: r?.data })),
          ),
        );
        if (cancelled) return;
        setPreScreenJobs((prev) => {
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
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rolesApi, preScreenVersion]);

  // ── Per-role status polling: process (cascade) ────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      if (cancelled) return;
      const ids = [...trackedProcessRef.current];
      if (ids.length > 0) {
        const results = await Promise.allSettled(
          ids.map((roleId) =>
            rolesApi?.processRoleStatus(roleId).then((r) => ({ roleId, data: r?.data })),
          ),
        );
        if (cancelled) return;
        setProcessJobs((prev) => {
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
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rolesApi, processVersion]);

  // ── Org-wide graph sync polling ───────────────────────────────────────────
  useEffect(() => {
    if (!graphSyncTracked) return undefined;
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      if (cancelled) return;
      try {
        const r = await rolesApi?.syncGraphStatus();
        if (cancelled) return;
        setGraphSyncJob(r?.data ?? null);
        const status = String(r?.data?.status ?? '').toLowerCase();
        if (status !== 'running' && status !== 'cancelling') {
          // Done — leave the last status visible until dismissed.
          setGraphSyncTracked(false);
        }
      } catch {}
      if (!cancelled) timer = setTimeout(poll, ROLE_POLL_MS);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [rolesApi, graphSyncTracked]);

  // ── Org-wide Workable sync polling ────────────────────────────────────────
  useEffect(() => {
    if (!workableSyncTracked) return undefined;
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      if (cancelled) return;
      try {
        const r = await rolesApi?.workableSyncStatus();
        if (cancelled) return;
        setWorkableSyncJob(r?.data ?? null);
        const status = String(r?.data?.workable_last_sync_status ?? r?.data?.status ?? '').toLowerCase();
        const inProgress = !!r?.data?.sync_in_progress;
        if (!inProgress && status !== 'running' && status !== 'cancelling') {
          setWorkableSyncTracked(false);
        }
      } catch {}
      if (!cancelled) timer = setTimeout(poll, ROLE_POLL_MS);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [rolesApi, workableSyncTracked]);

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

  // ── One-shot discovery on mount: pick up an in-flight workable sync ───────
  useEffect(() => {
    if (!rolesApi?.workableSyncStatus) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await rolesApi.workableSyncStatus();
        if (cancelled) return;
        if (r?.data?.sync_in_progress) {
          setWorkableSyncJob(r.data);
          setWorkableSyncTracked(true);
        }
      } catch {}
    })();
    return () => { cancelled = true; };
    // Run once on mount — rolesApi is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── One-shot discovery on mount: pick up an in-flight graph sync ──────────
  useEffect(() => {
    if (!rolesApi?.syncGraphStatus) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await rolesApi.syncGraphStatus();
        if (cancelled) return;
        const status = String(r?.data?.status ?? '').toLowerCase();
        if (status === 'running' || status === 'cancelling') {
          setGraphSyncJob(r.data);
          setGraphSyncTracked(true);
        }
      } catch {}
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Public API ────────────────────────────────────────────────────────────

  // Call these after triggering a batch so tracking starts immediately
  // (without waiting for the next discovery poll).
  const trackRole = useCallback((roleId) => addTracked(roleId), [addTracked]);
  const trackRoleFetchCvs = useCallback((roleId) => addTrackedFetch(roleId), [addTrackedFetch]);
  const trackRolePreScreen = useCallback((roleId) => addTrackedPreScreen(roleId), [addTrackedPreScreen]);
  const trackRoleProcess = useCallback((roleId) => addTrackedProcess(roleId), [addTrackedProcess]);
  const trackGraphSync = useCallback(() => setGraphSyncTracked(true), []);
  const trackWorkableSync = useCallback(() => setWorkableSyncTracked(true), []);

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

  const dismissFetchJob = useCallback(
    (roleId) => {
      removeTrackedFetch(roleId);
      setFetchJobs((prev) => {
        const next = { ...prev };
        delete next[Number(roleId)];
        return next;
      });
    },
    [removeTrackedFetch],
  );

  const dismissPreScreenJob = useCallback(
    (roleId) => {
      removeTrackedPreScreen(roleId);
      setPreScreenJobs((prev) => {
        const next = { ...prev };
        delete next[Number(roleId)];
        return next;
      });
    },
    [removeTrackedPreScreen],
  );

  const dismissProcessJob = useCallback(
    (roleId) => {
      removeTrackedProcess(roleId);
      setProcessJobs((prev) => {
        const next = { ...prev };
        delete next[Number(roleId)];
        return next;
      });
    },
    [removeTrackedProcess],
  );

  const cancelProcessJob = useCallback(
    async (roleId) => {
      try {
        await rolesApi?.cancelProcessRole(roleId);
        setProcessJobs((prev) => ({
          ...prev,
          [Number(roleId)]: { ...(prev[Number(roleId)] ?? {}), status: 'cancelling' },
        }));
      } catch {}
    },
    [rolesApi],
  );

  const dismissGraphSyncJob = useCallback(() => {
    setGraphSyncJob(null);
    setGraphSyncTracked(false);
  }, []);

  const dismissWorkableSyncJob = useCallback(() => {
    setWorkableSyncJob(null);
    setWorkableSyncTracked(false);
  }, []);

  const cancelGraphSync = useCallback(async () => {
    try {
      await rolesApi?.syncGraphCancel();
      setGraphSyncJob((prev) => (prev ? { ...prev, status: 'cancelling' } : prev));
    } catch {}
  }, [rolesApi]);

  const cancelWorkableSync = useCallback(
    async (runId = null) => {
      try {
        await rolesApi?.workableSyncCancel(runId);
        setWorkableSyncJob((prev) => (prev ? { ...prev, status: 'cancelling' } : prev));
      } catch {}
    },
    [rolesApi],
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

  const cancelFetchCvs = useCallback(
    async (roleId) => {
      try {
        await rolesApi?.cancelFetchCvs(roleId);
        setFetchJobs((prev) => ({
          ...prev,
          [Number(roleId)]: { ...(prev[Number(roleId)] ?? {}), status: 'cancelling' },
        }));
      } catch {}
    },
    [rolesApi],
  );

  const value = {
    jobs,
    fetchJobs,
    preScreenJobs,
    processJobs,
    graphSyncJob,
    workableSyncJob,
    trackRole,
    trackRoleFetchCvs,
    trackRolePreScreen,
    trackRoleProcess,
    trackGraphSync,
    trackWorkableSync,
    dismissJob,
    dismissFetchJob,
    dismissPreScreenJob,
    dismissProcessJob,
    dismissGraphSyncJob,
    dismissWorkableSyncJob,
    cancelBatch,
    cancelFetchCvs,
    cancelGraphSync,
    cancelWorkableSync,
    cancelProcessJob,
    trackedRoleIds: trackedRef.current,
    trackedFetchRoleIds: trackedFetchRef.current,
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
