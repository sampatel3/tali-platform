import api from './httpClient';

export const assessments = {
  list: (params = {}) => api.get('/assessments/', { params }),
  get: (id) => api.get(`/assessments/${id}`),
  create: (data) => api.post('/assessments/', data),
  startDemo: (data) => api.post('/assessments/demo/start', data),
  requestDemo: (data) => api.post('/assessments/demo/request', data),
  preview: (token) => api.get(`/assessments/token/${token}/preview`),
  start: (token, data = {}) => api.post(`/assessments/token/${token}/start`, data),
  execute: (id, payload, assessmentToken) =>
    api.post(`/assessments/${id}/execute`, typeof payload === 'string' ? { code: payload } : payload, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  saveRepoFile: (id, payload, assessmentToken) =>
    api.post(`/assessments/${id}/repo-file`, payload, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  // HTTP-based agentic Claude chat — the only candidate-facing assistant
  // transport (the legacy PTY terminal + non-tool `claude` helper were
  // removed alongside their backend routes).
  claudeChat: (assessmentId, payload, assessmentToken) =>
    api.post(`/assessments/${assessmentId}/claude/chat`, payload, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  submit: (id, payloadOrFinalCode, assessmentToken, metadata = {}) =>
    api.post(
      `/assessments/${id}/submit`,
      typeof payloadOrFinalCode === 'string'
        ? { final_code: payloadOrFinalCode, ...metadata }
        : { ...(payloadOrFinalCode || {}), ...metadata },
      {
      headers: { 'X-Assessment-Token': assessmentToken },
      },
    ),
  remove: (id) => api.delete(`/assessments/${id}`),
  resend: (id) => api.post(`/assessments/${id}/resend`),
  downloadReport: (id) => api.get(`/assessments/${id}/report.pdf`, { responseType: 'blob' }),
  generateInterviewDebrief: (id, data = {}) => api.post(`/assessments/${id}/interview-debrief`, data),
  updateManualEvaluation: (id, data) => api.patch(`/assessments/${id}/manual-evaluation`, data),
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
