import api from './httpClient';

export const organizations = {
  get: () => api.get('/organizations/me'),
  update: (data) => api.patch('/organizations/me', data),
  getWorkableAuthorizeUrl: (options = {}) => {
    const scopes = Array.isArray(options.scopes) ? options.scopes : [];
    const params = scopes.length ? { scopes: scopes.join(',') } : undefined;
    return api.get('/organizations/workable/authorize-url', { params });
  },
  connectWorkable: (code) => api.post('/organizations/workable/connect', { code }),
  connectWorkableToken: ({ access_token, subdomain, read_only = true }) =>
    api.post('/organizations/workable/connect-token', { access_token, subdomain, read_only }),
  getWorkableSyncJobs: () => api.get('/workable/sync/jobs'),
  syncWorkable: (data = {}) => api.post('/workable/sync', { mode: 'metadata', ...data }),
  getWorkableSyncStatus: (runId = null) => api.get('/workable/sync/status', {
    params: runId != null ? { run_id: runId } : undefined,
  }),
  cancelWorkableSync: (runId = null) => api.post('/workable/sync/cancel', runId != null ? { run_id: runId } : {}),
  clearWorkableData: () => api.post('/workable/clear'),
};
