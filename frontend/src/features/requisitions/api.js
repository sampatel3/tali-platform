// API client for the AI-native Requisition (hiring brief) flow.
// Paths are relative to the httpClient baseURL (which already includes /api/v1),
// matching the other feature api.js modules (e.g. decision_policy).
import api from '../../shared/api/httpClient';

const BASE = '/requisitions';

export const requisitionApi = {
  list: () => api.get(BASE).then((r) => r.data),
  create: (sourceKind = null) =>
    api.post(BASE, { source_kind: sourceKind }).then((r) => r.data),
  get: (id) => api.get(`${BASE}/${id}`).then((r) => r.data),
  // Run the intake agent over pasted notes / a transcript / a JD. Fills the brief.
  runIntake: (id, input, sourceKind = null) =>
    api
      .post(`${BASE}/${id}/intake`, { input, source_kind: sourceKind })
      .then((r) => r.data),
  update: (id, fields) => api.patch(`${BASE}/${id}`, fields).then((r) => r.data),
  submit: (id) => api.post(`${BASE}/${id}/submit`).then((r) => r.data),
  publish: (id) => api.post(`${BASE}/${id}/publish`).then((r) => r.data),
};

export default requisitionApi;
