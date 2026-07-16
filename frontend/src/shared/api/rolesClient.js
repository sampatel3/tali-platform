import api from './httpClient';

export const roles = {
  list: (params = {}) => api.get('/roles', { params }),
  get: (id) => api.get(`/roles/${id}`),
  // Fast first-paint payload for role detail. Expensive funnel/decision
  // aggregates and full job-spec relationships follow via `get` after the
  // shell is visible.
  getShell: (id) => api.get(`/roles/${id}`, { params: { shell: true } }),
  create: (data) => api.post('/roles', data),
  previewSister: (sourceRoleId) => api.get(`/roles/${sourceRoleId}/sisters/preview`),
  createSister: (sourceRoleId, data) => api.post(`/roles/${sourceRoleId}/sisters`, data),
  rescoreSister: (roleId) => api.post(`/roles/${roleId}/sister-rescore`),
  sisterScoringStatus: (roleId) => api.get(`/roles/${roleId}/sister-scoring-status`),
  // Shared role writes use optimistic concurrency. Callers must forward the
  // `version` they rendered as `expected_version`; a stale write returns 409
  // with the latest role so the draft/control can be reconciled safely.
  update: (id, data) => api.patch(`/roles/${id}`, data),
  updateJobSpec: (id, data) => api.put(`/roles/${id}/job-spec`, data),
  remove: (id, expectedVersion) => api.delete(`/roles/${id}`, {
    params: { expected_version: expectedVersion },
  }),
  star: (id, expectedVersion) => api.post(`/roles/${id}/star`, {
    expected_version: expectedVersion,
  }),
  unstar: (id, expectedVersion) => api.delete(`/roles/${id}/star`, {
    params: { expected_version: expectedVersion },
  }),
  // Requisition->Workable job lifecycle: draft | open | filled | filled_external | cancelled.
  setJobStatus: (id, status, reason, expectedVersion) =>
    api.post(`/roles/${id}/job-status`, {
      status,
      reason,
      expected_version: expectedVersion,
    }),
  // Assign (or clear, with clientId null) the consultancy client a role
  // belongs to — works for legacy / Workable-imported roles that never went
  // through a requisition. Returns the updated role (incl. client_id/_name).
  setClient: (id, clientId, expectedVersion) =>
    api.post(`/roles/${id}/client`, {
      client_id: clientId ?? null,
      expected_version: expectedVersion,
    }),
  // Auto-reject threshold recommendation. Returns {value, source,
  // rationale, sample_size}. Frontend calls this when the role's
  // ``auto_reject_threshold_mode`` is ``auto`` to show the computed
  // value + plain-English rationale next to the slider.
  suggestedAutoRejectThreshold: (id) => api.get(`/roles/${id}/auto-reject-threshold/suggested`),
  // Per-role criteria chip CRUD + workspace sync.
  createCriterion: (roleId, data, expectedVersion) => api.post(
    `/roles/${roleId}/criteria`,
    { ...data, expected_version: expectedVersion },
  ),
  updateCriterion: (roleId, criterionId, data, expectedVersion) =>
    api.patch(`/roles/${roleId}/criteria/${criterionId}`, {
      ...data,
      expected_version: expectedVersion,
    }),
  deleteCriterion: (roleId, criterionId, expectedVersion) =>
    api.delete(`/roles/${roleId}/criteria/${criterionId}`, {
      params: { expected_version: expectedVersion },
    }),
  syncCriteriaWithWorkspace: (roleId, expectedVersion) => api.post(
    `/roles/${roleId}/criteria/sync`,
    { expected_version: expectedVersion },
  ),
  resetCriteriaToWorkspace: (roleId, expectedVersion) => api.post(
    `/roles/${roleId}/criteria/reset`,
    { expected_version: expectedVersion },
  ),
  regenerateInterviewFocus: (roleId, expectedVersion) => api.post(
    `/roles/${roleId}/regenerate-interview-focus`,
    { expected_version: expectedVersion },
  ),
  // Recruiter feedback notes — append-only freeform observations the
  // recruiter writes about agent behaviour on this role. The agent
  // inlines the most recent notes into its system prompt; the full
  // history is the timeline UI's source of truth.
  listFeedbackNotes: (roleId) => api.get(`/roles/${roleId}/feedback-notes`),
  createFeedbackNote: (roleId, note, expectedVersion) =>
    api.post(`/roles/${roleId}/feedback-notes`, {
      note,
      expected_version: expectedVersion,
    }),
  // Public-application screening questions. The authenticated management
  // payload includes deterministic knockout configuration; the public job
  // payload deliberately strips the expected answers.
  listScreeningQuestions: (roleId, { includeInactive = false } = {}) =>
    api.get(`/roles/${roleId}/screening-questions`, {
      params: { include_inactive: includeInactive || undefined },
    }),
  createScreeningQuestion: (roleId, data) =>
    api.post(`/roles/${roleId}/screening-questions`, data),
  updateScreeningQuestion: (roleId, questionId, data) =>
    api.patch(`/roles/${roleId}/screening-questions/${questionId}`, data),
  deleteScreeningQuestion: (roleId, questionId, expectedVersion) =>
    api.delete(`/roles/${roleId}/screening-questions/${questionId}`, {
      params: { expected_version: expectedVersion },
    }),
  uploadJobSpec: (roleId, file, expectedVersion) => {
    const form = new FormData();
    form.append('file', file);
    if (Number.isInteger(expectedVersion)) {
      form.append('expected_version', String(expectedVersion));
    }
    return api.post(`/roles/${roleId}/upload-job-spec`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  listTasks: (roleId) => api.get(`/roles/${roleId}/tasks`),
  addTask: (roleId, taskId, expectedVersion) => api.post(`/roles/${roleId}/tasks`, {
    task_id: taskId,
    expected_version: expectedVersion,
  }),
  removeTask: (roleId, taskId, expectedVersion) => api.delete(
    `/roles/${roleId}/tasks/${taskId}`,
    { params: { expected_version: expectedVersion } },
  ),
  listApplications: (roleId, params = {}) => api.get(`/roles/${roleId}/applications`, { params }),
  listApplicationsPage: (roleId, params = {}) => api.get(`/roles/${roleId}/applications`, {
    params: { ...params, paginated: true },
  }),
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
  // WS2 — curated multi-candidate client submittal packs. POST mints a frozen,
  // client-safe snapshot of the selected candidates for one role and returns
  // { id, token, url_path, expires_at }; GET lists packs for the role (audit +
  // revoke); DELETE revokes one pack by id (org-scoped).
  listSubmittalPacks: (roleId) => api.get(`/roles/${roleId}/submittal-packs`),
  createSubmittalPack: (roleId, { applicationIds, title, notes, expiresIn = '7d' }) =>
    api.post(`/roles/${roleId}/submittal-packs`, {
      application_ids: applicationIds,
      title: title || null,
      notes: notes || null,
      expires_in: expiresIn,
    }),
  revokeSubmittalPack: (packId) => api.delete(`/submittal-packs/${packId}`),
  listApplicationEvents: (applicationId, params = {}) => api.get(`/applications/${applicationId}/events`, { params }),
  // Drop a recruiter note on the candidate's timeline. Works with or without
  // a linked assessment. `forAgent` (default true) makes the note visible to
  // the recruiting agent as standing per-candidate guidance. `extra` carries
  // the structured "add info" fields for the ranking / link quick-adds
  // (kind / ranking / link_url / link_label); omitted ⇒ a plain freeform note.
  addApplicationNote: (applicationId, note, forAgent = true, extra = {}) =>
    api.post(`/applications/${applicationId}/notes`, { note, for_agent: forAgent, ...extra }),
  generateApplicationInterviewDebrief: (applicationId, data = {}) => api.post(`/applications/${applicationId}/interview-debrief`, data),
  // Structured interview feedback — a recruiter's record of what happened in an
  // interview (round, recommendation, optional 5-Ds ratings, per-probe results,
  // notes). Recruiter-internal; joined by the score↔outcome calibration script.
  listInterviewFeedback: (applicationId) =>
    api.get(`/applications/${applicationId}/interview-feedback`),
  createInterviewFeedback: (applicationId, data) =>
    api.post(`/applications/${applicationId}/interview-feedback`, data),
  updateInterviewFeedback: (applicationId, feedbackId, data) =>
    api.patch(`/applications/${applicationId}/interview-feedback/${feedbackId}`, data),
  deleteInterviewFeedback: (applicationId, feedbackId) =>
    api.delete(`/applications/${applicationId}/interview-feedback/${feedbackId}`),
  // Scorecard lifecycle over the same interview_feedback rows: an interviewer
  // drafts and submits their OWN card, and the panel summary tallies the
  // submitted cards. Keyed per (application, interviewer) so re-posting edits
  // in place.
  listScorecards: (applicationId) =>
    api.get(`/applications/${applicationId}/interview-feedback`),
  getScorecardSummary: (applicationId) =>
    api.get(`/applications/${applicationId}/scorecards/summary`),
  upsertScorecard: (applicationId, data) =>
    api.post(`/applications/${applicationId}/scorecards`, data),
  submitScorecard: (applicationId, feedbackId) =>
    api.post(`/applications/${applicationId}/scorecards/${feedbackId}/submit`),
  // Agent-draft the caller's scorecard from a linked interview transcript. The
  // agent authors the DRAFT; the interviewer reviews, edits, and submits it. The
  // agent never submits. Optional { interview_id } picks a specific interview.
  draftScorecardFromTranscript: (applicationId, data = {}) =>
    api.post(`/applications/${applicationId}/scorecards/draft-from-transcript`, data),
  downloadApplicationReport: (applicationId) => api.get(`/applications/${applicationId}/report.pdf`, { responseType: 'blob' }),
  downloadApplicationDocument: (applicationId, docType = 'cv', config = {}) =>
    api.get(`/applications/${applicationId}/documents/${docType}`, { responseType: 'blob', ...config }),
  createApplication: (roleId, data) => api.post(`/roles/${roleId}/applications`, data),
  // Add a SOURCED prospect (pre-applied lead) to a role — un-scored, no decision.
  createSourcedCandidate: (roleId, data) => api.post(`/roles/${roleId}/sourced-candidates`, data),
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
  // Provider-neutral ATS hand-back. The backend resolves the application's
  // owning provider (Workable or Bullhorn) and applies the same target-stage
  // contract without making the role UI branch on endpoint names.
  moveApplicationToAtsStage: (applicationId, data) =>
    api.post(`/applications/${applicationId}/ats/move-stage`, data),
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
  // Body: { fetch_cvs, refresh_cvs, pre_screen, refresh_pre_screen,
  //         score: 'none'|'new'|'all', sync_graph, refresh_graph }.
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
  backgroundJobRun: (runId) => api.get(`/background-jobs/runs/${runId}`),
  // Sourcing assist (copy-paste artefacts — no LinkedIn API/scraping).
  // Deterministic X-ray + LinkedIn boolean plus a metered refined expansion.
  sourcingSearches: (roleId) => api.post(`/roles/${roleId}/sourcing-searches`),
  // Paste-a-profile first-touch outreach draft. tone: warm|direct, channel: linkedin|email.
  outreachDraft: (roleId, data) => api.post(`/roles/${roleId}/outreach-draft`, data),
  // Distribute a PUBLISHED role: copy-paste artefacts (LinkedIn post + share URLs)
  // + the org careers XML feed URL. Returns { published: false } before publish.
  distribution: (roleId) => api.get(`/roles/${roleId}/distribution`),
  createAssessment: (applicationId, data) => api.post(`/applications/${applicationId}/assessments`, data),
  retakeAssessment: (applicationId, data) => api.post(`/applications/${applicationId}/assessments/retake`, data),
};
