import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import '../../styles/16-job-pipeline.css';
import { useParams, useNavigate } from 'react-router-dom';
import {
  BriefcaseBusiness,
  ChevronDown,
  RefreshCw,
  Share2,
  Sparkles,
} from 'lucide-react';

import * as apiClient from '../../shared/api';
import { prefetchDocumentBlob } from '../../shared/api/documentCache';
import { useToast } from '../../context/ToastContext';
import { useJobStatus } from '../../contexts/JobStatusContext';
import { Dialog, Button, PageLoader, Spinner } from '../../shared/ui/TaaliPrimitives';
import { readCache, writeCache } from '../../shared/api/resourceCache';
import { RoleViewTabs, useRoleView } from './RoleViewTabs';
import { HiringTeamPanel } from './HiringTeamPanel';
import { useRoleProgressPolling } from './useRoleProgressPolling';
import { parseJobSpec, FormattedJobSpecSection } from './jobSpecFormatting';
import { RequisitionSpecSections, JobStatusControl, ClientControl } from './RequisitionSpecSections';
import { clientApi } from '../clients/api';
import { RoleAgentSettingsTab } from './RoleAgentSettingsTab';
import { ProcessCandidatesDialog } from './ProcessCandidatesDialog';
import SubmittalPackDialog from './SubmittalPackDialog';
import { useAgentStatus } from '../../shared/layout/AgentBar';
import { AgentHeader, buildAgentPropFromStatus } from '../../shared/layout/AgentHeader';
import { AgentLoop, motionSafeScrollBehavior } from '../../shared/motion';
// AgentRail (the legacy left "cockpit rail") was retired with the v3
// role detail layout — top AgentBar replaces it. Component file stays
// in the tree until any other surface that may import it is also
// migrated; remove that import here to avoid unused-import warnings.
import { BackgroundJobsToaster } from '../candidates/BackgroundJobsToaster';
import { CandidateSheet } from '../candidates/CandidateSheet';
// CandidatesDirectoryPage is no longer embedded on the role detail —
// the Candidates tab now renders a canvas-spec inline ctable directly.
// Standalone /candidates route still uses the directory.
import { CandidateTriageDrawer, candidateReportHref } from '../candidates/CandidateTriageDrawer';
import { ScoreProvenance } from '../candidates/ScoreProvenance';
import { useCandidateTriage } from './useCandidateTriage';
import { RoleSpecEditPanel } from './RoleSpecEditPanel';
import { DistributeRolePanel } from './DistributeRolePanel';
import { AtsTypeTag, atsTypeColumnLabel, roleAtsType } from './atsType';
import { getErrorMessage, trimOrUndefined, formatStatusLabel, renderJobPipelineScoreCell } from '../candidates/candidatesUiUtils';
import {
  formatCount,
  budgetTile,
  applicationFunnelBucket,
  awaitingHitlFromDecisions,
  decisionPendingFromCounts,
} from '../../shared/metrics';
import { FunnelBoard } from '../../shared/ui/FunnelBoard';
import { KpiStrip } from '../../shared/ui/KpiStrip';

const EMPTY_PROGRESS = { status: 'idle', total: 0, scored: 0, errors: 0, include_scored: false };
const EMPTY_FETCH_PROGRESS = { status: 'idle', total: 0, fetched: 0, errors: 0 };
const EMPTY_PRE_SCREEN_PROGRESS = { status: 'idle', total: 0, processed: 0, errors: 0, refresh: false };

// Mirror of backend settings.PRE_SCREEN_THRESHOLD (config.py). The agent's
// auto-scoring pass (_auto_enqueue_scoring) skips unscored candidates whose
// pre-screen score sits below this cutoff — they were screened OUT, and
// re-scoring them just reproduces the same verdict.
const PRE_SCREEN_FILTER_THRESHOLD = 30;
// Kanban columns + segmented stage filters — keys are the shared funnel
// buckets (applicationFunnelBucket) so they read identically to the funnel.
const PIPELINE_STAGE_ORDER = [
  { key: 'applied', label: 'Applied', countLabel: 'new' },
  { key: 'scored', label: 'Scored', countLabel: 'ready to invite' },
  { key: 'invited', label: 'Invited', countLabel: 'awaiting response' },
  { key: 'completed', label: 'Completed', countLabel: 'need a decision' },
  { key: 'advanced', label: 'Advanced', countLabel: 'with you' },
];

// Hover-intent CV prefetch. A recruiter sweeping the cursor down a long table
// would otherwise fire a presigned-S3 PDF download per row crossed — a burst
// of parallel multi-MB requests they never asked for, competing with the
// page's own traffic on the UAE link. Gate it: only prefetch when the pointer
// rests on a row for HOVER_INTENT_MS, and cap concurrent hover prefetches so a
// held-down scroll can't queue dozens at once.
const HOVER_INTENT_MS = 200;
const HOVER_PREFETCH_MAX = 3;
let hoverPrefetchActive = 0;
const makeHoverPrefetch = () => {
  let timer = null;
  const start = (applicationId) => {
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      timer = null;
      if (hoverPrefetchActive >= HOVER_PREFETCH_MAX) return;
      hoverPrefetchActive += 1;
      Promise.resolve(prefetchDocumentBlob({ applicationId, docType: 'cv' }))
        .catch(() => {})
        .finally(() => { hoverPrefetchActive = Math.max(0, hoverPrefetchActive - 1); });
    }, HOVER_INTENT_MS);
  };
  const cancel = () => {
    if (timer) { window.clearTimeout(timer); timer = null; }
  };
  return { start, cancel };
};

const normalizeThreshold = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '';
  return String(Math.max(0, Math.min(100, Math.round(numeric))));
};

const formatRelativeShort = (value) => {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  const diffMs = Date.now() - parsed.getTime();
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 60) return `${Math.max(1, minutes)}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
};

const buildApplicationTitle = (application) => (
  application?.candidate_name
  || application?.candidate_email
  || `Candidate #${application?.candidate_id || application?.id || '—'}`
);

const resolveAssessmentId = (application) => (
  application?.score_summary?.assessment_id
  || application?.valid_assessment_id
  || null
);

const resolveOptionalPercent = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.max(0, Math.min(100, Math.round(numeric)));
};

// Raw pipeline_stage → a clean, consistently-cased label. PIPELINE_STAGE_ORDER is
// keyed by FUNNEL buckets (scored/completed), so it can't label the raw stages
// in_assessment/review — this does, and never leaves a value lower-cased.
const PIPELINE_STAGE_LABELS = {
  applied: 'Applied',
  invited: 'Invited',
  in_assessment: 'In assessment',
  review: 'Review',
  advanced: 'Advanced',
};
const formatStageLabel = (stage) => (
  PIPELINE_STAGE_LABELS[stage]
  || (stage ? stage.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : '—')
);

// Humanize a REAL agent decision's recommendation enum. Only ever called with an
// actual AgentDecision.recommendation — the UI must NEVER fabricate one from a
// score band (that reads as a real, actionable decision when it isn't).
const DECISION_LABELS = {
  advance_to_interview: 'Advance to interview',
  send_assessment: 'Send assessment',
  resend_assessment_invite: 'Resend invite',
  reject: 'Reject',
  skip_assessment_reject: 'Reject',
  escalate_low_confidence: 'Needs your review',
};
const formatDecisionLabel = (recommendation) => {
  const key = String(recommendation || '').toLowerCase();
  if (!key) return null;
  return DECISION_LABELS[key] || key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
};

// The "what's actually needed" lens — complementary to the funnel stage, and
// HONEST about whether a decision is genuinely pending. 'Decision ready' shows
// ONLY when a real agent decision is queued; a completed assessment with none
// reads as 'Completed — your decision' (a human call); a candidate the recruiter
// is interviewing in Workable reads as 'With recruiter'.
const resolvePipelineCardFooterStatus = (application, pendingDecision = null) => {
  const stage = String(application?.pipeline_stage || '').toLowerCase();
  const outcome = String(application?.application_outcome || '').toLowerCase();
  if (outcome === 'rejected') return 'Rejected';
  if (outcome === 'hired') return 'Hired';
  if (stage === 'applied') return 'Not invited';
  if (stage === 'invited') return 'Awaiting start';
  if (stage === 'in_assessment') return 'Assessment live';
  if (stage === 'advanced') return 'With recruiter';
  if (stage === 'review') {
    if (pendingDecision) return 'Decision ready';
    return resolveAssessmentId(application) ? 'Completed — your decision' : 'With recruiter';
  }
  return resolveAssessmentId(application) ? 'Assessment linked' : 'No assessment yet';
};

