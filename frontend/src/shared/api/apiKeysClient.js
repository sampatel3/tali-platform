import api from './httpClient';

// Public-API key management (Settings → Developers). Backed by the
// JWT-authed /api/v1/api-keys endpoints; the plaintext secret is returned
// once, on create.
export const apiKeys = {
  list: () => api.get('/api-keys'),
  create: (data) => api.post('/api-keys', data),
  revoke: (id) => api.delete(`/api-keys/${id}`),
};
