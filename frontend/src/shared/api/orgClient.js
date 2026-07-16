import api from './httpClient';

export const organizations = {
  get: () => api.get('/organizations/me'),
  update: (data) => api.patch('/organizations/me', data),
  getWorkableAuthorizeUrl: (options = {}) => {
    const scopes = Array.isArray(options.scopes) ? options.scopes : [];
    const params = scopes.length ? { scopes: scopes.join(',') } : undefined;
    return api.get('/organizations/workable/authorize-url', { params });
  },
  connectWorkable: (code, state) => api.post('/organizations/workable/connect', { code, state }),
  connectWorkableToken: ({ access_token, subdomain, read_only = true }) =>
    api.post('/organizations/workable/connect-token', { access_token, subdomain, read_only }),
  getWorkableSyncJobs: () => api.get('/workable/sync/jobs'),
  getWorkableMembers: (params = {}) => api.get('/workable/members', { params }),
  getWorkableDisqualificationReasons: () => api.get('/workable/disqualification-reasons'),
  getWorkableStages: (params = {}) => api.get('/workable/stages', { params }),
  syncWorkable: (data = {}) => api.post('/workable/sync', { mode: 'full', ...data }),
  getWorkableSyncStatus: (runId = null) => api.get('/workable/sync/status', {
    params: runId != null ? { run_id: runId } : undefined,
  }),
  cancelWorkableSync: (runId = null) => api.post('/workable/sync/cancel', runId != null ? { run_id: runId } : {}),
  clearWorkableData: () => api.post('/workable/clear'),
  // Bullhorn ATS integration (mirrors the Workable surface; staging-only until
  // the BULLHORN_ENABLED flag is on — every call 503s otherwise). The connect
  // body carries the API-user password ONE-TIME for the automated OAuth
  // exchange; the backend uses it in-memory only and never persists it.
  connectBullhorn: ({ username, client_id, client_secret, password }) =>
    api.post('/bullhorn/connect', { username, client_id, client_secret, password }),
  getBullhornStatus: () => api.get('/bullhorn/status'),
  syncBullhorn: (data = {}) => api.post('/bullhorn/sync', { mode: 'full', ...data }),
  getBullhornSyncStatus: () => api.get('/bullhorn/sync/status'),
  cancelBullhornSync: () => api.post('/bullhorn/sync/cancel', {}),
  getBullhornStageMap: () => api.get('/bullhorn/stage-map'),
  replaceBullhornStageMap: (mappings) => api.put('/bullhorn/stage-map', { mappings }),
  // Workspace criteria — Settings → AI agent chip composer.
  listCriteria: () => api.get('/organizations/me/criteria'),
  createCriterion: (data) => api.post('/organizations/me/criteria', data),
  updateCriterion: (id, data) => api.patch(`/organizations/me/criteria/${id}`, data),
  deleteCriterion: (id) => api.delete(`/organizations/me/criteria/${id}`),
};
