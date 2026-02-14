import api from './httpClient';

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
  claudeRetry: (id, assessmentToken) =>
    api.post(`/assessments/${id}/claude/retry`, {}, {
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
  aiEvalSuggestions: (id) => api.post(`/assessments/${id}/ai-eval-suggestions`),
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
