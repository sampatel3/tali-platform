import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { organizations as orgsApi, roles as rolesApi } from '../shared/api';
import { useAuth } from '../context/AuthContext';
import { jobTrackingScope, scopedJobTrackingKey } from '../shared/jobs/jobTrackingStorage';

// How often to poll each tracked role's status.
const ROLE_POLL_MS = 4000;
// How often to re-discover newly-started batches (catches batches started
// from other pages/tabs, or after a page refresh where local state was lost).
const DISCOVERY_POLL_MS = 10_000;

const STORAGE_KEY = 'tali_tracked_batch_roles';
const DISMISSED_SCORE_STORAGE_KEY = 'tali_dismissed_score_runs';
const SCORE_RUN_IDENTITIES_STORAGE_KEY = 'tali_tracked_score_run_identities';
let localScoreRunSequence = 0;

// A batch is still worth polling only while it's running or being cancelled;
// any other status (succeeded / failed / cancelled / not_found) is terminal and
// the loop can stop hitting that role. Mirrors the org-wide sync loops' guard.
const POLL_ACTIVE_STATES = new Set(['running', 'cancelling', 'pending', 'queued', 'starting']);
const isPollActive = (data) => {
  if (!data) return true; // no data yet → keep polling until the first status lands
  const status = String(data.status ?? '').toLowerCase();
  if (!status) return true; // unknown shape → don't prune, stay safe
  return POLL_ACTIVE_STATES.has(status);
};
const isTerminalPollError = (error) => [401, 403, 404].includes(Number(error?.response?.status || 0));
// True while the tab is backgrounded — we skip fetches (but keep the loop's
// timer alive) so hidden tabs don't hammer the API. Guarded for SSR/tests.
const docHidden = () => (typeof document !== 'undefined' && document.hidden);

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

function loadDismissedScoreRuns(key) {
  try {
    const raw = localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : {};
    return new Map(
      Object.entries(parsed ?? {})
        .map(([roleId, identity]) => [Number(roleId), String(identity)])
        .filter(([roleId, identity]) => Number.isFinite(roleId) && identity),
    );
  } catch {
    return new Map();
  }
}

function persistDismissedScoreRuns(key, dismissed) {
  try {
    localStorage.setItem(key, JSON.stringify(Object.fromEntries(dismissed)));
  } catch {}
}

function scoreRunIdentity(snapshot) {
  const roleId = Number(snapshot?.role_id);
  const runMarker = snapshot?.run_id ?? snapshot?.started_at ?? snapshot?.terminal_at;
  if (!Number.isFinite(roleId) || runMarker === null || runMarker === undefined || runMarker === '') {
    return null;
  }
  return `${roleId}:${String(runMarker)}`;
}

function newLocalScoreRunIdentity(roleId) {
  localScoreRunSequence += 1;
  return `${Number(roleId)}:local:${Date.now()}:${localScoreRunSequence}`;
}

const JobStatusContext = createContext(null);

