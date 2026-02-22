import api from './httpClient';

export const analytics = {
  get: (params = {}) => api.get('/analytics/', { params }),
  benchmarks: (taskId, params = {}) => api.get('/analytics/benchmarks', {
    params: { task_id: taskId, ...params },
  }),
};