export const JobPipelinePage = ({ onNavigate, onViewCandidate, NavComponent = null }) => {
  const { roleId } = useParams();
  const navigate = useNavigate();
  const rolesApi = apiClient.roles;
  const tasksApi = 'tasks' in apiClient ? apiClient.tasks : null;
  const { showToast } = useToast();
  const {
    jobs,
    processJobs,
    trackRole,
    trackRoleProcess,
  } = useJobStatus() ?? {};
  void onViewCandidate;

  const numericRoleId = Number(roleId);
  // Batch progress is owned by the global JobStatusContext — single source of truth.
  const batchScoreProgress = jobs?.[numericRoleId] ?? EMPTY_PROGRESS;
  // Live agent status for THIS role — backend serves /roles/{id}/agent/status
  // with monthly_spent_cents + monthly_budget_cents + pending_decisions +
  // last_activity. Polled every 30s, paused when the tab is hidden.
  const { status: agentStatus, setStatus: setAgentStatus, refetch: refetchAgentStatus } = useAgentStatus(Number.isFinite(numericRoleId) ? numericRoleId : null);
  // Per-feature spend breakdown for the role budget panel. Refetched
  // whenever the role's monthly spend ticks (a coarse proxy for "new
  // usage events landed"); cheap enough to call inline.
  const [usageBreakdown, setUsageBreakdown] = useState(null);
  useEffect(() => {
    if (!Number.isFinite(numericRoleId)) return undefined;
    if (!apiClient?.agent?.usageBreakdown) return undefined;
    let cancelled = false;
    apiClient.agent.usageBreakdown(numericRoleId)
      .then((res) => { if (!cancelled) setUsageBreakdown(res?.data || null); })
      .catch(() => { if (!cancelled) setUsageBreakdown(null); });
    return () => { cancelled = true; };
  }, [numericRoleId, agentStatus?.monthly_spent_cents]);
  // Pending agent decisions for this role, keyed by application_id so the
  // Pipeline-tab kanban cards can render the real Approve/Override flow
  // inline (HANDOFF v2 §4 / canvas jobs-detail-pipeline). Polls every 30s.
  const [pendingAgentDecisions, setPendingAgentDecisions] = useState({});
  const [resolvingDecisionId, setResolvingDecisionId] = useState(null);
  const fetchPendingDecisions = useCallback(async () => {
    if (!Number.isFinite(numericRoleId)) return;
    try {
      const res = await apiClient.agent.listDecisions({
        role_id: numericRoleId,
        status: 'pending',
        limit: 50,
      });
      const list = Array.isArray(res?.data) ? res.data : [];
      const next = list.reduce((acc, decision) => {
        const appId = Number(decision?.application_id);
        if (Number.isFinite(appId)) acc[appId] = decision;
        return acc;
      }, {});
      // Bail out of setState when the poll returns the same decisions (same
      // ids, same key set) so the 30s tick doesn't re-render + re-sort the
      // whole table when nothing changed.
      setPendingAgentDecisions((prev) => {
        const prevKeys = Object.keys(prev);
        const nextKeys = Object.keys(next);
        if (prevKeys.length === nextKeys.length
          && nextKeys.every((k) => prev[k]?.id === next[k]?.id)) {
          return prev;
        }
        return next;
      });
    } catch {
      // Quiet failure — the kanban cards just fall back to the
      // score-based decision verb until next poll succeeds.
    }
  }, [numericRoleId]);
  useEffect(() => {
    void fetchPendingDecisions();
    const handle = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      void fetchPendingDecisions();
    }, 30_000);
    return () => window.clearInterval(handle);
  }, [fetchPendingDecisions]);
  const handleApproveDecision = useCallback(async (decisionId) => {
    if (!decisionId) return;
    setResolvingDecisionId(decisionId);
    try {
      await apiClient.agent.approveDecision(decisionId);
      showToast('Recommendation approved.', 'success');
      setRoleApplications((apps) => apps.map((a) => (a?.pending_decision?.id === decisionId ? { ...a, pending_decision: null } : a)));
      await fetchPendingDecisions();
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to approve recommendation.'), 'error');
    } finally {
      setResolvingDecisionId(null);
    }
  }, [fetchPendingDecisions, showToast]);
  const handleOverrideDecision = useCallback(async (decisionId) => {
    if (!decisionId) return;
    setResolvingDecisionId(decisionId);
    try {
      await apiClient.agent.overrideDecision(decisionId, { override_action: 'manual_review' });
      showToast('Recommendation overridden — the candidate stays in your queue for manual review.', 'info');
      setRoleApplications((apps) => apps.map((a) => (a?.pending_decision?.id === decisionId ? { ...a, pending_decision: null } : a)));
      await fetchPendingDecisions();
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to override recommendation.'), 'error');
    } finally {
      setResolvingDecisionId(null);
    }
  }, [fetchPendingDecisions, showToast]);
  const [role, setRole] = useState(null);
  // Workspace chips loaded once per role-workspace load. Used by the
  // role page chip editor for the "Show hidden" suppressed-chips view
  // (we need the workspace text/bucket for chips the recruiter has
  // hidden from this role).
  const [workspaceCriteria, setWorkspaceCriteria] = useState([]);
  const [criteriaBusy, setCriteriaBusy] = useState(false);
  const [criteriaSyncing, setCriteriaSyncing] = useState(false);
  const [criteriaResetting, setCriteriaResetting] = useState(false);
  const [roleTasks, setRoleTasks] = useState([]);
  const [allTasks, setAllTasks] = useState([]);
  const [roleApplications, setRoleApplications] = useState([]);
  const [fetchCvsProgress, setFetchCvsProgress] = useState(EMPTY_FETCH_PROGRESS);
  const [preScreenProgress, setPreScreenProgress] = useState(EMPTY_PRE_SCREEN_PROGRESS);
  const [processDialogOpen, setProcessDialogOpen] = useState(false);
  const [submittalDialogOpen, setSubmittalDialogOpen] = useState(false);
  const [syncingStages, setSyncingStages] = useState(false);
  const [loading, setLoading] = useState(true);
  // Set only on a cold-load failure with nothing cached to paint — drives the
  // in-page error state (with Retry) instead of stranding an empty shell.
  const [loadError, setLoadError] = useState('');
  const [savingRoleConfig, setSavingRoleConfig] = useState(false);
  const [savingAssessmentTask, setSavingAssessmentTask] = useState(false);
  const [thresholdDraft, setThresholdDraft] = useState('');
  const [suggestedThreshold, setSuggestedThreshold] = useState(null);
  const [savingThresholdMode, setSavingThresholdMode] = useState(false);
  const handleThresholdModeChange = useCallback(async (nextMode) => {
    if (!Number.isFinite(numericRoleId)) return;
    if (nextMode !== 'auto' && nextMode !== 'manual') return;
    setSavingThresholdMode(true);
    setRole((cur) => (cur ? { ...cur, auto_reject_threshold_mode: nextMode } : cur));
    try {
      await rolesApi.update(numericRoleId, { auto_reject_threshold_mode: nextMode });
      if (nextMode === 'auto') {
        try {
          const res = await rolesApi.suggestedAutoRejectThreshold(numericRoleId);
          setSuggestedThreshold(res?.data || null);
        } catch { /* leave previous suggestion */ }
      }
      showToast(nextMode === 'auto' ? 'Threshold mode set to auto — agent will pick the cut-off.' : 'Threshold mode set to manual.', 'success');
    } catch (error) {
      setRole((cur) => (cur ? { ...cur, auto_reject_threshold_mode: nextMode === 'auto' ? 'manual' : 'auto' } : cur));
      showToast(getErrorMessage(error, 'Failed to update threshold mode.'), 'error');
    } finally {
      setSavingThresholdMode(false);
    }
  }, [numericRoleId, rolesApi, showToast]);
  // Requisition->Workable job lifecycle (draft/open/filled/filled_external/
  // cancelled). The control lives on the Job Spec tab; optimistic with rollback.
  const [savingJobStatus, setSavingJobStatus] = useState(false);
  const handleSetJobStatus = useCallback(async (nextStatus) => {
    if (!Number.isFinite(numericRoleId) || !nextStatus) return;
    const previous = role?.job_status;
    if (nextStatus === previous) return;
    setSavingJobStatus(true);
    setRole((cur) => (cur ? { ...cur, job_status: nextStatus } : cur));
    try {
      const res = await rolesApi.setJobStatus(numericRoleId, nextStatus);
      if (res?.data) setRole(res.data);
      showToast('Job status updated.', 'success');
    } catch (error) {
      setRole((cur) => (cur ? { ...cur, job_status: previous } : cur));
      showToast(getErrorMessage(error, 'Failed to update job status.'), 'error');
    } finally {
      setSavingJobStatus(false);
    }
  }, [numericRoleId, role?.job_status, rolesApi, showToast]);
  // Consultancy client assignment — the org's clients (for the picker) + the
  // mutation. Lets recruiters tag a client onto ANY role, including legacy /
  // Workable-imported jobs that never went through a requisition. Optimistic
  // with rollback, same shape as the job-status control above.
  const [clients, setClients] = useState([]);
  const [savingClient, setSavingClient] = useState(false);
  useEffect(() => {
    let cancelled = false;
    clientApi
      .list()
      .then((rows) => { if (!cancelled) setClients(Array.isArray(rows) ? rows : []); })
      .catch(() => { if (!cancelled) setClients([]); });
    return () => { cancelled = true; };
  }, []);
  const handleSetClient = useCallback(async (nextClientId) => {
    if (!Number.isFinite(numericRoleId)) return;
    const prevId = role?.client_id ?? null;
    const prevName = role?.client_name ?? null;
    if ((nextClientId ?? null) === prevId) return;
    const nextName = nextClientId == null
      ? null
      : (clients.find((c) => c.id === nextClientId)?.name ?? null);
    setSavingClient(true);
    setRole((cur) => (cur ? { ...cur, client_id: nextClientId ?? null, client_name: nextName } : cur));
    try {
      const res = await rolesApi.setClient(numericRoleId, nextClientId);
      if (res?.data) setRole(res.data);
      showToast(nextClientId == null ? 'Hiring department cleared.' : 'Hiring department assigned.', 'success');
    } catch (error) {
      setRole((cur) => (cur ? { ...cur, client_id: prevId, client_name: prevName } : cur));
      showToast(getErrorMessage(error, 'Failed to update hiring department.'), 'error');
    } finally {
      setSavingClient(false);
    }
  }, [numericRoleId, role?.client_id, role?.client_name, clients, rolesApi, showToast]);
  const [refreshTick, setRefreshTick] = useState(0);
  const [interviewFocusGenerating, setInterviewFocusGenerating] = useState(false);
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const [activeView, setActiveView] = useRoleView();
  // HANDOFF v2 §4 / canvas jobs-detail-candidates — primary stage filter
  // for the Candidates tab. The segmented row above the table toggles
  // this; the embedded directory re-mounts via key so its internal
  // `stageFilters` re-seeds from the new initial value.
  const [tableStageFilter, setTableStageFilter] = useState('all');
  // Candidates-table sort: which column (`tableSortField`) and direction
  // (`tableSortBy`, default desc → strongest score / most-recent first).
  const [tableSortBy, setTableSortBy] = useState('desc');
  const [tableSortField, setTableSortField] = useState('score');
  // Click a sortable header → sort on it (desc), or flip direction if active.
  const handleTableSort = useCallback((field) => {
    setTableSortBy((dir) => (tableSortField === field ? (dir === 'asc' ? 'desc' : 'asc') : 'desc'));
    setTableSortField(field);
  }, [tableSortField]);
  // Per-row Process selection. Non-empty → Process sends just these IDs
  // and ignores stage_filter. Reset on tab switch so off-screen ticks
  // don't silently fire when the recruiter jumps tabs.
  const [selectedAppIds, setSelectedAppIds] = useState(() => new Set());
  useEffect(() => { setSelectedAppIds(new Set()); }, [tableStageFilter]);
  // Table windowing — render the first PAGE_SIZE rows and reveal more on
  // demand. A thousand-applicant role otherwise mounts thousands of <tr>
  // (each with a ScoreProvenance subtree + hover prefetch), so every poll
  // tick re-diffs the lot. Reset the window whenever the filter or sort
  // changes so "Load more" always counts from the top of the fresh list.
  const TABLE_PAGE_SIZE = 100;
  const [tableVisibleCount, setTableVisibleCount] = useState(TABLE_PAGE_SIZE);
  useEffect(() => {
    setTableVisibleCount(TABLE_PAGE_SIZE);
  }, [tableStageFilter, tableSortField, tableSortBy]);
  const [candidateSheetOpen, setCandidateSheetOpen] = useState(false);
  const [roleSheetError, setRoleSheetError] = useState('');
  const [candidateSheetError, setCandidateSheetError] = useState('');
  // The legacy slide-out <AgentSettingsPanel> drawer state has been
  // retired — the canvas-spec Agent settings tab on this page owns
  // the same controls inline. See the AgentBar onPause handler below.
  const [savingRoleSheet, setSavingRoleSheet] = useState(false);
  // Job Specification tab is read-first: it shows the spec, and this flips it
  // into the inline edit form.
  const [editingSpec, setEditingSpec] = useState(false);
  const [addingCandidate, setAddingCandidate] = useState(false);
  // Only the most recently started loadRoleWorkspace may write state, so a
  // slow earlier load can't clobber fresher state (e.g. revert an optimistic
  // agent toggle to OFF). loadedRoleIdRef marks the last fully-loaded role so
  // a warm revalidate skips the (stale) cache repaint.
  const loadSeqRef = useRef(0);
  const loadedRoleIdRef = useRef(null);
  // One hover-intent controller for the whole page (rows + kanban cards share
  // it, so moving between them cancels the prior pending prefetch).
  const hoverPrefetchRef = useRef(null);
  if (!hoverPrefetchRef.current) hoverPrefetchRef.current = makeHoverPrefetch();

  const loadRoleWorkspace = useCallback(async () => {
    if (!Number.isFinite(numericRoleId)) return;
    const seq = (loadSeqRef.current += 1);
    // Stale-while-revalidate on a cold load (first visit to this role id):
    // paint cache, revalidate silently. A warm refresh (revalidate after a
    // mutation like the agent toggle or budget save) must NOT repaint from
    // cache — it lags the optimistic state and would flip it back.
    const cacheKey = `role-workspace:${numericRoleId}`;
    const isColdForRole = loadedRoleIdRef.current !== numericRoleId;
    const cached = isColdForRole ? readCache(cacheKey) : null;
    setLoadError('');
    if (cached?.data) {
      const c = cached.data;
      setRole(c.role || null);
      setRoleTasks(Array.isArray(c.roleTasks) ? c.roleTasks : []);
      setRoleApplications(Array.isArray(c.roleApplications) ? c.roleApplications : []);
      setWorkspaceCriteria(Array.isArray(c.workspaceCriteria) ? c.workspaceCriteria : []);
      setLoading(false);
      // Painted data for this role — later revalidates are warm (no repaint).
      loadedRoleIdRef.current = numericRoleId;
    } else if (isColdForRole) {
      setLoading(true);
    }
    try {
      // Two separate fetches (open + rejected) at the backend's 2000-row
      // ceiling — splits the budget so a long reject history can't crowd
      // open candidates out, and avoids the 500-row default that would
      // silently truncate thousand-applicant roles.
      const appsQuery = (outcome) => ({ sort_by: 'pre_screen_score', sort_order: 'desc', application_outcome: outcome, limit: 2000 });
      const [roleRes, tasksRes, openAppsRes, rejectedAppsRes, batchStatusRes, fetchStatusRes, preScreenStatusRes, orgCriteriaRes] = await Promise.all([
        rolesApi.get(numericRoleId),
        rolesApi.listTasks(numericRoleId),
        rolesApi.listApplications(numericRoleId, appsQuery('open')),
        rolesApi.listApplications(numericRoleId, appsQuery('rejected')),
        rolesApi.batchScoreStatus(numericRoleId),
        rolesApi.fetchCvsStatus(numericRoleId),
        rolesApi.batchPreScreenStatus(numericRoleId).catch(() => ({ data: EMPTY_PRE_SCREEN_PROGRESS })),
        // Workspace chips for the suppressed-chips ("hidden from this
        // role") view in the chip editor. Defensive: optional-chained
        // call + .catch so a missing API client or transient failure
        // doesn't blow up the whole role workspace load.
        Promise.resolve(apiClient.organizations?.listCriteria?.() ?? { data: [] })
          .catch(() => ({ data: [] })),
      ]);
      // A newer load started while we were in flight — drop this stale result.
      if (seq !== loadSeqRef.current) return;
      loadedRoleIdRef.current = numericRoleId;
      const nextRole = roleRes?.data || null;
      setRole(nextRole);
      setWorkspaceCriteria(Array.isArray(orgCriteriaRes?.data) ? orgCriteriaRes.data : []);
      setThresholdDraft(nextRole?.score_threshold != null ? String(nextRole.score_threshold) : '');
      // Fetch the agent's threshold recommendation when the role is
      // in auto mode so the panel shows it without waiting for click.
      if (nextRole?.auto_reject_threshold_mode === 'auto' && Number.isFinite(numericRoleId)) {
        rolesApi.suggestedAutoRejectThreshold(numericRoleId)
          .then((res) => setSuggestedThreshold(res?.data || null))
          .catch(() => setSuggestedThreshold(null));
      } else setSuggestedThreshold(null);
      const nextTasks = Array.isArray(tasksRes?.data) ? tasksRes.data : [];
      setRoleTasks(nextTasks);
      // Dedupe by id — defensive against any backend overlap.
      const byId = new Map();
      for (const a of [...(openAppsRes?.data || []), ...(rejectedAppsRes?.data || [])]) {
        if (a?.id != null && !byId.has(a.id)) byId.set(a.id, a);
      }
      const nextApps = [...byId.values()];
      setRoleApplications(nextApps);
      const nextCriteria = Array.isArray(orgCriteriaRes?.data) ? orgCriteriaRes.data : [];
      // Refresh the SWR cache so the next visit paints instantly.
      writeCache(cacheKey, {
        role: nextRole,
        roleTasks: nextTasks,
        roleApplications: nextApps,
        workspaceCriteria: nextCriteria,
      });
      // Hand off batch status to the global context — it owns display state.
      // If a batch is already running when this page loads, make the context
      // track it immediately (no waiting for the next 10s discovery poll).
      const initBatchStatus = String(batchStatusRes?.data?.status || '').toLowerCase();
      if (['running', 'cancelling', 'cancelled', 'completed'].includes(initBatchStatus)) {
        trackRole?.(numericRoleId);
      }
      setFetchCvsProgress(fetchStatusRes?.data || EMPTY_FETCH_PROGRESS);
      setPreScreenProgress(preScreenStatusRes?.data || EMPTY_PRE_SCREEN_PROGRESS);
    } catch (error) {
      // Don't wipe a cached paint if a background revalidate fails — only
      // surface a hard failure when there was nothing to show in the first
      // place (cold load with no cache).
      if (isColdForRole && !cached?.data) {
        setRole(null);
        setRoleTasks([]);
        setRoleApplications([]);
        setLoadError(getErrorMessage(error, 'Failed to load this job.'));
        showToast(getErrorMessage(error, 'Failed to load role pipeline.'), 'error');
      }
    } finally {
      setLoading(false);
    }
  }, [numericRoleId, rolesApi, showToast, trackRole]);

  // Patch a SINGLE application row after a single-candidate mutation instead
  // of reloading the whole workspace (which refetches up to 2×2000 rows over
  // the UAE→us-east4 link for one rejected/moved candidate). Refetches just
  // that application AND the (cheap) role record so the FunnelBoard + KPI strip
  // aggregates don't stay stale all session — the idle page has no periodic
  // workspace reload to reconcile them. A missing id or a failed refetch is a
  // no-op; the derived buckets (active/rejected) re-derive from the merged row.
  const patchApplicationRow = useCallback(async (applicationId) => {
    const numericId = Number(applicationId);
    if (!Number.isFinite(numericId) || !rolesApi?.getApplication) return;
    try {
      const [appRes, roleRes] = await Promise.all([
        rolesApi.getApplication(numericId),
        Number.isFinite(numericRoleId) && rolesApi?.get
          ? rolesApi.get(numericRoleId).catch(() => null)
          : Promise.resolve(null),
      ]);
      const fresh = appRes?.data;
      if (fresh?.id) {
        setRoleApplications((apps) => {
          const exists = apps.some((a) => Number(a?.id) === numericId);
          return exists
            ? apps.map((a) => (Number(a?.id) === numericId ? fresh : a))
            : [...apps, fresh];
        });
      }
      // Merge ONLY the aggregate fields the funnel/KPIs read — a full setRole
      // would revert optimistic role edits (agent on/off, budget) that the
      // /agent/status poll hasn't caught up to yet.
      const nextRole = roleRes?.data;
      if (nextRole) {
        setRole((cur) => (cur ? {
          ...cur,
          stage_counts: nextRole.stage_counts ?? cur.stage_counts,
          active_candidates_count: nextRole.active_candidates_count ?? cur.active_candidates_count,
          pending_decisions_by_type: nextRole.pending_decisions_by_type ?? cur.pending_decisions_by_type,
        } : cur));
      }
    } catch {
      // Quiet — the row keeps its last-known state until the next full load.
    }
  }, [rolesApi, numericRoleId]);

  // Pull this role's candidates' CURRENT Workable stages on demand — the manual
  // recovery for when the periodic sync lags or a Taali-side move raced a stale
  // sync snapshot. Updates workable_stage only (fast; no re-import / scoring).
  const handleSyncWorkableStages = useCallback(async () => {
    if (syncingStages) return;
    setSyncingStages(true);
    try {
      const res = await rolesApi.refreshWorkableStages(numericRoleId);
      const data = res?.data || {};
      showToast(data.message || 'Synced stages from Workable.', data.updated > 0 ? 'success' : 'info');
      if (data.updated > 0) await loadRoleWorkspace();
    } catch (error) {
      showToast(getErrorMessage(error, 'Could not sync stages from Workable.'), 'error');
    } finally {
      setSyncingStages(false);
    }
  }, [numericRoleId, rolesApi, showToast, loadRoleWorkspace, syncingStages]);

  useEffect(() => {
    void loadRoleWorkspace();
  }, [loadRoleWorkspace]);

  // The org-wide task list feeds the role-edit task picker on the Job
  // Specification tab and the assessment-task picker on the Agent settings
  // tab. It's not needed for the candidate table, so defer the fetch until
  // one of those tabs is first opened — one fewer request on every role-page
  // load. `loadedAllTasksRef` keeps it to a single fetch.
  const loadedAllTasksRef = useRef(false);
  useEffect(() => {
    if ((activeView !== 'activity' && activeView !== 'role-fit') || loadedAllTasksRef.current || !tasksApi?.list) return undefined;
    let cancelled = false;
    const loadAllTasks = async () => {
      try {
        const res = await tasksApi.list();
        if (!cancelled) {
          setAllTasks(Array.isArray(res?.data) ? res.data : []);
          loadedAllTasksRef.current = true;
        }
      } catch {
        if (!cancelled) setAllTasks([]);
      }
    };
    void loadAllTasks();
    return () => {
      cancelled = true;
    };
  }, [activeView, tasksApi]);

  // ── Reload applications when the global context tells us a batch finished ──
  // batchScoreProgress is read from JobStatusContext (single source of truth).
  // We track the previous status in a ref so we detect the running→terminal
  // transition and trigger a workspace reload to refresh candidate scores.
  const prevBatchStatusRef = useRef('');
  useEffect(() => {
    const current = String(batchScoreProgress?.status || '').toLowerCase();
    const prev = prevBatchStatusRef.current;
    prevBatchStatusRef.current = current;
    if (prev === 'running' && (current === 'completed' || current === 'cancelled')) {
      void loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
    }
  }, [batchScoreProgress?.status, loadRoleWorkspace]);

  // Poll fetchCvs + pre-screen progress while a job runs (pauses when the tab
  // is hidden, reloads the workspace on completion). Extracted to a hook.
  useRoleProgressPolling({
    numericRoleId,
    rolesApi,
    fetchCvsProgress,
    preScreenProgress,
    setFetchCvsProgress,
    setPreScreenProgress,
    loadRoleWorkspace,
    bumpRefreshTick: () => setRefreshTick((value) => value + 1),
  });

  const rejectedApplications = useMemo(() => (
    roleApplications.filter((application) => application?.application_outcome === 'rejected')
  ), [roleApplications]);
  const activeApplications = useMemo(() => (
    roleApplications.filter((application) => application?.application_outcome === 'open')
  ), [roleApplications]);

  const unscoredApplications = useMemo(() => (
    activeApplications.filter((application) => application?.cv_match_score == null)
  ), [activeApplications]);

  // "New CVs" must reflect what the agent's auto-scoring pass would actually
  // pick up (backend _auto_enqueue_scoring), not every null-score app —
  // otherwise a pre-screen-filtered cohort reads as "35 ready to score" while
  // the agent (correctly) touches none of them, and looks stuck. Held back:
  // pre-screen-filtered (screened OUT below the global cutoff, no newer CV
  // since that run) and no-CV (nothing to score). The backend's third skip —
  // error backoff — isn't visible in the list payload, so it isn't mirrored.
  const newCvBreakdown = useMemo(() => {
    let scoreable = 0;
    let preScreenFiltered = 0;
    let noCv = 0;
    for (const application of unscoredApplications) {
      // has_cv_text mirrors the auto-scorer's exact cv_text filter (a CV file
      // can exist while extraction produced nothing). Fall back to the file
      // metadata for cached payloads written before the field existed.
      const hasCvText = application?.has_cv_text
        ?? Boolean(application?.cv_uploaded_at || application?.cv_filename);
      if (!hasCvText) {
        noCv += 1;
        continue;
      }
      const cvAt = Date.parse(application?.cv_uploaded_at || '');
      const runAt = Date.parse(application?.pre_screen_run_at || '');
      const freshCv = Number.isFinite(cvAt) && Number.isFinite(runAt) && cvAt > runAt;
      const preScreen = Number(application?.pre_screen_score);
      if (Number.isFinite(preScreen) && preScreen < PRE_SCREEN_FILTER_THRESHOLD && !freshCv) {
        preScreenFiltered += 1;
      } else {
        scoreable += 1;
      }
    }
    return { scoreable, preScreenFiltered, noCv };
  }, [unscoredApplications]);

  const thresholdValue = useMemo(
    () => resolveOptionalPercent(role?.score_threshold),
    [role?.score_threshold]
  );
  const belowThresholdCount = useMemo(() => {
    if (thresholdValue == null) return 0;
    return activeApplications.filter((application) => {
      const score = Number(application?.pre_screen_score);
      return Number.isFinite(score) && score < thresholdValue;
    }).length;
  }, [activeApplications, thresholdValue]);

  // Role KPI row — role-scoped mirror of the org strip on Home / Jobs:
  // In pipeline · New CVs · Below threshold · Awaiting you · Role budget · MTD.
  // "Awaiting you" is this role's pending-decision queue; "Advanced" lives in
  // the funnel summary above. Formatting comes from src/shared/metrics.
  const pipelineStats = useMemo(() => {
    const monthlySpentCents = Number(agentStatus?.monthly_spent_cents || 0);
    // Cap from the role record (refreshed on save); agent/status only echoes
    // it on a 30s poll and lags a fresh edit.
    const monthlyBudgetCents = Number(
      role?.monthly_usd_budget_cents
      ?? agentStatus?.monthly_budget_cents
      ?? 0
    );
    const budget = budgetTile(monthlySpentCents, monthlyBudgetCents);
    // Awaiting you = this role's pending agent recommendations (HITL — what
    // needs your call), NOT every scored candidate. Scored/Completed candidates
    // the agent hasn't ruled on yet are "decision pending" (shown as context).
    const awaitingCount = awaitingHitlFromDecisions(role?.pending_decisions_by_type);
    const notYetDecided = decisionPendingFromCounts(role?.stage_counts, role?.pending_decisions_by_type);
    // Shaped as <KpiStrip> tiles so the role KPI row is the SAME card as the
    // home / jobs-list strips (no bespoke .stat cards, no black tile).
    // "Awaiting you" carries the purple-tint emphasis, matching home.
    return [
      {
        key: 'active',
        label: 'In pipeline',
        value: formatCount(role?.active_candidates_count || activeApplications.length || 0),
        sub: `${formatCount(role?.stage_counts?.completed || 0)} completed`,
      },
      (() => {
        const { scoreable, preScreenFiltered, noCv } = newCvBreakdown;
        const held = [
          preScreenFiltered > 0 ? `${formatCount(preScreenFiltered)} pre-screen filtered` : null,
          noCv > 0 ? `${formatCount(noCv)} no CV` : null,
        ].filter(Boolean);
        return {
          key: 'unscored',
          label: 'New CVs',
          value: formatCount(scoreable),
          sub: held.length
            ? [scoreable > 0 ? 'ready to score' : null, ...held].filter(Boolean).join(' · ')
            : (scoreable > 0 ? 'ready to score' : 'all visible CVs scored'),
          subTitle: held.length
            ? 'Pre-screen filtered candidates scored below the pre-screen cutoff and are only re-run when a newer CV arrives; no-CV candidates have nothing to score. Neither is picked up by auto-scoring.'
            : null,
        };
      })(),
      {
        key: 'below-threshold',
        label: 'Below threshold',
        value: formatCount(belowThresholdCount),
        sub: thresholdValue != null ? `flagged at < ${thresholdValue}` : 'set a reject threshold',
      },
      {
        key: 'awaiting',
        label: 'Awaiting you',
        value: formatCount(awaitingCount),
        emph: awaitingCount > 0,
        sub: notYetDecided > 0
          ? `${formatCount(notYetDecided)} not yet decided by the agent`
          : (awaitingCount > 0 ? 'all flagged' : 'queue clear'),
        subTitle: notYetDecided > 0
          ? "Scored candidates the agent hasn't ruled on yet — usually because the agent is paused on this role. Each is decided from its current score when the agent runs; these are not waiting on you."
          : null,
      },
      {
        key: 'spend',
        label: 'Role budget · MTD',
        value: budget.value,
        unit: monthlyBudgetCents > 0 ? budget.unit : null,
        bar: monthlyBudgetCents > 0 ? budget : null,
        sub: budget.sub,
      },
    ];
  }, [activeApplications.length, agentStatus, belowThresholdCount, newCvBreakdown, role, thresholdValue]);

  const groupedApplications = useMemo(() => [
    ...PIPELINE_STAGE_ORDER.map((stage) => ({
      ...stage,
      items: activeApplications.filter((application) => applicationFunnelBucket(application) === stage.key),
    })),
    { key: 'rejected', label: 'Rejected', countLabel: 'closed', items: rejectedApplications },
  ], [activeApplications, rejectedApplications]);

  // Candidates-table rows: filter by the active stage segment, then sort by
  // the chosen column. Memoized on the data + sort so the 30s decision/agent
  // polls don't re-filter and re-sort thousands of rows on every tick (this
  // used to run inline in render). Rendering is windowed via tableVisibleCount.
  const sortedTableApplications = useMemo(() => {
    const filtered = tableStageFilter === 'rejected'
      ? rejectedApplications
      : tableStageFilter === 'all'
        ? activeApplications
        : activeApplications.filter((a) => applicationFunnelBucket(a) === tableStageFilter);
    const cmpScore = (a) => {
      const raw = a?.score_summary?.taali_score
        ?? a?.taali_score
        ?? a?.assessment_score
        ?? a?.cv_match_score;
      // raw == null guard: Number(null) === 0 IS finite, so unscored sorts as a real zero without it.
      return raw != null && Number.isFinite(Number(raw)) ? Number(raw) : -1;
    };
    // Last-activity sort key — server-computed last_activity_at, with fallbacks.
    const cmpLastUpdated = (a) => {
      const raw = a?.last_activity_at || a?.updated_at || a?.created_at;
      const ms = raw ? new Date(raw).getTime() : NaN;
      return Number.isFinite(ms) ? ms : -Infinity;
    };
    const sortKey = tableSortField === 'last_updated' ? cmpLastUpdated : cmpScore;
    return [...filtered].sort((a, b) => (
      tableSortBy === 'asc' ? sortKey(a) - sortKey(b) : sortKey(b) - sortKey(a)
    ));
  }, [activeApplications, rejectedApplications, tableStageFilter, tableSortField, tableSortBy]);

  // Recruiter chips on this role (excludes derived_from_spec entries — those
  // come from the job spec parser). Used for the read-view "Recruiter
  // requirements" list + the At-a-glance count.
  const roleCriteria = useMemo(() => (
    Array.isArray(role?.criteria)
      ? role.criteria.filter((c) => !c.deleted_at && c.source !== 'derived_from_spec')
      : []
  ), [role]);
  // Everything the Agent-settings criteria editor should show + let you edit:
  // the recruiter chips AND the requirements derived from the job spec (so the
  // spec-sourced criteria are visible and editable, not hidden).
  const agentCriteria = useMemo(() => (
    Array.isArray(role?.criteria)
      ? role.criteria.filter((c) => !c.deleted_at)
      : []
  ), [role]);
  const recruiterCriteria = useMemo(() => roleCriteria.map((c) => c.text).filter(Boolean), [roleCriteria]);
  const parsedJobSpec = useMemo(() => parseJobSpec(
    role?.job_spec_text || role?.description || role?.summary || role?.job_summary || '',
    role?.name || ''
  ), [role?.description, role?.job_spec_text, role?.job_summary, role?.name, role?.summary]);
  const roleSummary = useMemo(() => (
    parsedJobSpec.summary
    || String(role?.summary || role?.job_summary || '').trim()
  ), [parsedJobSpec.summary, role?.job_summary, role?.summary]);
  const roleHighlights = useMemo(() => {
    const questions = Array.isArray(role?.interview_focus?.questions) ? role.interview_focus.questions : [];
    const triggers = Array.isArray(role?.interview_focus?.manual_screening_triggers)
      ? role.interview_focus.manual_screening_triggers
      : [];
    const items = [];
    if (role?.workable_job_id) items.push({ title: 'Workable-linked role', description: 'Candidate sync and role metadata stay anchored to your ATS source of truth.' });
    if (recruiterCriteria.length) items.push({ title: 'Recruiter-specific criteria', description: `${recruiterCriteria.length} recruiter requirement${recruiterCriteria.length === 1 ? '' : 's'} shape the CV scoring pass.` });
    if (questions.length) items.push({ title: 'Interview focus ready', description: `${questions.length} generated interview prompts are ready for the hiring loop.` });
    if (triggers.length) items.push({ title: 'Screening triggers', description: triggers.slice(0, 2).join(' · ') });
    if (!items.length) {
      items.push({ title: 'Role workspace', description: 'Tune scoring, review pipeline flow, and move quickly from screening to decision.' });
    }
    return items.slice(0, 4);
  }, [recruiterCriteria.length, role?.interview_focus?.manual_screening_triggers, role?.interview_focus?.questions, role?.workable_job_id]);

  const roleFactValues = useMemo(() => ({
    location: role?.location || role?.candidate_location || parsedJobSpec.meta.location || 'Location not captured',
    department: role?.department || parsedJobSpec.meta.department || role?.organization_name || 'Hiring team',
    employment: role?.employment_type || parsedJobSpec.meta.employmentType || 'Full-time',
  }), [parsedJobSpec.meta.department, parsedJobSpec.meta.employmentType, parsedJobSpec.meta.location, role?.candidate_location, role?.department, role?.employment_type, role?.location, role?.organization_name]);

  // NOTE: the legacy 3-step batch-action confirm flow (openConfirm /
  // runConfirmedAction / ConfirmActionDialog) was removed here — it was dead
  // (nothing called openConfirm since ProcessCandidatesDialog took over) and
  // its score branch referenced an undefined setBatchScoreProgress, a
  // ReferenceError waiting for anyone who re-wired a button to it. Batch
  // progress is owned by JobStatusContext; ProcessCandidatesDialog owns the
  // cascade run.

  const handleSaveRoleConfig = async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setSavingRoleConfig(true);
    try {
      await rolesApi.update(numericRoleId, {
        score_threshold: thresholdDraft === '' ? null : Number(normalizeThreshold(thresholdDraft)),
      });
      await loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
      showToast('Reject threshold updated.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save reject threshold.'), 'error');
    } finally {
      setSavingRoleConfig(false);
    }
  };

  // Per-role chip CRUD + sync/reset. Merge the returned chip into role.criteria —
  // a full role-workspace refetch would drag in 2× 2000-row application lists per edit.
  const handleCreateRoleCriterion = useCallback(async ({ text, bucket }) => {
    if (!Number.isFinite(numericRoleId)) return;
    setCriteriaBusy(true);
    try {
      const { data } = await rolesApi.createCriterion(numericRoleId, { text, bucket });
      if (data) setRole((cur) => cur && ({
        ...cur,
        criteria: [...(cur.criteria || []).filter((c) => c.id !== data.id), data],
      }));
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to add criterion.'), 'error');
    } finally {
      setCriteriaBusy(false);
    }
  }, [numericRoleId, rolesApi, showToast]);

  const handleUpdateRoleCriterion = useCallback(async (criterionId, updates) => {
    if (!Number.isFinite(numericRoleId)) return;
    setCriteriaBusy(true);
    try {
      const { data } = await rolesApi.updateCriterion(numericRoleId, criterionId, updates);
      if (data) setRole((cur) => cur && ({
        ...cur,
        criteria: (cur.criteria || []).map((c) => (c.id === criterionId ? data : c)),
      }));
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to update criterion.'), 'error');
    } finally {
      setCriteriaBusy(false);
    }
  }, [numericRoleId, rolesApi, showToast]);

  const handleDeleteRoleCriterion = useCallback(async (criterionId) => {
    if (!Number.isFinite(numericRoleId)) return;
    // Optimistic remove. If the chip is workspace-derived, mirror the backend
    // and append its org_criterion_id to the suppressed list.
    setRole((cur) => {
      if (!cur) return cur;
      const target = (cur.criteria || []).find((c) => c.id === criterionId);
      const orgId = target?.org_criterion_id;
      const suppressed = cur.suppressed_org_criterion_ids || [];
      return {
        ...cur,
        criteria: (cur.criteria || []).filter((c) => c.id !== criterionId),
        suppressed_org_criterion_ids: orgId != null
          ? Array.from(new Set([...suppressed, Number(orgId)]))
          : suppressed,
      };
    });
    try {
      await rolesApi.deleteCriterion(numericRoleId, criterionId);
    } catch (error) {
      // Refetch authoritative state; a stale snapshot restore would clobber
      // concurrent successful deletes of other criteria.
      await loadRoleWorkspace();
      showToast(getErrorMessage(error, 'Failed to remove criterion.'), 'error');
    }
  }, [numericRoleId, rolesApi, showToast, loadRoleWorkspace]);

  const handleSyncRoleCriteria = useCallback(async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setCriteriaSyncing(true);
    try {
      const res = await rolesApi.syncCriteriaWithWorkspace(numericRoleId);
      if (res?.data) setRole(res.data);
      showToast('Workspace updates pulled in.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to sync workspace criteria.'), 'error');
    } finally {
      setCriteriaSyncing(false);
    }
  }, [numericRoleId, rolesApi, showToast]);

  const handleResetRoleCriteria = useCallback(async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setCriteriaResetting(true);
    try {
      const res = await rolesApi.resetCriteriaToWorkspace(numericRoleId);
      if (res?.data) setRole(res.data);
      showToast('Criteria reset to workspace defaults.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to reset criteria.'), 'error');
    } finally {
      setCriteriaResetting(false);
    }
  }, [numericRoleId, rolesApi, showToast]);

  // Restore a hidden (suppressed) workspace chip on this role: re-add it
  // by calling create with the workspace text + bucket. The backend
  // doesn't drop the suppressed_org_criterion_ids entry automatically
  // here — Sync workspace would still skip the chip — so we additionally
  // remove it from the suppressed list via PATCH.
  const handleRestoreHiddenCriterion = useCallback(async (workspaceChip) => {
    if (!Number.isFinite(numericRoleId) || !workspaceChip) return;
    setCriteriaBusy(true);
    try {
      const remainingSuppressed = (role?.suppressed_org_criterion_ids || [])
        .filter((id) => Number(id) !== Number(workspaceChip.id));
      // First, drop the suppression so Sync would also re-add it next time.
      await rolesApi.update(numericRoleId, { suppressed_org_criterion_ids: remainingSuppressed });
      // Then sync to bring the chip back with full provenance (org_criterion_id set).
      const res = await rolesApi.syncCriteriaWithWorkspace(numericRoleId);
      if (res?.data) setRole(res.data);
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to restore criterion.'), 'error');
    } finally {
      setCriteriaBusy(false);
    }
  }, [numericRoleId, role, rolesApi, showToast]);

  const handleRoleSheetSubmit = async ({
    name,
    description,
    jobSpecFile,
    taskIds,
  }) => {
    if (!Number.isFinite(numericRoleId)) return;
    setSavingRoleSheet(true);
    setRoleSheetError('');
    try {
      await rolesApi.update(numericRoleId, {
        name,
        description: trimOrUndefined(description),
      });

      if (jobSpecFile && rolesApi.uploadJobSpec) {
        await rolesApi.uploadJobSpec(numericRoleId, jobSpecFile);
      }

      const nextTaskIds = new Set((taskIds || []).map((value) => Number(value)));
      const currentTaskIds = new Set((roleTasks || []).map((task) => Number(task.id)));

      if (rolesApi.addTask) {
        for (const taskId of nextTaskIds) {
          if (!currentTaskIds.has(taskId)) {
            await rolesApi.addTask(numericRoleId, taskId);
          }
        }
      }
      if (rolesApi.removeTask) {
        for (const taskId of currentTaskIds) {
          if (!nextTaskIds.has(taskId)) {
            await rolesApi.removeTask(numericRoleId, taskId);
          }
        }
      }

      if (jobSpecFile && rolesApi.regenerateInterviewFocus) {
        try {
          await rolesApi.regenerateInterviewFocus(numericRoleId);
        } catch {
          // Keep edit flow resilient if interview-focus generation is temporarily unavailable.
        }
      }

      await loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
      showToast('Role updated.', 'success');
      return true;
    } catch (error) {
      setRoleSheetError(getErrorMessage(error, 'Failed to save role.'));
      return false;
    } finally {
      setSavingRoleSheet(false);
    }
  };

  // Assign / change / clear the role's assessment task from the Agent settings
  // tab. Reuses the same role↔task link the Job spec editor writes
  // (rolesApi.addTask/removeTask) — this is a single-task convenience path, so
  // it drives the role to exactly [taskId] (or [] to clear). Multi-task A/B
  // sets are managed on the Job spec tab, which is why the settings control
  // hands off to that tab when more than one task is linked.
  const handleAssignAssessmentTask = useCallback(async (taskId) => {
    if (!Number.isFinite(numericRoleId)) return;
    setSavingAssessmentTask(true);
    try {
      const desired = taskId == null ? [] : [Number(taskId)];
      const currentIds = (roleTasks || []).map((task) => Number(task.id));
      if (rolesApi.addTask) {
        for (const id of desired) {
          if (!currentIds.includes(id)) await rolesApi.addTask(numericRoleId, id);
        }
      }
      if (rolesApi.removeTask) {
        for (const id of currentIds) {
          if (!desired.includes(id)) await rolesApi.removeTask(numericRoleId, id);
        }
      }
      await loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
      showToast(taskId == null ? 'Assessment task cleared.' : 'Assessment task assigned.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to update the assessment task.'), 'error');
    } finally {
      setSavingAssessmentTask(false);
    }
  }, [numericRoleId, roleTasks, loadRoleWorkspace, showToast]);

  const handleCandidateSubmit = async ({ email, name, position, cvFile }) => {
    if (!Number.isFinite(numericRoleId) || !rolesApi.createApplication) return;
    setAddingCandidate(true);
    setCandidateSheetError('');
    try {
      const res = await rolesApi.createApplication(numericRoleId, {
        candidate_email: email,
        candidate_name: name,
        candidate_position: trimOrUndefined(position),
      });
      if (cvFile && rolesApi.uploadApplicationCv && res?.data?.id) {
        await rolesApi.uploadApplicationCv(res.data.id, cvFile);
      }
      setCandidateSheetOpen(false);
      setActiveView('table');
      await loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
      showToast('Candidate added to this role.', 'success');
    } catch (error) {
      setCandidateSheetError(getErrorMessage(error, 'Failed to add candidate.'));
    } finally {
      setAddingCandidate(false);
    }
  };

  const handleShareRole = async () => {
    const shareUrl = `${window.location.origin}/jobs/${numericRoleId}`;
    try {
      await navigator.clipboard.writeText(shareUrl);
      showToast('Role pipeline link copied.', 'success');
    } catch {
      showToast('Copy failed. Copy the URL from your browser instead.', 'error');
    }
  };

  const handleOpenRoleSettings = () => {
    document.getElementById('role-scoring-panel')?.scrollIntoView({ behavior: motionSafeScrollBehavior('smooth'), block: 'start' });
  };

  const viewCandidateReport = useCallback((application) => {
    if (!application?.id) return;
    const navOptions = { candidateApplicationId: application.id };
    if (Number.isFinite(numericRoleId)) {
      navOptions.fromRoleId = numericRoleId;
    }
    onNavigate('candidate-report', navOptions);
  }, [numericRoleId, onNavigate]);

  // Triage drawer state, handlers and Workable-stage fetch live in the
  // useCandidateTriage hook so this page stays under the architecture
  // gate's line cap. Plain row click opens the drawer; modifier-click
  // keeps the anchor's default behaviour so the standing-report escape
  // hatch still works in a new tab.
  const {
    triageApplication,
    drawerProps: triageDrawerProps,
    handleRowClick: handlePipelineReportClick,
  } = useCandidateTriage({
    role,
    roleApplications,
    roleTasks,
    loadRoleWorkspace,
    patchApplicationRow,
    showToast,
    rolesApi,
    viewCandidateReport,
  });

  const handleRegenerateInterviewFocus = async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setInterviewFocusGenerating(true);
    try {
      await rolesApi.regenerateInterviewFocus(numericRoleId);
      await loadRoleWorkspace();
      showToast('Interview focus regenerated.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to regenerate interview focus.'), 'error');
    } finally {
      setInterviewFocusGenerating(false);
    }
  };

  // HANDOFF unified-headers.md §2-§4 — Role detail uses the single
  // AgentHeader with a role-scoped agent panel on the right. Builds the
  // panel agent prop from the polled /agent/status payload, with the
  // role's own `agentic_mode_enabled` flag deciding whether it's ON or
  // OFF. The previous role-hero + AgentBar duo collapses into this hero.
  const roleAgent = useMemo(() => {
    const enabled = Boolean(role?.agentic_mode_enabled);
    if (!agentStatus) {
      return {
        on: enabled,
        paused: false,
        pending: 0,
        spentCents: 0,
        budgetCents: Number(role?.monthly_usd_budget_cents || 0) || 5000,
        tick: enabled ? 'Loading agent status…' : null,
        inFlight: false,
      };
    }
    return buildAgentPropFromStatus(agentStatus, { isEnabled: enabled });
  }, [agentStatus, role]);

  // When the agent is actively running this role it already processes new
  // candidates and keeps stages synced on its own, so the manual "Process"
  // and "Sync" buttons are redundant mirrors. We don't remove them (they stay
  // as a manual override) — we just demote them from primary/secondary to a
  // muted state so they stop competing with "the agent's got it". HITL
  // controls (approve / reject / override / share / submittal) are untouched.
  const agentRunning = Boolean(roleAgent?.on && !roleAgent?.paused);

  // Turn-off confirm dialog state (the "also discard pending decisions" opt-in).
  // Declared with the other hooks — before any early return — so hook order
  // stays stable across the loading/loaded renders.
  const [turnOffOpen, setTurnOffOpen] = useState(false);
  const [turnOffDiscard, setTurnOffDiscard] = useState(false);

  if (loading && !role) {
    return (
      <div>
        {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
        <div className="page">
          <PageLoader />
        </div>
      </div>
    );
  }

  // Cold-load failure with nothing to paint — a real in-page error state with
  // Retry and a way back to Jobs, instead of stranding an empty shell after
  // the toast auto-dismisses. Routine for UAE users on a flaky link.
  if (!loading && !role && loadError) {
    return (
      <div>
        {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
        <div className="page">
          <div className="ctable-wrap" style={{ padding: '2rem', textAlign: 'center' }}>
            <p style={{ marginBottom: '1rem', color: 'var(--ink-2)' }}>
              {loadError} Check your connection and try again.
            </p>
            <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center' }}>
              <Button type="button" variant="primary" onClick={() => { void loadRoleWorkspace(); }}>
                Retry
              </Button>
              <Button type="button" variant="ghost" onClick={() => onNavigate('jobs')}>
                Back to Jobs
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const goToAgentSettings = () => {
    setActiveView('role-fit');
    const tabsEl = document.querySelector('.sub-tabs-sticky');
    if (tabsEl && typeof tabsEl.scrollIntoView === 'function') {
      tabsEl.scrollIntoView({ behavior: motionSafeScrollBehavior('smooth'), block: 'start' });
    }
  };

  // OFF→ON / ON→OFF / PAUSED→ON. Optimistic + fire-and-forget: flip local role
  // state in one frame; PATCH in the background. `statusPatch` mirrors the change
  // into the polled /agent/status too — the strip derives on/paused from
  // `paused_at`, so Resume MUST clear it or the box stays PAUSED until the next
  // 30s poll. On settle we refetch authoritative status; on failure, revert + toast.
  const patchAgentMode = (nextRoleFields, errorFallback, statusPatch = null) => {
    if (!Number.isFinite(numericRoleId)) return;
    setRole((cur) => (cur ? { ...cur, ...nextRoleFields } : cur));
    if (statusPatch && setAgentStatus) setAgentStatus((cur) => (cur ? { ...cur, ...statusPatch } : cur));
    rolesApi
      .update(numericRoleId, nextRoleFields)
      .then(() => { void refetchAgentStatus?.(); void loadRoleWorkspace(); })
      .catch((error) => {
        void refetchAgentStatus?.();
        void loadRoleWorkspace();
        showToast(getErrorMessage(error, errorFallback), 'error');
      });
  };

  const handleActivateAgent = (monthlyBudgetCents) => {
    if (!Number.isFinite(monthlyBudgetCents) || monthlyBudgetCents <= 0) {
      showToast('Set a monthly cap greater than $0 before activating.', 'error');
      return;
    }
    patchAgentMode(
      { agentic_mode_enabled: true, monthly_usd_budget_cents: monthlyBudgetCents },
      'Failed to turn on agent mode.',
      { paused_at: null, paused: false, paused_reason: null },
    );
  };

  // Manual SOFT pause — stop the agent and its spend, but KEEP this role's
  // pending decisions (you can still action them). Resume brings it back.
  // Distinct from Turn off (handleTurnOffAgent), which disables the agent
  // indefinitely. Optimistically flip the polled status to paused so the strip
  // morphs to amber without waiting for the 30s poll.
  const handlePauseAgent = () => {
    if (!Number.isFinite(numericRoleId)) return;
    if (setAgentStatus) {
      setAgentStatus((cur) => (cur
        ? { ...cur, paused: true, paused_at: new Date().toISOString(), paused_reason: 'paused by recruiter' }
        : cur));
    }
    apiClient.agent
      .pause(numericRoleId)
      .then(() => { void refetchAgentStatus?.(); })
      .catch((error) => {
        void refetchAgentStatus?.();
        showToast(getErrorMessage(error, 'Failed to pause agent.'), 'error');
      });
  };

  // PAUSED → ON. Clears the pause (manual or budget) server-side and kicks an
  // immediate cycle; clear it locally too for an instant flip.
  const handleResumeAgent = () => {
    if (!Number.isFinite(numericRoleId)) return;
    if (setAgentStatus) {
      setAgentStatus((cur) => (cur
        ? { ...cur, paused: false, paused_at: null, paused_reason: null }
        : cur));
    }
    apiClient.agent
      .resume(numericRoleId)
      .then(() => { void refetchAgentStatus?.(); void loadRoleWorkspace(); })
      .catch((error) => {
        void refetchAgentStatus?.();
        void loadRoleWorkspace();
        showToast(getErrorMessage(error, 'Failed to resume agent.'), 'error');
      });
  };

  // Turn the agent OFF for this role — indefinite, no auto-resume. Opens a
  // confirm: off KEEPS pending decisions by default (they stay actionable),
  // with an opt-in to also discard the queue for a clean slate.
  const handleTurnOffAgent = () => {
    setTurnOffDiscard(false);
    setTurnOffOpen(true);
  };

  const confirmTurnOffAgent = () => {
    if (!Number.isFinite(numericRoleId)) return;
    const alsoDiscard = turnOffDiscard && (roleAgent?.pending || 0) > 0;
    setTurnOffOpen(false);
    // Optimistic: roleAgent.on is driven by role.agentic_mode_enabled, so flip
    // that in one frame; zero the pending count too when discarding.
    setRole((cur) => (cur ? { ...cur, agentic_mode_enabled: false } : cur));
    if (alsoDiscard && setAgentStatus) {
      setAgentStatus((cur) => (cur ? { ...cur, pending_decisions: 0 } : cur));
    }
    rolesApi
      .update(numericRoleId, { agentic_mode_enabled: false })
      .then(() => (alsoDiscard ? apiClient.agent.discardPending(numericRoleId) : null))
      .then(() => { void refetchAgentStatus?.(); void loadRoleWorkspace(); })
      .catch((error) => {
        void refetchAgentStatus?.();
        void loadRoleWorkspace();
        showToast(getErrorMessage(error, 'Failed to turn off agent.'), 'error');
      });
  };

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
      <AgentHeader
        kicker={`${role?.name || 'Role'} · #${role?.id || '—'}`}
        title={(
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
            <span>{role?.name || 'Role'}<span className="ah-period">.</span></span>
            {/* States the mode this pipeline runs in: synced from an external
                ATS, or Taali's own full ATS. */}
            {role ? <AtsTypeTag role={role} size="sm" /> : null}
          </span>
        )}
        period={false}
        breadcrumbs={[{ label: 'Jobs', page: 'jobs' }, { label: role?.name || 'Role' }]}
        actions={(
          <>
            {/* Reverse deep-link to the Hub: when this role has pending
                agent decisions, surface a one-click jump to the Home
                review queue filtered to this role. Hidden when zero. */}
            {(roleAgent?.pending || 0) > 0 ? (
              <button
                type="button"
                className="btn btn-outline btn-sm"
                title={`${roleAgent.pending} pending agent decisions for this role`}
                onClick={() => {
                  // SPA nav — a full document reload here re-downloads the JS
                  // bundle and re-runs the auth/bootstrap chain (several extra
                  // UAE round-trips) and discards in-memory state.
                  const params = new URLSearchParams({
                    role: String(role?.id || ''),
                    status: 'pending',
                  });
                  navigate(`/home?${params.toString()}`);
                }}
              >
                {roleAgent.pending} pending → Home
              </button>
            ) : null}
            <button type="button" className="btn btn-outline btn-sm" title="Share role" onClick={handleShareRole}>
              <Share2 size={13} />
              Share
            </button>
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={() => {
                setRoleSheetError('');
                setActiveView('activity');
              }}
            >
              Edit role
            </button>
            <button
              type="button"
              className="btn btn-purple btn-sm"
              onClick={() => {
                setCandidateSheetError('');
                setCandidateSheetOpen(true);
              }}
            >
              Invite candidate <span className="arrow">→</span>
            </button>
          </>
        )}
        postTitle={(
          <div className="ah-facts">
            <div className="f"><span className="k">Location</span><span className="v">{roleFactValues.location}</span></div>
            <div className="f"><span className="k">Department</span><span className="v">{roleFactValues.department}</span></div>
            <div className="f"><span className="k">Employment</span><span className="v">{roleFactValues.employment}</span></div>
            <div className="f"><span className="k">{roleTasks.length > 1 ? 'Tasks · A/B' : 'Linked task'}</span><span className="v purple">{roleTasks.length ? roleTasks.map((t) => t.name).join(' · ') : 'Not linked yet'}</span></div>
          </div>
        )}
        agent={roleAgent}
        onActivateAgent={handleActivateAgent}
        onPauseAgent={handlePauseAgent}
        onResumeAgent={handleResumeAgent}
        onTurnOffAgent={handleTurnOffAgent}
        onAgentSettings={goToAgentSettings}
      />
      <div className="page">
        <div className="mc-cockpit-main">
        {/* Flat single-strip funnel (matches pipeline-preview): each stage cell
            stacks value + label + the agent's pending-decision chips inline, with
            the terminal Rejected cell set apart. The home hub uses the same
            variant — one funnel look across surfaces. */}
        <FunnelBoard variant="flat" stageCounts={role?.stage_counts} decisionsByType={role?.pending_decisions_by_type} scopeLabel="this role" />

        <RoleViewTabs activeView={activeView} />

        {activeView === 'pipeline' ? (
          <div className="pipeline-layout">
            <div className="kanban">
              {groupedApplications.map((stage) => {
                const visibleItems = stage.items.slice(0, 3);
                const hiddenCount = Math.max(0, stage.items.length - visibleItems.length);
                return (
                  <div key={stage.key} className="kanban-col" data-stage={stage.key}>
                    <div className="kanban-col-head">
                      <div className="title"><span className="dot" />{stage.label}</div>
                      <div className="count">{stage.items.length} · {stage.countLabel}</div>
                    </div>
                    {/* HANDOFF v2 §4 / canvas jobs-detail-pipeline — kanban
                        card per v3:
                          avatar · name + position
                          CV n% · score · ago [· LIVE]
                          (review stage only) agent recommendation block:
                            Advance / Reject + reasoning + Approve · Override
                        Approve/Override are surfaced in the
                        PendingAgentDecisionsPanel above the table for now;
                        the in-card buttons are deep-link entry points. */}
                    {visibleItems.map((application) => {
                      const cvPct = Number.isFinite(Number(application?.cv_match_score))
                        ? Math.round(Number(application.cv_match_score))
                        : null;
                      const compositeRaw = application?.score_summary?.taali_score
                        ?? application?.taali_score
                        ?? application?.assessment_score
                        ?? null;
                      const compositeScore = Number.isFinite(Number(compositeRaw))
                        ? Math.round(Number(compositeRaw))
                        : null;
                      const isLive = String(application?.pipeline_stage || '').toLowerCase() === 'in_assessment';
                      // 'review'-stage candidates bucket into the 'completed' column.
                      const isReview = stage.key === 'completed';
                      // Approve/Override act ONLY on the freshly-polled map, not
                      // the per-row snapshot (which can go stale and expose
                      // actions against an already-resolved decision).
                      const pendingDecision = pendingAgentDecisions[application?.id] || null;
                      const decisionResolving = pendingDecision?.id != null
                        && resolvingDecisionId === pendingDecision.id;
                      return (
                        <a
                          key={application.id}
                          className={`kanban-card text-left ${isReview ? 'is-review' : ''}`}
                          href={candidateReportHref(application, numericRoleId)}
                          onClick={(event) => handlePipelineReportClick(event, application)}
                          onMouseEnter={() => hoverPrefetchRef.current.start(application.id)}
                          onMouseLeave={() => hoverPrefetchRef.current.cancel()}
                        >
                          <div className="cc-top">
                            <div className="av">{buildApplicationTitle(application).slice(0, 2).toUpperCase()}</div>
                            <div className="cc-id">
                              <div className="n">{buildApplicationTitle(application)}</div>
                              <div className="pos">
                                {application?.candidate_position
                                  || application?.candidate_email
                                  || 'No position captured'}
                              </div>
                            </div>
                          </div>
                          {/* Inline meta, left-aligned to match pipeline-preview's
                              .kline: CV n% · score · LIVE · ago (LIVE before the
                              timestamp, no right-pushed spacer). */}
                          <div className="cc-line">
                            {cvPct != null ? <span>CV {cvPct}%</span> : <span className="mute">No CV score</span>}
                            {compositeScore != null ? <>
                              <span className="dot-sep">·</span>
                              <span className="score-pip">{compositeScore}</span>
                            </> : null}
                            {isLive ? <>
                              <span className="dot-sep">·</span>
                              <span className="live-pip">LIVE</span>
                            </> : null}
                            <span className="dot-sep">·</span>
                            <span>{formatRelativeShort(application?.updated_at || application?.created_at)}</span>
                          </div>
                          <ScoreProvenance
                            provenance={application?.score_summary?.score_provenance}
                            density="pill"
                          />
                          {pendingDecision ? (
                            <div className="cc-agent">
                              <div className="cc-agent-glyph" aria-hidden="true">
                                <Sparkles size={11} strokeWidth={2} />
                              </div>
                              <div className="cc-agent-body">
                                <AgentLoop kind="flow" className="cc-agent-action agent-flow-text">
                                  {formatDecisionLabel(pendingDecision.recommendation)}
                                </AgentLoop>
                                <div className="cc-agent-why">
                                  {pendingDecision.reasoning
                                    || resolvePipelineCardFooterStatus(application, pendingDecision)}
                                </div>
                                <div className="cc-agent-actions">
                                  <AgentLoop
                                    as="button"
                                    kind="flow"
                                    type="button"
                                    className="btn btn-purple btn-xs"
                                    onClick={(event) => {
                                      event.preventDefault();
                                      event.stopPropagation();
                                      void handleApproveDecision(pendingDecision.id);
                                    }}
                                    disabled={decisionResolving}
                                  >
                                    {decisionResolving ? '…' : 'Approve'}
                                  </AgentLoop>
                                  <button
                                    type="button"
                                    className="btn btn-outline btn-xs"
                                    onClick={(event) => {
                                      event.preventDefault();
                                      event.stopPropagation();
                                      void handleOverrideDecision(pendingDecision.id);
                                    }}
                                    disabled={decisionResolving}
                                  >
                                    Override
                                  </button>
                                </div>
                              </div>
                            </div>
                          ) : null}
                        </a>
                      );
                    })}
                    {hiddenCount > 0 ? (
                      <button type="button" className="kanban-card more" onClick={() => setActiveView('table')}>
                        + {hiddenCount} more →
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </div>

            {triageApplication ? (
              <div className="kanban-triage-row">
                <CandidateTriageDrawer {...triageDrawerProps} agentRunning={agentRunning} />
              </div>
            ) : null}

            {/* Role-level interview focus panel removed — interview guidance is per-candidate now,
                surfaced in the candidate score sheet (kit + screening pack). */}
          </div>
        ) : activeView === 'role-fit' ? (
          <RoleAgentSettingsTab
            role={role}
            agentStatus={agentStatus}
            roleCriteria={agentCriteria}
            workspaceCriteria={workspaceCriteria}
            criteriaBusy={criteriaBusy}
            criteriaSyncing={criteriaSyncing}
            criteriaResetting={criteriaResetting}
            onCreateCriterion={handleCreateRoleCriterion}
            onUpdateCriterion={handleUpdateRoleCriterion}
            onDeleteCriterion={handleDeleteRoleCriterion}
            onSyncCriteria={handleSyncRoleCriteria}
            onResetCriteria={handleResetRoleCriteria}
            onRestoreHiddenCriterion={handleRestoreHiddenCriterion}
            thresholdDraft={thresholdDraft}
            setThresholdDraft={setThresholdDraft}
            thresholdValue={thresholdValue}
            recruiterCriteria={recruiterCriteria}
            activeApplications={activeApplications}
            belowThresholdCount={belowThresholdCount}
            savingRoleConfig={savingRoleConfig}
            usageBreakdown={usageBreakdown}
            onSave={handleSaveRoleConfig}
            onScrollToReview={() => document.getElementById('pipeline-table')?.scrollIntoView({ behavior: motionSafeScrollBehavior('smooth'), block: 'start' })}
            onSaveBudget={async (dollars) => {
              if (!Number.isFinite(numericRoleId)) return;
              const cents = Math.max(0, Math.round(Number(dollars) * 100));
              try {
                const res = await rolesApi.update(numericRoleId, { monthly_usd_budget_cents: cents });
                // Apply the committed value at once so the cap reflects the
                // save instead of blocking the spinner on the full workspace
                // reload; revalidate the rest in the background.
                const updated = res?.data || { monthly_usd_budget_cents: cents };
                setRole((cur) => (cur ? { ...cur, ...updated } : cur));
                // The top agent strip reads the cap from the polled
                // /agent/status payload, not the role record — mirror the new
                // cap in at once (as patchAgentMode does for on/off) so the
                // strip syncs instantly instead of lagging until the next 30s
                // poll, then refetch the authoritative status in the background.
                if (setAgentStatus) setAgentStatus((cur) => (cur ? { ...cur, monthly_budget_cents: cents } : cur));
                showToast('Monthly budget updated.', 'success');
                void loadRoleWorkspace();
                void refetchAgentStatus?.();
              } catch (error) {
                showToast(getErrorMessage(error, 'Failed to update budget.'), 'error');
                throw error;
              }
            }}
            onAutonomyChange={async (key, value) => {
              if (!Number.isFinite(numericRoleId)) return;
              const labels = {
                auto_reject: 'Auto-reject',
                auto_reject_pre_screen: 'Auto-reject pre-screen only',
                auto_promote: 'Auto-promote',
                auto_skip_assessment: 'Auto skip assessment',
              };
              if (!labels[key]) return;
              setRole((cur) => (cur ? { ...cur, [key]: value } : cur));
              try {
                await rolesApi.update(numericRoleId, { [key]: value });
                showToast(
                  key === 'auto_skip_assessment'
                    ? (value
                      ? 'Auto skip assessment on — strong candidates queue for advance instead of receiving an assessment.'
                      : 'Auto skip assessment off — assessment invites resume for this role.')
                    : (value
                      ? `${labels[key]} on — agent will execute without approval.`
                      : `${labels[key]} off — every decision goes to the Decision Hub.`),
                  'success',
                );
              } catch (error) {
                setRole((cur) => (cur ? { ...cur, [key]: !value } : cur));
                showToast(getErrorMessage(error, 'Failed to update autonomy setting.'), 'error');
              }
            }}
            thresholdMode={role?.auto_reject_threshold_mode || 'manual'}
            suggestedThreshold={suggestedThreshold}
            savingThresholdMode={savingThresholdMode}
            onThresholdModeChange={handleThresholdModeChange}
            roleTasks={roleTasks}
            allTasks={allTasks}
            onAssignAssessmentTask={handleAssignAssessmentTask}
            savingAssessmentTask={savingAssessmentTask}
          />
        ) : activeView === 'activity' ? (
          // HANDOFF v2 §4.4 / canvas jobs-detail-spec — Job spec tab is the
          // dedicated spec view: workable-ingested description with formatted
          // sections + recruiter requirements + an "At a glance" sidebar.
          // The pipeline-activity timeline that previously rendered here was
          // a leftover from the v1 "Activity" tab; v2 only has 4 tabs and
          // this one is "Job spec".
          <div className="role-desc">
            <div className="role-desc-main">
              {/* Read-first Job Specification: show the spec, with a single
                  Edit button that flips these fields (name, description, tasks)
                  into the inline form. The spec text is updated by pasting it
                  into the agent — no file upload here (showJobSpec={false}). */}
              {editingSpec ? (
                <RoleSpecEditPanel
                  role={role}
                  roleTasks={roleTasks}
                  allTasks={allTasks}
                  saving={savingRoleSheet}
                  error={roleSheetError}
                  showJobSpec={false}
                  onSubmit={async (payload) => {
                    const ok = await handleRoleSheetSubmit(payload);
                    if (ok) setEditingSpec(false);
                  }}
                  onCancel={() => { setRoleSheetError(''); setEditingSpec(false); }}
                />
              ) : (
                <>
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <h3 className="text-lg font-semibold text-[var(--taali-text)]">{role?.name || 'Job specification'}</h3>
                    <button
                      type="button"
                      className="btn btn-outline btn-sm"
                      onClick={() => { setRoleSheetError(''); setEditingSpec(true); }}
                    >
                      Edit
                    </button>
                  </div>

              {/* Job lifecycle control (mark filled / external / cancelled) —
                  shown for requisition-origin roles that carry a job_status. */}
              {role?.job_status ? (
                <JobStatusControl
                  status={role.job_status}
                  onChange={handleSetJobStatus}
                  busy={savingJobStatus}
                />
              ) : null}

              {/* Hiring-department assignment — shown whenever the org has any
                  departments (or this role already has one), so legacy / imported
                  roles with no requisition can still be tagged. */}
              {(clients.length > 0 || role?.client_id) ? (
                <ClientControl
                  clientId={role?.client_id ?? null}
                  clientName={role?.client_name ?? null}
                  clients={clients}
                  onChange={handleSetClient}
                  busy={savingClient}
                />
              ) : null}

              {/* The linked requisition's structured spec — always visible (it's
                  the richest source); the raw ingested spec sits in the expand. */}
              {role?.requisition ? (
                <RequisitionSpecSections requisition={role.requisition} />
              ) : null}

              {/* Distribute this role — copy-paste LinkedIn post + share links +
                  the careers XML feed URL. Points at the public job page; no
                  LinkedIn API/automation. */}
              {role?.id ? <DistributeRolePanel key={role.id} roleId={role.id} /> : null}

              <button
                type="button"
                className={`desc-toggle ${detailsExpanded ? 'open' : ''}`}
                onClick={() => setDetailsExpanded((current) => !current)}
              >
                <span>{detailsExpanded ? 'Hide full description' : 'Read full description'}</span>
                <ChevronDown className="caret" size={10} />
              </button>

              <div className={`role-sections ${detailsExpanded ? 'expanded' : ''}`}>
                <div className="role-spec-source">
                  {role?.source === 'workable' ? 'Workable ingested job spec' : 'Role job spec'}
                  {parsedJobSpec.meta.applyUrl ? (
                    <a href={parsedJobSpec.meta.applyUrl} target="_blank" rel="noopener noreferrer">Open source posting</a>
                  ) : null}
                </div>
                {parsedJobSpec.sections.length ? parsedJobSpec.sections.map((section, index) => (
                  <FormattedJobSpecSection
                    key={`${section.title}-${index}`}
                    section={section}
                    marker={String(index + 1).padStart(2, '0')}
                  />
                )) : (
                  <div className="role-sec">
                    <div className="role-sec-title"><span className="marker">01</span>About the role</div>
                    <p>{roleSummary || 'This recruiter workspace mirrors the job spec, scoring guidance, and active pipeline for the role.'}</p>
                  </div>
                )}
                {recruiterCriteria.length ? (
                  <div className="role-sec">
                    <div className="role-sec-title">
                      <span className="marker">{String((parsedJobSpec.sections.length || 1) + 1).padStart(2, '0')}</span>
                      Recruiter requirements
                    </div>
                    <ul>
                      {recruiterCriteria.map((criterion, index) => (
                        <li key={`${criterion}-${index}`}>{criterion}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
                </>
              )}
            </div>

            <div className="role-highlights">
              <h4>At a glance</h4>
              {roleHighlights.map((item) => (
                <div key={item.title} className="hi">
                  <div className="icon"><BriefcaseBusiness size={13} /></div>
                  <div>
                    <div className="t">{item.title}</div>
                    <div className="d">{item.description}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : activeView === 'hiring-team' ? (
          <HiringTeamPanel roleId={role?.id} />
        ) : (
          <>
            {/* HANDOFF v2 §4 / canvas jobs-detail-candidates — KPI row
                (In pipeline · New CVs · Below threshold · Agent spend) is
                the first thing inside the Candidates tab, mirroring the
                CandidatesTab artboard in tali-pages.jsx. Other tabs do not
                show these KPIs. */}
            <div style={{ marginBottom: 20 }}>
              <KpiStrip columns={5} tiles={pipelineStats} />
            </div>

            {/* HANDOFF v2 §4 / canvas jobs-detail-candidates — segmented
                stage filter + Sort + Score new toolbar above the table.
                Stage counts read off groupedApplications (already memoized).
                Sort is currently a label-only display until the directory
                exposes a controlled sort-by; "Score new" is wired to the
                same handler the score panel uses. */}
            <div className="ctable-toolbar">
              <div className="seg" role="tablist" aria-label="Filter candidates by stage">
                {[
                  { key: 'all', label: 'All', count: activeApplications.length },
                  ...PIPELINE_STAGE_ORDER.map((stage) => {
                    const items = (groupedApplications.find((g) => g.key === stage.key)?.items) || [];
                    return { key: stage.key, label: stage.label, count: items.length };
                  }),
                  // Rejected is an *outcome* not a *stage*; it lives at
                  // the right so the active-pipeline tabs (All / Applied /
                  // Invited / In assessment / Review / Advanced) read
                  // left-to-right as a recruiter would walk the funnel.
                  { key: 'rejected', label: 'Rejected', count: rejectedApplications.length },
                ].map((seg) => (
                  <button
                    key={seg.key}
                    type="button"
                    role="tab"
                    aria-selected={tableStageFilter === seg.key}
                    className={tableStageFilter === seg.key ? 'on' : ''}
                    onClick={() => setTableStageFilter(seg.key)}
                  >
                    {seg.label}
                    {seg.count > 0 ? <span className="ct">{seg.count}</span> : null}
                  </button>
                ))}
              </div>
              <div className="ctable-toolbar-grow" />
              {/* Sorting lives on the column headers (Score / Last updated). */}
              {/* Manual stage refresh: pull each candidate's current Workable
                  stage on demand (recovery for sync lag / a move that raced a
                  stale sync). Only for Workable-linked roles. */}
              {agentRunning ? (
                <span className="pipeline-agent-running" title="The agent is processing and syncing this role automatically. The controls to the right are a manual override.">
                  <AgentLoop kind="pulse"><Sparkles size={12} strokeWidth={2} /></AgentLoop>
                  Agent is running this role
                </span>
              ) : null}
              {role?.workable_job_id ? (
                <button
                  type="button"
                  className={`btn btn-sm ${agentRunning ? 'btn-ghost' : 'btn-outline'}`}
                  onClick={handleSyncWorkableStages}
                  disabled={syncingStages}
                  title={agentRunning
                    ? 'Manual override — the agent already keeps candidate stages synced. Use this only to force a refresh.'
                    : "Pull each candidate's current Workable stage and update it here"}
                >
                  {syncingStages ? (
                    <><Spinner size={12} className="!text-current" />Syncing…</>
                  ) : (
                    <><RefreshCw size={12} />Sync from Workable</>
                  )}
                </button>
              ) : null}
              {/* WS2 — curated multi-candidate client submittal. Only offered
                  once the recruiter has ticked the candidates to include, so it
                  reads as an action on the current selection. */}
              {selectedAppIds.size > 0 ? (
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => setSubmittalDialogOpen(true)}
                  title="Share a curated, client-safe shortlist for this role as one link"
                >
                  <Share2 size={12} />Create submittal pack
                </button>
              ) : null}
              {/* HANDOFF v2 §4 / canvas jobs-detail-candidates — primary
                  recruiter action: cascade Process opened via
                  ProcessCandidatesDialog. Label flips live during runs. */}
              <button
                type="button"
                className={`btn btn-sm ${agentRunning ? 'btn-outline' : 'btn-purple'}`}
                onClick={() => setProcessDialogOpen(true)}
                disabled={String(processJobs?.[numericRoleId]?.status || '').toLowerCase() === 'running'}
                title={agentRunning
                  ? 'Manual override — the agent processes new candidates automatically. Use this only to run a pass yourself.'
                  : undefined}
              >
                {(() => {
                  const pj = processJobs?.[numericRoleId];
                  const status = String(pj?.status || '').toLowerCase();
                  if (status === 'running') {
                    const step = pj?.current_step;
                    const label = step === 'fetch' ? 'Fetching CVs' : step === 'pre_screen' ? 'Pre-screening' : step === 'score' ? 'Scoring' : 'Processing';
                    return (<><Spinner size={12} className="!text-current" />{label}…</>);
                  }
                  const selCount = selectedAppIds.size;
                  if (selCount > 0) return (<><Sparkles size={12} />Process {selCount} selected</>);
                  const tabCount = tableStageFilter === 'rejected' ? rejectedApplications.length
                    : tableStageFilter === 'all' ? activeApplications.length
                    : activeApplications.filter((a) => applicationFunnelBucket(a) === tableStageFilter).length;
                  return (<><Sparkles size={12} />Process {tabCount} candidate{tabCount === 1 ? '' : 's'}</>);
                })()}
              </button>
            </div>
            {/* HANDOFF v2 §4 / canvas jobs-detail-candidates — clean
                ctable with Candidate / Score / Stage / Workable / Status /
                Agent / View →. Filtered by tableStageFilter, sorted client-side
                by tableSortBy. The full CandidatesDirectoryPage was too
                heavy here — it carried bulk-action chrome, pagination,
                NL-search, and filter chips that don't belong on the
                role detail page. The standalone /candidates route still
                uses the directory. */}
            {(() => {
              const sorted = sortedTableApplications;
              if (sorted.length === 0) {
                return (
                  <div className="ctable-wrap">
                    <div className="ctable-empty">
                      No candidates match the current filter. Try widening the stage segment above.
                    </div>
                  </div>
                );
              }
              // Window: only render the first tableVisibleCount rows. "Select
              // all" and "Load more" both operate on the rendered window, so a
              // recruiter never ticks rows they can't see.
              const visible = sorted.slice(0, tableVisibleCount);
              const hiddenCount = sorted.length - visible.length;
              const visibleIds = visible.map((a) => a.id);
              const allSel = visibleIds.length > 0 && visibleIds.every((id) => selectedAppIds.has(id));
              const someSel = visibleIds.some((id) => selectedAppIds.has(id));
              const toggleAll = (checked) => { const next = new Set(selectedAppIds); visibleIds.forEach((id) => { if (checked) next.add(id); else next.delete(id); }); setSelectedAppIds(next); };
              return (
                <div className="ctable-wrap">
                  <table className="ctable">
                    <thead>
                      <tr>
                        <th aria-label="Select" style={{ width: 28 }}><input type="checkbox" aria-label="Select all visible candidates" checked={allSel} ref={(el) => { if (el) el.indeterminate = !allSel && someSel; }} onChange={(e) => toggleAll(e.target.checked)} /></th>
                        <th>Candidate</th>
                        <th aria-sort={tableSortField === 'score' ? (tableSortBy === 'asc' ? 'ascending' : 'descending') : 'none'}>
                          <button type="button" className="ctable-sort" onClick={() => handleTableSort('score')} aria-label="Sort by score" title="Sort by score">Score{tableSortField === 'score' ? <span className="ctable-sort-arrow">{tableSortBy === 'asc' ? '↑' : '↓'}</span> : null}</button>
                        </th>
                        <th>Stage</th>
                        {/* External-ATS roles show the synced ATS stage;
                            full-ATS roles show the native Taali pipeline stage
                            (never a wall of dashes). */}
                        <th title={roleAtsType(role) === 'full_ats' ? 'Stage in the Taali pipeline' : `Current stage in ${atsTypeColumnLabel(role)}`}>{atsTypeColumnLabel(role)}</th>
                        <th>Agent</th>
                        <th aria-sort={tableSortField === 'last_updated' ? (tableSortBy === 'asc' ? 'ascending' : 'descending') : 'none'}>
                          <button type="button" className="ctable-sort" onClick={() => handleTableSort('last_updated')} aria-label="Sort by last updated" title="Sort by last updated">Last updated{tableSortField === 'last_updated' ? <span className="ctable-sort-arrow">{tableSortBy === 'asc' ? '↑' : '↓'}</span> : null}</button>
                        </th>
                        <th aria-label="Open" />
                      </tr>
                    </thead>
                    <tbody>
                      {visible.map((application) => {
                        const stage = String(application?.pipeline_stage || '').toLowerCase();
                        const compositeRaw = application?.score_summary?.taali_score
                          ?? application?.taali_score
                          ?? application?.assessment_score
                          ?? application?.cv_match_score;
                        // compositeRaw == null guard: Number(null) === 0 IS finite — without this, unscored renders as a literal "0" pill instead of "—".
                        const score = compositeRaw != null && Number.isFinite(Number(compositeRaw)) ? Math.round(Number(compositeRaw)) : null;
                        const scoreClass = score == null ? '' : score >= 80 ? 'hi' : score >= 60 ? 'mid' : 'lo';
                        const stageLabel = formatStageLabel(stage);
                        // Use only the freshly-polled map, not the per-row
                        // snapshot — the snapshot isn't refreshed by the poll,
                        // so it keeps showing a decision after it's resolved.
                        const pendingDecision = pendingAgentDecisions[application?.id] || null;
                        // Show ONLY a real, queued agent decision — never a
                        // score-band guess dressed up as a recommendation.
                        const agentLabel = pendingDecision ? formatDecisionLabel(pendingDecision.recommendation) : null;
                        const isAgentRow = Boolean(pendingDecision);
                        const isTriageRow = (
                          triageApplication
                          && Number(triageApplication.id) === Number(application.id)
                        );
                        const isSelected = selectedAppIds.has(application.id);
                        return (
                          <React.Fragment key={application.id}>
                            <tr
                              className={isAgentRow ? 'agent-row' : ''}
                              onClick={(event) => handlePipelineReportClick(event, application)}
                              onMouseEnter={() => hoverPrefetchRef.current.start(application.id)}
                              onMouseLeave={() => hoverPrefetchRef.current.cancel()}
                              style={{ cursor: 'pointer' }}
                            >
                              <td onClick={(e) => e.stopPropagation()} style={{ width: 28 }}><input type="checkbox" aria-label={`Select ${buildApplicationTitle(application)}`} checked={isSelected} onChange={() => { const next = new Set(selectedAppIds); if (next.has(application.id)) next.delete(application.id); else next.add(application.id); setSelectedAppIds(next); }} /></td>
                              <td>
                                <div className="name">{buildApplicationTitle(application)}</div>
                                <div className="sub">
                                  {application?.candidate_position
                                    || application?.candidate_email
                                    || 'No position captured'}
                                </div>
                              </td>
                              <td>
                                {renderJobPipelineScoreCell(score, scoreClass, application?.score_status)}
                                <ScoreProvenance
                                  provenance={application?.score_summary?.score_provenance}
                                  density="compact"
                                  className="mt-0.5"
                                />
                              </td>
                              <td>
                                <span className="stage-pill">{stageLabel}</span>
                              </td>
                              <td>{roleAtsType(role) === 'full_ats' ? (<span className="stage-pill" title="Stage in the Taali pipeline">{stageLabel}</span>) : application?.workable_disqualified ? (<span className="stage-pill is-disqualified" title={application?.workable_stage ? `Disqualified in Workable (was: ${formatStatusLabel(application.workable_stage)})` : 'Disqualified in Workable'}>Disqualified</span>) : application?.workable_stage ? (<span className="stage-pill" title="Current stage in Workable">{formatStatusLabel(application.workable_stage)}</span>) : (<span className="ctable-em">—</span>)}</td>
                              <td>
                                {agentLabel ? (
                                  <span className="ai-action">
                                    <AgentLoop kind="pulse"><Sparkles size={11} strokeWidth={2} /></AgentLoop>
                                    <AgentLoop kind="flow" className="ai-action-label">{agentLabel}</AgentLoop>
                                  </span>
                                ) : (
                                  <span className="ctable-em">—</span>
                                )}
                              </td>
                              <td className="ctable-status" title={(application?.last_activity_at || application?.updated_at || application?.created_at) ? new Date(application.last_activity_at || application.updated_at || application.created_at).toLocaleString() : undefined}>{formatRelativeShort(application?.last_activity_at || application?.updated_at || application?.created_at)}</td>
                              <td>
                                <a
                                  href={candidateReportHref(application, numericRoleId)}
                                  className="btn btn-ghost btn-sm"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    handlePipelineReportClick(event, application);
                                  }}
                                >
                                  View →
                                </a>
                              </td>
                            </tr>
                            {isTriageRow ? (
                              <tr className="ctable-triage-row">
                                <td colSpan={8} className="ctable-triage-cell">
                                  <CandidateTriageDrawer {...triageDrawerProps} agentRunning={agentRunning} />
                                </td>
                              </tr>
                            ) : null}
                          </React.Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                  {hiddenCount > 0 ? (
                    <div className="ctable-more">
                      <button
                        type="button"
                        className="btn btn-outline btn-sm"
                        onClick={() => setTableVisibleCount((n) => n + TABLE_PAGE_SIZE)}
                      >
                        Show more — {formatCount(hiddenCount)} not shown
                      </button>
                      <span className="ctable-more-count">
                        Showing {formatCount(visible.length)} of {formatCount(sorted.length)}
                      </span>
                    </div>
                  ) : null}
                </div>
              );
            })()}
          </>
        )}

        {/* Role editing is now inline on the Job Specification tab
            (<RoleSpecEditPanel>), so the role-edit slide-over is retired here. */}

        <CandidateSheet
          open={candidateSheetOpen}
          role={role}
          saving={addingCandidate}
          error={candidateSheetError}
          onClose={() => setCandidateSheetOpen(false)}
          onSubmit={handleCandidateSubmit}
        />

        <Dialog
          open={turnOffOpen}
          onClose={() => setTurnOffOpen(false)}
          title="Turn off the agent for this role?"
          description="The agent stops running and won't resume on its own. You can turn it back on anytime. To pause temporarily instead, use Pause — it keeps everything and resumes on its own."
          footer={(
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setTurnOffOpen(false)}>Cancel</Button>
              <Button type="button" variant="danger" onClick={confirmTurnOffAgent}>Turn off</Button>
            </div>
          )}
        >
          <div className="space-y-3 text-sm">
            {(roleAgent?.pending || 0) > 0 ? (
              <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={turnOffDiscard}
                  onChange={(e) => setTurnOffDiscard(e.target.checked)}
                  style={{ marginTop: 3 }}
                />
                <span>
                  Also discard the <strong>{roleAgent.pending}</strong> pending decision{roleAgent.pending === 1 ? '' : 's'} awaiting your review.
                  <br />
                  <span style={{ opacity: 0.7 }}>
                    Leave unchecked to keep them in your review queue — you can still action them after turning the agent off.
                  </span>
                </span>
              </label>
            ) : (
              <p style={{ opacity: 0.7 }}>This role has no pending decisions.</p>
            )}
          </div>
        </Dialog>

        {/* Candidates in table order, not raw fetch order — the backend
            freezes the submitted order into the pack, so the client sees the
            same ranking the recruiter curated on screen. */}
        <SubmittalPackDialog
          open={submittalDialogOpen}
          roleId={numericRoleId}
          roleTitle={role?.name || ''}
          applications={sortedTableApplications.filter((a) => selectedAppIds.has(a.id))}
          onClose={() => setSubmittalDialogOpen(false)}
        />

        <ProcessCandidatesDialog
          open={processDialogOpen}
          roleId={numericRoleId}
          stage={tableStageFilter}
          stageLabel={tableStageFilter === 'all' ? null : tableStageFilter === 'rejected' ? 'Rejected' : (PIPELINE_STAGE_ORDER.find((s) => s.key === tableStageFilter)?.label || tableStageFilter)}
          applicationIds={selectedAppIds.size > 0 ? Array.from(selectedAppIds) : null}
          onClose={() => setProcessDialogOpen(false)}
          onConfirm={async (body) => {
            try {
              const res = await rolesApi.processRole(numericRoleId, body);
              const payload = res?.data ?? {};
              if (payload.status === 'already_running') {
                showToast('This role is already being processed.', 'info');
              } else {
                // No success toast — the persistent BackgroundJobsToaster
                // already shows the cascade progress in the bottom-right.
                // Two surfaces for the same event was visual noise.
                trackRoleProcess?.(numericRoleId);
                // Clear selection now that the cascade has been launched
                // — leaving it ticked would suggest the next click still
                // targets the same rows when actually they're now mid-run.
                setSelectedAppIds(new Set());
              }
              setProcessDialogOpen(false);
            } catch (error) {
              showToast(getErrorMessage(error, 'Failed to start.'), 'error');
            }
          }}
        />
          </div>

        {/* The legacy slide-out <AgentSettingsPanel scope="role"> drawer
            was retired — the canvas-spec Agent settings tab on this page
            owns the same controls inline (hero banner ON/OFF + budget
            sidebar + autonomy toggles + reject threshold + must-haves +
            pause threshold). Surfacing both was duplicate chrome. */}
      </div>
    </div>
  );
};

export default JobPipelinePage;
