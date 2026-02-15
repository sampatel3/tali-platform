import api from './httpClient';

export const organizations = {
  get: () => api.get('/organizations/me'),
  update: (data) => api.patch('/organizations/me', data),
  getWorkableAuthorizeUrl: () => api.get('/organizations/workable/authorize-url'),
  connectWorkable: (code) => api.post('/organizations/workable/connect', { code }),
  syncWorkable: (data = {}) => api.post('/workable/sync', data),
  getWorkableSyncStatus: () => api.get('/workable/sync/status'),
};
