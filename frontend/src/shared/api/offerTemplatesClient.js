import api from './httpClient';

// P2 reusable offer templates (admin/recruiter).
export const offerTemplates = {
  list: (includeInactive = false) => api.get('/offer-templates', { params: { include_inactive: includeInactive } }).then((r) => r.data),
  create: (payload) => api.post('/offer-templates', payload).then((r) => r.data),
  update: (id, payload) => api.patch(`/offer-templates/${id}`, payload).then((r) => r.data),
  remove: (id) => api.delete(`/offer-templates/${id}`),
};
