import api from './httpClient';

// Per-role hiring team (P0.5). Assign a hiring manager / interviewers to a job.
export const hiringTeam = {
  list: (roleId) => api.get(`/roles/${roleId}/hiring-team`).then((r) => r.data),
  set: (roleId, payload) => api.post(`/roles/${roleId}/hiring-team`, payload).then((r) => r.data),
  remove: (roleId, userId) => api.delete(`/roles/${roleId}/hiring-team/${userId}`),
};
