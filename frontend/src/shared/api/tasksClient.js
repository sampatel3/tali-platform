import api from './httpClient';

export const tasks = {
  list: () => api.get('/tasks/'),
  get: (id) => api.get(`/tasks/${id}`),
  rubric: (id) => api.get(`/tasks/${id}/rubric`),
  create: (data) => api.post('/tasks/', data),
  update: (id, data) => api.patch(`/tasks/${id}`, data),
  delete: (id) => api.delete(`/tasks/${id}`),
  remove: (id) => api.delete(`/tasks/${id}`),
  generateWithAi: (id, payload = {}) => api.post(`/tasks/${id}/generate`, payload),
  // Generated draft review (auto-provisioned tasks awaiting approval).
  drafts: () => api.get('/tasks/drafts'),
  approve: (id) => api.post(`/tasks/${id}/approve`, {}),
  reject: (id) => api.delete(`/tasks/${id}/reject`),
};
