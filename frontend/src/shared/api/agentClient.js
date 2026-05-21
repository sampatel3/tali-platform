import api from './httpClient';

export const agent = {
  // Decisions queue
  listDecisions: (params = {}) => api.get('/agent-decisions', { params }),
  approveDecision: (decisionId, body = {}) => api.post(`/agent-decisions/${decisionId}/approve`, body),
  overrideDecision: (decisionId, body = {}) => api.post(`/agent-decisions/${decisionId}/override`, body),
  discardPending: (roleId) => api.post('/agent-decisions/discard', { role_id: roleId }),
  // Approve a batch of pending decisions in one request. Each is
  // executed independently server-side; the response carries a
  // per-failure summary so the UI can surface partial successes.
  bulkApproveDecisions: (decisionIds, note = null) =>
    api.post('/agent-decisions/bulk-approve', { decision_ids: decisionIds, note }),
  // Hide a pending decision for 1h. Body intentionally empty — duration is
  // server-fixed; if we ever need 4h/24h we change it there, not per call.
  snoozeDecision: (decisionId) => api.post(`/agent-decisions/${decisionId}/snooze`, {}),

  // Run log
  listRuns: (params = {}) => api.get('/agent-runs', { params }),

  // Manual trigger
  runNow: (roleId, body = {}) => api.post(`/roles/${roleId}/agent/run-now`, body),

  // Per-role agent status
  status: (roleId) => api.get(`/roles/${roleId}/agent/status`),

  // Per-role activity feed — merged stream of runs, decisions, stage moves,
  // and recruiter-input prompts. Backs the collapsible "Activity" section
  // on the role Agent settings tab.
  activity: (roleId, params = {}) => api.get(`/roles/${roleId}/agent/activity`, { params }),

  // Per-feature spend breakdown for the role this calendar month — backs
  // the Role budget panel so recruiters see where their cap is going.
  usageBreakdown: (roleId) => api.get(`/roles/${roleId}/usage/breakdown`),

  // ---- Hub (org-wide) ----
  // 30-second poll target for the live tab badge + Hub KPI strip.
  orgStatus: () => api.get('/agent/org-status'),
  // Time-windowed KPIs (range = '24h' | '7d' | '30d').
  kpis: (params = {}) => api.get('/agent/kpis', { params }),
  // Per-role table on the Hub.
  rolesBreakdown: () => api.get('/agent/roles/breakdown'),

  // ---- Teach loop ("Send back & teach") ----
  // body: { decision_id, failure_mode, correction_text, scope, role_id? }
  sendFeedback: (body) => api.post('/agent/feedback', body),
  cosignFeedback: (feedbackId) => api.post(`/agent/feedback/${feedbackId}/cosign`, {}),
  revertFeedback: (feedbackId) => api.post(`/agent/feedback/${feedbackId}/revert`, {}),
  listFeedback: (params = {}) => api.get('/agent/feedback', { params }),
  // The "world says" learning loop — what actually happened to candidates
  // downstream of approved agent decisions (interviewed / hired /
  // rejected_confirmed). Sourced from role.agent_calibration["outcomes"].
  realisedOutcomes: (params = {}) => api.get('/agent/realised-outcomes', { params }),
  // NOTE: rubric-revisions surface was removed deliberately — see
  // backend/app/domains/agentic/hub_feedback_routes.py. The Hub does not
  // claim automated retunes; that's a separate scoring rework.
};
