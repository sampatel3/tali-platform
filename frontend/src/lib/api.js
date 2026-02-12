import axios from 'axios';

const API_URL = (import.meta.env.VITE_API_URL || '').replace(/[\r\n\s]+/g, '').trim();

const api = axios.create({
  baseURL: `${API_URL}/api/v1`,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor: attach JWT token
api.interceptors.request.use((config) => {
  const url = config.url || '';
  const token = localStorage.getItem('tali_access_token');
  const isCandidateTokenEndpoint = url.includes('/assessments/token/') && url.includes('/start');
  if (token && !isCandidateTokenEndpoint) {
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
  verifyEmail: (token) => api.get('/auth/verify-email', { params: { token } }),
  resendVerification: (email) => api.post('/auth/resend-verification', { email }),
  forgotPassword: (email) => api.post('/auth/forgot-password', { email }),
  resetPassword: (token, new_password) => api.post('/auth/reset-password', { token, new_password }),
};

// ---- Assessments ----
export const assessments = {
  list: (params = {}) => api.get('/assessments/', { params }),
  get: (id) => api.get(`/assessments/${id}`),
  create: (data) => api.post('/assessments/', data),
  start: (token) => api.post(`/assessments/token/${token}/start`),
  execute: (id, code, assessmentToken) =>
    api.post(`/assessments/${id}/execute`, { code }, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  claude: (id, message, conversationHistory, assessmentToken, metadata = {}) =>
    api.post(`/assessments/${id}/claude`, {
      message,
      conversation_history: conversationHistory,
      ...metadata,
    }, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  submit: (id, finalCode, assessmentToken, metadata = {}) =>
    api.post(`/assessments/${id}/submit`, { final_code: finalCode, ...metadata }, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  remove: (id) => api.delete(`/assessments/${id}`),
  resend: (id) => api.post(`/assessments/${id}/resend`),
  postToWorkable: (id) => api.post(`/assessments/${id}/post-to-workable`),
  downloadReport: (id) => api.get(`/assessments/${id}/report.pdf`, { responseType: 'blob' }),
  addNote: (id, note) => api.post(`/assessments/${id}/notes`, { note }),
  uploadCv: (assessmentId, token, file) => {
    const form = new FormData();
    form.append('file', file);
    const url = assessmentId
      ? `/assessments/${assessmentId}/upload-cv`
      : `/assessments/token/${token}/upload-cv`;
    if (assessmentId) {
      form.append('token', token);
    }
    return api.post(url, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
};

// ---- Billing ----
export const billing = {
  usage: () => api.get('/billing/usage'),
  createCheckoutSession: (data) => api.post('/billing/checkout-session', data),
};

// ---- Organizations ----
export const organizations = {
  get: () => api.get('/organizations/me'),
  update: (data) => api.patch('/organizations/me', data),
  getWorkableAuthorizeUrl: () => api.get('/organizations/workable/authorize-url'),
  connectWorkable: (code) =>
    api.post('/organizations/workable/connect', { code }),
};

// ---- Analytics ----
export const analytics = {
  get: () => api.get('/analytics/'),
};

// ---- Tasks ----
export const tasks = {
  list: () => api.get('/tasks/'),
  get: (id) => api.get(`/tasks/${id}`),
  create: (data) => api.post('/tasks/', data),
  update: (id, data) => api.patch(`/tasks/${id}`, data),
  delete: (id) => api.delete(`/tasks/${id}`),
  generate: (data) => api.post('/tasks/generate/', data),
};

// ---- Candidates ----
export const candidates = {
  list: (params = {}) => api.get('/candidates/', { params }),
  get: (id) => api.get(`/candidates/${id}`),
  create: (data) => api.post('/candidates/', data),
  update: (id, data) => api.patch(`/candidates/${id}`, data),
  remove: (id) => api.delete(`/candidates/${id}`),
  uploadCv: (candidateId, file) => {
    const form = new FormData();
    form.append('file', file);
    return api.post(`/candidates/${candidateId}/upload-cv`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  uploadJobSpec: (candidateId, file) => {
    const form = new FormData();
    form.append('file', file);
    return api.post(`/candidates/${candidateId}/upload-job-spec`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
};

// ---- Team / Users ----
export const team = {
  list: () => api.get('/users/'),
  invite: (data) => api.post('/users/invite', data),
};

export default api;