export function JobStatusProvider({ children }) {
  // Bullhorn sync status/cancel already live on the organizations client
  // (mirrors the Workable sync surface); reuse them here rather than
  // duplicating the endpoints on rolesApi.
  // Re-key auth-gated effects (discovery polling) on login state so they
  // re-run after a load-then-login — the provider is mounted at app root
  // and never remounts on authentication.
  const { isAuthenticated, user } = useAuth();
  const trackingScope = useMemo(
    () => jobTrackingScope(user),
    [user],
  );
  const storageKeys = useMemo(() => ({
    score: scopedJobTrackingKey(STORAGE_KEY, trackingScope),
    fetch: scopedJobTrackingKey(FETCH_STORAGE_KEY, trackingScope),
    preScreen: scopedJobTrackingKey(PRESCREEN_STORAGE_KEY, trackingScope),
    process: scopedJobTrackingKey(PROCESS_STORAGE_KEY, trackingScope),
    dismissedScore: scopedJobTrackingKey(DISMISSED_SCORE_STORAGE_KEY, trackingScope),
    scoreRunIdentities: scopedJobTrackingKey(
      SCORE_RUN_IDENTITIES_STORAGE_KEY,
      trackingScope,
    ),
  }), [trackingScope]);

  // jobs / fetchJobs / preScreenJobs are keyed by role_id.
  // Each job kind has its own polling loop because they have different
  // backend endpoints (different status payload shapes).
  const [jobs, setJobs] = useState({});
  // Keep the latest committed/queued score snapshots available synchronously.
  // Dismissal must record its run identity before a pending discovery response
  // can enqueue the same terminal run again.
  const jobsRef = useRef({});
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
  // Org-wide Bullhorn sync — same single-active assumption. Bullhorn has no
  // per-run table, so (unlike Workable) there's no run history — only the one
  // live run tracked off the org's live progress marker.
  const [bullhornSyncJob, setBullhornSyncJob] = useState(null);
  const [bullhornSyncTracked, setBullhornSyncTracked] = useState(false);

  // tracked sets: role IDs we're actively polling for each job kind
  const trackedRef = useRef(new Set(loadPersistedFromKey(storageKeys.score)));
  const trackedFetchRef = useRef(new Set(loadPersistedFromKey(storageKeys.fetch)));
  const trackedPreScreenRef = useRef(new Set(loadPersistedFromKey(storageKeys.preScreen)));
  const trackedProcessRef = useRef(new Set(loadPersistedFromKey(storageKeys.process)));
  const dismissedScoreRunsRef = useRef(
    loadDismissedScoreRuns(storageKeys.dismissedScore),
  );
  const scoreRunIdentitiesRef = useRef(
    loadDismissedScoreRuns(storageKeys.scoreRunIdentities),
  );
  const [trackedVersion, setTrackedVersion] = useState(0);
  const [fetchVersion, setFetchVersion] = useState(0);
  const [preScreenVersion, setPreScreenVersion] = useState(0);
  const [processVersion, setProcessVersion] = useState(0);

  const bumpVersion = useCallback(() => setTrackedVersion((v) => v + 1), []);
  const bumpFetch = useCallback(() => setFetchVersion((v) => v + 1), []);
  const bumpPreScreen = useCallback(() => setPreScreenVersion((v) => v + 1), []);
  const bumpProcess = useCallback(() => setProcessVersion((v) => v + 1), []);

  const persistScoreRunIdentities = useCallback(() => {
    persistDismissedScoreRuns(
      storageKeys.scoreRunIdentities,
      scoreRunIdentitiesRef.current,
    );
  }, [storageKeys.scoreRunIdentities]);

  const ensureScoreRunIdentity = useCallback(
    (roleId) => {
      const id = Number(roleId);
      let identity = scoreRunIdentitiesRef.current.get(id);
      if (!identity) {
        identity = newLocalScoreRunIdentity(id);
        scoreRunIdentitiesRef.current.set(id, identity);
        persistScoreRunIdentities();
      }
      return identity;
    },
    [persistScoreRunIdentities],
  );

  const activeScopeRef = useRef(trackingScope);
  useEffect(() => {
    if (activeScopeRef.current === trackingScope) return;
    activeScopeRef.current = trackingScope;
    trackedRef.current = new Set(loadPersistedFromKey(storageKeys.score));
    trackedFetchRef.current = new Set(loadPersistedFromKey(storageKeys.fetch));
    trackedPreScreenRef.current = new Set(loadPersistedFromKey(storageKeys.preScreen));
    trackedProcessRef.current = new Set(loadPersistedFromKey(storageKeys.process));
    dismissedScoreRunsRef.current = loadDismissedScoreRuns(storageKeys.dismissedScore);
    scoreRunIdentitiesRef.current = loadDismissedScoreRuns(
      storageKeys.scoreRunIdentities,
    );
    jobsRef.current = {};
    setJobs({});
    setFetchJobs({});
    setPreScreenJobs({});
    setProcessJobs({});
    setGraphSyncJob(null);
    setWorkableSyncJob(null);
    setBullhornSyncJob(null);
    setGraphSyncTracked(false);
    setWorkableSyncTracked(false);
    setBullhornSyncTracked(false);
    bumpVersion();
    bumpFetch();
    bumpPreScreen();
    bumpProcess();
  }, [bumpFetch, bumpPreScreen, bumpProcess, bumpVersion, storageKeys, trackingScope]);

  const addTracked = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (!trackedRef.current.has(id)) {
        trackedRef.current = new Set([...trackedRef.current, id]);
        persistToKey(storageKeys.score, trackedRef.current);
        bumpVersion();
      }
    },
    [bumpVersion, storageKeys.score],
  );

  const removeTracked = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (trackedRef.current.has(id)) {
        const next = new Set(trackedRef.current);
        next.delete(id);
        trackedRef.current = next;
        persistToKey(storageKeys.score, next);
        bumpVersion();
      }
    },
    [bumpVersion, storageKeys.score],
  );

  const addTrackedFetch = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (!trackedFetchRef.current.has(id)) {
        trackedFetchRef.current = new Set([...trackedFetchRef.current, id]);
        persistToKey(storageKeys.fetch, trackedFetchRef.current);
        bumpFetch();
      }
    },
    [bumpFetch, storageKeys.fetch],
  );

  const removeTrackedFetch = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (trackedFetchRef.current.has(id)) {
        const next = new Set(trackedFetchRef.current);
        next.delete(id);
        trackedFetchRef.current = next;
        persistToKey(storageKeys.fetch, next);
        bumpFetch();
      }
    },
    [bumpFetch, storageKeys.fetch],
  );

  const addTrackedProcess = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (!trackedProcessRef.current.has(id)) {
        trackedProcessRef.current = new Set([...trackedProcessRef.current, id]);
        persistToKey(storageKeys.process, trackedProcessRef.current);
        bumpProcess();
      }
    },
    [bumpProcess, storageKeys.process],
  );

  const removeTrackedProcess = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (trackedProcessRef.current.has(id)) {
        const next = new Set(trackedProcessRef.current);
        next.delete(id);
        trackedProcessRef.current = next;
        persistToKey(storageKeys.process, next);
        bumpProcess();
      }
    },
    [bumpProcess, storageKeys.process],
  );

  const addTrackedPreScreen = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (!trackedPreScreenRef.current.has(id)) {
        trackedPreScreenRef.current = new Set([...trackedPreScreenRef.current, id]);
        persistToKey(storageKeys.preScreen, trackedPreScreenRef.current);
        bumpPreScreen();
      }
    },
    [bumpPreScreen, storageKeys.preScreen],
  );

  const removeTrackedPreScreen = useCallback(
    (roleId) => {
      const id = Number(roleId);
      if (trackedPreScreenRef.current.has(id)) {
        const next = new Set(trackedPreScreenRef.current);
        next.delete(id);
        trackedPreScreenRef.current = next;
        persistToKey(storageKeys.preScreen, next);
        bumpPreScreen();
      }
    },
    [bumpPreScreen, storageKeys.preScreen],
  );

  // ── Per-role status polling: batch-score ──────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const poll = async () => {
      if (cancelled) return;
      const ids = [...trackedRef.current];
      // Skip the network round-trip while the tab is backgrounded — the timer
      // still reschedules, so polling resumes the moment the tab is visible.
      if (ids.length > 0 && !docHidden()) {
        const requestIdentities = new Map(
          ids.map((roleId) => [roleId, ensureScoreRunIdentity(roleId)]),
        );
        const requestDismissals = new Map(dismissedScoreRunsRef.current);
        const results = await Promise.allSettled(
          ids.map((roleId) =>
            rolesApi?.batchScoreStatus(roleId)
              .then((r) => ({ roleId, data: r?.data }))
              .catch((error) => ({ roleId, error })),
          ),
        );
        if (cancelled) return;
        const done = [];
        let jobsChanged = false;
        let identitiesChanged = false;
        const nextJobs = { ...jobsRef.current };
        for (const r of results) {
          if (r.status !== 'fulfilled') continue;
          const roleId = Number(r.value?.roleId);
          const requestIdentity = requestIdentities.get(roleId);
          const currentIdentity = scoreRunIdentitiesRef.current.get(roleId);
          const dismissedIdentity = dismissedScoreRunsRef.current.get(roleId);
          // Ignore a result for a run that was dismissed or superseded while
          // this request was in flight. Otherwise a late status read can undo
          // the user's dismissal just like a late discovery response can.
          if (requestIdentity && currentIdentity !== requestIdentity) continue;
          if (
            dismissedIdentity
            && dismissedIdentity === requestIdentity
            && requestDismissals.get(roleId) !== dismissedIdentity
          ) {
            continue;
          }
          if (r.value?.data) {
            const serverIdentity = scoreRunIdentity({
              ...r.value.data,
              role_id: roleId,
            });
            if (serverIdentity && dismissedIdentity === serverIdentity) continue;
            if (serverIdentity && serverIdentity !== currentIdentity) {
              scoreRunIdentitiesRef.current.set(roleId, serverIdentity);
              identitiesChanged = true;
            }
            nextJobs[roleId] = {
              ...nextJobs[roleId],
              ...r.value.data,
            };
            jobsChanged = true;
            if (!isPollActive(r.value.data)) done.push(roleId);
          } else if (isTerminalPollError(r.value?.error)) {
            if (Object.hasOwn(nextJobs, roleId)) {
              delete nextJobs[roleId];
              jobsChanged = true;
            }
            done.push(roleId);
          }
        }
        if (identitiesChanged) persistScoreRunIdentities();
        if (jobsChanged) {
          jobsRef.current = nextJobs;
          setJobs(nextJobs);
        }
        // Stop polling terminal batches — their last status stays in `jobs` for
        // display until dismissed, but we don't keep hitting the API for a job
        // that's finished. Prune in place (no version bump) so the running loop
        // simply skips them next tick instead of restarting.
        if (done.length) {
          const nextTracked = new Set(trackedRef.current);
          done.forEach((id) => nextTracked.delete(id));
          trackedRef.current = nextTracked;
          persistToKey(storageKeys.score, nextTracked);
        }
      }
      if (!cancelled) timer = setTimeout(poll, ROLE_POLL_MS);
    };

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // trackedVersion re-runs this effect when the tracked set changes.

  }, [
    ensureScoreRunIdentity,
    persistScoreRunIdentities,
    storageKeys.score,
    trackedVersion,
  ]);

  // ── Per-role status polling: fetch-cvs ────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      if (cancelled) return;
      const ids = [...trackedFetchRef.current];
      if (ids.length > 0 && !docHidden()) {
        const results = await Promise.allSettled(
          ids.map((roleId) =>
            rolesApi?.fetchCvsStatus(roleId)
              .then((r) => ({ roleId, data: r?.data }))
              .catch((error) => ({ roleId, error })),
          ),
        );
        if (cancelled) return;
        const done = [];
        setFetchJobs((prev) => {
          const next = { ...prev };
          for (const r of results) {
            if (r.status === 'fulfilled' && r.value?.data) {
              next[r.value.roleId] = r.value.data;
              if (!isPollActive(r.value.data)) done.push(Number(r.value.roleId));
            } else if (r.status === 'fulfilled' && isTerminalPollError(r.value?.error)) {
              delete next[r.value.roleId];
              done.push(Number(r.value.roleId));
            }
          }
          return next;
        });
        if (done.length) {
          const nextTracked = new Set(trackedFetchRef.current);
          done.forEach((id) => nextTracked.delete(id));
          trackedFetchRef.current = nextTracked;
          persistToKey(storageKeys.fetch, nextTracked);
        }
      }
      if (!cancelled) timer = setTimeout(poll, ROLE_POLL_MS);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };

  }, [fetchVersion, storageKeys.fetch]);

  // ── Per-role status polling: pre-screen ───────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      if (cancelled) return;
      const ids = [...trackedPreScreenRef.current];
      if (ids.length > 0 && !docHidden()) {
        const results = await Promise.allSettled(
          ids.map((roleId) =>
            rolesApi?.batchPreScreenStatus(roleId)
              .then((r) => ({ roleId, data: r?.data }))
              .catch((error) => ({ roleId, error })),
          ),
        );
        if (cancelled) return;
        const done = [];
        setPreScreenJobs((prev) => {
          const next = { ...prev };
          for (const r of results) {
            if (r.status === 'fulfilled' && r.value?.data) {
              next[r.value.roleId] = r.value.data;
              if (!isPollActive(r.value.data)) done.push(Number(r.value.roleId));
            } else if (r.status === 'fulfilled' && isTerminalPollError(r.value?.error)) {
              delete next[r.value.roleId];
              done.push(Number(r.value.roleId));
            }
          }
          return next;
        });
        if (done.length) {
          const nextTracked = new Set(trackedPreScreenRef.current);
          done.forEach((id) => nextTracked.delete(id));
          trackedPreScreenRef.current = nextTracked;
          persistToKey(storageKeys.preScreen, nextTracked);
        }
      }
      if (!cancelled) timer = setTimeout(poll, ROLE_POLL_MS);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };

  }, [preScreenVersion, storageKeys.preScreen]);

  // ── Per-role status polling: process (cascade) ────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      if (cancelled) return;
      const ids = [...trackedProcessRef.current];
      if (ids.length > 0 && !docHidden()) {
        const results = await Promise.allSettled(
          ids.map((roleId) =>
            rolesApi?.processRoleStatus(roleId)
              .then((r) => ({ roleId, data: r?.data }))
              .catch((error) => ({ roleId, error })),
          ),
        );
        if (cancelled) return;
        const done = [];
        setProcessJobs((prev) => {
          const next = { ...prev };
          for (const r of results) {
            if (r.status === 'fulfilled' && r.value?.data) {
              next[r.value.roleId] = r.value.data;
              if (!isPollActive(r.value.data)) done.push(Number(r.value.roleId));
            } else if (r.status === 'fulfilled' && isTerminalPollError(r.value?.error)) {
              delete next[r.value.roleId];
              done.push(Number(r.value.roleId));
            }
          }
          return next;
        });
        if (done.length) {
          const nextTracked = new Set(trackedProcessRef.current);
          done.forEach((id) => nextTracked.delete(id));
          trackedProcessRef.current = nextTracked;
          persistToKey(storageKeys.process, nextTracked);
        }
      }
      if (!cancelled) timer = setTimeout(poll, ROLE_POLL_MS);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };

  }, [processVersion, storageKeys.process]);

  // ── Org-wide graph sync polling ───────────────────────────────────────────
  useEffect(() => {
    if (!graphSyncTracked) return undefined;
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      if (cancelled) return;
      if (!docHidden()) {
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
      }
      if (!cancelled) timer = setTimeout(poll, ROLE_POLL_MS);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [graphSyncTracked, trackingScope]);

  // ── Org-wide Workable sync polling ────────────────────────────────────────
  useEffect(() => {
    if (!workableSyncTracked) return undefined;
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      if (cancelled) return;
      if (!docHidden()) {
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
      }
      if (!cancelled) timer = setTimeout(poll, ROLE_POLL_MS);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [trackingScope, workableSyncTracked]);

  // ── Org-wide Bullhorn sync polling ────────────────────────────────────────
  useEffect(() => {
    if (!bullhornSyncTracked) return undefined;
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      if (cancelled) return;
      if (!docHidden()) {
        try {
          const r = await orgsApi?.getBullhornSyncStatus();
          if (cancelled) return;
          setBullhornSyncJob(r?.data ?? null);
          const status = String(r?.data?.last_sync_status ?? r?.data?.status ?? '').toLowerCase();
          const inProgress = !!r?.data?.sync_in_progress;
          if (!inProgress && status !== 'running' && status !== 'cancelling') {
            setBullhornSyncTracked(false);
          }
        } catch {}
      }
      if (!cancelled) timer = setTimeout(poll, ROLE_POLL_MS);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [bullhornSyncTracked, trackingScope]);

  // ── Discovery polling — finds batches started from other pages/tabs ───────
  useEffect(() => {
    if (!rolesApi?.activeBatchScores) return undefined;
    // Skip polling entirely when there's no auth token. Otherwise the call
    // 401s, the httpClient's response interceptor reacts to the 401 by
    // redirecting to /login, and the marketing showcase iframe gets bounced
    // to the sign-in page even though the demo route is meant to be public.
    if (typeof window !== 'undefined' && !localStorage.getItem('taali_access_token')) {
      return undefined;
    }
    let cancelled = false;
    let timer = null;

    const discover = async () => {
      if (cancelled) return;
      if (docHidden()) {
        // Backgrounded tab — don't discover, just reschedule.
        timer = setTimeout(discover, DISCOVERY_POLL_MS);
        return;
      }
      try {
        // Capture which local run this request belongs to. A user can dismiss
        // that run while the response is in flight; comparing the captured
        // identity prevents even a stale "running" snapshot from resurrecting
        // it. Persist identities for legacy tracked-role entries before I/O.
        for (const roleId of trackedRef.current) ensureScoreRunIdentity(roleId);
        const requestIdentities = new Map(scoreRunIdentitiesRef.current);
        const requestDismissals = new Map(dismissedScoreRunsRef.current);
        const res = await rolesApi.activeBatchScores();
        if (cancelled) return;
        const active = res?.data?.active ?? [];
        const nextTracked = new Set(trackedRef.current);
        const nextJobs = { ...jobsRef.current };
        let trackingChanged = false;
        let jobsChanged = false;
        let dismissedChanged = false;
        let identitiesChanged = false;
        for (const snapshot of active) {
          const roleId = Number(snapshot?.role_id);
          if (!Number.isFinite(roleId)) continue;
          const serverIdentity = scoreRunIdentity(snapshot);
          const requestIdentity = requestIdentities.get(roleId);
          const currentIdentity = scoreRunIdentitiesRef.current.get(roleId);
          const dismissedIdentity = dismissedScoreRunsRef.current.get(roleId);
          const requestWasSuperseded = Boolean(
            requestIdentity
            && currentIdentity
            && requestIdentity !== currentIdentity,
          );
          if (requestWasSuperseded) continue;

          const dismissedDuringRequest = Boolean(
            dismissedIdentity
            && requestIdentity
            && dismissedIdentity === requestIdentity
            && currentIdentity === requestIdentity
            && requestDismissals.get(roleId) !== dismissedIdentity,
          );
          const sameDismissedServerRun = Boolean(
            serverIdentity && dismissedIdentity === serverIdentity,
          );
          // A fast job can finish and be dismissed before discovery ever gives
          // us its server run id. Its persisted local identity is the bridge:
          // the first terminal discovery binds that tombstone to the server id
          // instead of re-showing the same completed job.
          const unresolvedDismissedTerminal = Boolean(
            dismissedIdentity
            && dismissedIdentity === currentIdentity
            && dismissedIdentity.includes(':local:')
            && !isPollActive(snapshot),
          );
          if (
            dismissedDuringRequest
            || sameDismissedServerRun
            || unresolvedDismissedTerminal
          ) {
            if (serverIdentity && dismissedIdentity !== serverIdentity) {
              dismissedScoreRunsRef.current.set(roleId, serverIdentity);
              scoreRunIdentitiesRef.current.set(roleId, serverIdentity);
              dismissedChanged = true;
              identitiesChanged = true;
            }
            if (nextTracked.delete(roleId)) trackingChanged = true;
            if (Object.hasOwn(nextJobs, roleId)) {
              delete nextJobs[roleId];
              jobsChanged = true;
            }
            continue;
          }

          if (serverIdentity) {
            if (dismissedIdentity && dismissedIdentity !== serverIdentity) {
              dismissedScoreRunsRef.current.delete(roleId);
              dismissedChanged = true;
            }
            if (currentIdentity !== serverIdentity) {
              scoreRunIdentitiesRef.current.set(roleId, serverIdentity);
              identitiesChanged = true;
            }
          } else if (!currentIdentity || dismissedIdentity === currentIdentity) {
            scoreRunIdentitiesRef.current.set(
              roleId,
              newLocalScoreRunIdentity(roleId),
            );
            identitiesChanged = true;
            if (dismissedIdentity) {
              dismissedScoreRunsRef.current.delete(roleId);
              dismissedChanged = true;
            }
          }

          const currentJob = nextJobs[roleId];
          const snapshotChanged = Object.entries(snapshot).some(
            ([key, value]) => currentJob?.[key] !== value,
          );
          if (snapshotChanged) {
            nextJobs[roleId] = { ...currentJob, ...snapshot };
            jobsChanged = true;
          }
          if (isPollActive(snapshot)) {
            if (!nextTracked.has(roleId)) {
              nextTracked.add(roleId);
              trackingChanged = true;
            }
          } else if (nextTracked.delete(roleId)) {
            trackingChanged = true;
          }
        }
        if (jobsChanged) {
          jobsRef.current = nextJobs;
          setJobs(nextJobs);
        }
        if (trackingChanged) {
          trackedRef.current = nextTracked;
          persistToKey(storageKeys.score, nextTracked);
          bumpVersion();
        }
        if (dismissedChanged) {
          persistDismissedScoreRuns(
            storageKeys.dismissedScore,
            dismissedScoreRunsRef.current,
          );
        }
        if (identitiesChanged) persistScoreRunIdentities();
      } catch {}
      if (!cancelled) timer = setTimeout(discover, DISCOVERY_POLL_MS);
    };

    discover();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // isAuthenticated re-runs this after a load-then-login so discovery,
    // gated on the access token above, actually starts once signed in.
  }, [
    bumpVersion,
    ensureScoreRunIdentity,
    isAuthenticated,
    persistScoreRunIdentities,
    storageKeys.dismissedScore,
    storageKeys.score,
  ]);

  // ── One-shot discovery on mount: pick up an in-flight workable sync ───────
  useEffect(() => {
    if (!rolesApi?.workableSyncStatus) return;
    if (typeof window !== 'undefined' && !localStorage.getItem('taali_access_token')) return;
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
    // Re-run when the mounted provider moves to a different authenticated
    // organization/user scope; rolesApi itself is stable.
  }, [isAuthenticated, trackingScope]);

  // ── One-shot discovery on mount: pick up an in-flight Bullhorn sync ───────
  useEffect(() => {
    if (!orgsApi?.getBullhornSyncStatus) return;
    if (typeof window !== 'undefined' && !localStorage.getItem('taali_access_token')) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await orgsApi.getBullhornSyncStatus();
        if (cancelled) return;
        if (r?.data?.sync_in_progress) {
          setBullhornSyncJob(r.data);
          setBullhornSyncTracked(true);
        }
      } catch {}
    })();
    return () => { cancelled = true; };
    // Re-run for a same-mounted organization/user scope change.
  }, [isAuthenticated, trackingScope]);

  // ── One-shot discovery on mount: pick up an in-flight graph sync ──────────
  useEffect(() => {
    if (!rolesApi?.syncGraphStatus) return;
    if (typeof window !== 'undefined' && !localStorage.getItem('taali_access_token')) return;
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

  }, [isAuthenticated, trackingScope]);

  // ── Public API ────────────────────────────────────────────────────────────

  // Call these after triggering a batch so tracking starts immediately
  // (without waiting for the next discovery poll).
  const trackRole = useCallback(
    (roleId, snapshot = null) => {
      const id = Number(roleId);
      if (!Number.isFinite(id)) return;
      const hasSnapshot = snapshot && typeof snapshot === 'object';
      const normalized = hasSnapshot ? { ...snapshot, role_id: id } : null;
      const serverIdentity = scoreRunIdentity(normalized);
      const dismissedIdentity = dismissedScoreRunsRef.current.get(id);
      const currentIdentity = scoreRunIdentitiesRef.current.get(id);

      // A page bootstrap may call trackRole for the terminal status it just
      // loaded. Keep a previously dismissed server run hidden; discovery will
      // make a genuinely different run visible by its different identity.
      if (serverIdentity && dismissedIdentity === serverIdentity) return;
      if (!serverIdentity && hasSnapshot && !isPollActive(normalized) && dismissedIdentity) {
        return;
      }

      let nextIdentity = serverIdentity || currentIdentity;
      const needsNewLocalIdentity = !nextIdentity || (
        (!hasSnapshot || isPollActive(normalized))
        && (!trackedRef.current.has(id) || dismissedIdentity)
        && !serverIdentity
      );
      if (needsNewLocalIdentity) nextIdentity = newLocalScoreRunIdentity(id);
      if (nextIdentity !== currentIdentity) {
        scoreRunIdentitiesRef.current.set(id, nextIdentity);
        persistScoreRunIdentities();
      }
      if (dismissedIdentity && dismissedIdentity !== serverIdentity) {
        dismissedScoreRunsRef.current.delete(id);
        persistDismissedScoreRuns(
          storageKeys.dismissedScore,
          dismissedScoreRunsRef.current,
        );
      }
      addTracked(id);
    },
    [addTracked, persistScoreRunIdentities, storageKeys.dismissedScore],
  );
  const trackRoleFetchCvs = useCallback((roleId) => addTrackedFetch(roleId), [addTrackedFetch]);
  const trackRolePreScreen = useCallback((roleId) => addTrackedPreScreen(roleId), [addTrackedPreScreen]);
  const trackRoleProcess = useCallback((roleId) => addTrackedProcess(roleId), [addTrackedProcess]);
  const trackGraphSync = useCallback(() => setGraphSyncTracked(true), []);
  const trackWorkableSync = useCallback(() => setWorkableSyncTracked(true), []);
  const trackBullhornSync = useCallback(() => setBullhornSyncTracked(true), []);

  // Dismiss a completed/cancelled job and stop tracking it.
  const dismissJob = useCallback(
    (roleId) => {
      const numericRoleId = Number(roleId);
      const identity = scoreRunIdentity({
        ...jobsRef.current[numericRoleId],
        role_id: numericRoleId,
      }) || ensureScoreRunIdentity(numericRoleId);
      if (identity) {
        if (scoreRunIdentitiesRef.current.get(numericRoleId) !== identity) {
          scoreRunIdentitiesRef.current.set(numericRoleId, identity);
          persistScoreRunIdentities();
        }
        dismissedScoreRunsRef.current.set(numericRoleId, identity);
        persistDismissedScoreRuns(
          storageKeys.dismissedScore,
          dismissedScoreRunsRef.current,
        );
      }
      removeTracked(numericRoleId);
      const next = { ...jobsRef.current };
      delete next[numericRoleId];
      jobsRef.current = next;
      setJobs(next);
    },
    [
      ensureScoreRunIdentity,
      persistScoreRunIdentities,
      removeTracked,
      storageKeys.dismissedScore,
    ],
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
    [],
  );

  const dismissGraphSyncJob = useCallback(() => {
    setGraphSyncJob(null);
    setGraphSyncTracked(false);
  }, []);

  const dismissWorkableSyncJob = useCallback(() => {
    setWorkableSyncJob(null);
    setWorkableSyncTracked(false);
  }, []);

  const dismissBullhornSyncJob = useCallback(() => {
    setBullhornSyncJob(null);
    setBullhornSyncTracked(false);
  }, []);

  const cancelGraphSync = useCallback(async () => {
    try {
      await rolesApi?.syncGraphCancel();
      setGraphSyncJob((prev) => (prev ? { ...prev, status: 'cancelling' } : prev));
    } catch {}
  }, []);

  const cancelWorkableSync = useCallback(
    async (runId = null) => {
      try {
        await rolesApi?.workableSyncCancel(runId);
        setWorkableSyncJob((prev) => (prev ? { ...prev, status: 'cancelling' } : prev));
      } catch {}
    },
    [],
  );

  const cancelBullhornSync = useCallback(async () => {
    try {
      await orgsApi?.cancelBullhornSync();
      setBullhornSyncJob((prev) => (prev ? { ...prev, status: 'cancelling' } : prev));
    } catch {}
  }, []);

  // Cancel a running batch. Updates local state optimistically; next poll
  // confirms the server-side status.
  const cancelBatch = useCallback(
    async (roleId) => {
      try {
        await rolesApi?.cancelBatchScore(roleId);
        setJobs((prev) => {
          const next = {
            ...prev,
            [Number(roleId)]: {
              ...(prev[Number(roleId)] ?? {}),
              status: 'cancelling',
            },
          };
          jobsRef.current = next;
          return next;
        });
      } catch {}
    },
    [],
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
    [],
  );

  const value = {
    jobs,
    fetchJobs,
    preScreenJobs,
    processJobs,
    graphSyncJob,
    workableSyncJob,
    bullhornSyncJob,
    trackRole,
    trackRoleFetchCvs,
    trackRolePreScreen,
    trackRoleProcess,
    trackGraphSync,
    trackWorkableSync,
    trackBullhornSync,
    dismissJob,
    dismissFetchJob,
    dismissPreScreenJob,
    dismissProcessJob,
    dismissGraphSyncJob,
    dismissWorkableSyncJob,
    dismissBullhornSyncJob,
    cancelBatch,
    cancelFetchCvs,
    cancelGraphSync,
    cancelWorkableSync,
    cancelBullhornSync,
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
