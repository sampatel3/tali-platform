import axios from 'axios';
import { isPublicPath } from '../../app/routePolicy';

export { isPublicPath } from '../../app/routePolicy';

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

// Public requests deliberately use a separate instance so a recruiter's JWT
// can never leak onto a shared link, job page, intake, or unsubscribe request.
// Keep the same finite timeout as authenticated traffic: bare axios has no
// timeout and could otherwise leave a public screen spinning indefinitely.
export const publicApi = axios.create({ timeout: 60000 });

// Public unauth share-link endpoint lives at the app root (no /api/v1
// prefix) so recipients can open it without a recruiter session. The
// SPA's /share/:token route uses this to fetch the application payload
// in one round-trip — the backend scrubs to client-view shape when
// the share link's mode is "client".
export const viewShareLink = (token) =>
  publicApi.get(`${API_URL}/share/${encodeURIComponent(token)}`);

// Public unauth "top candidates report" — same pattern as the share link.
export const viewTopReport = (token) =>
  publicApi.get(`${API_URL}/report/${encodeURIComponent(token)}`);

// Public unauth curated client submittal pack — same pattern: a role-scoped
// shortlist frozen at mint time, served read-only by token. Bare axios so the
// recruiter's JWT is never attached — anyone with the link can read it.
export const viewSubmittalPack = (token) =>
  publicApi.get(`${API_URL}/submittal/${encodeURIComponent(token)}`);

// Public unauth job posting — the careers-style page a published requisition
// links to. Lives UNDER /api/v1 (unlike the share/report endpoints), but we
// still use a bare axios.get rather than the shared `api` instance so the
// recruiter's JWT is never attached: anyone with the link can read it.
export const viewPublicJob = (token) =>
  publicApi.get(`${API_URL}/api/v1/public/job/${encodeURIComponent(token)}`);

// Public unauth CAREERS BOARD — an org's per-org page listing all of its
// published jobs. Same JWT-free pattern as viewPublicJob (bare axios so the
// recruiter's token is never attached — anyone with the link can read it).
// Returns `{ organization_name, slug, jobs: [ { token, url, title, location,
//   workplace_type, employment_type, seniority, salary, published_at } ] }`.
export const viewCareers = (slug, params = {}) =>
  publicApi.get(`${API_URL}/api/v1/public/careers/${encodeURIComponent(slug)}`, { params });

// Public unauth NATIVE APPLY — a candidate submits an application to a published
// job page. Multipart (name/email/phone + a JSON `answers` field + optional
// resume file). Bare axios (JWT-free); the browser sets the multipart boundary.
// Returns `{ status, message, application_id, eeo_token }`.
export const applyToJob = (token, formData) =>
  publicApi.post(
    `${API_URL}/api/v1/public/job-pages/${encodeURIComponent(token)}/apply`,
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );

// Public unauth VOLUNTARY EEO self-ID — keyed by the opaque `eeo_token` the
// apply response carried (never a raw application_id). Bare axios (JWT-free).
// Returns 204 (no body).
export const submitJobEeo = (token, payload) =>
  publicApi.post(`${API_URL}/api/v1/public/eeo/${encodeURIComponent(token)}`, payload);

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
export const viewClientIntake = (token) => publicApi.get(intakeBase(token));

