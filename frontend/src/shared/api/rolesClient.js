import api from './httpClient';

export const roles = {
  list: (params = {}) => api.get('/roles', { params }),
  get: (id) => api.get(`/roles/${id}`),
  create: (data) => api.post('/roles', data),
  update: (id, data) => api.patch(`/roles/${id}`, data),
  remove: (id) => api.delete(`/roles/${id}`),
  star: (id) => api.post(`/roles/${id}/star`),
  unstar: (id) => api.delete(`/roles/${id}/star`),
  regenerateInterviewFocus: (roleId) => api.post(`/roles/${roleId}/regenerate-interview-focus`),
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
  listPipeline: (roleId, params = {}) => api.get(`/roles/${roleId}/pipeline`, { params }),
  listApplicationsGlobal: (params = {}) => api.get('/applications', { params }),
  getApplication: (applicationId, config = {}) => api.get(`/applications/${applicationId}`, config),
  getApplicationByShareToken: (shareToken, config = {}) => api.get(`/applications/share/${shareToken}`, config),
  getApplicationShareLink: (applicationId) => api.post(`/applications/${applicationId}/share-link`),
  listApplicationEvents: (applicationId, params = {}) => api.get(`/applications/${applicationId}/events`, { params }),
  generateApplicationInterviewDebrief: (applicationId, data = {}) => api.post(`/applications/${applicationId}/interview-debrief`, data),
  downloadApplicationReport: (applicationId) => api.get(`/applications/${applicationId}/report.pdf`, { responseType: 'blob' }),
  downloadApplicationDocument: (applicationId, docType = 'cv', config = {}) =>
    api.get(`/applications/${applicationId}/documents/${docType}`, { responseType: 'blob', ...config }),
  createApplication: (roleId, data) => api.post(`/roles/${roleId}/applications`, data),
  updateApplication: (applicationId, data) => api.patch(`/applications/${applicationId}`, data),
  updateApplicationStage: (applicationId, data) => api.patch(`/applications/${applicationId}/stage`, data),
  updateApplicationOutcome: (applicationId, data) => api.patch(`/applications/${applicationId}/outcome`, data),
  uploadApplicationCv: (applicationId, file) => {
    const form = new FormData();
    form.append('file', file);
    return api.post(`/applications/${applicationId}/upload-cv`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  createManualInterview: (applicationId, data) => api.post(`/applications/${applicationId}/interviews`, data),
  linkFirefliesInterview: (applicationId, data) => api.post(`/applications/${applicationId}/interviews/fireflies-link`, data),
  generateTaaliCvAi: (applicationId) => api.post(`/applications/${applicationId}/generate-taali-cv-ai`),
  refreshInterviewSupport: (applicationId) => api.post(`/applications/${applicationId}/refresh-interview-support`),
  scoreSelected: (roleId, applicationIds, options = {}) => api.post(
    `/roles/${roleId}/applications/score-selected`,
    { application_ids: applicationIds, force: options.force === true },
  ),
  fetchCvsSelected: (roleId, applicationIds) => api.post(
    `/roles/${roleId}/applications/fetch-cvs-selected`,
    { application_ids: applicationIds },
  ),
  refreshInterviewSupportBulk: (roleId, applicationIds) => api.post(
    `/roles/${roleId}/applications/refresh-interview-support-bulk`,
    { application_ids: applicationIds },
  ),
  enrichApplication: (applicationId) => api.post(`/applications/${applicationId}/enrich`),
  batchScore: (roleId, options = {}) => api.post(
    `/roles/${roleId}/batch-score`,
    null,
    {
      params: {
        include_scored: options.include_scored === true ? true : undefined,
        dry_run: options.dry_run === true ? true : undefined,
      },
    },
  ),
  batchScoreStatus: (roleId) => api.get(`/roles/${roleId}/batch-score/status`),
  activeBatchScores: () => api.get('/batch-score/active'),
  cancelBatchScore: (roleId) => api.post(`/roles/${roleId}/batch-score/cancel`),
  // Pre-screen — runs the cheap pre-screen LLM only (no full v3 score).
  // Use refresh=true to re-run for already-pre-screened apps.
  batchPreScreen: (roleId, options = {}) => api.post(
    `/roles/${roleId}/batch-pre-screen`,
    null,
    {
      params: {
        refresh: options.refresh === true ? true : undefined,
        dry_run: options.dry_run === true ? true : undefined,
      },
    },
  ),
  batchPreScreenStatus: (roleId) => api.get(`/roles/${roleId}/batch-pre-screen/status`),
  cancelFetchCvs: (roleId) => api.post(`/roles/${roleId}/fetch-cvs/cancel`),
  fetchCvs: (roleId, options = {}) => api.post(
    `/roles/${roleId}/fetch-cvs`,
    null,
    { params: { dry_run: options.dry_run === true ? true : undefined } },
  ),
  fetchCvsStatus: (roleId) => api.get(`/roles/${roleId}/fetch-cvs/status`),
  // Org-wide knowledge-graph sync. Lives on /candidates/* not /roles/* because
  // the graph projection is org-scoped, not role-scoped.
  syncGraph: (options = {}) => api.post(
    `/candidates/sync-graph`,
    null,
    {
      params: {
        refresh: options.refresh === true ? true : undefined,
        dry_run: options.dry_run === true ? true : undefined,
      },
    },
  ),
  syncGraphStatus: () => api.get(`/candidates/sync-graph/status`),
  syncGraphCancel: () => api.post(`/candidates/sync-graph/cancel`),
  // Workable sync — org-wide. Live status reads the latest run; runs lists history.
  workableSync: (mode = 'metadata') => api.post('/workable/sync', { mode }),
  workableSyncStatus: () => api.get('/workable/sync/status'),
  workableSyncRuns: (limit = 10) => api.get('/workable/sync/runs', { params: { limit } }),
  workableSyncCancel: (runId = null) =>
    api.post('/workable/sync/cancel', runId == null ? {} : { run_id: runId }),
  // Background jobs panel: history listing across scoring batch / CV fetch / graph sync.
  // Workable sync history is at /workable/sync/runs.
  backgroundJobsRuns: (limit = 20) => api.get('/background-jobs/runs', { params: { limit } }),
  createAssessment: (applicationId, data) => api.post(`/applications/${applicationId}/assessments`, data),
  retakeAssessment: (applicationId, data) => api.post(`/applications/${applicationId}/assessments/retake`, data),
};
