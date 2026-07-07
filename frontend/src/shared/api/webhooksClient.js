import api from './httpClient';

// P4 outbound webhooks (admin/recruiter).
export const webhooks = {
  list: () => api.get('/webhooks').then((r) => r.data),
  create: (payload) => api.post('/webhooks', payload).then((r) => r.data),
  update: (id, payload) => api.patch(`/webhooks/${id}`, payload).then((r) => r.data),
  remove: (id) => api.delete(`/webhooks/${id}`),
  deliveries: (id) => api.get(`/webhooks/${id}/deliveries`).then((r) => r.data),
};
