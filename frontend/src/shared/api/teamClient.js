import api from './httpClient';

export const team = {
  list: () => api.get('/users/'),
  invite: (data) => api.post('/users/invite', data),
  resendInvite: (id) => api.post(`/users/${id}/resend-invite`),
  inviteLink: (id) => api.post(`/users/${id}/invite-link`),
  remove: (id) => api.delete(`/users/${id}`),
};