// One conversational turn from the client — `message` text plus optional File
// attachments — as multipart/form-data (mirrors requisitionApi.chat). Returns
// `{ reply, messages, captured, gaps, suggested_replies }`.
export const sendClientIntakeChat = (token, { message = '', files = [] } = {}) => {
  const form = new FormData();
  form.append('message', message ?? '');
  (files || []).forEach((file) => {
    if (file) form.append('files', file);
  });
  return publicApi.post(`${intakeBase(token)}/chat`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

// Submit the captured brief back to the consultancy. Returns `{ ok, status }`.
export const submitClientIntake = (token) =>
  publicApi.post(`${intakeBase(token)}/submit`);

// ---- Public, no-auth one-click UNSUBSCRIBE ----
//
// The outreach opt-out embedded in each campaign email. Bare axios (JWT-free)
// like the other public endpoints — recipients have no recruiter session. GET
// validates the token + returns `{ organization_name, email_masked }` and does
// NOT write (prefetchers follow GET links); POST records the suppression.
const unsubscribeBase = (token) =>
  `${API_URL}/api/v1/public/unsubscribe/${encodeURIComponent(token)}`;

export const fetchUnsubscribe = (token) => publicApi.get(unsubscribeBase(token));
export const submitUnsubscribe = (token) => publicApi.post(unsubscribeBase(token));

// ---- Sliding session ----
// Access tokens expire after 30 minutes. Rather than silently logging active
// users out mid-work (the old behavior), we note when the current token was
// issued and swap it for a fresh one via POST /auth/jwt/refresh once it's
// REFRESH_TOKEN_AFTER_MS old — triggered by any API activity and by a
// visible-tab heartbeat. Idle sessions still expire with the last token.
export const REFRESH_TOKEN_AFTER_MS = 10 * 60 * 1000;

const TOKEN_KEY = 'taali_access_token';
const TOKEN_ISSUED_AT_KEY = 'taali_token_issued_at';

export const setAccessToken = (token) => {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(TOKEN_ISSUED_AT_KEY, String(Date.now()));
};

export const clearAccessToken = () => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(TOKEN_ISSUED_AT_KEY);
};

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

const maybeRefreshToken = () => {
  if (refreshInFlight) return;
  if (!isUserActive(lastUserActivityAt)) return;
  const tokenAtStart = localStorage.getItem(TOKEN_KEY);
  if (!tokenAtStart) return;
  if (!shouldRefreshToken(localStorage.getItem(TOKEN_ISSUED_AT_KEY))) return;
  refreshInFlight = api
    .post('/auth/jwt/refresh')
    .then(({ data }) => {
      // The session may have changed while this was in flight (logout, or
      // logout + login as someone else) — only store the result if the token
      // we refreshed is still the active one.
      if (data?.access_token && localStorage.getItem(TOKEN_KEY) === tokenAtStart) {
        setAccessToken(data.access_token);
      }
    })
    // A 401 here means the token is already dead — the response interceptor
    // below handles the logout; any other failure just retries next trigger.
    .catch(() => {})
    .finally(() => {
      refreshInFlight = null;
    });
};

// Heartbeat so a user reading/typing without firing API calls stays signed in.
// Only ticks while the tab is visible; the user-activity gate inside
// maybeRefreshToken keeps an unattended tab from sliding forever.
// (Skipped under vitest — a live interval would keep test workers alive.)
if (typeof window !== 'undefined' && typeof document !== 'undefined' && import.meta.env?.MODE !== 'test') {
  setInterval(() => {
    if (document.visibilityState === 'visible') maybeRefreshToken();
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

// Exported for tests: the 401 interceptor must never bounce these
// marketing/public routes to /login (a stale token in localStorage plus a
// failed bootstrap call would otherwise hijack a public page).
const buildLoginRedirectPath = () => {
  if (typeof window === 'undefined') return '/login';
  const nextPath = `${window.location.pathname || '/'}${window.location.search || ''}${window.location.hash || ''}`;
  const encoded = encodeURIComponent(nextPath || '/');
  return `/login?next=${encoded}`;
};

// Attach auth token for recruiter-side endpoints.
api.interceptors.request.use((config) => {
  const url = config.url || '';
  const token = localStorage.getItem('taali_access_token');
  const isCandidateTokenEndpoint = url.includes('/assessments/token/') && url.includes('/start');
  if (token && !isCandidateTokenEndpoint) {
    config.headers.Authorization = `Bearer ${token}`;
    // Any authenticated activity slides the session forward (skip auth
    // endpoints and the refresh call itself to avoid loops).
    if (!isAuthEndpoint(url) && !url.includes('/auth/jwt/refresh')) {
      maybeRefreshToken();
    }
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = Number(error.response?.status || 0);
    const url = String(error.config?.url || '');
    // A 401 from a request signed with a token that is no longer the active
    // one (stale in-flight call racing a logout + re-login) must not clear
    // the CURRENT session.
    const currentToken = localStorage.getItem(TOKEN_KEY);
    const sentAuth = String(error.config?.headers?.Authorization || '');
    const isStaleSessionRequest = Boolean(currentToken) && sentAuth !== `Bearer ${currentToken}`;
    if (status === 401 && !isAuthEndpoint(url) && !isStaleSessionRequest) {
      clearAccessToken();
      localStorage.removeItem('taali_user');
      window.dispatchEvent(new Event('auth:logout'));
      if (typeof window !== 'undefined' && !isPublicPath(window.location.pathname, window.location.search)) {
        window.location.replace(buildLoginRedirectPath());
      }
    }
    return Promise.reject(error);
  }
);

export default api;
