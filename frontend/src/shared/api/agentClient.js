import api from './httpClient';

export const agent = {
  // Decisions queue
  listDecisions: (params = {}) => api.get('/agent-decisions', { params }),
  approveDecision: (decisionId, body = {}) => api.post(`/agent-decisions/${decisionId}/approve`, body),
  overrideDecision: (decisionId, body = {}) => api.post(`/agent-decisions/${decisionId}/override`, body),
  discardPending: (roleId) => api.post('/agent-decisions/discard', { role_id: roleId }),

  // Run log
  listRuns: (params = {}) => api.get('/agent-runs', { params }),

  // Manual trigger
  runNow: (roleId, body = {}) => api.post(`/roles/${roleId}/agent/run-now`, body),

  // Consolidated bar payload (poll target)
  status: (roleId) => api.get(`/roles/${roleId}/agent/status`),
};
