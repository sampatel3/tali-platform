import api from './httpClient';

export const roles = {
  list: (params = {}) => api.get('/roles', { params }),
  get: (id) => api.get(`/roles/${id}`),
  create: (data) => api.post('/roles', data),
  update: (id, data) => api.patch(`/roles/${id}`, data),
  remove: (id) => api.delete(`/roles/${id}`),
  star: (id) => api.post(`/roles/${id}/star`),
  unstar: (id) => api.delete(`/roles/${id}/star`),
  // Requisition->Workable job lifecycle: draft | open | filled | filled_external | cancelled.
  setJobStatus: (id, status, reason) =>
    api.post(`/roles/${id}/job-status`, { status, reason }),
  // Auto-reject threshold recommendation. Returns {value, source,
  // rationale, sample_size}. Frontend calls this when the role's
  // ``auto_reject_threshold_mode`` is ``auto`` to show the computed
  // value + plain-English rationale next to the slider.
  suggestedAutoRejectThreshold: (id) => api.get(`/roles/${id}/auto-reject-threshold/suggested`),
  // Per-role criteria chip CRUD + workspace sync.
  createCriterion: (roleId, data) => api.post(`/roles/${roleId}/criteria`, data),
  updateCriterion: (roleId, criterionId, data) =>
    api.patch(`/roles/${roleId}/criteria/${criterionId}`, data),
  deleteCriterion: (roleId, criterionId) =>
    api.delete(`/roles/${roleId}/criteria/${criterionId}`),
  syncCriteriaWithWorkspace: (roleId) => api.post(`/roles/${roleId}/criteria/sync`),
  resetCriteriaToWorkspace: (roleId) => api.post(`/roles/${roleId}/criteria/reset`),
  regenerateInterviewFocus: (roleId) => api.post(`/roles/${roleId}/regenerate-interview-focus`),
  // Recruiter feedback notes — append-only freeform observations the
  // recruiter writes about agent behaviour on this role. The agent
  // inlines the most recent notes into its system prompt; the full
  // history is the timeline UI's source of truth.
  listFeedbackNotes: (roleId) => api.get(`/roles/${roleId}/feedback-notes`),
  createFeedbackNote: (roleId, note) =>
    api.post(`/roles/${roleId}/feedback-notes`, { note }),
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
  // Talent-pool rediscovery (Phase B): start a bounded, cost-confirmed re-score
  // of a shortlist against a NEW requirement, then poll it. Results are kept off
  // the canonical role score.
  startPoolRescore: (requirementText, applicationIds) =>
    api.post('/candidates/pool-rescore', {
      requirement_text: requirementText,
      application_ids: applicationIds,
    }),
  getPoolRescore: (jobId) => api.get(`/candidates/pool-rescore/${jobId}`),
  getApplication: (applicationId, config = {}) => api.get(`/applications/${applicationId}`, config),
  // HANDOFF v2 §3 — multi-link share contract.
  // POST mints a new link with mode + expiry preset; GET lists all links
  // (active + revoked + expired so the report footer can render audit
  // history); DELETE revokes a single link by id without affecting the
  // others.
  listApplicationShareLinks: (applicationId) =>
    api.get(`/applications/${applicationId}/share-links`),
  createApplicationShareLink: (applicationId, { mode, expiry }) =>
    api.post(`/applications/${applicationId}/share-links`, { mode, expiry }),
  revokeShareLink: (linkId) => api.delete(`/share-links/${linkId}`),
  listApplicationEvents: (applicationId, params = {}) => api.get(`/applications/${applicationId}/events`, { params }),
  // Drop a recruiter note on the candidate's timeline. Works with or without
  // a linked assessment. `forAgent` (default true) makes the note visible to
  // the recruiting agent as standing per-candidate guidance. `extra` carries
  // the structured "add info" fields for the ranking / link quick-adds
  // (kind / ranking / link_url / link_label); omitted ⇒ a plain freeform note.
  addApplicationNote: (applicationId, note, forAgent = true, extra = {}) =>
    api.post(`/applications/${applicationId}/notes`, { note, for_agent: forAgent, ...extra }),
  generateApplicationInterviewDebrief: (applicationId, data = {}) => api.post(`/applications/${applicationId}/interview-debrief`, data),
  downloadApplicationReport: (applicationId) => api.get(`/applications/${applicationId}/report.pdf`, { responseType: 'blob' }),
  downloadApplicationDocument: (applicationId, docType = 'cv', config = {}) =>
    api.get(`/applications/${applicationId}/documents/${docType}`, { responseType: 'blob', ...config }),
  createApplication: (roleId, data) => api.post(`/roles/${roleId}/applications`, data),
  updateApplication: (applicationId, data) => api.patch(`/applications/${applicationId}`, data),
  updateApplicationStage: (applicationId, data) => api.patch(`/applications/${applicationId}/stage`, data),
  updateApplicationOutcome: (applicationId, data) => api.patch(`/applications/${applicationId}/outcome`, data),
  // Record/update a recruiter's manual decision (advance/hold/reject +
  // rationale, confidence, next steps) on an application with no assessment
  // linked. `data` carries { status, expected_version, decision, rationale,
  // confidence, next_steps }. Idempotent upsert with optimistic locking.
  updateApplicationDecision: (applicationId, data) => api.patch(`/applications/${applicationId}/manual-decision`, data),
  // Hand-back to Workable: pushes the candidate into the chosen Workable
  // stage. `data` is `{ target_stage: string, reason?: string }`. Used at
  // the end of the Tali pipeline (typically when stage === 'review').
  moveApplicationToWorkableStage: (applicationId, data) =>
    api.post(`/applications/${applicationId}/workable/move-stage`, data),
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
    {
      application_ids: applicationIds,
      force: options.force === true,
      // Opt-out of the cheap pre-screen gate so a below-threshold candidate
      // still gets a full v3 cv_match score (the "Run full evaluation" override).
      bypass_pre_screen: options.bypassPreScreen === true,
    },
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
  // Unified Process action — replaces individual fetch / pre-screen / score buttons.
  // Body: { fetch_cvs, pre_screen, refresh_pre_screen, score: 'none'|'new'|'all' }.
  // Pass { dry_run: true } in options to get cascade-aware preview counts.
  processRole: (roleId, body = {}, options = {}) => api.post(
    `/roles/${roleId}/process`,
    body,
    { params: { dry_run: options.dry_run === true ? true : undefined } },
  ),
  processRoleStatus: (roleId) => api.get(`/roles/${roleId}/process/status`),
  cancelProcessRole: (roleId) => api.post(`/roles/${roleId}/process/cancel`),
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
  // Fast, targeted: pull this role's candidates' current Workable stages and
  // update workable_stage only (no re-import / scoring). For the job page button.
  refreshWorkableStages: (roleId) =>
    api.post(`/workable/roles/${roleId}/refresh-stages`),
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
