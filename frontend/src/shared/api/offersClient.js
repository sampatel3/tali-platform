import api from './httpClient';

// Offers, per application (+ HRIS / e-sign handoff shapes). Each call resolves
// to the response body so callers work with plain data.
export const offers = {
  listForApplication: (applicationId) =>
    api.get(`/applications/${applicationId}/offers`).then((r) => r.data),
  create: (applicationId, payload) =>
    api.post(`/applications/${applicationId}/offers`, payload).then((r) => r.data),
  get: (offerId) => api.get(`/offers/${offerId}`).then((r) => r.data),
  transition: (offerId, status) =>
    api.post(`/offers/${offerId}/transition`, { status }).then((r) => r.data),
  hrisExport: (offerId) => api.get(`/offers/${offerId}/hris-export`).then((r) => r.data),
  esignRequest: (offerId) => api.get(`/offers/${offerId}/esign-request`).then((r) => r.data),
};

export default offers;
