import axios from 'axios';

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

const isAuthEndpoint = (url = '') => (
  url.includes('/auth/jwt/login')
  || url.includes('/auth/register')
  || url.includes('/auth/forgot-password')
  || url.includes('/auth/reset-password')
  || url.includes('/auth/verify')
  || url.includes('/auth/request-verify-token')
  || url.includes('/auth/sso-')
);

// Exported for tests: the 401 interceptor must never bounce these
// marketing/public routes to /login (a stale token in localStorage plus a
// failed bootstrap call would otherwise hijack a public page).
export const isPublicPath = (pathname = '', search = '') => {
  if (pathname === '/'
    || pathname.startsWith('/login')
    || pathname.startsWith('/register')
    || pathname.startsWith('/forgot-password')
    || pathname.startsWith('/reset-password')
    || pathname.startsWith('/verify-email')
    || pathname.startsWith('/demo')
    || pathname.startsWith('/blog')
    || pathname.startsWith('/developers')
    || pathname.startsWith('/c/')
    || pathname.startsWith('/share/')
    || pathname.startsWith('/report/')
    || pathname.startsWith('/assess/')
    || pathname.startsWith('/assessment/')
    || pathname.startsWith('/job/')
    || pathname.startsWith('/careers/')
    || pathname.startsWith('/intake/')
    || pathname === '/showcase'
    || pathname.startsWith('/showcase/')) {
    return true;
  }
  // Marketing showcase mode runs the recruiter pages with auth-bypassed
  // demo data. We must never bounce these to /login on a stray 401, since
  // they're loaded inside the public marketing iframe.
  if ((pathname === '/jobs' || pathname === '/candidates') && search.includes('showcase=1') && search.includes('demo=1')) {
    return true;
  }
  return false;
};

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
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = Number(error.response?.status || 0);
    const url = String(error.config?.url || '');
    if (status === 401 && !isAuthEndpoint(url)) {
      localStorage.removeItem('taali_access_token');
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
