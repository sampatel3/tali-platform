// API client for the AI-native Requisition (hiring brief) flow.
//
// Paths are relative to the httpClient baseURL (which already includes
// /api/v1), matching the other feature api.js modules (e.g. decision_policy).
//
// The redesigned flow is conversational: `create()` returns a serialized
// brief that ALREADY carries an opening assistant message, and `chat()`
// drives every subsequent turn (text + attachments) as multipart/form-data.
// A live brief — rendered from the org's requisition spec template — fills in
// beside the conversation as the agent extracts fields.
import api, { viewPublicJob } from '../../shared/api/httpClient';

const BASE = '/requisitions';

export const requisitionApi = {
  // List the org's requisitions (title, status, completeness, …).
  list: () => api.get(BASE).then((r) => r.data),

  // Start a new requisition. The backend seeds the brief with an opening
  // assistant message, so the conversation is never empty on first render.
  create: (sourceKind = null) =>
    api.post(BASE, { source_kind: sourceKind }).then((r) => r.data),

  // Load one requisition's serialized brief (incl. messages, gaps,
  // completeness, custom_fields).
  get: (id) => api.get(`${BASE}/${id}`).then((r) => r.data),

  // One conversational turn. `message` is the recruiter's text (may be empty
  // when only attachments are sent); `files` is a list of File objects —
  // transcripts (.txt/.vtt/.srt/.md/.pdf) and/or screenshots of a JD.
  //
  // Sent as multipart/form-data: we build the FormData and let the browser
  // set the Content-Type boundary (matching candidatesClient.createWithCv —
  // axios is told `multipart/form-data` and fills the boundary itself).
  // Returns `{ brief, reply, messages, gaps }`.
  chat: (id, { message = '', files = [] } = {}) => {
    const form = new FormData();
    form.append('message', message ?? '');
    (files || []).forEach((file) => {
      if (file) form.append('files', file);
    });
    return api
      .post(`${BASE}/${id}/chat`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      .then((r) => r.data);
  },

  // Manual field edits from the live-brief click-to-edit. Pass column fields
  // directly (e.g. `{ summary: '…' }`) or custom template keys under
  // `custom_fields` (e.g. `{ custom_fields: { relocation_support: 'yes' } }`).
  update: (id, fields) => api.patch(`${BASE}/${id}`, fields).then((r) => r.data),

  // Publish the brief: snapshots the rendered JD markdown onto a public job
  // page (and provisions the live role behind it). `jdMarkdown` is the fully
  // rendered job description — the recruiter's per-requisition override if set,
  // else the template-filled draft (see RequisitionsPage). Returns
  // `{ job_page_id, token, url, status, published_at }`; re-calling re-snapshots.
  publish: (id, jdMarkdown) =>
    api.post(`${BASE}/${id}/publish`, { jd_markdown: jdMarkdown }).then((r) => r.data),

  // The org's canonical requisition spec template — drives BOTH the live
  // brief panel and the settings editor. Returns `{ template }`; the backend
  // hands back a sensible DEFAULT when the org hasn't customised one.
  getTemplate: () => api.get('/settings/requisition-template').then((r) => r.data),

  // Persist an edited spec template for the org.
  saveTemplate: (template) =>
    api.put('/settings/requisition-template', { template }).then((r) => r.data),
};

// Public, UNAUTHENTICATED job-posting client — used by the careers-style
// /job/:token page. The recruiter's JWT must never be attached here (the
// underlying helper uses a bare axios call, not the auth-interceptor instance),
// so the link works for anyone. Returns the public job payload
// `{ title, jd_markdown, location, workplace_type, employment_type, seniority,
//    salary_min, salary_max, salary_currency, status, organization_name }`.
export const publicJobApi = {
  get: (token) => viewPublicJob(token).then((r) => r.data),
};

export default requisitionApi;
