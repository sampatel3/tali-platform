import api from './httpClient';

// Compliance admin: GDPR data-subject requests + the aggregate EEO report.
// Owner-gated server-side (require_org_owner) — a non-owner gets 403, which the
// Compliance tab surfaces as a friendly note rather than data.
export const compliance = {
  listRequests: () => api.get('/compliance/data-requests').then((r) => r.data),
  createRequest: (payload) => api.post('/compliance/data-requests', payload).then((r) => r.data),
  fulfillRequest: (id) => api.post(`/compliance/data-requests/${id}/fulfill`).then((r) => r.data),
  rejectRequest: (id, reason) =>
    api.post(`/compliance/data-requests/${id}/reject`, { reason }).then((r) => r.data),
  // Aggregate-only, small-cell-suppressed (cells below the k-anonymity threshold
  // arrive as the string "<5"). Never per-candidate.
  eeoReport: (params = {}) => api.get('/compliance/eeo-report', { params }).then((r) => r.data),
};
