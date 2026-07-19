// Helpers for the conversation-management endpoints. The streaming /turn
// endpoint is hit directly with fetch from useChatStream — axios doesn't
// expose ReadableStream so we bypass it for that one call.
import api, { ensureFreshAccessToken } from '../../shared/api/httpClient';

export const conversationsApi = {
  list: () => api.get('/taali-chat/conversations').then((r) => r.data),
  get: (id) => api.get(`/taali-chat/conversations/${id}`).then((r) => r.data),
  rename: (id, title) =>
    api.patch(`/taali-chat/conversations/${id}`, { title }).then((r) => r.data),
  remove: (id) => api.delete(`/taali-chat/conversations/${id}`),
};

const API_URL = (import.meta.env.VITE_API_URL || '').replace(/[\r\n\s]+/g, '').trim();

export const turnUrl = () => `${API_URL}/api/v1/taali-chat/turn`;

export const authHeaders = () => {
  const token = localStorage.getItem('taali_access_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
};

export const freshAuthHeaders = async () => {
  await ensureFreshAccessToken();
  return authHeaders();
};
