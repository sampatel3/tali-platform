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
import api, {
  viewPublicJob,
  viewCareers,
  applyToJob,
  submitJobEeo,
  viewClientIntake,
  sendClientIntakeChat,
  submitClientIntake,
} from '../../shared/api/httpClient';

const BASE = '/requisitions';

export const requisitionApi = {
  // List the org's requisitions (title, status, completeness, …).
  list: (params = {}) => api.get(BASE, { params }).then((r) => r.data),

  // Start a new requisition. The backend seeds the brief with an opening
  // assistant message, so the conversation is never empty on first render.
  create: (sourceKind = null) =>
    api.post(BASE, { source_kind: sourceKind }).then((r) => r.data),

  // Start the same conversational job draft, cloned from an existing ATS
  // role. The backend snapshots the full spec and structured brief and returns
  // it with a related-role-specific opening message.
  createRelated: (sourceRoleId) =>
    api.post(BASE, { source_role_id: Number(sourceRoleId) }).then((r) => r.data),

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
  chat: (id, { message = '', files = [], expectedVersion = null } = {}) => {
    const form = new FormData();
    form.append('message', message ?? '');
    if (Number.isInteger(expectedVersion)) {
      form.append('expected_version', String(expectedVersion));
    }
    (files || []).forEach((file) => {
      if (file) form.append('files', file);
    });
    return api
      .post(`${BASE}/${id}/chat`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      .then((r) => r.data);
  },

  // Record ONE brief field DETERMINISTICALLY — no LLM. Used by the tappable
  // quick replies: a clean structured answer (e.g. a template SELECT option)
  // maps to exactly one field=value, so the backend can record it without a
  // model call and advance to the next gap. Returns the SAME shape as chat()
  // — `{ brief, reply, messages, gaps, suggested_replies }` — so the caller
  // merges the response identically (and `suggested_replies` carries the next
  // gap's template options). Typing free-form text / pasting a transcript or
  // screenshot still goes through chat() (the LLM path).
  answer: (id, fieldKey, value, expectedVersion = null) =>
    api.post(`${BASE}/${id}/answer`, {
      field_key: fieldKey,
      value,
      ...(Number.isInteger(expectedVersion) ? { expected_version: expectedVersion } : {}),
    }).then((r) => r.data),

  // Manual field edits from the live-brief click-to-edit. Pass column fields
  // directly (e.g. `{ summary: '…' }`) or custom template keys under
  // `custom_fields` (e.g. `{ custom_fields: { relocation_support: 'yes' } }`).
  update: (id, fields, expectedVersion = null) => api.patch(`${BASE}/${id}`, {
    ...fields,
    ...(Number.isInteger(expectedVersion) ? { expected_version: expectedVersion } : {}),
  }).then((r) => r.data),

  // Ask the agent to AI-draft the role's responsibilities (the "What you'll
  // do" bullets on the JD). Returns the FULL serialized brief — same shape as
  // update()/get() — with `custom_fields.responsibilities` populated, so the
  // caller merges it into state exactly like an update.
  draftResponsibilities: (id, expectedVersion = null) =>
    api.post(`${BASE}/${id}/draft-responsibilities`, null, {
      params: Number.isInteger(expectedVersion)
        ? { expected_version: expectedVersion }
        : undefined,
    }).then((r) => r.data),

  // Publish the brief: snapshots the rendered JD markdown onto a public job
  // page (and provisions the live role behind it). `jdMarkdown` is the fully
  // rendered job description — the recruiter's per-requisition override if set,
  // else the template-filled draft (see RequisitionsPage). Returns
  // `{ job_page_id, token, url, status, published_at }`; re-calling re-snapshots.
  publish: (id, jdMarkdown, expectedVersion = null, relatedRoleAuthorization = null) =>
    api.post(`${BASE}/${id}/publish`, {
      jd_markdown: jdMarkdown,
      ...(Number.isInteger(expectedVersion) ? { expected_version: expectedVersion } : {}),
      ...(relatedRoleAuthorization
        ? { related_role_authorization: relatedRoleAuthorization }
        : {}),
    }).then((r) => r.data),

  // Mint (or fetch) the public CLIENT INTAKE link for this requisition — the
  // no-login URL a consultancy recruiter sends to their client so the client
  // can describe the role via the same conversational agent (company/economics
  // hidden). Returns `{ token, url }`. The serialized brief also carries
  // `client_link` ({ token, url } or null), so an existing link shows on load
  // without calling this.
  clientLink: (id) => api.post(`${BASE}/${id}/client-link`).then((r) => r.data),

  // The org's canonical requisition spec template — drives BOTH the live
  // brief panel and the settings editor. Returns `{ template }`; the backend
  // hands back a sensible DEFAULT when the org hasn't customised one.
  getTemplate: () => api.get('/settings/requisition-template').then((r) => r.data),

  // Persist an edited spec template for the org.
  saveTemplate: (template) =>
    api.put('/settings/requisition-template', { template }).then((r) => r.data),

  // The org's "About the company" boilerplate (role-agnostic — reused on every
  // spec). `getTemplate` returns it as `company_blurb`; these edit + regenerate it.
  saveCompanyBlurb: (companyBlurb) =>
    api.put('/settings/requisition-template/company-blurb', { company_blurb: companyBlurb }).then((r) => r.data),
  generateCompanyBlurb: () =>
    api.post('/settings/requisition-template/company-blurb/generate').then((r) => r.data),
};

// Public, UNAUTHENTICATED job-posting client — used by the careers-style
// /job/:token page. The recruiter's JWT must never be attached here (the
// underlying helper uses a bare axios call, not the auth-interceptor instance),
// so the link works for anyone. Returns the public job payload
// `{ title, jd_markdown, location, workplace_type, employment_type, seniority,
//    salary_min, salary_max, salary_currency, status, organization_name }`.
export const publicJobApi = {
  get: (token) => viewPublicJob(token).then((r) => r.data),
  // Submit a native application. `fields` = { full_name, email, phone, answers }
  // (answers is a plain object, JSON-encoded into the multipart form) plus an
  // optional `resume` File. Returns `{ status, message, application_id,
  // eeo_token }`.
  apply: (token, { full_name, email, phone, answers = {}, resume = null } = {}) => {
    const form = new FormData();
    form.append('full_name', full_name ?? '');
    if (email) form.append('email', email);
    if (phone) form.append('phone', phone);
    form.append('answers', JSON.stringify(answers || {}));
    if (resume) form.append('resume', resume);
    return applyToJob(token, form).then((r) => r.data);
  },
  // Optional voluntary EEO self-ID for a just-submitted application, keyed by the
  // opaque token the apply response returned. Resolves on 204.
  submitEeo: (token, payload) => submitJobEeo(token, payload).then((r) => r.data),
};

// Public, UNAUTHENTICATED careers board — used by the per-org /careers/:slug
// page that lists all of an org's published jobs. Same JWT-free pattern as
// publicJobApi. Returns `{ organization_name, slug, jobs: [ { token, url,
//   title, location, workplace_type, employment_type, seniority, salary,
//   published_at } ] }` (jobs may be empty).
export const publicCareersApi = {
  get: (slug, params = {}) => viewCareers(slug, params).then((r) => r.data),
};

// Public, UNAUTHENTICATED client-intake client — used by the no-login
// /intake/:token page where a consultancy's client describes the role to the
// same conversational agent. Like publicJobApi, the recruiter's JWT is never
// attached (the underlying helpers use a bare axios call). All payloads expose
// ROLE-only fields — no economics, no client/company internals.
export const publicIntakeApi = {
  // `{ organization_name, messages, captured, gaps, completeness, status }`.
  get: (token) => viewClientIntake(token).then((r) => r.data),
  // One turn — `{ message, files }` as multipart. Returns
  // `{ reply, messages, captured, gaps, suggested_replies }`.
  chat: (token, { message = '', files = [] } = {}) =>
    sendClientIntakeChat(token, { message, files }).then((r) => r.data),
  // Submit the brief back to the consultancy → `{ ok, status }`.
  submit: (token) => submitClientIntake(token).then((r) => r.data),
};

export default requisitionApi;
