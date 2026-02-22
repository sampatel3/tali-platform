import api from './httpClient';

const buildTerminalWsUrl = (assessmentId, assessmentToken) => {
  const rawApi = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '');
  const origin = rawApi || (typeof window !== 'undefined' ? window.location.origin : '');
  const wsOrigin = origin.replace(/^http:/i, 'ws:').replace(/^https:/i, 'wss:');
  const token = encodeURIComponent(assessmentToken || '');
  return `${wsOrigin}/api/v1/assessments/${assessmentId}/terminal/ws?token=${token}`;
};

export const assessments = {
  list: (params = {}) => api.get('/assessments/', { params }),
  get: (id) => api.get(`/assessments/${id}`),
  create: (data) => api.post('/assessments/', data),
  startDemo: (data) => api.post('/assessments/demo/start', data),
  preview: (token) => api.get(`/assessments/token/${token}/preview`),
  start: (token, data = {}) => api.post(`/assessments/token/${token}/start`, data),
  execute: (id, code, assessmentToken) =>
    api.post(`/assessments/${id}/execute`, { code }, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  terminalStatus: (id, assessmentToken) =>
    api.get(`/assessments/${id}/terminal/status`, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  terminalStop: (id, assessmentToken) =>
    api.post(`/assessments/${id}/terminal/stop`, {}, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  claude: (id, payload, assessmentToken) =>
    api.post(`/assessments/${id}/claude`, payload, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  terminalWsUrl: (id, assessmentToken) => buildTerminalWsUrl(id, assessmentToken),
  submit: (id, finalCode, assessmentToken, metadata = {}) =>
    api.post(`/assessments/${id}/submit`, { final_code: finalCode, ...metadata }, {
      headers: { 'X-Assessment-Token': assessmentToken },
    }),
  remove: (id) => api.delete(`/assessments/${id}`),
  resend: (id) => api.post(`/assessments/${id}/resend`),
  postToWorkable: (id) => api.post(`/assessments/${id}/post-to-workable`),
  downloadReport: (id) => api.get(`/assessments/${id}/report.pdf`, { responseType: 'blob' }),
  finalizeCandidateFeedback: (id, data = {}) => api.post(`/assessments/${id}/finalize-candidate-feedback`, data),
  getCandidateFeedback: (token) => api.get(`/assessments/${encodeURIComponent(token)}/feedback`),
  downloadCandidateFeedbackPdf: (token) =>
    api.get(`/assessments/${encodeURIComponent(token)}/feedback.pdf`, { responseType: 'blob' }),
  generateInterviewDebrief: (id, data = {}) => api.post(`/assessments/${id}/interview-debrief`, data),
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
