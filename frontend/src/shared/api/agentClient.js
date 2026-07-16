import api from './httpClient';

export const agent = {
  // Decisions queue
  listDecisions: (params = {}) => api.get('/agent-decisions', { params }),
  // Accurate "Needs re-eval" total for the current role/type scope — computed
  // server-side over the whole queue (the per-row is_stale on listDecisions
  // only covers the capped page, so a deep backlog under-counts client-side).
  needsReevalCount: (params = {}) => api.get('/agent-decisions/needs-reeval-count', { params }),
  // ``opts.force`` approves even when the decision's inputs are stale — the
  // recruiter deliberately taking the recommended action (parity with the
  // always-available Reject / Skip & advance overrides).
  approveDecision: (decisionId, body = {}, opts = {}) =>
    api.post(`/agent-decisions/${decisionId}/approve${opts.force ? '?force=true' : ''}`, body),
  overrideDecision: (decisionId, body = {}) => api.post(`/agent-decisions/${decisionId}/override`, body),
  // A4: discard a stale decision and re-run the agent on fresh inputs.
  // Surfaced by the "Re-evaluate" button when a decision is_stale.
  reEvaluateDecision: (decisionId) => api.post(`/agent-decisions/${decisionId}/re-evaluate`, {}),
  discardPending: (roleId, expectedVersion) => api.post('/agent-decisions/discard', {
    role_id: roleId,
    expected_version: expectedVersion,
  }),
  // Approve a batch of pending decisions in one request. Each is
  // executed independently server-side; the response carries a
  // per-failure summary so the UI can surface partial successes.
  // ``workableTargetStages`` is the per-role advance-stage map
  // (role_id → Workable stage) for the advancing decisions in the batch.
  bulkApproveDecisions: (decisionIds, note = null, workableTargetStages = null) =>
    api.post('/agent-decisions/bulk-approve', {
      decision_ids: decisionIds,
      note,
      workable_target_stages: workableTargetStages,
    }),
  // Apply ONE override action (e.g. 'skip_assessment_advance') to a batch of
  // pending decisions — the bulk counterpart of overrideDecision. Each is
  // dispatched independently server-side (serialized per org); the response
  // carries a per-failure summary. ``workableTargetStages`` is the per-role
  // advance-stage map (role_id → Workable stage) for advance-type actions.
  bulkOverrideDecisions: (decisionIds, overrideAction, note = null, workableTargetStages = null) =>
    api.post('/agent-decisions/bulk-override', {
      decision_ids: decisionIds,
      override_action: overrideAction,
      note,
      workable_target_stages: workableTargetStages,
    }),
  // Hide a pending decision for 1h. Body intentionally empty — duration is
  // server-fixed; if we ever need 4h/24h we change it there, not per call.
  snoozeDecision: (decisionId) => api.post(`/agent-decisions/${decisionId}/snooze`, {}),

  // Run log
  listRuns: (params = {}) => api.get('/agent-runs', { params }),

  // Manual trigger
  runNow: (roleId, body = {}) => api.post(`/roles/${roleId}/agent/run-now`, body),

  // Workspace-wide pause overlay. It gates every role without rewriting any
  // role's own ON / locally-paused / OFF choice; resumeAll clears only that
  // overlay. Both commands use the viewed workspace version so concurrent
  // recruiters cannot silently overwrite one another.
  pauseAll: (expectedControlVersion) => api.post('/agent/pause-all', {
    expected_control_version: expectedControlVersion,
  }),
  resumeAll: (expectedControlVersion) => api.post('/agent/resume-all', {
    expected_control_version: expectedControlVersion,
  }),

  // Per-role soft pause / resume — the per-role twin of pauseAll/resumeAll.
  // pause sets agent_paused_at WITHOUT disabling the agent, so the role's
  // pending decisions are KEPT; resume clears it when back under the monthly
  // cap and kicks an immediate cycle. Distinct from the role PATCH
  // agentic_mode_enabled toggle, which turns the agent fully off.
  pause: (roleId, expectedVersion) => api.post(`/roles/${roleId}/agent/pause`, {
    expected_version: expectedVersion,
  }),
  resume: (roleId, expectedVersion) => api.post(`/roles/${roleId}/agent/resume`, {
    expected_version: expectedVersion,
  }),

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
  // This request gates the workspace pause/resume control. Keep its failure
  // bound short so a dropped connection cannot leave the control looking
  // permanently disabled behind the global 60-second API timeout.
  orgStatus: () => api.get('/agent/org-status', { timeout: 10000 }),
  // Time-windowed KPIs (range = '24h' | '7d' | '30d').
  kpis: (params = {}) => api.get('/agent/kpis', { params }),
  // Per-role table on the Hub.
  rolesBreakdown: () => api.get('/agent/roles/breakdown'),
  // Settings → Background jobs "Agents" view: one round-trip with pulse,
  // KPIs, per-agent cards, 24h time-series, decisions-by-type, decision log.
  panel: () => api.get('/agent/panel'),
  // Org-wide merged activity feed (runs / decisions / stage moves / questions).
  orgActivity: (params = {}) => api.get('/agent/activity', { params }),

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
