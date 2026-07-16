import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import '../../styles/16-job-pipeline.css';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ChevronDown,
  GitFork,
  MessageSquare,
  RefreshCw,
  Send,
  Sparkles,
} from 'lucide-react';

import * as apiClient from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { useJobStatus } from '../../contexts/JobStatusContext';
import { Dialog, Button, PageLoader, Spinner } from '../../shared/ui/TaaliPrimitives';
import { ConfirmActionDialog } from '../../shared/ui/ConfirmActionDialog';
import { readCache, writeCache } from '../../shared/api/resourceCache';
import { RoleViewTabs, useRoleView } from './RoleViewTabs';
import { HiringTeamPanel } from './HiringTeamPanel';
import { useRoleProgressPolling } from './useRoleProgressPolling';
import { parseJobSpec, FormattedJobSpecSection } from './jobSpecFormatting';
import { RequisitionSpecSections, JobStatusControl, ClientControl } from './RequisitionSpecSections';
import { clientApi } from '../clients/api';
import { RoleAgentSettingsTab } from './RoleAgentSettingsTab';
import { requisitionApi } from '../requisitions/api';
import { useAgentStatus } from '../../shared/layout/AgentBar';
import { AgentHeader, buildAgentPropFromStatus } from '../../shared/layout/AgentHeader';
import {
  AgentLoop,
  MotionDisclosure,
  MotionStagger,
  PresenceSwap,
  m,
  motionSafeScrollBehavior,
  motionTransition,
} from '../../shared/motion';
import { BackgroundJobsToaster } from '../candidates/BackgroundJobsToaster';
import { CandidateTriageDrawer, candidateReportHref } from '../candidates/CandidateTriageDrawer';
import { ScoreProvenance } from '../candidates/ScoreProvenance';
import { useCandidateTriage } from './useCandidateTriage';
import { RoleSpecEditPanel } from './RoleSpecEditPanel';
import { conflictActorLabel, reconcileRoleVersionConflict, roleExpectedVersion, roleVersionConflict, versionedRolePayload } from './roleConcurrency';
import { ReachOutDialog } from './ReachOutDialog';
import { CampaignsMonitorPanel } from './CampaignsMonitorPanel';
import {
  agentIntakeLifecycleCopy,
  applicationAtsStage,
  atsProviderLabel,
  AtsTypeTag,
  atsTypeColumnLabel,
  roleAtsProvider,
  roleAtsType,
} from './atsType';
import { getErrorMessage, formatStatusLabel, renderJobPipelineScoreCell } from '../candidates/candidatesUiUtils';
import {
  formatCount,
  budgetTile,
  applicationFunnelBucket,
  awaitingHitlFromDecisions,
  decisionPendingFromCounts,
} from '../../shared/metrics';
import { FunnelBoard } from '../../shared/ui/FunnelBoard';
import { KpiStrip } from '../../shared/ui/KpiStrip';
import { makeCandidateCvHoverPrefetch } from './candidateCvHoverPrefetch';
import { useRoleAutonomyChange } from './useRoleAutonomyChange';
import { useRoleAgentControls } from './useRoleAgentControls';
import {
  EMPTY_FETCH_PROGRESS,
  EMPTY_PRE_SCREEN_PROGRESS,
  EMPTY_PROGRESS,
  GRANULAR_AUTOMATION_KEYS,
  PIPELINE_STAGE_ORDER,
  activationAutonomyPayload,
  buildApplicationTitle,
  formatDecisionLabel,
  formatRelativeShort,
  formatStageLabel,
  matchesPipelineStage,
  normalizeThreshold,
  resolveOptionalPercent,
  resolvedDeterministicReject,
  resolvedRoleAutomation,
  summarizeUnscoredApplications,
} from './jobPipelineUtils';
import {
  buildRelatedRolePipelineStats,
  isRelatedRoleScoringActive,
  RelatedRoleContextBanner,
  RelatedRolePipelineLabel,
  RelatedRoleScoringInlineStatus,
  relatedRoleScoringActionLabel,
  useEffectiveRelatedAgentResume,
  useRelatedRoleScoringPolling,
} from './relatedRoleScoringUi';

