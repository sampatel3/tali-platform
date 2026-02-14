import api from './httpClient';

export const tasks = {
  list: () => api.get('/tasks/'),
  get: (id) => api.get(`/tasks/${id}`),
  create: (data) => api.post('/tasks/', data),
  update: (id, data) => api.patch(`/tasks/${id}`, data),
  delete: (id) => api.delete(`/tasks/${id}`),
  generate: (data) => api.post('/tasks/generate/', data),
};
