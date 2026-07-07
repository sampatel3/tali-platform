import api from './httpClient';

// P3 interview scorecards, per application.
export const scorecards = {
  list: (applicationId) => api.get(`/applications/${applicationId}/scorecards`).then((r) => r.data),
  summary: (applicationId) => api.get(`/applications/${applicationId}/scorecards/summary`).then((r) => r.data),
  upsert: (applicationId, payload) => api.post(`/applications/${applicationId}/scorecards`, payload).then((r) => r.data),
  submit: (scorecardId) => api.post(`/scorecards/${scorecardId}/submit`).then((r) => r.data),
  remove: (scorecardId) => api.delete(`/scorecards/${scorecardId}`),
};
