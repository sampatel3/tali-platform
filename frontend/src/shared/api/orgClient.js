import api from './httpClient';

export const organizations = {
  get: () => api.get('/organizations/me'),
  update: (data) => api.patch('/organizations/me', data),
  getWorkableAuthorizeUrl: () => api.get('/organizations/workable/authorize-url'),
  connectWorkable: (code) => api.post('/organizations/workable/connect', { code }),
  connectWorkableToken: ({ access_token, subdomain, read_only = true }) =>
    api.post('/organizations/workable/connect-token', { access_token, subdomain, read_only }),
  syncWorkable: (data = {}) => api.post('/workable/sync', data),
  getWorkableSyncStatus: () => api.get('/workable/sync/status'),
};
