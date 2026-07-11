import api from './httpClient';

// Per-job hiring team: who is on a role's hiring team and in what per-job role.
export const hiringTeam = {
  list: (roleId) => api.get(`/roles/${roleId}/hiring-team`).then((r) => r.data),
  set: (roleId, userId, teamRole) =>
    api
      .post(`/roles/${roleId}/hiring-team`, { user_id: userId, team_role: teamRole })
      .then((r) => r.data),
  remove: (roleId, userId) => api.delete(`/roles/${roleId}/hiring-team/${userId}`),
};

export default hiringTeam;
