import axios from 'axios';

const API_URL = (import.meta.env.VITE_API_URL || '').replace(/[\r\n\s]+/g, '').trim();

const api = axios.create({
  baseURL: `${API_URL}/api/v1`,
  headers: {
    'Content-Type': 'application/json',
  },
});

const isAuthEndpoint = (url = '') => (
  url.includes('/auth/jwt/login')
  || url.includes('/auth/register')
  || url.includes('/auth/forgot-password')
  || url.includes('/auth/reset-password')
  || url.includes('/auth/verify')
  || url.includes('/auth/request-verify-token')
  || url.includes('/auth/sso-')
);

const isPublicPath = (pathname = '') => (
  pathname === '/'
  || pathname.startsWith('/login')
  || pathname.startsWith('/register')
  || pathname.startsWith('/forgot-password')
  || pathname.startsWith('/reset-password')
  || pathname.startsWith('/verify-email')
  || pathname.startsWith('/demo')
  || pathname.startsWith('/c/')
  || (/^\/candidates\/shr_[^/]+$/.test(pathname))
  || pathname.startsWith('/assess/')
  || pathname.startsWith('/assessment/')
);

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
      if (typeof window !== 'undefined' && !isPublicPath(window.location.pathname)) {
        window.location.replace(buildLoginRedirectPath());
      }
    }
    return Promise.reject(error);
  }
);

export default api;
