import axios from 'axios';

export const API_URL = (import.meta.env.VITE_API_URL || '').replace(/[\r\n\s]+/g, '').trim();

const api = axios.create({
  baseURL: `${API_URL}/api/v1`,
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

const isAuthEndpoint = (url = '') => (
  url.includes('/auth/jwt/login')
  || url.includes('/auth/register')
  || url.includes('/auth/forgot-password')
  || url.includes('/auth/reset-password')
  || url.includes('/auth/verify')
  || url.includes('/auth/request-verify-token')
  || url.includes('/auth/sso-')
);

const isPublicPath = (pathname = '', search = '') => {
  if (pathname === '/'
    || pathname.startsWith('/login')
    || pathname.startsWith('/register')
    || pathname.startsWith('/forgot-password')
    || pathname.startsWith('/reset-password')
    || pathname.startsWith('/verify-email')
    || pathname.startsWith('/demo')
    || pathname.startsWith('/c/')
    || pathname.startsWith('/share/')
    || pathname.startsWith('/report/')
    || pathname.startsWith('/assess/')
    || pathname.startsWith('/assessment/')
    || pathname.startsWith('/careers/')
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
