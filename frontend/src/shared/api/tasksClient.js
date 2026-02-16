import api from './httpClient';

export const tasks = {
  list: () => api.get('/tasks/'),
  get: (id) => api.get(`/tasks/${id}`),
};
