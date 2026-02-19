import api from './httpClient';

export const roles = {
  list: () => api.get('/roles'),
  get: (id) => api.get(`/roles/${id}`),
  create: (data) => api.post('/roles', data),
  update: (id, data) => api.patch(`/roles/${id}`, data),
  remove: (id) => api.delete(`/roles/${id}`),
  uploadJobSpec: (roleId, file) => {
    const form = new FormData();
    form.append('file', file);
    return api.post(`/roles/${roleId}/upload-job-spec`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  listTasks: (roleId) => api.get(`/roles/${roleId}/tasks`),
  addTask: (roleId, taskId) => api.post(`/roles/${roleId}/tasks`, { task_id: taskId }),
  removeTask: (roleId, taskId) => api.delete(`/roles/${roleId}/tasks/${taskId}`),
  listApplications: (roleId, params = {}) => api.get(`/roles/${roleId}/applications`, { params }),
  getApplication: (applicationId, config = {}) => api.get(`/applications/${applicationId}`, config),
  createApplication: (roleId, data) => api.post(`/roles/${roleId}/applications`, data),
  updateApplication: (applicationId, data) => api.patch(`/applications/${applicationId}`, data),
  uploadApplicationCv: (applicationId, file) => {
    const form = new FormData();
    form.append('file', file);
    return api.post(`/applications/${applicationId}/upload-cv`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  generateTaaliCvAi: (applicationId) => api.post(`/applications/${applicationId}/generate-taali-cv-ai`),
  enrichApplication: (applicationId) => api.post(`/applications/${applicationId}/enrich`),
  batchScore: (roleId) => api.post(`/roles/${roleId}/batch-score`),
  batchScoreStatus: (roleId) => api.get(`/roles/${roleId}/batch-score/status`),
  fetchCvs: (roleId) => api.post(`/roles/${roleId}/fetch-cvs`),
  fetchCvsStatus: (roleId) => api.get(`/roles/${roleId}/fetch-cvs/status`),
  createAssessment: (applicationId, data) => api.post(`/applications/${applicationId}/assessments`, data),
};
