import api from './httpClient';

export const organizations = {
  getMe: () => api.get('/organizations/me'),
  get: () => api.get('/organizations/me'),
  update: (data) => api.patch('/organizations/me', data),
  getWorkableAuthUrl: (options = {}) => organizations.getWorkableAuthorizeUrl(options),
  getWorkableAuthorizeUrl: (options = {}) => {
    const scopes = Array.isArray(options.scopes) ? options.scopes : [];
    const params = scopes.length ? { scopes: scopes.join(',') } : undefined;
    return api.get('/organizations/workable/authorize-url', { params });
  },
  connectWorkable: (code) => api.post('/organizations/workable/connect', { code }),
  connectWorkableToken: ({ access_token, subdomain, read_only = true }) =>
    api.post('/organizations/workable/connect-token', { access_token, subdomain, read_only }),
  disconnectWorkable: () => api.delete('/organizations/workable'),
  getWorkableSyncJobs: () => api.get('/workable/sync/jobs'),
  triggerWorkableSync: (data = {}) => organizations.syncWorkable(data),
  syncWorkable: (data = {}) => api.post('/workable/sync', { mode: 'metadata', ...data }),
  getWorkableStatus: (runId = null) => organizations.getWorkableSyncStatus(runId),
  getWorkableSyncStatus: (runId = null) => api.get('/workable/sync/status', {
    params: runId != null ? { run_id: runId } : undefined,
  }),
  cancelWorkableSync: (runId = null) => api.post('/workable/sync/cancel', runId != null ? { run_id: runId } : {}),
  clearWorkableData: () => api.post('/workable/clear'),
};
