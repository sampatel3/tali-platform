import api, {
  ASSESSMENT_TOKEN_AUTH_MODE,
  PUBLIC_NO_AUTH_MODE,
} from './httpClient';
import {
  createCandidateProofHeaders,
  getOrCreateCandidateProofBinding,
} from '../assessment/candidateProofBinding';

const API_PATH_PREFIX = '/api/v1';

const candidateRuntimeHeaders = async (
  assessmentToken,
  candidateSessionKey,
  request,
) => ({
  'X-Assessment-Token': assessmentToken,
  ...(candidateSessionKey ? { 'X-Assessment-Session': candidateSessionKey } : {}),
  ...await createCandidateProofHeaders(assessmentToken, request),
});

const candidatePost = async (
  path,
  body,
  assessmentToken,
  candidateSessionKey,
  config = {},
) => api.post(path, body, {
  ...config,
  authMode: ASSESSMENT_TOKEN_AUTH_MODE,
  headers: {
    ...(config.headers || {}),
    ...await candidateRuntimeHeaders(assessmentToken, candidateSessionKey, {
      method: 'POST',
      pathAndQuery: `${API_PATH_PREFIX}${path}`,
      body,
    }),
  },
});

export const assessments = {
  list: (params = {}) => api.get('/assessments/', { params }),
  stats: () => api.get('/assessments/stats'),
  get: (id) => api.get(`/assessments/${id}`),
  create: (data) => api.post('/assessments/', data),
  startDemo: (data) => api.post('/assessments/demo/start', data, {
    authMode: PUBLIC_NO_AUTH_MODE,
  }),
  requestDemo: (data) => api.post('/assessments/demo/request', data, {
    authMode: PUBLIC_NO_AUTH_MODE,
  }),
  preview: (token) => api.get(`/assessments/token/${encodeURIComponent(token)}/preview`, {
    authMode: ASSESSMENT_TOKEN_AUTH_MODE,
  }),
  start: async (token, data = {}) => {
    const encodedToken = encodeURIComponent(token);
    const path = `/assessments/token/${encodedToken}/start`;
    const proofBinding = await getOrCreateCandidateProofBinding(token);
    const body = {
      ...data,
      candidate_proof_key_id: proofBinding.keyId,
      candidate_proof_public_jwk: proofBinding.publicJwk,
    };
    const headers = await createCandidateProofHeaders(token, {
      method: 'POST',
      pathAndQuery: `${API_PATH_PREFIX}${path}`,
      body,
    });
    return api.post(path, body, {
      authMode: ASSESSMENT_TOKEN_AUTH_MODE,
      headers,
    });
  },
  execute: (id, payload, assessmentToken, candidateSessionKey) => candidatePost(
    `/assessments/${id}/execute`,
    typeof payload === 'string' ? { code: payload } : payload,
    assessmentToken,
    candidateSessionKey,
  ),
  saveRepoFile: (id, payload, assessmentToken, candidateSessionKey) => candidatePost(
    `/assessments/${id}/repo-file`,
    payload,
    assessmentToken,
    candidateSessionKey,
  ),
  getRepoFile: async (id, path, assessmentToken, candidateSessionKey) => {
    const requestPath = `/assessments/${id}/repo-file?path=${encodeURIComponent(path)}`;
    return api.get(requestPath, {
      authMode: ASSESSMENT_TOKEN_AUTH_MODE,
      headers: await candidateRuntimeHeaders(assessmentToken, candidateSessionKey, {
        method: 'GET',
        pathAndQuery: `${API_PATH_PREFIX}${requestPath}`,
        body: null,
      }),
    });
  },
  // HTTP-based agentic Claude chat — the only candidate-facing assistant
  // transport (the legacy PTY terminal + non-tool `claude` helper were
  // removed alongside their backend routes). A per-request 120s timeout
  // (Claude turns are long, but a stalled connection must not freeze the
  // chat in "Working…" forever) so the composer always unlocks even when
  // the shared httpClient default doesn't apply to this long-poll call.
  claudeChat: (assessmentId, payload, assessmentToken, candidateSessionKey) =>
    candidatePost(`/assessments/${assessmentId}/claude/chat`, payload, assessmentToken, candidateSessionKey, {
      timeout: 120000,
    }),
  // Fire-and-forget candidate runtime event. Engagement beacons are deduped
  // server-side; advisory integrity events accept only bounded metadata and
  // never include clipboard or repository content.
  runtimeEvent: (id, eventType, assessmentToken, fields = {}, candidateSessionKey) => candidatePost(
    `/assessments/${id}/runtime-event`,
    { event_type: eventType, ...fields },
    assessmentToken,
    candidateSessionKey,
  ),
  keepalive: (id, assessmentToken, candidateSessionKey) => candidatePost(
    `/assessments/${id}/keepalive`,
    {},
    assessmentToken,
    candidateSessionKey,
  ),
  submit: (id, payloadOrFinalCode, assessmentToken, metadata = {}, candidateSessionKey) => {
    const body = typeof payloadOrFinalCode === 'string'
      ? { final_code: payloadOrFinalCode, ...metadata }
      : { ...(payloadOrFinalCode || {}), ...metadata };
    return candidatePost(
      `/assessments/${id}/submit`,
      body,
      assessmentToken,
      candidateSessionKey,
    );
  },
  // Post-submit understanding check. The work is already frozen by the time
  // these run, so they carry the token + live session but no request proof —
  // that exists to protect workspace mutations, and these only append answers.
  getUnderstandingCheck: async (id, assessmentToken, candidateSessionKey) => {
    const requestPath = `/assessments/${id}/understanding-check`;
    return api.get(requestPath, {
      authMode: ASSESSMENT_TOKEN_AUTH_MODE,
      headers: {
        'X-Assessment-Token': assessmentToken,
        ...(candidateSessionKey ? { 'X-Assessment-Session': candidateSessionKey } : {}),
      },
    });
  },
  answerUnderstandingCheck: (id, payload, assessmentToken, candidateSessionKey) => api.post(
    `/assessments/${id}/understanding-check/answer`,
    payload,
    {
      authMode: ASSESSMENT_TOKEN_AUTH_MODE,
      headers: {
        'X-Assessment-Token': assessmentToken,
        ...(candidateSessionKey ? { 'X-Assessment-Session': candidateSessionKey } : {}),
      },
    },
  ),
  remove: (id) => api.delete(`/assessments/${id}`),
  resend: (id) => api.post(`/assessments/${id}/resend`),
  recoverCandidateDevice: (id) => api.post(`/assessments/${id}/recover-candidate-device`),
  updateClipboardAccommodation: (id, enabled) => api.patch(
    `/assessments/${id}/clipboard-accommodation`,
    { allow_external_clipboard: enabled },
  ),
  downloadReport: (id) => api.get(`/assessments/${id}/report.pdf`, { responseType: 'blob' }),
  generateInterviewDebrief: (id, data = {}) => api.post(`/assessments/${id}/interview-debrief`, data),
  updateManualEvaluation: (id, data) => api.patch(`/assessments/${id}/manual-evaluation`, data),
  addNote: (id, note) => api.post(`/assessments/${id}/notes`, { note }),
  uploadCv: (assessmentId, token, file) => {
    const form = new FormData();
    form.append('file', file);
    const url = assessmentId
      ? `/assessments/${assessmentId}/upload-cv`
      : `/assessments/token/${token}/upload-cv`;
    if (assessmentId) {
      form.append('token', token);
    }
    return api.post(url, form, {
      authMode: ASSESSMENT_TOKEN_AUTH_MODE,
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
};
