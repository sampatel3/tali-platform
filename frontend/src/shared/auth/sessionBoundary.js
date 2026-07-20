const SESSION_BOUNDARY_STORAGE_KEY = 'taali_session_boundary';
const SESSION_BOUNDARY_EVENT = 'auth:session-boundary';

let lastSeenBoundary = null;
let initializedForThisTab = false;
let requestSessionActive = true;

const readStoredBoundary = () => {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem(SESSION_BOUNDARY_STORAGE_KEY);
};

const newBoundaryMarker = () => {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
};

const publishBoundaryEvent = () => {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(SESSION_BOUNDARY_EVENT));
  }
};

/**
 * Adopt the browser's current session when the root AuthProvider mounts.
 * A missing marker is a pre-boundary/legacy session, so give it one without
 * changing the authenticated user or token.
 */
export const initializeSessionBoundary = () => {
  if (typeof window === 'undefined') {
    initializedForThisTab = true;
    requestSessionActive = true;
    return null;
  }
  let marker = readStoredBoundary();
  if (!marker) {
    marker = newBoundaryMarker();
    window.localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, marker);
  }
  lastSeenBoundary = marker;
  initializedForThisTab = true;
  requestSessionActive = true;
  return marker;
};

/**
 * Start a new session boundary in this tab and notify every other tab through
 * localStorage. `active=false` is used for logout; a successful login/invite
 * replacement uses `active=true` so its own profile bootstrap may proceed.
 */
export const announceSessionBoundary = ({ active = true } = {}) => {
  if (typeof window === 'undefined') {
    initializedForThisTab = true;
    requestSessionActive = active;
    return null;
  }
  const marker = newBoundaryMarker();
  lastSeenBoundary = marker;
  initializedForThisTab = true;
  requestSessionActive = active;
  // Write the marker before changing the token. A stale tab can then reject a
  // request synchronously even if its asynchronous `storage` event is late.
  window.localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, marker);
  publishBoundaryEvent();
  return marker;
};

const invalidateForExternalBoundary = (marker) => {
  lastSeenBoundary = marker || null;
  initializedForThisTab = true;
  requestSessionActive = false;
  publishBoundaryEvent();
};

/**
 * True only while this tab still owns the session boundary it rendered. The
 * request interceptor checks this both before and after awaiting token refresh
 * so an account switch cannot swap a different account's token into a request.
 */
export const isRequestSessionCurrent = () => {
  if (typeof window === 'undefined') return true;
  const stored = readStoredBoundary();
  if (!initializedForThisTab) initializeSessionBoundary();
  if (!stored) {
    // Tests, legacy sessions, and users clearing site data can remove the
    // marker. Re-establish it only while this tab has not observed an external
    // boundary; production logout/replacement never removes this key.
    if (!requestSessionActive) return false;
    initializeSessionBoundary();
    return true;
  }
  if (stored !== lastSeenBoundary) {
    invalidateForExternalBoundary(stored);
    return false;
  }
  return requestSessionActive;
};

/**
 * Authentication endpoints intentionally run without an active recruiter
 * session. Capture the raw marker when an exchange starts so its response can
 * still be rejected if another tab logs out or signs in before it completes.
 */
export const captureStoredSessionBoundary = () => {
  if (typeof window === 'undefined') return null;
  if (!initializedForThisTab) initializeSessionBoundary();
  return readStoredBoundary();
};

export const isStoredSessionBoundaryCurrent = (marker) => {
  if (typeof window === 'undefined') return true;
  return Boolean(marker) && readStoredBoundary() === marker;
};

/**
 * Capture the boundary that a protected request belongs to. Callers should
 * verify the captured marker again after reading shared credentials: another
 * tab can replace localStorage between two otherwise synchronous reads.
 */
export const getCurrentSessionBoundary = () => (
  isRequestSessionCurrent() ? lastSeenBoundary : null
);

export const isSessionBoundaryCurrent = (marker) => (
  Boolean(marker)
  && isRequestSessionCurrent()
  && marker === lastSeenBoundary
);

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== SESSION_BOUNDARY_STORAGE_KEY) return;
    if (event.storageArea && event.storageArea !== window.localStorage) return;
    const marker = event.newValue || null;
    // Storage events are queued. A delayed event from another tab must not
    // invalidate a newer boundary this tab already published or observed.
    if (marker !== readStoredBoundary()) return;
    if (marker === lastSeenBoundary) return;
    invalidateForExternalBoundary(marker);
  });
}

export {
  SESSION_BOUNDARY_EVENT,
  SESSION_BOUNDARY_STORAGE_KEY,
};
