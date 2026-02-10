import axios from 'axios';

const API_URL = import.meta.env.VITE_API_URL || '';

const api = axios.create({
  baseURL: `${API_URL}/api/v1`,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor: attach JWT token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('tali_access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor: handle 401
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('tali_access_token');
      localStorage.removeItem('tali_user');
      window.dispatchEvent(new Event('auth:logout'));
    }
    return Promise.reject(error);
  }
);

// ---- Auth ----
export const auth = {
  login: (email, password) => {
    const formData = new URLSearchParams();
    formData.append('username', email);
    formData.append('password', password);
    return api.post('/auth/login', formData, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
  },
  register: (data) => api.post('/auth/register', data),
  me: () => api.get('/auth/me'),
};

// ---- Assessments ----
export const assessments = {
  list: () => api.get('/assessments'),
  get: (id) => api.get(`/assessments/${id}`),
  create: (data) => api.post('/assessments', data),
  start: (token) => api.post(`/assessments/token/${token}/start`),
  execute: (id, code, assessmentToken) =>
    api.post(`/assessments/${id}/execute`, { code }, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  claude: (id, message, conversationHistory, assessmentToken) =>
    api.post(`/assessments/${id}/claude`, {
      message,
      conversation_history: conversationHistory,
    }, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  submit: (id, finalCode, assessmentToken) =>
    api.post(`/assessments/${id}/submit`, { final_code: finalCode }, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
};

// ---- Organizations ----
export const organizations = {
  get: () => api.get('/organizations/me'),
  update: (data) => api.patch('/organizations/me', data),
  connectWorkable: (code) =>
    api.post('/organizations/workable/connect', { code }),
};

// ---- Tasks ----
export const tasks = {
  list: () => api.get('/tasks'),
  get: (id) => api.get(`/tasks/${id}`),
  create: (data) => api.post('/tasks', data),
  generate: (data) => api.post('/tasks/generate', data),
};

export default api;
