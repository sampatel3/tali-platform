// API client for CLIENTS — in a consultancy, requisitions belong to clients.
//
// Paths are relative to the httpClient baseURL (which already includes
// /api/v1), matching the other feature api.js modules (e.g. requisitions,
// decision_policy).
//
// A client owns zero-or-more requisitions; the recruiter assigns a client +
// a client rate to a requisition (via requisitionApi.update — see the
// requisitions module) and the backend computes the margin from the rate vs.
// the role's salary. The Clients view lists every client with a live
// open-jobs count.
import api from '../../shared/api/httpClient';

const BASE = '/clients';

export const clientApi = {
  // List the org's clients with a live open-jobs count.
  // → [{ id, name, contact_name, contact_email, status, open_job_count }]
  list: () => api.get(BASE).then((r) => r.data),

  // Create a client. Only `name` is required; contact fields are optional.
  // → the serialized client.
  create: ({ name, contact_name = null, contact_email = null } = {}) =>
    api
      .post(BASE, { name, contact_name, contact_email })
      .then((r) => r.data),

  // Load one client + its requisitions.
  // → { …client, requisitions: [{ id, title, status, completeness }] }
  get: (id) => api.get(`${BASE}/${id}`).then((r) => r.data),

  // Patch a client's fields (name / contact / status).
  update: (id, fields) => api.patch(`${BASE}/${id}`, fields).then((r) => r.data),
};

export default clientApi;
