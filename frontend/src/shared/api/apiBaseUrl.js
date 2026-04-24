const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1']);

export const DEFAULT_LOCAL_API_URL = 'http://localhost:8000';
export const DEFAULT_REMOTE_API_URL = 'https://resourceful-adaptation-production.up.railway.app';

const sanitizeUrl = (value) => String(value || '').replace(/[\r\n\s]+/g, '').trim().replace(/\/+$/, '');

export const resolveApiUrl = (locationLike = (typeof window !== 'undefined' ? window.location : null)) => {
  const configuredUrl = sanitizeUrl(import.meta.env.VITE_API_URL);
  if (configuredUrl) return configuredUrl;

  const hostname = String(locationLike?.hostname || '').toLowerCase();
  if (LOCAL_HOSTS.has(hostname)) {
    return DEFAULT_LOCAL_API_URL;
  }

  if (hostname.endsWith('.railway.app')) {
    return sanitizeUrl(locationLike?.origin);
  }

  return DEFAULT_REMOTE_API_URL;
};