export const JobPipelinePage = ({ onNavigate, onViewCandidate, NavComponent = null }) => {
  const { roleId } = useParams();
  const navigate = useNavigate();
  const rolesApi = apiClient.roles;
  const tasksApi = 'tasks' in apiClient ? apiClient.tasks : null;
  const { showToast } = useToast();
  const {
    jobs,
    trackRole,
  } = useJobStatus() ?? {};
  void onViewCandidate;

  const numericRoleId = Number(roleId);
  const batchScoreProgress = jobs?.[numericRoleId] ?? EMPTY_PROGRESS;
  // Live status is polled every 30s and pauses when the tab is hidden.
  const {
    status: agentStatus,
    phase: agentStatusPhase,
    setStatus: setAgentStatus,
    refetch: refetchAgentStatus,
    mutateStatus: mutateAgentStatus,
  } = useAgentStatus(Number.isFinite(numericRoleId) ? numericRoleId : null);
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
      // Quiet failure — no recommendation is shown until the next successful
      // poll. A score alone must never masquerade as an agent decision.
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
  // Workspace chips also power the role editor's suppressed-chip view.
  const [workspaceCriteria, setWorkspaceCriteria] = useState([]);
  const [criteriaBusy, setCriteriaBusy] = useState(false);
  const [criteriaSyncing, setCriteriaSyncing] = useState(false);
  const [criteriaResetting, setCriteriaResetting] = useState(false);
  const [roleTasks, setRoleTasks] = useState([]);
  const [allTasks, setAllTasks] = useState([]);
  const [roleApplications, setRoleApplications] = useState([]);
  const [applicationsLoading, setApplicationsLoading] = useState(false);
  const [applicationsLoadError, setApplicationsLoadError] = useState('');
  const [fetchCvsProgress, setFetchCvsProgress] = useState(EMPTY_FETCH_PROGRESS);
  const [preScreenProgress, setPreScreenProgress] = useState(EMPTY_PRE_SCREEN_PROGRESS);
  const [sisterScoringStatus, setSisterScoringStatus] = useState(null);
  const [sisterRescoring, setSisterRescoring] = useState(false);
  const [sisterPollVersion, setSisterPollVersion] = useState(0);
  const [startingRelatedRole, setStartingRelatedRole] = useState(false);
  const previousSisterScoringStateRef = useRef(null);
  const [loading, setLoading] = useState(true);
  const [roleDetailLoading, setRoleDetailLoading] = useState(true);
  const [roleDetailLoadError, setRoleDetailLoadError] = useState('');
  // Cold-load failure state with an in-page retry.
  const [loadError, setLoadError] = useState('');
  const [savingRoleConfig, setSavingRoleConfig] = useState(false);
  const [savingAssessmentTask, setSavingAssessmentTask] = useState(false);
  const [thresholdDraft, setThresholdDraft] = useState('');
  const [suggestedThreshold, setSuggestedThreshold] = useState(null);
  const [savingThresholdMode, setSavingThresholdMode] = useState(false);
  const handleRoleVersionConflict = useCallback(
    (error) => reconcileRoleVersionConflict(error, setRole, showToast),
    [showToast],
  );
  const handleAutonomyChange = useRoleAutonomyChange({
    numericRoleId,
    role,
    rolesApi,
    setRole,
    showToast,
  });

  useRelatedRoleScoringPolling(role?.role_kind === 'sister', numericRoleId, rolesApi, sisterPollVersion, setSisterScoringStatus);
  const handleThresholdModeChange = useCallback(async (nextMode) => {
    if (!Number.isFinite(numericRoleId)) return;
    if (nextMode !== 'auto' && nextMode !== 'manual') return;
    setSavingThresholdMode(true);
    setRole((cur) => (cur ? { ...cur, auto_reject_threshold_mode: nextMode } : cur));
    try {
      const response = await rolesApi.update(numericRoleId, versionedRolePayload(role, {
        auto_reject_threshold_mode: nextMode,
      }));
      if (response?.data) setRole(response.data);
      if (nextMode === 'auto') {
        try {
          const res = await rolesApi.suggestedAutoRejectThreshold(numericRoleId);
          setSuggestedThreshold(res?.data || null);
        } catch { /* leave previous suggestion */ }
      }
      showToast(nextMode === 'auto' ? 'Threshold mode set to auto — agent will pick the cut-off.' : 'Threshold mode set to manual.', 'success');
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        setRole((cur) => (cur ? { ...cur, auto_reject_threshold_mode: nextMode === 'auto' ? 'manual' : 'auto' } : cur));
        showToast(getErrorMessage(error, 'Failed to update threshold mode.'), 'error');
      }
    } finally {
      setSavingThresholdMode(false);
    }
  }, [handleRoleVersionConflict, numericRoleId, role, rolesApi, showToast]);
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
      const res = await rolesApi.setJobStatus(
        numericRoleId,
        nextStatus,
        undefined,
        role?.version,
      );
      if (res?.data) setRole(res.data);
      showToast('Job status updated.', 'success');
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        setRole((cur) => (cur ? { ...cur, job_status: previous } : cur));
        showToast(getErrorMessage(error, 'Failed to update job status.'), 'error');
      }
    } finally {
      setSavingJobStatus(false);
    }
  }, [handleRoleVersionConflict, numericRoleId, role?.job_status, role?.version, rolesApi, showToast]);
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
      const res = await rolesApi.setClient(numericRoleId, nextClientId, role?.version);
      if (res?.data) setRole(res.data);
      showToast(nextClientId == null ? 'Hiring department cleared.' : 'Hiring department assigned.', 'success');
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        setRole((cur) => (cur ? { ...cur, client_id: prevId, client_name: prevName } : cur));
        showToast(getErrorMessage(error, 'Failed to update hiring department.'), 'error');
      }
    } finally {
      setSavingClient(false);
    }
  }, [clients, handleRoleVersionConflict, numericRoleId, role?.client_id, role?.client_name, role?.version, rolesApi, showToast]);
  const [, setRefreshTick] = useState(0);
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const [activeView, setActiveView] = useRoleView();
  const [tableStageFilter, setTableStageFilter] = useState('all');
  // Only the Sourced lens supports selection; sending outreach is its HITL.
  const [selectedSourcedAppIds, setSelectedSourcedAppIds] = useState(() => new Set());
  const [reachOutOpen, setReachOutOpen] = useState(false);
  const [focusCampaignId, setFocusCampaignId] = useState(null);
  // Candidates-table sort: which column (`tableSortField`) and direction
  // (`tableSortBy`, default desc → strongest score / most-recent first).
  const [tableSortBy, setTableSortBy] = useState('desc');
  const [tableSortField, setTableSortField] = useState('score');
  // Click a sortable header → sort on it (desc), or flip direction if active.
  const handleTableSort = useCallback((field) => {
    setTableSortBy((dir) => (tableSortField === field ? (dir === 'asc' ? 'desc' : 'asc') : 'desc'));
    setTableSortField(field);
  }, [tableSortField]);
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
  useEffect(() => {
    if (tableStageFilter !== 'sourced') setSelectedSourcedAppIds(new Set());
  }, [tableStageFilter]);
  const [jobSpecError, setJobSpecError] = useState('');
  const [jobSpecConflict, setJobSpecConflict] = useState(null);
  // The legacy slide-out <AgentSettingsPanel> drawer state has been
  // retired — the canvas-spec Agent settings tab on this page owns
  // the same controls inline. See the AgentBar onPause handler below.
  const [savingJobSpec, setSavingJobSpec] = useState(false);
  // Job Specification tab is read-first: it shows the spec, and this flips it
  // into the inline edit form.
  const [editingSpec, setEditingSpec] = useState(false);
  const [specEditorDirty, setSpecEditorDirty] = useState(false);
  const [pendingRoleView, setPendingRoleView] = useState(null);

  // Sister roles are read-only scoring projections. Their authoritative job
  // specification belongs to the original ATS role, and the API rejects spec
  // writes here, so do not expose an editor that can only fail on save.
  const canEditJobSpec = Boolean(role) && role?.role_kind !== 'sister';
  useEffect(() => {
    if (role?.role_kind !== 'sister') return;
    setEditingSpec(false);
    setSpecEditorDirty(false);
    setPendingRoleView(null);
    setJobSpecError('');
    setJobSpecConflict(null);
  }, [role?.role_kind]);
  // Only the most recent workspace load may write state.
  const loadSeqRef = useRef(0);
  const loadedRoleIdRef = useRef(null);
  const hoverPrefetchRef = useRef(null);
  if (!hoverPrefetchRef.current) hoverPrefetchRef.current = makeCandidateCvHoverPrefetch();

  const loadRoleWorkspace = useCallback(async () => {
    if (!Number.isFinite(numericRoleId)) return;
    const seq = (loadSeqRef.current += 1);
    const cacheKey = `role-workspace:${numericRoleId}`;
    const isColdForRole = loadedRoleIdRef.current !== numericRoleId;
    const cached = isColdForRole ? readCache(cacheKey) : null;
    setLoadError('');
    setApplicationsLoadError('');
    setRoleDetailLoadError('');
    if (cached?.data) {
      const c = cached.data;
      setRole(c.role || null);
      setRoleTasks(Array.isArray(c.roleTasks) ? c.roleTasks : []);
      setRoleApplications(Array.isArray(c.roleApplications) ? c.roleApplications : []);
      setWorkspaceCriteria(Array.isArray(c.workspaceCriteria) ? c.workspaceCriteria : []);
      setLoading(false);
      setRoleDetailLoading(false);
      loadedRoleIdRef.current = numericRoleId;
    } else if (isColdForRole) {
      setLoading(true);
      setRoleDetailLoading(true);
    }
    setApplicationsLoading(true);
    let rolePainted = Boolean(cached?.data);
    try {
      const appsQuery = (outcome) => ({ sort_by: 'pre_screen_score', sort_order: 'desc', application_outcome: outcome, limit: 2000 });
      const shellRes = rolesApi.getShell
        ? await rolesApi.getShell(numericRoleId)
        : await rolesApi.get(numericRoleId);
      if (seq !== loadSeqRef.current) return;
      loadedRoleIdRef.current = numericRoleId;
      let nextRole = shellRes?.data || null;
      setRole((current) => (
        current && !isColdForRole
          ? {
              ...current,
              ...nextRole,
              stage_counts: current.stage_counts,
              pending_decisions_by_type: current.pending_decisions_by_type,
              active_candidates_count: current.active_candidates_count,
            }
          : nextRole
      ));
      rolePainted = Boolean(nextRole);
      setThresholdDraft(nextRole?.score_threshold != null ? String(nextRole.score_threshold) : '');
      setLoading(false);

      try {
        const roleRes = await rolesApi.get(numericRoleId);
        if (seq !== loadSeqRef.current) return;
        nextRole = roleRes?.data || nextRole;
        setRole(nextRole);
        setThresholdDraft(nextRole?.score_threshold != null ? String(nextRole.score_threshold) : '');
      } catch (error) {
        if (seq !== loadSeqRef.current) return;
        setRoleDetailLoadError(getErrorMessage(error, 'Pipeline summary could not be loaded.'));
      } finally {
        if (seq === loadSeqRef.current) setRoleDetailLoading(false);
      }

      const [tasksRes, batchStatusRes, fetchStatusRes, preScreenStatusRes, orgCriteriaRes] = await Promise.all([
        rolesApi.listTasks(numericRoleId).catch(() => ({ data: [] })),
        rolesApi.batchScoreStatus(numericRoleId).catch(() => ({ data: null })),
        rolesApi.fetchCvsStatus(numericRoleId).catch(() => ({ data: EMPTY_FETCH_PROGRESS })),
        rolesApi.batchPreScreenStatus(numericRoleId).catch(() => ({ data: EMPTY_PRE_SCREEN_PROGRESS })),
        Promise.resolve(apiClient.organizations?.listCriteria?.() ?? { data: [] })
          .catch(() => ({ data: [] })),
      ]);
      if (seq !== loadSeqRef.current) return;
      const nextTasks = Array.isArray(tasksRes?.data) ? tasksRes.data : [];
      const nextCriteria = Array.isArray(orgCriteriaRes?.data) ? orgCriteriaRes.data : [];
      setRoleTasks(nextTasks);
      setWorkspaceCriteria(nextCriteria);
      setFetchCvsProgress(fetchStatusRes?.data || EMPTY_FETCH_PROGRESS);
      setPreScreenProgress(preScreenStatusRes?.data || EMPTY_PRE_SCREEN_PROGRESS);

      let applicationPayloads = [];
      let applicationError = null;
      try {
        const openAppsRes = await rolesApi.listApplications(numericRoleId, appsQuery('open'));
        if (seq !== loadSeqRef.current) return;
        applicationPayloads = [...(openAppsRes?.data || [])];
        setRoleApplications(applicationPayloads);
      } catch (error) {
        applicationError = error;
      }
      try {
        const rejectedAppsRes = await rolesApi.listApplications(numericRoleId, appsQuery('rejected'));
        if (seq !== loadSeqRef.current) return;
        applicationPayloads = [...applicationPayloads, ...(rejectedAppsRes?.data || [])];
        setRoleApplications(applicationPayloads);
      } catch (error) {
        applicationError ||= error;
      }
      if (nextRole?.role_kind === 'sister') {
        const [hiredAppsRes, withdrawnAppsRes] = await Promise.all([
          rolesApi.listApplications(numericRoleId, appsQuery('hired')),
          rolesApi.listApplications(numericRoleId, appsQuery('withdrawn')),
        ]);
        if (seq !== loadSeqRef.current) return;
        applicationPayloads = [
          ...applicationPayloads,
          ...(hiredAppsRes?.data || []),
          ...(withdrawnAppsRes?.data || []),
        ];
        setRoleApplications(applicationPayloads);
      }
      if (applicationError) {
        setApplicationsLoadError(getErrorMessage(applicationError, 'Some candidates could not be loaded.'));
      }
      // Preload the automatic threshold recommendation.
      if (nextRole?.auto_reject_threshold_mode === 'auto' && Number.isFinite(numericRoleId)) {
        rolesApi.suggestedAutoRejectThreshold(numericRoleId)
          .then((res) => setSuggestedThreshold(res?.data || null))
          .catch(() => setSuggestedThreshold(null));
      } else setSuggestedThreshold(null);
      // Dedupe by id — defensive against any backend overlap.
      const byId = new Map();
      for (const a of applicationPayloads) {
        if (a?.id != null && !byId.has(a.id)) byId.set(a.id, a);
      }
      const nextApps = [...byId.values()];
      setRoleApplications(nextApps);
      writeCache(cacheKey, {
        role: nextRole,
        roleTasks: nextTasks,
        roleApplications: nextApps,
        workspaceCriteria: nextCriteria,
      });
      const initBatchStatus = String(batchStatusRes?.data?.status || '').toLowerCase();
      if (['running', 'cancelling', 'cancelled', 'completed'].includes(initBatchStatus)) {
        trackRole?.(numericRoleId);
      }
    } catch (error) {
      // Preserve any shell/cache paint when a background request fails.
      if (!rolePainted && isColdForRole && !cached?.data) {
        setRole(null);
        setRoleTasks([]);
        setRoleApplications([]);
        setLoadError(getErrorMessage(error, 'Failed to load this job.'));
        showToast(getErrorMessage(error, 'Failed to load role pipeline.'), 'error');
      } else if (rolePainted) {
        setApplicationsLoadError(getErrorMessage(error, 'Some job data could not be loaded.'));
      }
    } finally {
      if (seq === loadSeqRef.current) {
        setLoading(false);
        setRoleDetailLoading(false);
        setApplicationsLoading(false);
      }
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
    // A sister row is a projection of a source application + alternate score;
    // the one-application endpoint only returns the source view. Reload the
    // projected roster after an ATS action so we do not replace it with the
    // original role's score.
    if (role?.role_kind === 'sister') {
      await loadRoleWorkspace();
      return;
    }
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
  }, [loadRoleWorkspace, role?.role_kind, rolesApi, numericRoleId]);

  useEffect(() => {
    void loadRoleWorkspace();
  }, [loadRoleWorkspace]);

  useEffect(() => {
    const previous = previousSisterScoringStateRef.current;
    const current = sisterScoringStatus?.status || null;
    previousSisterScoringStateRef.current = current;
    if (previous === 'running' && current && current !== 'running') {
      void loadRoleWorkspace();
    }
  }, [loadRoleWorkspace, sisterScoringStatus?.status]);

  // The org-wide task catalogue belongs to Agent settings. The job-spec editor
  // is a focused writing surface and never needs to load it.
  const loadedAllTasksRef = useRef(false);
  useEffect(() => {
    if (activeView !== 'role-fit' || loadedAllTasksRef.current || !tasksApi?.list) return undefined;
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
    roleApplications.filter((application) => (
      role?.role_kind === 'sister'
        ? application?.application_outcome !== 'open'
        : application?.application_outcome === 'rejected'
    ))
  ), [role?.role_kind, roleApplications]);
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
  const newCvBreakdown = useMemo(
    () => summarizeUnscoredApplications(unscoredApplications),
    [unscoredApplications],
  );

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
    if (role?.role_kind === 'sister') {
      return buildRelatedRolePipelineStats({
        status: sisterScoringStatus,
        rosterFallback: activeApplications.length + rejectedApplications.length,
        belowThresholdCount,
        thresholdValue,
        budget,
        monthlyBudgetCents,
      });
    }
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
  }, [activeApplications.length, agentStatus, belowThresholdCount, newCvBreakdown, rejectedApplications.length, role, sisterScoringStatus, thresholdValue]);

  const groupedApplications = useMemo(() => [
    ...PIPELINE_STAGE_ORDER.map((stage) => ({
      ...stage,
      items: activeApplications.filter((application) => matchesPipelineStage(application, stage.key)),
    })),
    { key: 'rejected', label: role?.role_kind === 'sister' ? 'Closed' : 'Rejected', items: rejectedApplications },
  ], [activeApplications, rejectedApplications, role?.role_kind]);

  // Candidates-table rows: filter by the active stage segment, then sort by
  // the chosen column. Memoized on the data + sort so the 30s decision/agent
  // polls don't re-filter and re-sort thousands of rows on every tick (this
  // used to run inline in render). Rendering is windowed via tableVisibleCount.
  const sortedTableApplications = useMemo(() => {
    const filtered = tableStageFilter === 'rejected'
      ? rejectedApplications
      : tableStageFilter === 'all'
        ? activeApplications
        : activeApplications.filter((a) => matchesPipelineStage(a, tableStageFilter));
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
  const roleFactValues = useMemo(() => ({
    location: role?.location || role?.candidate_location || parsedJobSpec.meta.location || 'Location not captured',
    department: role?.department || parsedJobSpec.meta.department || role?.organization_name || 'Hiring team',
    employment: role?.employment_type || parsedJobSpec.meta.employmentType || 'Full-time',
  }), [parsedJobSpec.meta.department, parsedJobSpec.meta.employmentType, parsedJobSpec.meta.location, role?.candidate_location, role?.department, role?.employment_type, role?.location, role?.organization_name]);
  const roleHighlights = useMemo(() => ([
    { title: 'Location', description: roleFactValues.location },
    { title: 'Department', description: roleFactValues.department },
    { title: 'Employment', description: roleFactValues.employment },
    {
      title: roleTasks.length === 1 ? 'Assessment' : 'Assessments',
      description: roleTasks.length ? roleTasks.map((task) => task.name).join(' · ') : 'No assessment task linked',
    },
  ]), [roleFactValues, roleTasks]);

  // Batch progress is owned by JobStatusContext. This page observes the agent's
  // work but does not expose a second manual processing flow.

  const handleSaveRoleConfig = async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setSavingRoleConfig(true);
    try {
      const response = await rolesApi.update(numericRoleId, versionedRolePayload(role, {
        score_threshold: thresholdDraft === '' ? null : Number(normalizeThreshold(thresholdDraft)),
      }));
      if (response?.data) setRole(response.data);
      await loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
      showToast('Screening threshold updated.', 'success');
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        showToast(getErrorMessage(error, 'Failed to save reject threshold.'), 'error');
      }
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
      const { data } = await rolesApi.createCriterion(
        numericRoleId,
        { text, bucket },
        role?.version,
      );
      if (data) setRole((cur) => cur && ({
        ...cur,
        version: data.role_version ?? cur.version,
        criteria: [...(cur.criteria || []).filter((c) => c.id !== data.id), data],
      }));
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        showToast(getErrorMessage(error, 'Failed to add criterion.'), 'error');
      }
    } finally {
      setCriteriaBusy(false);
    }
  }, [handleRoleVersionConflict, numericRoleId, role?.version, rolesApi, showToast]);

  const handleUpdateRoleCriterion = useCallback(async (criterionId, updates) => {
    if (!Number.isFinite(numericRoleId)) return;
    setCriteriaBusy(true);
    try {
      const { data } = await rolesApi.updateCriterion(
        numericRoleId,
        criterionId,
        updates,
        role?.version,
      );
      if (data) setRole((cur) => cur && ({
        ...cur,
        version: data.role_version ?? cur.version,
        criteria: (cur.criteria || []).map((c) => (c.id === criterionId ? data : c)),
      }));
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        showToast(getErrorMessage(error, 'Failed to update criterion.'), 'error');
      }
    } finally {
      setCriteriaBusy(false);
    }
  }, [handleRoleVersionConflict, numericRoleId, role?.version, rolesApi, showToast]);

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
      await rolesApi.deleteCriterion(numericRoleId, criterionId, role?.version);
      // Criterion deletion advances exactly one server-side role revision.
      setRole((cur) => cur && ({ ...cur, version: roleExpectedVersion(cur) + 1 }));
    } catch (error) {
      // Refetch authoritative state; a stale snapshot restore would clobber
      // concurrent successful deletes of other criteria.
      const conflict = handleRoleVersionConflict(error);
      await loadRoleWorkspace();
      if (!conflict) {
        showToast(getErrorMessage(error, 'Failed to remove criterion.'), 'error');
      }
    }
  }, [handleRoleVersionConflict, loadRoleWorkspace, numericRoleId, role?.version, rolesApi, showToast]);

  const handleSyncRoleCriteria = useCallback(async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setCriteriaSyncing(true);
    try {
      const res = await rolesApi.syncCriteriaWithWorkspace(numericRoleId, role?.version);
      if (res?.data) setRole(res.data);
      showToast('Workspace updates pulled in.', 'success');
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        showToast(getErrorMessage(error, 'Failed to sync workspace criteria.'), 'error');
      }
    } finally {
      setCriteriaSyncing(false);
    }
  }, [handleRoleVersionConflict, numericRoleId, role?.version, rolesApi, showToast]);

  const handleResetRoleCriteria = useCallback(async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setCriteriaResetting(true);
    try {
      const res = await rolesApi.resetCriteriaToWorkspace(numericRoleId, role?.version);
      if (res?.data) setRole(res.data);
      showToast('Criteria reset to workspace defaults.', 'success');
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        showToast(getErrorMessage(error, 'Failed to reset criteria.'), 'error');
      }
    } finally {
      setCriteriaResetting(false);
    }
  }, [handleRoleVersionConflict, numericRoleId, role?.version, rolesApi, showToast]);

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
      const patchRes = await rolesApi.update(numericRoleId, versionedRolePayload(role, {
        suppressed_org_criterion_ids: remainingSuppressed,
      }));
      if (patchRes?.data) setRole(patchRes.data);
      // Then sync to bring the chip back with full provenance (org_criterion_id set).
      const res = await rolesApi.syncCriteriaWithWorkspace(
        numericRoleId,
        patchRes?.data?.version ?? roleExpectedVersion(role) + 1,
      );
      if (res?.data) setRole(res.data);
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        showToast(getErrorMessage(error, 'Failed to restore criterion.'), 'error');
      }
    } finally {
      setCriteriaBusy(false);
    }
  }, [handleRoleVersionConflict, numericRoleId, role, rolesApi, showToast]);

  const handleJobSpecSubmit = async ({ name, jobSpecText }) => {
    if (!Number.isFinite(numericRoleId) || !canEditJobSpec) return false;
    setSavingJobSpec(true);
    setJobSpecError('');
    setJobSpecConflict(null);
    try {
      const payload = versionedRolePayload(role, {
        job_spec_text: jobSpecText,
        ...(name ? { name } : {}),
      });
      const response = await rolesApi.updateJobSpec(numericRoleId, payload);
      if (response?.data?.role) {
        setRole(response.data.role);
      }
      await loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
      const affectedCount = Number(response?.data?.would_rescreen?.count || 0);
      showToast(
        affectedCount > 0
          ? `Job spec saved. The updated criteria affect ${formatCount(affectedCount)} existing candidate${affectedCount === 1 ? '' : 's'}.`
          : 'Job spec saved.',
        'success',
      );
      return true;
    } catch (error) {
      const conflict = roleVersionConflict(error);
      if (conflict) {
        setRole((current) => current && ({
          ...current, ...(conflict.currentRole || {}),
          version: conflict.currentVersion ?? conflict.currentRole?.version ?? current.version,
        }));
        setJobSpecConflict({ message: conflict.message, changedBy: conflictActorLabel(conflict.changedBy), currentVersion: conflict.currentVersion });
      } else {
        setJobSpecError(getErrorMessage(error, 'Failed to save the job specification.'));
      }
      return false;
    } finally {
      setSavingJobSpec(false);
    }
  };

  const handleRoleViewNavigate = useCallback((event, nextView) => {
    if (!editingSpec || nextView === 'activity') return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (specEditorDirty) {
      event.preventDefault();
      setPendingRoleView(nextView);
      return;
    }
    setEditingSpec(false);
    setJobSpecError('');
    setJobSpecConflict(null);
  }, [editingSpec, specEditorDirty]);

  const discardSpecAndNavigate = useCallback(() => {
    const nextView = pendingRoleView;
    setPendingRoleView(null);
    setSpecEditorDirty(false);
    setEditingSpec(false);
    setJobSpecError('');
    setJobSpecConflict(null);
    if (nextView) setActiveView(nextView);
  }, [pendingRoleView, setActiveView]);

  // Assign, change, clear, or A/B-test assessment tasks from Agent settings.
  // The callback always drives the role to exactly the requested id set.
  const handleAssignAssessmentTasks = useCallback(async (taskIds) => {
    if (!Number.isFinite(numericRoleId)) return false;
    setSavingAssessmentTask(true);
    try {
      const desired = [...new Set((taskIds || []).map((id) => Number(id)).filter(Number.isFinite))];
      const currentIds = (roleTasks || []).map((task) => Number(task.id));
      let expectedVersion = roleExpectedVersion(role);
      if (rolesApi.addTask) {
        for (const id of desired) {
          if (!currentIds.includes(id)) {
            const response = await rolesApi.addTask(numericRoleId, id, expectedVersion);
            expectedVersion = response?.data?.version ?? expectedVersion + 1;
          }
        }
      }
      if (rolesApi.removeTask) {
        for (const id of currentIds) {
          if (!desired.includes(id)) {
            await rolesApi.removeTask(numericRoleId, id, expectedVersion);
            expectedVersion += 1;
          }
        }
      }
      await loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
      showToast(
        desired.length === 0
          ? (role?.agentic_mode_enabled
            ? 'Assessment tasks cleared — this role will now skip the assessment stage.'
            : 'Assessment tasks cleared.')
          : desired.length === 1
            ? 'Assessment task assigned.'
            : `${desired.length}-task A/B set saved.`,
        'success',
      );
      return true;
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        showToast(getErrorMessage(error, 'Failed to update assessment tasks.'), 'error');
      }
      await loadRoleWorkspace();
      throw error;
    } finally {
      setSavingAssessmentTask(false);
    }
  }, [handleRoleVersionConflict, numericRoleId, role, roleTasks, rolesApi, loadRoleWorkspace, showToast]);

  /*
   * Job-spec text and linked assessments intentionally have separate owners:
   * this editor updates the screening document atomically, while Agent settings
   * owns the assessment set. Keeping those workflows separate prevents a text
   * edit from silently changing candidate assignment behavior.
   */

  const handleRescoreSister = useCallback(async () => {
    if (!Number.isFinite(numericRoleId) || sisterRescoring) return;
    setSisterRescoring(true);
    try {
      const res = await rolesApi.rescoreSister(numericRoleId);
      setSisterScoringStatus(res?.data || null);
      setSisterPollVersion((value) => value + 1);
      showToast('Re-scoring queued for the coupled candidate roster.', 'success');
      window.setTimeout(() => { void loadRoleWorkspace(); }, 1000);
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to queue the related-role re-score.'), 'error');
    } finally {
      setSisterRescoring(false);
    }
  }, [loadRoleWorkspace, numericRoleId, rolesApi, showToast, sisterRescoring]);

  const handleStartRelatedRole = useCallback(async () => {
    if (!role?.id || startingRelatedRole) return;
    setStartingRelatedRole(true);
    try {
      const draft = await requisitionApi.createRelated(role.id);
      if (!draft?.id) throw new Error('Related-role draft was not returned.');
      navigate(`/requisitions?brief=${encodeURIComponent(draft.id)}`);
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to start the related-role draft.'), 'error');
    } finally {
      setStartingRelatedRole(false);
    }
  }, [navigate, role?.id, showToast, startingRelatedRole]);

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

  const canControlRoleAgent = agentStatus != null
    && agentStatus.can_control_agent !== false;
  const roleAgentControlDisabledReason = agentStatus == null
    ? (agentStatusPhase === 'error'
      ? 'Agent controls are temporarily unavailable because the current status could not be loaded.'
      : 'Checking your role agent permissions…')
    : canControlRoleAgent
      ? null
      : 'Only workspace owners, hiring managers, and recruiters assigned to this role can change its agent controls.';
  const {
    controlAction: roleAgentControlAction,
    pauseAgent: handlePauseAgent,
    resumeAgent: handleResumeAgent,
  } = useRoleAgentControls({
    roleId: numericRoleId,
    role,
    agentStatus,
    canControlAgent: canControlRoleAgent,
    mutateAgentStatus,
    setAgentStatus,
    setRole,
    loadRoleWorkspace,
    handleRoleVersionConflict,
    showToast,
  });
  const handleResumeEffectiveAgent = useEffectiveRelatedAgentResume({ agentStatus, onResumeRole: handleResumeAgent, refetchAgentStatus, resumeWorkspace: apiClient.agent.resumeAll, reloadRole: loadRoleWorkspace, setPollingVersion: setSisterPollVersion, showToast });

  // HANDOFF unified-headers.md §2-§4 — Role detail uses the single
  // AgentHeader with a role-scoped agent panel on the right. Builds the
  // panel agent prop from the polled /agent/status payload, with the
  // role's own `agentic_mode_enabled` flag deciding whether it's ON or
  // OFF. The previous role-hero + AgentBar duo collapses into this hero.
  const roleAgent = useMemo(() => {
    const enabled = Boolean(role?.agentic_mode_enabled);
    if (!agentStatus) {
      return {
        loading: agentStatusPhase !== 'error',
        unavailable: agentStatusPhase === 'error',
        on: false,
        paused: false,
        tick: agentStatusPhase === 'error'
          ? 'Could not load current controls.'
          : 'Checking role and workspace controls…',
        controlScope: 'role',
        controlAction: roleAgentControlAction,
      };
    }
    const built = buildAgentPropFromStatus(agentStatus, { isEnabled: enabled });
    return built ? { ...built, controlAction: roleAgentControlAction } : built;
  }, [agentStatus, agentStatusPhase, role, roleAgentControlAction]);
  const rolePendingReviewTitle = (() => {
    const total = Number(roleAgent?.pending || 0);
    const decisions = roleAgent?.pendingBreakdown?.decisions;
    const questions = roleAgent?.pendingBreakdown?.questions;
    const parts = [];
    if (Number.isFinite(decisions)) {
      parts.push(`${decisions} candidate decision${decisions === 1 ? '' : 's'}`);
    }
    if (Number.isFinite(questions)) {
      parts.push(`${questions} agent question${questions === 1 ? '' : 's'}`);
    }
    return parts.length
      ? `${total} awaiting you: ${parts.join(' and ')}`
      : `${total} item${total === 1 ? '' : 's'} awaiting you`;
  })();

  // Agent state remains explicit in decision/candidate detail surfaces. Routine
  // processing and ATS sync controls are intentionally absent from this page:
  // the role agent owns that operational work.
  const agentRunning = Boolean(roleAgent?.on && !roleAgent?.paused);
  const persistedActivationIntent = role?.assessment_task_provisioning?.activation_intent || null;
  const persistedActivationStatus = String(persistedActivationIntent?.status || '');
  const activationIsPending = ['pending', 'retry_wait'].includes(persistedActivationStatus)
    && !Boolean(role?.agentic_mode_enabled);
  const activationIsBlocked = persistedActivationStatus === 'blocked'
    && !Boolean(role?.agentic_mode_enabled);

  // Turn-off confirm dialog state (the "also discard pending decisions" opt-in).
  // Declared with the other hooks — before any early return — so hook order
  // stays stable across the loading/loaded renders.
  const [turnOffOpen, setTurnOffOpen] = useState(false);
  const [turnOffDiscard, setTurnOffDiscard] = useState(false);
  // Turn on authorizes the single generated, battle-tested assessment. Keep
  // the validation progress visible here, but do not turn it into a second
  // manual setup step.
  const [activationPreflight, setActivationPreflight] = useState(null);
  const [activationReview, setActivationReview] = useState(null);
  const activationReviewOpen = Boolean(activationReview);
  const activationBattleVerdict = activationReview?.draft?.battle_test?.verdict || null;
  useEffect(() => {
    if (!activationReviewOpen) {
      return undefined;
    }
    // Polling is presentation-only. The persisted backend intent owns task
    // generation, battle validation, repository approval, readiness, and the
    // OFF->ON transition even if this dialog/tab disappears.
    if (
      !activationReviewOpen
      || activationBattleVerdict === 'pass'
      || !Number.isFinite(numericRoleId)
    ) return undefined;
    let cancelled = false;
    const refreshGeneratedAssessment = async () => {
      try {
        const [tasksRes, roleRes] = await Promise.all([
          rolesApi.listTasks(numericRoleId),
          rolesApi.get(numericRoleId),
        ]);
        if (cancelled) return;
        const nextTasks = Array.isArray(tasksRes?.data) ? tasksRes.data : [];
        const generatedDraft = nextTasks.find((task) => (
          task?.is_active === false && task?.generated && task?.needs_review !== false
        )) || null;
        setRoleTasks(nextTasks);
        if (roleRes?.data) setRole(roleRes.data);
        if (generatedDraft) {
          setActivationReview((current) => (
            current ? { ...current, draft: generatedDraft } : current
          ));
        }
      } catch {
        // The ordinary workspace refresh and the next poll remain recovery
        // paths; don't collapse the dialog on a transient read failure.
      }
    };
    void refreshGeneratedAssessment();
    const timer = window.setInterval(refreshGeneratedAssessment, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    activationReviewOpen,
    activationBattleVerdict,
    loadRoleWorkspace,
    numericRoleId,
    refetchAgentStatus,
    rolesApi,
    setAgentStatus,
    showToast,
  ]);

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

  const activateAgentWithAssessmentChoice = (monthlyBudgetCents, assessmentAction = null) => {
    if (!Number.isFinite(numericRoleId)) return;
    const assessmentFields = assessmentAction === 'skip_assessment'
      ? { activation_assessment_action: assessmentAction, auto_skip_assessment: true }
      : assessmentAction
        ? { activation_assessment_action: assessmentAction }
        : {};
    setActivationReview(null);
    // First activation is not optimistic. The OFF strip remains truthful until
    // the backend has accepted the autonomy grant and returned authoritative
    // role state; a rejected PATCH must never flash ON or "starting".
    rolesApi
      .update(numericRoleId, versionedRolePayload(role, {
        agentic_mode_enabled: true,
        monthly_usd_budget_cents: monthlyBudgetCents,
        // Turn on executes the policy the recruiter reviewed. It must never
        // silently broaden a role's existing autonomy grant.
        ...activationAutonomyPayload(role),
        ...assessmentFields,
      }))
      .then((response) => {
        if (response?.data) setRole(response.data);
        void refetchAgentStatus?.();
        void loadRoleWorkspace();
      })
      .catch((error) => {
        void refetchAgentStatus?.();
        void loadRoleWorkspace();
        if (!handleRoleVersionConflict(error)) {
          showToast(getErrorMessage(error, 'Failed to turn on agent mode.'), 'error');
        }
      });
  };

  const requestAgentActivationWhenReady = (monthlyBudgetCents, draft = null) => {
    if (!Number.isFinite(numericRoleId)) return;
    setActivationReview({
      monthlyBudgetCents,
      draft,
      activationSubmitting: true,
      activationRequested: false,
      activationError: null,
    });
    rolesApi.update(numericRoleId, versionedRolePayload(role, {
      agentic_mode_enabled: true,
      monthly_usd_budget_cents: monthlyBudgetCents,
      ...activationAutonomyPayload(role),
      activation_assessment_action: 'approve_when_ready',
    }))
      .then((response) => {
        if (response?.data) setRole(response.data);
        setActivationReview((current) => (current ? {
          ...current,
          activationSubmitting: false,
          activationRequested: true,
          activationError: null,
        } : current));
        void refetchAgentStatus?.();
        void loadRoleWorkspace();
      })
      .catch((error) => {
        const conflict = roleVersionConflict(error);
        const detail = conflict?.message || getErrorMessage(error, 'Failed to queue agent activation.');
        setActivationReview((current) => (current ? {
          ...current,
          activationSubmitting: false,
          activationRequested: false,
          activationError: detail,
        } : current));
        if (!handleRoleVersionConflict(error)) showToast(detail, 'error');
      });
  };

  const handleActivateAgent = (monthlyBudgetCents) => {
    if (!canControlRoleAgent) return;
    if (!Number.isFinite(monthlyBudgetCents) || monthlyBudgetCents <= 0) {
      showToast('Set a monthly cap greater than $0 before activating.', 'error');
      return;
    }
    setActivationPreflight({ monthlyBudgetCents });
  };

  const confirmAgentActivation = () => {
    if (!canControlRoleAgent) return;
    const monthlyBudgetCents = Number(activationPreflight?.monthlyBudgetCents);
    if (!Number.isFinite(monthlyBudgetCents) || monthlyBudgetCents <= 0) {
      setActivationPreflight(null);
      showToast('Set a monthly cap greater than $0 before activating.', 'error');
      return;
    }
    setActivationPreflight(null);
    if (role?.role_kind === 'sister') return activateAgentWithAssessmentChoice(monthlyBudgetCents, 'skip_assessment');
    const activeTasks = (roleTasks || []).filter((task) => task?.is_active !== false);
    if (Boolean(role?.agent_effective_policy?.auto_skip_assessment ?? role?.auto_skip_assessment) || activeTasks.length > 0) {
      activateAgentWithAssessmentChoice(monthlyBudgetCents);
      return;
    }
    const generatedDraft = (roleTasks || []).find((task) => (
      task?.is_active === false && task?.generated && task?.needs_review !== false
    )) || null;
    requestAgentActivationWhenReady(monthlyBudgetCents, generatedDraft);
  };

  // Turn the agent OFF for this role — indefinite, no auto-resume. Opens a
  // confirm: off KEEPS pending decisions by default (they stay actionable),
  // with an opt-in to also discard the queue for a clean slate.
  const handleTurnOffAgent = () => {
    if (!canControlRoleAgent) return;
    setTurnOffDiscard(false);
    setTurnOffOpen(true);
  };

  const confirmTurnOffAgent = () => {
    if (!canControlRoleAgent || !Number.isFinite(numericRoleId)) return;
    const alsoDiscard = turnOffDiscard && (roleAgent?.pending || 0) > 0;
    const previousRole = role;
    setTurnOffOpen(false);
    // Optimistic: roleAgent.on is driven by role.agentic_mode_enabled, so flip
    // that in one frame; zero the pending count too when discarding.
    setRole((cur) => (cur ? { ...cur, agentic_mode_enabled: false } : cur));
    if (alsoDiscard && setAgentStatus) {
      setAgentStatus((cur) => (cur ? { ...cur, pending_decisions: 0 } : cur));
    }
    rolesApi
      .update(numericRoleId, versionedRolePayload(role, { agentic_mode_enabled: false }))
      .then((response) => {
        if (response?.data) setRole((current) => (current ? {
          ...current, ...response.data,
          stage_counts: current.stage_counts, pending_decisions_by_type: current.pending_decisions_by_type,
          active_candidates_count: current.active_candidates_count,
        } : response.data));
        return alsoDiscard
          ? apiClient.agent.discardPending(
              numericRoleId,
              roleExpectedVersion(response?.data),
            )
          : null;
      })
      .then(() => {
        void refetchAgentStatus?.();
        if (alsoDiscard) {
          void fetchPendingDecisions();
          void rolesApi.get(numericRoleId).then((response) => {
            if (response?.data) setRole(response.data);
          }).catch(() => {});
        }
      })
      .catch((error) => {
        setRole(previousRole);
        void refetchAgentStatus?.();
        if (!handleRoleVersionConflict(error)) {
          showToast(getErrorMessage(error, 'Failed to turn off agent.'), 'error');
        }
      });
  };

  const externalProvider = roleAtsProvider(role);
  const externalProviderLabel = atsProviderLabel(externalProvider);
  const relatedScoringActive = isRelatedRoleScoringActive(sisterScoringStatus);
  const intakeLifecycleCopy = agentIntakeLifecycleCopy(role);
  const manualPauseLifecycleCopy = externalProvider
    ? `A manual Pause also stops Taali processing until you Resume; it does not change the ${externalProviderLabel} posting.`
    : 'A manual Pause uses the same native-intake hold and waits for you to Resume.';

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
      <AgentHeader
        kicker={`ROLE · #${role?.id || '—'}`}
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
            {/* Reverse deep-link to the Hub: the total includes candidate
                decisions and open agent questions, so call them review items
                rather than implying every item is a decision. */}
            {(roleAgent?.pending || 0) > 0 ? (
              <button
                type="button"
                className="btn btn-outline btn-sm"
                title={rolePendingReviewTitle}
                aria-label={`${rolePendingReviewTitle}. Open the Home review queue.`}
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
                Review {roleAgent.pending} {roleAgent.pending === 1 ? 'item' : 'items'} →
              </button>
            ) : null}
            {role?.role_kind === 'sister' ? (
              <button type="button" className="btn btn-outline btn-sm" onClick={handleRescoreSister} disabled={sisterRescoring || relatedScoringActive}>
                {sisterRescoring || sisterScoringStatus?.status === 'running' || sisterScoringStatus?.status === 'retrying'
                  ? <Spinner size={12} />
                  : (sisterScoringStatus?.status === 'waiting' ? null : <RefreshCw size={12} />)}
                {relatedRoleScoringActionLabel(sisterScoringStatus)}
              </button>
            ) : null}
            {role?.role_kind !== 'sister' ? (
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => navigate(`/chat/agents/${role.id}`)}
                title="Open this job's agent chat"
              >
                <MessageSquare size={12} />
                Ask agent
              </button>
            ) : null}
            {role?.role_kind !== 'sister' && externalProvider ? (
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={handleStartRelatedRole}
                disabled={startingRelatedRole}
                title={`Create a separate scoring role over this ${externalProviderLabel} candidate pool`}
              >
                {startingRelatedRole ? <Spinner size={12} /> : <GitFork size={12} />}
                {startingRelatedRole ? 'Opening draft…' : 'Create related role'}
              </button>
            ) : null}
            {canEditJobSpec ? (
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => {
                  setJobSpecError('');
                  setJobSpecConflict(null);
                  setSpecEditorDirty(false);
                  setEditingSpec(true);
                  setActiveView('activity');
                }}
              >
                Edit job spec
              </button>
            ) : null}
          </>
        )}
        postTitle={(
          <div className="ah-facts">
            <div className="f"><span className="k">Location</span><span className="v">{roleFactValues.location}</span></div>
            <div className="f"><span className="k">Department</span><span className="v">{roleFactValues.department}</span></div>
            <div className="f"><span className="k">Employment</span><span className="v">{roleFactValues.employment}</span></div>
            {role?.role_kind === 'sister' ? (
              <div className="f"><span className="k">{externalProviderLabel} owner</span><span className="v purple">{role?.ats_owner_role_name || 'Original role'}</span></div>
            ) : (
              (() => {
                const activeTasks = roleTasks.filter((task) => task?.is_active !== false);
                const draftTasks = roleTasks.filter((task) => task?.is_active === false && task?.generated);
                return (
                  <div className="f">
                    <span className="k">{activeTasks.length > 1 ? 'Tasks · A/B' : activeTasks.length ? 'Linked task' : 'Assessment'}</span>
                    <span className="v purple">
                      {activeTasks.length
                        ? activeTasks.map((task) => task.name).join(' · ')
                        : draftTasks.length
                          ? `${draftTasks[0].name} · draft`
                          : Boolean(role?.agent_effective_policy?.auto_skip_assessment ?? role?.auto_skip_assessment)
                            ? 'Skipped'
                            : 'Generated after Turn on'}
                    </span>
                  </div>
                );
              })()
            )}
            {role?.role_kind !== 'sister' && Number(role?.sister_role_count || 0) > 0 ? (
              <div className="f"><span className="k">Related roles</span><span className="v purple">{role.sister_role_count} related role{role.sister_role_count === 1 ? '' : 's'}</span></div>
            ) : null}
          </div>
        )}
        agent={roleAgent}
        onActivateAgent={handleActivateAgent}
        onPauseAgent={handlePauseAgent}
        onResumeAgent={handleResumeEffectiveAgent}
        onTurnOffAgent={handleTurnOffAgent}
        onAgentSettings={goToAgentSettings}
        controlsDisabledReason={roleAgentControlDisabledReason}
      />
      {role?.role_kind === 'sister' ? (
        <RelatedRoleContextBanner
          role={role}
          providerLabel={externalProviderLabel}
          status={sisterScoringStatus}
          agentStatus={agentStatus}
          onResumeWorkspace={handleResumeEffectiveAgent}
          onOpenOriginal={() => navigate(`/jobs/${role.ats_owner_role_id}`)}
        />
      ) : null}
      <div className="page">
        {(activationIsPending || activationIsBlocked) ? (
          <div
            className="mc-agent-warn"
            role={activationIsBlocked ? 'alert' : 'status'}
            style={{ marginBottom: '1rem' }}
          >
            <div>
              <div className="mc-agent-warn-title">
                {activationIsBlocked ? 'Agent turn-on needs input' : 'Agent turn-on is queued'}
              </div>
              <div className="mc-agent-warn-body">
                {activationIsBlocked
                  ? (persistedActivationIntent?.last_error || 'The requisition needs a usable assessment task before the agent can turn on. Update the job specification, then press Turn on again.')
                  : (persistedActivationIntent?.last_error
                    ? `The saved request will retry automatically: ${persistedActivationIntent.last_error}`
                    : 'The saved request is generating and validating the assessment. You can leave this page; the agent will turn on automatically when production readiness passes.')}
              </div>
            </div>
          </div>
        ) : null}
        <div className="mc-cockpit-main">
        {/* Flat single-strip funnel (matches pipeline-preview): each stage cell
            stacks value + label + the agent's pending-decision chips inline, with
            the terminal Rejected cell set apart. The home hub uses the same
            variant — one funnel look across surfaces. */}
        {roleDetailLoading ? (
          <div className="mb-4 flex min-h-[88px] items-center justify-center rounded-xl border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] text-sm text-[var(--taali-text-muted)]" role="status">
            <Spinner size={18} />
            <span className="ml-2">Loading pipeline summary…</span>
          </div>
        ) : (
          <>
            {roleDetailLoadError ? (
              <div className="mc-agent-warn mb-3" role="alert">
                <div>
                  <div className="mc-agent-warn-title">Pipeline summary unavailable</div>
                  <div className="mc-agent-warn-body">{roleDetailLoadError}</div>
                </div>
                <button type="button" className="btn btn-outline btn-sm" onClick={loadRoleWorkspace}>Retry</button>
              </div>
            ) : null}
            {role?.role_kind === 'sister' ? (
              <RelatedRolePipelineLabel providerLabel={externalProviderLabel} />
            ) : null}
            <FunnelBoard variant="flat" stageCounts={role?.stage_counts} decisionsByType={role?.pending_decisions_by_type} scopeLabel="this role" />
          </>
        )}

        <RoleViewTabs activeView={activeView} onBeforeNavigate={handleRoleViewNavigate} />

        <PresenceSwap presenceKey={activeView} className="role-view-panel">
          {activeView === 'pipeline' ? (
          <div className="pipeline-layout">
            <MotionStagger className="kanban" data-motion-stagger="job-pipeline-columns">
              {groupedApplications.map((stage) => {
                const visibleItems = stage.items.slice(0, 3);
                const hiddenCount = Math.max(0, stage.items.length - visibleItems.length);
                return (
                  <div key={stage.key} className="kanban-col" data-stage={stage.key}>
                    <div className="kanban-col-head">
                      <div className="title"><span className="dot" />{stage.label}</div>
                      <div className="count">{formatCount(stage.items.length)}</div>
                    </div>
                    {visibleItems.map((application) => {
                      const relatedRoleLocked = application?.related_role_availability === 'disqualified';
                      const cvRaw = application?.cv_match_score;
                      const cvPct = cvRaw != null && Number.isFinite(Number(cvRaw))
                        ? Math.round(Number(cvRaw))
                        : null;
                      const compositeRaw = application?.score_summary?.taali_score
                        ?? application?.taali_score
                        ?? application?.assessment_score
                        ?? null;
                      const compositeScore = compositeRaw != null && Number.isFinite(Number(compositeRaw))
                        ? Math.round(Number(compositeRaw))
                        : null;
                      const isLive = String(application?.pipeline_stage || '').toLowerCase() === 'in_assessment';
                      const isReview = applicationFunnelBucket(application) === 'completed';
                      // Approve/Override act ONLY on the freshly-polled map, not
                      // the per-row snapshot (which can go stale and expose
                      // actions against an already-resolved decision).
                      const pendingDecision = pendingAgentDecisions[application?.id] || null;
                      const decisionResolving = pendingDecision?.id != null
                        && resolvingDecisionId === pendingDecision.id;
                      const applicationTitle = buildApplicationTitle(application);
                      return (
                        <div
                          key={application.id}
                          className={`kanban-card text-left ${isReview ? 'is-review' : ''}${relatedRoleLocked ? ' related-role-locked' : ''}`}
                          onMouseEnter={() => hoverPrefetchRef.current.start(application.id)}
                          onMouseLeave={() => hoverPrefetchRef.current.cancel()}
                        >
                          <a
                            className="kanban-card-main"
                            href={candidateReportHref(application, numericRoleId)}
                            aria-label={`Open ${applicationTitle}`}
                            onClick={(event) => handlePipelineReportClick(event, application)}
                          >
                            <div className="cc-top">
                              <div className="av">{applicationTitle.slice(0, 2).toUpperCase()}</div>
                              <div className="cc-id">
                                <div className="n">{applicationTitle}</div>
                                <div className="pos">
                                  {application?.candidate_position
                                    || application?.candidate_email
                                    || 'No position captured'}
                                </div>
                              </div>
                            </div>
                            <div className="cc-line">
                              <span>CV {cvPct != null ? `${cvPct}%` : '—'}</span>
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
                          </a>
                          {pendingDecision ? (
                            <div className="cc-agent">
                              <div className="cc-agent-glyph" aria-hidden="true">
                                <Sparkles size={11} strokeWidth={2} />
                              </div>
                              <div className="cc-agent-body">
                                <div className="cc-agent-action">
                                  {formatDecisionLabel(pendingDecision.recommendation)}
                                </div>
                                <div className="cc-agent-actions">
                                  <AgentLoop
                                    as="button"
                                    kind="flow"
                                    type="button"
                                    className="btn btn-purple btn-xs"
                                    onClick={(event) => {
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
                        </div>
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
            </MotionStagger>

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
            canControlAgent={canControlRoleAgent}
            controlDisabledReason={roleAgentControlDisabledReason}
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
              const cents = Math.max(1, Math.round(Number(dollars) * 100));
              try {
                const res = await rolesApi.update(numericRoleId, versionedRolePayload(role, {
                  monthly_usd_budget_cents: cents,
                }));
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
                if (!handleRoleVersionConflict(error)) {
                  showToast(getErrorMessage(error, 'Failed to update budget.'), 'error');
                }
                throw error;
              }
            }}
            onAutonomyChange={handleAutonomyChange}
            thresholdMode={role?.auto_reject_threshold_mode || 'manual'}
            suggestedThreshold={suggestedThreshold}
            savingThresholdMode={savingThresholdMode}
            onThresholdModeChange={handleThresholdModeChange}
            roleTasks={roleTasks}
            allTasks={allTasks}
            onAssignAssessmentTasks={handleAssignAssessmentTasks}
            savingAssessmentTask={savingAssessmentTask}
            onRoleVersionChange={(version) => {
              setRole((current) => (current ? { ...current, version } : current));
            }}
            onRoleConflict={loadRoleWorkspace}
          />
        ) : activeView === 'activity' ? (
          <div className={`role-spec-layout${editingSpec ? ' is-editing' : ''}`}>
            <section className="role-spec-document" aria-labelledby="job-spec-heading">
              <header className="role-spec-document-head">
                <div>
                  <span className="role-spec-eyebrow">{editingSpec ? 'Editing job brief' : 'Job brief'}</span>
                  <h2 id="job-spec-heading">{editingSpec ? 'Edit job specification' : 'Role specification'}</h2>
                </div>
                {!editingSpec && canEditJobSpec ? (
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={() => {
                      setJobSpecError('');
                      setJobSpecConflict(null);
                      setSpecEditorDirty(false);
                      setEditingSpec(true);
                    }}
                  >
                    Edit
                  </button>
                ) : null}
              </header>

              <PresenceSwap presenceKey={editingSpec ? 'edit' : 'read'} className="role-spec-mode">
                {editingSpec ? (
                  <RoleSpecEditPanel
                    role={role}
                    saving={savingJobSpec}
                    error={jobSpecError}
                    conflict={jobSpecConflict}
                    onDirtyChange={setSpecEditorDirty}
                    onSubmit={async (payload) => {
                      const ok = await handleJobSpecSubmit(payload);
                      if (ok) setEditingSpec(false);
                    }}
                    onResolveConflict={() => setJobSpecConflict(null)}
                    onCancel={() => {
                      setJobSpecError('');
                      setJobSpecConflict(null);
                      setEditingSpec(false);
                    }}
                  />
                ) : (
                  <div className="role-spec-read">
                    {roleSummary ? <p className="role-desc-summary">{roleSummary}</p> : null}

                    {(role?.job_status || clients.length > 0 || role?.client_id) ? (
                      <div className="role-spec-controls">
                        {role?.job_status ? (
                          <JobStatusControl
                            status={role.job_status}
                            onChange={handleSetJobStatus}
                            busy={savingJobStatus}
                          />
                        ) : null}
                        {(clients.length > 0 || role?.client_id) ? (
                          <ClientControl
                            clientId={role?.client_id ?? null}
                            clientName={role?.client_name ?? null}
                            clients={clients}
                            onChange={handleSetClient}
                            busy={savingClient}
                          />
                        ) : null}
                      </div>
                    ) : null}

                    {role?.requisition ? <RequisitionSpecSections requisition={role.requisition} /> : null}

                    <div className="role-spec-source-row">
                      <div>
                        <span className="role-spec-source-label">
                          {role?.job_spec_manually_edited_at
                            ? `Taali override${externalProvider ? ` · ${externalProviderLabel} connected` : ''}`
                            : externalProvider
                              ? `${externalProviderLabel} source description`
                              : 'Source description'}
                        </span>
                        {parsedJobSpec.meta.applyUrl ? (
                          <a href={parsedJobSpec.meta.applyUrl} target="_blank" rel="noopener noreferrer">Open source posting ↗</a>
                        ) : null}
                      </div>
                      <button
                        type="button"
                        className="desc-toggle"
                        aria-expanded={detailsExpanded}
                        aria-controls="job-source-description"
                        onClick={() => setDetailsExpanded((current) => !current)}
                      >
                        <span>{detailsExpanded ? 'Hide description' : 'View description'}</span>
                        <m.span
                          aria-hidden="true"
                          className="desc-toggle-caret"
                          animate={{ rotate: detailsExpanded ? 180 : 0 }}
                          transition={motionTransition.fast}
                        >
                          <ChevronDown size={11} />
                        </m.span>
                      </button>
                    </div>

                    <MotionDisclosure open={detailsExpanded} id="job-source-description">
                      <MotionStagger className="role-sections expanded" data-motion-stagger="job-spec-sections">
                        {parsedJobSpec.sections.length ? parsedJobSpec.sections.map((section, index) => (
                          <FormattedJobSpecSection
                            key={`${section.title}-${index}`}
                            section={section}
                            marker={String(index + 1).padStart(2, '0')}
                          />
                        )) : (
                          <div className="role-sec">
                            <div className="role-sec-title"><span className="marker">01</span>About the role</div>
                            <p>{roleSummary || 'No source description has been captured for this role yet.'}</p>
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
                      </MotionStagger>
                    </MotionDisclosure>
                  </div>
                )}
              </PresenceSwap>
            </section>

            {!editingSpec ? <aside className="role-highlights" aria-labelledby="job-glance-heading">
              <h3 id="job-glance-heading">At a glance</h3>
              {roleHighlights.map((item) => (
                <div key={item.title} className="hi">
                  <div className="t">{item.title}</div>
                  <div className="d">{item.description}</div>
                </div>
              ))}
              <div className="role-spec-agent-note">
                <Sparkles size={13} aria-hidden="true" />
                <div>
                  <strong>Agent context</strong>
                  <span>
                    {agentCriteria.length
                      ? `${agentCriteria.length} role requirement${agentCriteria.length === 1 ? '' : 's'} shape screening.`
                      : 'Scoring rules live in Agent settings.'}
                  </span>
                </div>
              </div>
            </aside> : null}
          </div>
        ) : activeView === 'hiring-team' ? (
          <HiringTeamPanel
            roleId={role?.id}
            roleVersion={role?.version}
            onChanged={loadRoleWorkspace}
          />
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

            {/* Read-only stage lens. Candidate ingestion, processing and ATS
                sync are operational agent work, not toolbar actions. */}
            <div className="ctable-toolbar">
              <div className="seg" role="tablist" aria-label="Filter candidates by stage">
                {[
                  { key: 'all', label: 'All', count: activeApplications.length },
                  ...PIPELINE_STAGE_ORDER.map((stage) => {
                    const items = (groupedApplications.find((g) => g.key === stage.key)?.items) || [];
                    return { key: stage.key, label: stage.label, count: items.length };
                  }),
                  // Rejected is an outcome, kept at the far edge of the lens.
                  { key: 'rejected', label: role?.role_kind === 'sister' ? 'Closed' : 'Rejected', count: rejectedApplications.length },
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
              {tableStageFilter === 'sourced' && selectedSourcedAppIds.size > 0 ? (
                <button
                  type="button"
                  className="btn btn-purple btn-sm"
                  onClick={() => setReachOutOpen(true)}
                  title="Draft and send an approval-gated outreach campaign to the selected sourced candidates"
                >
                  <Send size={12} />Reach out ({selectedSourcedAppIds.size})
                </button>
              ) : null}
              {role?.role_kind === 'sister' ? (
                <RelatedRoleScoringInlineStatus status={sisterScoringStatus} />
              ) : null}
            </div>
            {tableStageFilter === 'sourced' && numericRoleId ? (
              <CampaignsMonitorPanel
                roleId={numericRoleId} focusCampaignId={focusCampaignId}
                defaultOpen={focusCampaignId != null}
              />
            ) : null}
            {(() => {
              const sorted = sortedTableApplications;
              if (applicationsLoading && roleApplications.length === 0) {
                return (
                  <div className="ctable-wrap">
                    <div className="ctable-empty" role="status">
                      <Spinner size={16} /> Loading candidates…
                    </div>
                  </div>
                );
              }
              if (applicationsLoadError && roleApplications.length === 0) {
                return (
                  <div className="ctable-wrap">
                    <div className="ctable-empty" role="alert">
                      {applicationsLoadError}{' '}
                      <button type="button" className="btn btn-outline btn-sm" onClick={loadRoleWorkspace}>Retry</button>
                    </div>
                  </div>
                );
              }
              if (sorted.length === 0) {
                return (
                  <div className="ctable-wrap">
                    <div className="ctable-empty">
                      No candidates match the current filter. Try widening the stage segment above.
                    </div>
                  </div>
                );
              }
              const visible = sorted.slice(0, tableVisibleCount);
              const hiddenCount = sorted.length - visible.length;
              const sourcingSelection = tableStageFilter === 'sourced';
              const visibleIds = sourcingSelection ? visible.map((a) => a.id) : [];
              const allSelected = visibleIds.length > 0
                && visibleIds.every((id) => selectedSourcedAppIds.has(id));
              const someSelected = visibleIds.some((id) => selectedSourcedAppIds.has(id));
              const toggleAllSourced = (checked) => {
                const next = new Set(selectedSourcedAppIds);
                visibleIds.forEach((id) => {
                  if (checked) next.add(id);
                  else next.delete(id);
                });
                setSelectedSourcedAppIds(next);
              };
              return (
                <div className="ctable-wrap">
                  <table className="ctable">
                    <thead>
                      <tr>
                        {sourcingSelection ? (
                          <th aria-label="Select" style={{ width: 28 }}>
                            <input
                              type="checkbox"
                              aria-label="Select all visible sourced candidates"
                              checked={allSelected}
                              ref={(element) => { if (element) element.indeterminate = !allSelected && someSelected; }}
                              onChange={(event) => toggleAllSourced(event.target.checked)}
                            />
                          </th>
                        ) : null}
                        <th>Candidate</th>
                        <th aria-sort={tableSortField === 'score' ? (tableSortBy === 'asc' ? 'ascending' : 'descending') : 'none'}>
                          <button type="button" className="ctable-sort" onClick={() => handleTableSort('score')} aria-label="Sort by score" title="Sort by score">{role?.role_kind === 'sister' ? 'Related-role score' : 'Score'}{tableSortField === 'score' ? <span className="ctable-sort-arrow">{tableSortBy === 'asc' ? '↑' : '↓'}</span> : null}</button>
                        </th>
                        {role?.role_kind === 'sister' ? <th title={`Fit score on ${role?.ats_owner_role_name || 'the original role'}`}>Original fit</th> : null}
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
                        const isSelected = selectedSourcedAppIds.has(application.id);
                        const isTriageRow = (
                          triageApplication
                          && Number(triageApplication.id) === Number(application.id)
                        );
                        return (
                          <React.Fragment key={application.id}>
                            <tr
                              className={`${isAgentRow ? 'agent-row ' : ''}${application?.related_role_availability === 'disqualified' ? 'related-role-locked' : ''}`.trim()}
                              onClick={(event) => handlePipelineReportClick(event, application)}
                              onMouseEnter={() => hoverPrefetchRef.current.start(application.id)}
                              onMouseLeave={() => hoverPrefetchRef.current.cancel()}
                              style={{ cursor: 'pointer' }}
                            >
                              {sourcingSelection ? (
                                <td onClick={(event) => event.stopPropagation()} style={{ width: 28 }}>
                                  <input
                                    type="checkbox"
                                    aria-label={`Select ${buildApplicationTitle(application)}`}
                                    checked={isSelected}
                                    onChange={() => {
                                      const next = new Set(selectedSourcedAppIds);
                                      if (next.has(application.id)) next.delete(application.id);
                                      else next.add(application.id);
                                      setSelectedSourcedAppIds(next);
                                    }}
                                  />
                                </td>
                              ) : null}
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
                              {role?.role_kind === 'sister' ? (
                                <td>
                                  {application?.source_role_score != null
                                    ? <span className="stage-pill">{Math.round(Number(application.source_role_score))}</span>
                                    : <span className="ctable-em">—</span>}
                                </td>
                              ) : null}
                              <td>
                                <span className="stage-pill">{stageLabel}</span>
                              </td>
                              <td>{(() => {
                                if (roleAtsType(role) === 'full_ats') {
                                  return <span className="stage-pill" title="Stage in the Taali pipeline">{stageLabel}</span>;
                                }
                                const externalStage = applicationAtsStage(application, role);
                                if (externalProvider === 'workable' && application?.workable_disqualified) {
                                  return (
                                    <span
                                      className="stage-pill is-disqualified"
                                      title={externalStage ? `Disqualified in Workable (was: ${formatStatusLabel(externalStage)})` : 'Disqualified in Workable'}
                                    >
                                      Disqualified
                                    </span>
                                  );
                                }
                                return externalStage ? (
                                  <span className="stage-pill" title={`Current stage in ${externalProviderLabel}`}>
                                    {formatStatusLabel(externalStage)}
                                  </span>
                                ) : <span className="ctable-em">—</span>;
                              })()}</td>
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
                                <td colSpan={(role?.role_kind === 'sister' ? 8 : 7) + (sourcingSelection ? 1 : 0)} className="ctable-triage-cell">
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
        </PresenceSwap>

        {/* Role editing is now inline on the Job Specification tab
            (<RoleSpecEditPanel>), so the role-edit slide-over is retired here. */}

        <ConfirmActionDialog
          open={pendingRoleView != null}
          title="Leave without saving?"
          description="Your unsaved job specification edits will be lost."
          warning="Save the job specification first if you want to keep this draft."
          confirmLabel="Discard and leave"
          variant="danger"
          onClose={() => setPendingRoleView(null)}
          onConfirm={discardSpecAndNavigate}
        />

        <ReachOutDialog
          open={reachOutOpen}
          roleId={numericRoleId}
          roleTitle={role?.name || ''}
          applications={sortedTableApplications.filter((application) => selectedSourcedAppIds.has(application.id))}
          onClose={() => setReachOutOpen(false)}
          onCompleted={() => setSelectedSourcedAppIds(new Set())}
          onSent={(campaignId) => {
            setReachOutOpen(false);
            setSelectedSourcedAppIds(new Set());
            setFocusCampaignId(campaignId ?? null);
            setTableStageFilter('sourced');
          }}
        />

        <Dialog
          open={Boolean(activationPreflight)}
          onClose={() => setActivationPreflight(null)}
          title="Turn on this role’s agent?"
          description="Confirm once and the saved policy keeps running after you close this page."
          footer={(
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setActivationPreflight(null)}>Cancel</Button>
              <Button type="button" variant="primary" onClick={confirmAgentActivation} disabled={!canControlRoleAgent} title={!canControlRoleAgent ? roleAgentControlDisabledReason : undefined}>Turn on with this policy</Button>
            </div>
          )}
        >
          <div className="space-y-3 text-sm">
            <div className="mc-agent-settings-card-help">
              <strong>Effective automation</strong>
              <ul style={{ margin: '8px 0 0', paddingLeft: 20 }}>
                <li>
                  {externalProvider
                    ? `${externalProviderLabel} remains the intake source; its current publish state is unchanged.`
                    : 'The native job page opens for applications after activation succeeds.'}
                </li>
                <li>Applications that reach this role are parsed, screened, scored, and monitored while the agent is on.</li>
                <li>
                  Initial assessments {resolvedRoleAutomation(role, 'auto_send_assessment') ? 'send automatically' : 'wait for recruiter approval'};
                  {' '}resends {resolvedRoleAutomation(role, 'auto_resend_assessment') ? 'run automatically' : 'wait for recruiter approval'}.
                </li>
                <li>
                  Qualified candidates {resolvedRoleAutomation(role, 'auto_advance') ? 'advance automatically to recruiter handoff' : 'wait for recruiter approval before advancing'}.
                </li>
                <li>
                  Pre-screen failures {
                    resolvedDeterministicReject(role)
                      ? 'reject automatically when provider and safety checks pass'
                      : 'wait for recruiter approval'
                  }. Full CV-score and assessment rejections still need approval.
                </li>
                <li>
                  Assessment stage: {
                    Boolean(role?.agent_effective_policy?.auto_skip_assessment ?? role?.auto_skip_assessment)
                      ? 'explicitly skipped for this role'
                      : (roleTasks || []).some((task) => task?.is_active !== false)
                        ? 'uses the active approved task'
                        : 'the agent generates, repairs, battle-tests, and approves a role-specific task automatically'
                  }.
                </li>
              </ul>
            </div>
            <div className="mc-agent-warn" role="status">
              <div>
                <div className="mc-agent-warn-title">
                  Monthly AI-usage cap: ${Math.round(Number(activationPreflight?.monthlyBudgetCents || 0) / 100)}
                </div>
                <div className="mc-agent-warn-body">
                  Pause or Turn off stops autonomous processing and AI spend. {intakeLifecycleCopy}
                </div>
              </div>
            </div>
          </div>
        </Dialog>

        <Dialog
          open={Boolean(activationReview)}
          onClose={() => setActivationReview(null)}
          title="Preparing the assessment and turning on"
          description={activationReview?.activationSubmitting
            ? 'Saving Turn-on… The agent remains off until the backend confirms this request.'
            : activationReview?.activationRequested
              ? 'Your Turn-on request is saved. The agent will validate the generated task and turn on automatically, even after you close this dialog.'
              : activationReview?.activationError
                ? 'The Turn-on request was not saved. The agent remains off; retry when ready.'
                : 'The Turn-on request has not been saved.'}
          footer={(
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setActivationReview(null)}>Close</Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => activateAgentWithAssessmentChoice(activationReview?.monthlyBudgetCents, 'skip_assessment')}
                disabled={!canControlRoleAgent || Boolean(activationReview?.activationSubmitting)}
                title={!canControlRoleAgent ? roleAgentControlDisabledReason : undefined}
              >
                Skip assessment &amp; turn on
              </Button>
              {activationReview?.activationError ? (
                  <Button
                    type="button"
                    variant="primary"
                    disabled={!canControlRoleAgent}
                    title={!canControlRoleAgent ? roleAgentControlDisabledReason : undefined}
                    onClick={() => requestAgentActivationWhenReady(
                      activationReview?.monthlyBudgetCents,
                      activationReview?.draft || null,
                    )}
                  >
                    Retry request
                  </Button>
                ) : null}
            </div>
          )}
        >
          <div className="space-y-3 text-sm">
            {activationReview?.activationError ? (
              <div className="mc-agent-warn" role="alert">
                <div>
                  <div className="mc-agent-warn-title">Turn-on request failed</div>
                  <div className="mc-agent-warn-body">{activationReview.activationError}</div>
                </div>
              </div>
            ) : null}
            {activationReview?.draft ? (
              <>
                <div>
                  <strong>{activationReview.draft.name}</strong>
                  <span style={{ opacity: 0.7 }}>
                    {' '}· {activationReview.draft.duration_minutes || 30} minutes
                  </span>
                </div>
                <p style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                  {activationReview.draft.scenario || activationReview.draft.description || 'Generated from this requisition.'}
                </p>
                {activationReview.draft.battle_test?.verdict === 'pass' ? (
                  <p style={{ margin: 0, color: 'var(--success, #16804b)' }}>
                    {activationReview?.activationSubmitting
                      ? 'Automated battle test passed. Saving the Turn-on request…'
                      : activationReview?.activationRequested
                        ? 'Automated battle test passed. The saved request will turn the agent on as soon as production readiness passes.'
                        : 'Automated battle test passed. Retry Turn-on to save the activation request.'}
                  </p>
                ) : (
                  <div className="mc-agent-warn" role="alert">
                    <div>
                      <div className="mc-agent-warn-title">
                        {activationReview.draft.battle_test?.verdict === 'fail'
                          ? 'Automated battle test did not pass'
                          : 'Automated battle test is still pending'}
                      </div>
                      <div className="mc-agent-warn-body">
                        {activationReview?.activationSubmitting
                          ? 'Saving Turn-on… The agent remains off until the backend confirms the request.'
                          : activationReview?.activationRequested
                            ? 'Automatic repair and validation are still running. You can close this dialog; the saved request will continue, or you can explicitly skip the assessment stage.'
                            : 'Turn-on was not saved. Retry the request, or explicitly skip the assessment stage.'}
                      </div>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="mc-agent-warn" role="status">
                <div>
                  <div className="mc-agent-warn-title">Assessment generation is still pending</div>
                  <div className="mc-agent-warn-body">
                    {activationReview?.activationSubmitting
                      ? 'Saving Turn-on… The agent remains off until the backend confirms the request.'
                      : activationReview?.activationRequested
                        ? 'You can close this dialog: generation and validation continue from the saved request. You can also turn on now with the assessment stage explicitly skipped.'
                        : 'Turn-on was not saved. Retry the request, or explicitly skip the assessment stage.'}
                    {role?.assessment_task_provisioning?.last_error
                      ? ` Latest authoring attempt: ${role.assessment_task_provisioning.last_error}`
                      : ''}
                  </div>
                </div>
              </div>
            )}
          </div>
        </Dialog>

        <Dialog
          open={turnOffOpen}
          onClose={() => setTurnOffOpen(false)}
          title="Turn off the agent for this role?"
          description={`The agent stops autonomous processing and AI spend and won't resume on its own. ${intakeLifecycleCopy} ${manualPauseLifecycleCopy}`}
          footer={(
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setTurnOffOpen(false)}>Cancel</Button>
              <Button type="button" variant="danger" onClick={confirmTurnOffAgent} disabled={!canControlRoleAgent} title={!canControlRoleAgent ? roleAgentControlDisabledReason : undefined}>Turn off</Button>
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
