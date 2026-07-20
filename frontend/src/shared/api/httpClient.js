import axios from 'axios';

import {
  captureStoredSessionBoundary,
  getCurrentSessionSnapshot,
  getCurrentSessionBoundary,
  isRequestSessionCurrent,
  isSessionBoundaryCurrent,
  revokeSessionBoundary,
  updateSessionAccessToken,
} from '../auth/sessionBoundary';

const API_URL = (import.meta.env.VITE_API_URL || '').replace(/[\r\n\s]+/g, '').trim();

const api = axios.create({
  baseURL: `${API_URL}/api/v1`,
  // A dropped connection (common on the UAE→us-east4 hop) would otherwise hang
  // a request forever — the browser waits on OS TCP retransmission and the
  // promise never rejects, freezing "Working…" states with locked composers.
  // 60s is a sane default for normal reads/writes; long-poll or streaming
  // callers (e.g. the assessment Claude chat) pass their own larger per-request
  // `timeout` to override this. axios rejects with code 'ECONNABORTED' on hit.
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Public unauth share-link endpoint lives at the app root (no /api/v1
// prefix) so recipients can open it without a recruiter session. The
// SPA's /share/:token route uses this to fetch the application payload
// in one round-trip — the backend scrubs to client-view shape when
// the share link's mode is "client".
export const viewShareLink = (token) =>
  axios.get(`${API_URL}/share/${encodeURIComponent(token)}`);

// Public unauth "top candidates report" — same pattern as the share link.
export const viewTopReport = (token) =>
  axios.get(`${API_URL}/report/${encodeURIComponent(token)}`);

// Public unauth curated client submittal pack — same pattern: a role-scoped
// shortlist frozen at mint time, served read-only by token. Bare axios so the
// recruiter's JWT is never attached — anyone with the link can read it.
export const viewSubmittalPack = (token) =>
  axios.get(`${API_URL}/submittal/${encodeURIComponent(token)}`);

// Public unauth job posting — the careers-style page a published requisition
// links to. Lives UNDER /api/v1 (unlike the share/report endpoints), but we
// still use a bare axios.get rather than the shared `api` instance so the
// recruiter's JWT is never attached: anyone with the link can read it.
export const viewPublicJob = (token) =>
  axios.get(`${API_URL}/api/v1/public/job/${encodeURIComponent(token)}`);

// Public unauth CAREERS BOARD — an org's per-org page listing all of its
// published jobs. Same JWT-free pattern as viewPublicJob (bare axios so the
// recruiter's token is never attached — anyone with the link can read it).
// Returns `{ organization_name, slug, jobs: [ { token, url, title, location,
//   workplace_type, employment_type, seniority, salary, published_at } ] }`.
export const viewCareers = (slug) =>
  axios.get(`${API_URL}/api/v1/public/careers/${encodeURIComponent(slug)}`);

// Public unauth NATIVE APPLY — a candidate submits an application to a published
// job page. Multipart (name/email/phone + a JSON `answers` field + optional
// resume file). Bare axios (JWT-free); the browser sets the multipart boundary.
// Returns `{ status, message, application_id, eeo_token }`.
export const applyToJob = (token, formData) =>
  axios.post(
    `${API_URL}/api/v1/public/job-pages/${encodeURIComponent(token)}/apply`,
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );

// Public unauth VOLUNTARY EEO self-ID — keyed by the opaque `eeo_token` the
// apply response carried (never a raw application_id). Bare axios (JWT-free).
// Returns 204 (no body).
export const submitJobEeo = (token, payload) =>
  axios.post(`${API_URL}/api/v1/public/eeo/${encodeURIComponent(token)}`, payload);

// ---- Public, no-auth CLIENT INTAKE (a consultancy's client describing the
// role via the conversational agent) ----
//
// Same JWT-free pattern as viewPublicJob: a consultancy recruiter shares the
// /intake/:token link with their client, who talks to the SAME agent with all
// company/economics fields hidden. Bare axios so the recruiter's token is never
// attached — anyone with the link can use it.
const intakeBase = (token) =>
  `${API_URL}/api/v1/public/intake/${encodeURIComponent(token)}`;

// Snapshot the intake conversation + captured ROLE fields.
// Returns `{ organization_name, messages, captured, gaps, completeness, status }`.
export const viewClientIntake = (token) => axios.get(intakeBase(token));

// One conversational turn from the client — `message` text plus optional File
// attachments — as multipart/form-data (mirrors requisitionApi.chat). Returns
// `{ reply, messages, captured, gaps, suggested_replies }`.
export const sendClientIntakeChat = (token, { message = '', files = [] } = {}) => {
  const form = new FormData();
  form.append('message', message ?? '');
  (files || []).forEach((file) => {
    if (file) form.append('files', file);
  });
  return axios.post(`${intakeBase(token)}/chat`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

// Submit the captured brief back to the consultancy. Returns `{ ok, status }`.
export const submitClientIntake = (token) =>
  axios.post(`${intakeBase(token)}/submit`);

// ---- Public, no-auth one-click UNSUBSCRIBE ----
//
// The outreach opt-out embedded in each campaign email. Bare axios (JWT-free)
// like the other public endpoints — recipients have no recruiter session. GET
// validates the token + returns `{ organization_name, email_masked }` and does
// NOT write (prefetchers follow GET links); POST records the suppression.
const unsubscribeBase = (token) =>
  `${API_URL}/api/v1/public/unsubscribe/${encodeURIComponent(token)}`;

export const fetchUnsubscribe = (token) => axios.get(unsubscribeBase(token));
export const submitUnsubscribe = (token) => axios.post(unsubscribeBase(token));

// ---- Sliding session ----
// Access tokens expire after 30 minutes. Rather than silently logging active
// users out mid-work (the old behavior), we note when the current token was
// issued and swap it for a fresh one via POST /auth/jwt/refresh once it's
// REFRESH_TOKEN_AFTER_MS old — triggered by any API activity and by a
// visible-tab heartbeat. Idle sessions still expire with the last token.
export const REFRESH_TOKEN_AFTER_MS = 10 * 60 * 1000;

// Candidate assessment requests use their own opaque token (in the URL,
// X-Assessment-Token header, or multipart body). They must never inherit or
// mutate a recruiter session that happens to exist in the same browser.
export const ASSESSMENT_TOKEN_AUTH_MODE = 'assessment-token';
export const PUBLIC_NO_AUTH_MODE = 'public-no-auth';

const usesAssessmentTokenAuth = (config = {}) => (
  config.authMode === ASSESSMENT_TOKEN_AUTH_MODE
);

const usesPublicNoAuth = (config = {}) => config.authMode === PUBLIC_NO_AUTH_MODE;

// Exported for tests. A missing/garbled issued-at stamp counts as stale so
// sessions created before this feature shipped refresh on their next request.
export const shouldRefreshToken = (issuedAtRaw, now = Date.now()) => {
  const issuedAt = Number(issuedAtRaw);
  if (!issuedAtRaw || !Number.isFinite(issuedAt)) return true;
  return now - issuedAt > REFRESH_TOKEN_AFTER_MS;
};

// The session slides only for a PRESENT user: every refresh (interceptor
// trigger and heartbeat alike) requires input in the last USER_IDLE_CUTOFF_MS.
// Without this, a visible-but-unattended tab — or one kept warm by background
// polling — would mint fresh tokens forever and never idle out.
export const USER_IDLE_CUTOFF_MS = 15 * 60 * 1000;

// Exported for tests.
export const isUserActive = (lastActivityAt, now = Date.now()) => (
  Number.isFinite(Number(lastActivityAt)) && now - Number(lastActivityAt) <= USER_IDLE_CUTOFF_MS
);

let lastUserActivityAt = Date.now(); // page load counts as activity

if (typeof window !== 'undefined') {
  ['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach((evt) => {
    window.addEventListener(evt, () => { lastUserActivityAt = Date.now(); }, { passive: true, capture: true });
  });
}

let refreshInFlight = null;

export const ensureFreshAccessToken = () => {
  if (!isUserActive(lastUserActivityAt)) return Promise.resolve();
  const sessionAtStart = getCurrentSessionSnapshot();
  const tokenAtStart = sessionAtStart?.token;
  if (!sessionAtStart?.boundary || !tokenAtStart) return Promise.resolve();
  if (!shouldRefreshToken(sessionAtStart.issuedAt)) return Promise.resolve();
  if (refreshInFlight?.boundary === sessionAtStart.boundary) {
    return refreshInFlight.promise;
  }
  const refreshRecord = {
    boundary: sessionAtStart.boundary,
    promise: null,
  };
  refreshRecord.promise = api
    .post('/auth/jwt/refresh')
    .then(({ data }) => {
      if (data?.access_token) updateSessionAccessToken(
        sessionAtStart.boundary,
        data.access_token,
        { expectedToken: tokenAtStart },
      );
    })
    // A 401 here means the token is already dead — the response interceptor
    // below handles the logout; any other failure just retries next trigger.
    .catch(() => {})
    .finally(() => {
      if (refreshInFlight === refreshRecord) refreshInFlight = null;
    });
  refreshInFlight = refreshRecord;
  return refreshRecord.promise;
};

// Streaming fetch callers cannot use the Axios interceptor. Give them the
// same refresh + cross-tab boundary guarantees before they construct a bearer
// header, including a final marker check after reading the shared token.
export const getFreshSessionAuth = async () => {
  if (!isRequestSessionCurrent()) {
    throw new axios.CanceledError('Session changed in another tab. Please sign in again.');
  }
  const requestSessionBoundary = getCurrentSessionBoundary();
  if (!requestSessionBoundary) {
    throw new axios.CanceledError('Session changed in another tab. Please sign in again.');
  }
  await ensureFreshAccessToken();
  if (!isSessionBoundaryCurrent(requestSessionBoundary)) {
    throw new axios.CanceledError('Session changed in another tab. Please sign in again.');
  }
  const session = getCurrentSessionSnapshot();
  const token = session?.boundary === requestSessionBoundary ? session.token : null;
  if (!isSessionBoundaryCurrent(requestSessionBoundary)) {
    throw new axios.CanceledError('Session changed in another tab. Please sign in again.');
  }
  return {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    sessionBoundary: requestSessionBoundary,
  };
};

// Heartbeat so a user reading/typing without firing API calls stays signed in.
// Only ticks while the tab is visible; the user-activity gate inside
// maybeRefreshToken keeps an unattended tab from sliding forever.
// (Skipped under vitest — a live interval would keep test workers alive.)
if (typeof window !== 'undefined' && typeof document !== 'undefined' && import.meta.env?.MODE !== 'test') {
  setInterval(() => {
    if (document.visibilityState === 'visible') void ensureFreshAccessToken();
  }, 60 * 1000);
}

const isAuthEndpoint = (url = '') => (
  url.includes('/auth/jwt/login')
  || url.includes('/auth/register')
  || url.includes('/auth/forgot-password')
  || url.includes('/auth/reset-password')
  || url.includes('/auth/accept-invite')
  || url.includes('/auth/verify')
  || url.includes('/auth/request-verify-token')
  || url.includes('/auth/sso-')
);

// Attach auth token for recruiter-side endpoints.
api.interceptors.request.use(async (config) => {
  const url = config.url || '';
  const isAssessmentTokenRequest = usesAssessmentTokenAuth(config);
  const isPublicNoAuthRequest = usesPublicNoAuth(config);
  const isRefreshRequest = url.includes('/auth/jwt/refresh');
  const isSessionCreatingRequest = isAuthEndpoint(url);
  const isProtectedSessionRequest = !isAssessmentTokenRequest
    && !isPublicNoAuthRequest
    && !isSessionCreatingRequest;
  if (isProtectedSessionRequest && !isRequestSessionCurrent()) {
    throw new axios.CanceledError('Session changed in another tab. Please sign in again.');
  }
  // Bind the operation before any await. If token refresh yields while another
  // login wins, this request must cancel rather than adopt the next account.
  const requestSessionBoundary = isProtectedSessionRequest
    ? getCurrentSessionBoundary()
    : null;
  if (isProtectedSessionRequest && !requestSessionBoundary) {
    throw new axios.CanceledError('Session changed in another tab. Please sign in again.');
  }
  // Wait for an already-needed refresh before signing the request. Previously
  // the request went out with the old JWT while refresh ran in parallel, so a
  // normal page load could fail even though the refresh succeeded moments
  // later. The refresh request itself must bypass this wait to avoid a cycle.
  if (!isAssessmentTokenRequest
    && !isPublicNoAuthRequest
    && !isAuthEndpoint(url)
    && !isRefreshRequest) {
    await ensureFreshAccessToken();
  }
  // The refresh await above yields to the event loop. Re-check immediately
  // before reading the shared token so a concurrent account switch cannot
  // attach its token to a request created by this tab's previous account.
  const session = isProtectedSessionRequest ? getCurrentSessionSnapshot() : null;
  const token = session?.boundary === requestSessionBoundary ? session.token : null;
  if (isAssessmentTokenRequest || isPublicNoAuthRequest) {
    // AxiosHeaders exposes delete(); the object fallback keeps this safe if a
    // test adapter or future caller supplies plain headers. Removing both
    // casings also protects candidate calls from a shared default header.
    if (typeof config.headers?.delete === 'function') {
      config.headers.delete('Authorization');
    } else if (config.headers) {
      delete config.headers.Authorization;
      delete config.headers.authorization;
    }
  } else if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  // The marker-scoped credential record is authoritative. Verify ownership
  // once more so a transition concurrent with this read cancels the request.
  if (isProtectedSessionRequest) {
    if (!isSessionBoundaryCurrent(requestSessionBoundary)) {
      throw new axios.CanceledError('Session changed in another tab. Please sign in again.');
    }
    config.taaliSessionBoundary = requestSessionBoundary;
  }
  return config;
});

api.interceptors.response.use(
  (response) => {
    const requestSessionBoundary = response.config?.taaliSessionBoundary;
    if (requestSessionBoundary && !isSessionBoundaryCurrent(requestSessionBoundary)) {
      return Promise.reject(new axios.CanceledError(
        'Session changed in another tab. Please sign in again.',
        response.config,
        response.request,
      ));
    }
    return response;
  },
  (error) => {
    const status = Number(error.response?.status || 0);
    const url = String(error.config?.url || '');
    const isAssessmentTokenRequest = usesAssessmentTokenAuth(error.config);
    const isPublicNoAuthRequest = usesPublicNoAuth(error.config);
    const requestSessionBoundary = error.config?.taaliSessionBoundary;
    if (requestSessionBoundary && !isSessionBoundaryCurrent(requestSessionBoundary)) {
      return Promise.reject(new axios.CanceledError(
        'Session changed in another tab. Please sign in again.',
        error.config,
        error.request,
      ));
    }
    // A 401 from a request signed with a token that is no longer the active
    // one (stale in-flight call racing a logout + re-login) must not clear
    // the CURRENT session.
    const currentToken = getCurrentSessionSnapshot()?.token || null;
    const sentAuth = String(error.config?.headers?.Authorization || '');
    const sentToken = sentAuth.startsWith('Bearer ') ? sentAuth.slice(7) : null;
    const isStaleSessionRequest = Boolean(currentToken)
      && sentAuth !== `Bearer ${currentToken}`;
    if (status === 401
      && !isAssessmentTokenRequest
      && !isPublicNoAuthRequest
      && !isAuthEndpoint(url)
      && !isStaleSessionRequest) {
      revokeSessionBoundary(requestSessionBoundary, { expectedToken: sentToken });
      return Promise.reject(new axios.CanceledError(
        'Your session expired. Please sign in again.',
        error.config,
        error.request,
      ));
    }
    return Promise.reject(error);
  }
);

export default api;
