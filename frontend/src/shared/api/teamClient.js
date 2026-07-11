import api from './httpClient';

export const team = {
  list: () => api.get('/users/'),
  invite: (data) => api.post('/users/invite', data),
  setRole: (userId, role) => api.patch(`/users/${userId}/role`, { role }),
};
