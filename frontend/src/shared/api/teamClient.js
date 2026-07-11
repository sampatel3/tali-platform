import api from './httpClient';

export const team = {
  list: () => api.get('/users/'),
  invite: (data) => api.post('/users/invite', data),
  resendInvite: (id) => api.post(`/users/${id}/resend-invite`),
  remove: (id) => api.delete(`/users/${id}`),
  setRole: (userId, role) => api.patch(`/users/${userId}/role`, { role }),
};
