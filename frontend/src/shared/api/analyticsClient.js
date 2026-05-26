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
};
