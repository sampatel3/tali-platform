import api from './httpClient';

export const analytics = {
  get: (params = {}) => api.get('/analytics/', { params }),
  // Aggregated payload backing the Mission Control "Your agent in
  // narrative" reporting page — KPIs with deltas, narrator + chips,
  // decisions feed, anomalies, named funnel stages, score buckets.
  reportingSummary: (params = {}) => api.get('/analytics/reporting-summary', { params }),
  benchmarks: (taskId, params = {}) => api.get('/analytics/benchmarks', {
    params: { task_id: taskId, ...params },
  }),
};
