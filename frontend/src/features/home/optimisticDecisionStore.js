import { SESSION_BOUNDARY_EVENT } from '../../shared/auth/sessionBoundary';

// Per-tab optimistic decision locks.
//
// Home is remounted when the recruiter navigates between app sections. Keeping
// these locks outside the component prevents a pending mutation from becoming
// actionable again during that remount window. The original async handler can
// still stamp/clear the shared entry after its component has unmounted, and all
// mounted consumers update through useSyncExternalStore.

let snapshot = new Map();
let decisionLoadSequence = 0;
const listeners = new Set();

export const getOptimisticDecisions = () => snapshot;

export const subscribeOptimisticDecisions = (listener) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

export const updateOptimisticDecisions = (updater) => {
  const next = updater(snapshot);
  if (!(next instanceof Map) || next === snapshot) return snapshot;
  snapshot = next;
  listeners.forEach((listener) => listener());
  return snapshot;
};

export const resetOptimisticDecisions = () => {
  if (snapshot.size === 0) return;
  snapshot = new Map();
  listeners.forEach((listener) => listener());
};

// Tickets must remain monotonic across HomePage remounts because an async
// mutation started by the previous instance can finish after the new instance
// has already published a decision snapshot.
export const nextDecisionLoadTicket = () => {
  decisionLoadSequence += 1;
  return decisionLoadSequence;
};

if (typeof window !== 'undefined') {
  window.addEventListener('auth:logout', resetOptimisticDecisions);
  window.addEventListener(SESSION_BOUNDARY_EVENT, resetOptimisticDecisions);
}
