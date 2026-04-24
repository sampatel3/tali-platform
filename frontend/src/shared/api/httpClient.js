import axios from 'axios';
import { resolveApiUrl } from './apiBaseUrl';

const API_URL = resolveApiUrl();

const api = axios.create({
  baseURL: `${API_URL}/api/v1`,
  headers: {
    'Content-Type': 'application/json',
  },
});

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
    if (error.response?.status === 401) {
      localStorage.removeItem('taali_access_token');
      localStorage.removeItem('taali_user');
      window.dispatchEvent(new Event('auth:logout'));
    }
    return Promise.reject(error);
  }
);

export default api;
