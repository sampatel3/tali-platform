import api from './httpClient';

// P5 compliance: GDPR data-subject requests + EEO aggregate (admin only).
export const compliance = {
  listRequests: () => api.get('/compliance/data-requests').then((r) => r.data),
  createRequest: (payload) => api.post('/compliance/data-requests', payload).then((r) => r.data),
  fulfillRequest: (id) => api.post(`/compliance/data-requests/${id}/fulfill`).then((r) => r.data),
  rejectRequest: (id, reason) => api.post(`/compliance/data-requests/${id}/reject`, { reason }).then((r) => r.data),
  eeoReport: (params = {}) => api.get('/compliance/eeo-report', { params }).then((r) => r.data),
};
