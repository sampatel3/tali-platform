// API client for the DecisionPolicy Hub.
import api from '../../shared/api/httpClient';

const BASE = '/admin/decision-policy';

export const decisionPolicyApi = {
  active: () => api.get(BASE).then((r) => r.data),
  pending: () => api.get(`${BASE}/pending`).then((r) => r.data),
  signals: (days = 30) => api.get(`${BASE}/signals?days=${days}`).then((r) => r.data),
  activate: (policyId) => api.post(`${BASE}/${policyId}/activate`).then((r) => r.data),
  discard: (policyId) => api.post(`${BASE}/${policyId}/discard`).then((r) => r.data),
};
