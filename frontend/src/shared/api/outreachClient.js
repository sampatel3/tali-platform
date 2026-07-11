import api from './httpClient';

// Outreach campaigns — draft, approve, send, track. Org-scoped via the
// recruiter session. Generate + send are two-phase: call without `confirm` to
// get an estimate, then with `confirm: true` to enqueue (mirrors PoolRescore).
export const outreach = {
  listCampaigns: (roleId) =>
    api.get('/outreach/campaigns', { params: roleId ? { role_id: roleId } : {} }),
  getCampaign: (id) => api.get(`/outreach/campaigns/${id}`),
  createCampaign: (data) => api.post('/outreach/campaigns', data),
  patchCampaign: (id, data) => api.patch(`/outreach/campaigns/${id}`, data),
  archiveCampaign: (id) => api.post(`/outreach/campaigns/${id}/archive`),

  addAudience: (id, { prospect_ids = [], application_ids = [] }) =>
    api.post(`/outreach/campaigns/${id}/audience`, { prospect_ids, application_ids }),

  // confirm=false → { count, estimated_cost_usd }; confirm=true → enqueues.
  generate: (id, confirm = false) =>
    api.post(`/outreach/campaigns/${id}/generate`, { confirm }),

  editMessage: (id, mid, data) =>
    api.post(`/outreach/campaigns/${id}/messages/${mid}`, data),
  approve: (id, { message_ids = null, all_drafts = false }) =>
    api.post(`/outreach/campaigns/${id}/messages/approve`, { message_ids, all_drafts }),
  reject: (id, mid) => api.post(`/outreach/campaigns/${id}/messages/${mid}/reject`),

  // confirm=false → { approved_count }; confirm=true → enqueues send.
  send: (id, confirm = false) =>
    api.post(`/outreach/campaigns/${id}/send`, { confirm }),
};
