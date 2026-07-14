import api from './httpClient';

export const analytics = {
  get: (params = {}) => api.get('/analytics/', { params }),
  // Aggregated payload backing the Mission Control "Your agent in
  // narrative" reporting page — KPIs with deltas, narrator + chips,
  // decisions feed, anomalies, named funnel stages, score buckets.
  reportingSummary: (params = {}) => api.get('/analytics/reporting-summary', { params }),
  // All-time decisions + Workable-stage outcomes grouped by role — backs the
  // by-role breakdown rendered inside the Hub's funnel accordion.
  decisionsBreakdown: (params = {}) => api.get('/analytics/decisions-breakdown', { params }),
  // BILLED spend per funnel outcome ($ per pre-screen / score / advanced / hire)
  // over the selected window — the unit-economics view next to the Outcomes
  // funnel. Role/window scoped like the rest of the Analytics feeds.
  costPerOutcome: (params = {}) => api.get('/analytics/cost-per-outcome', { params }),
  // Daily decisions + notification-backlog curve (role-filterable) plus the
  // current Workable-error requeue callout — backs the Home activity-trends section.
  activityTimeseries: (params = {}) => api.get('/analytics/activity-timeseries', { params }),
  benchmarks: (taskId, params = {}) => api.get('/analytics/benchmarks', {
    params: { task_id: taskId, ...params },
  }),
  // A/B experiment arm comparison (discrimination + completion/time + outcome +
  // candidate experience). Without experiment_id, returns the org's experiments
  // so the UI can populate a selector.
  experimentsComparison: (params = {}) => api.get('/analytics/experiments/comparison', { params }),
  // Monthly override / agreement rate over resolved agent decisions (trailing
  // ~6 months, optional role_id) — backs the Analytics Outcomes "override rate
  // over time" bars and the Teaching "agreement trend" bars. Real verdicts only.
  decisionTrend: (params = {}) => api.get('/analytics/decision-trend', { params }),
  // A role's score-threshold change history from the persisted
  // ThresholdCalibration rows; `has_history=false` + a single current-threshold
  // entry when no calibration was ever activated (never fabricates past changes).
  thresholdHistory: (roleId) => api.get('/analytics/threshold-history', { params: { role_id: roleId } }),
  // ATS native pipeline analytics — headcount per configured stage (funnel
  // order, canonical-seed fallback pre-config) + outcome mix. Distinct from the
  // Workable-stage reporting above.
  pipelineFunnel: (params = {}) => api.get('/analytics/pipeline-funnel', { params }),
  // Days from application to accepted offer — overall summary + per-role breakdown.
  timeToFill: (params = {}) => api.get('/analytics/time-to-fill', { params }),
};
