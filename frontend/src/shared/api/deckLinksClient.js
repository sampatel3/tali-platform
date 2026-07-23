import api from './httpClient';

// Per-prospect sales-deck links (internal, owner-gated). Backed by the
// JWT-authed /api/v1/deck-links endpoints. The link URL is returned on create
// and on every list, so the UI never has to build it from the token.
export const deckLinks = {
  list: () => api.get('/deck-links'),
  create: (data) => api.post('/deck-links', data),
  revoke: (id) => api.delete(`/deck-links/${id}`),
};
