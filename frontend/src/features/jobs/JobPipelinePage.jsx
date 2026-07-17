import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import '../../styles/16-job-pipeline.css';
import '../../styles/03-settings-agent.css';
import { useParams, useNavigate } from 'react-router-dom';
import { ChevronDown, Send, Sparkles } from 'lucide-react';

import * as apiClient from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { useJobStatus } from '../../contexts/JobStatusContext';
import { Dialog, Button, PageLoader, Spinner } from '../../shared/ui/TaaliPrimitives';
import { ConfirmActionDialog } from '../../shared/ui/ConfirmActionDialog';
import { readCache, writeCache } from '../../shared/api/resourceCache';
import { RoleViewTabs, useRoleView } from './RoleViewTabs';
import { HiringTeamPanel } from './HiringTeamPanel';
import { useRoleProgressPolling } from './useRoleProgressPolling';
import { parseJobSpec, FormattedJobSpecSection } from './jobSpecFormatting';
import { RequisitionSpecSections, JobStatusControl, ClientControl } from './RequisitionSpecSections';
import { RoleAgentSettingsTab } from './RoleAgentSettingsTab';
import { requisitionApi } from '../requisitions/api';
import { useAgentStatus } from '../../shared/layout/AgentBar';
import { buildAgentPropFromStatus } from '../../shared/layout/AgentHeader';
import { AgentLoop, MotionDisclosure, MotionStagger, PresenceSwap, m, motionSafeScrollBehavior, motionTransition } from '../../shared/motion';
import { BackgroundJobsToaster } from '../candidates/BackgroundJobsToaster';
import { CandidateTriageDrawer, candidateReportHref } from '../candidates/CandidateTriageDrawer';
import { ScoreProvenance } from '../candidates/ScoreProvenance';
import { OverrideModal } from '../home/OverrideModal';
import { useCandidateTriage } from './useCandidateTriage';
import { RoleSpecEditPanel } from './RoleSpecEditPanel';
import { conflictActorLabel, reconcileRoleVersionConflict, roleExpectedVersion, roleVersionConflict, versionedRolePayload } from './roleConcurrency';
import { ReachOutDialog } from './ReachOutDialog';
import { ProcessCandidatesDialog } from './ProcessCandidatesDialog';
import { JobPipelineRoleHeader } from './JobPipelineRoleHeader';
import { CampaignsMonitorPanel } from './CampaignsMonitorPanel';
import {
  agentIntakeLifecycleCopy,
  applicationAtsStage,
  atsProviderLabel,
  atsTypeColumnLabel,
  roleAtsProvider,
  roleAtsType,
} from './atsType';
import { getErrorMessage, formatStatusLabel, renderJobPipelineScoreCell } from '../candidates/candidatesUiUtils';
import { formatCount, budgetTile, applicationFunnelBucket, awaitingHitlFromDecisions, decisionPendingFromCounts } from '../../shared/metrics';
import { FunnelBoard } from '../../shared/ui/FunnelBoard';
import { KpiStrip } from '../../shared/ui/KpiStrip';
import { makeCandidateCvHoverPrefetch } from './candidateCvHoverPrefetch';
import { useRoleAutonomyChange } from './useRoleAutonomyChange';
import { useRoleActivationFlow } from './useRoleActivationFlow';
import { useRoleAgentControls } from './useRoleAgentControls';
import { useRoleBriefControls } from './useRoleBriefControls';
import { useTaskCatalogue } from './useTaskCatalogue';
import { useRoleTaskRefresh } from './useRoleTaskRefresh';
import { useRoleWorkspaceRouteIdentity, useRoleWorkspaceRouteReset } from './useRoleWorkspaceRouteReset';
import { useApplicationRowPatch } from './useApplicationRowPatch';
import { usePipelineDecisionControls } from './usePipelineDecisionControls';
import { mergeRoleShell } from './roleShellMerge';
import {
  EMPTY_FETCH_PROGRESS, EMPTY_PRE_SCREEN_PROGRESS, EMPTY_PROGRESS, GRANULAR_AUTOMATION_KEYS,
  PIPELINE_STAGE_ORDER, buildApplicationTitle, formatDecisionLabel, formatRelativeShort,
  formatStageLabel, matchesPipelineStage, normalizeThreshold, resolveOptionalPercent,
  hasActiveAssessmentTask, resolvedDeterministicReject, resolvedRoleAutomation, summarizeUnscoredApplications,
} from './jobPipelineUtils';
import { APPLICATION_ROSTER_PAGE_SIZE, loadApplicationRoster } from './applicationRosterLoader';
import { buildRelatedRolePipelineStats, RelatedRoleContextBanner, RelatedRolePipelineLabel, RelatedRoleScoringInlineStatus, relatedRoleRecoveryAuthorization, relatedRoleRescoreAuthorization, shouldRefreshRelatedRoleWorkspace, useEffectiveRelatedAgentResume, useRelatedRoleRecoveryScope, useRelatedRoleRescoreApproval, useRelatedRoleScoringPolling } from './relatedRoleScoringUi';
import { linkedRoleTargetCopy, roleReferenceLabel } from './RoleFamilyHeaderUi';

const isRelatedRoleApplicationLocked = (application) => (
  ['disqualified', 'closed'].includes(application?.related_role_availability)
);
const captureTaskRequest = (request) => request.then(
  (response) => ({ response, error: null }), (error) => ({ response: null, error }),
);
export const JobPipelinePage = ({ onNavigate, onViewCandidate, NavComponent = null }) => {
  const { roleId } = useParams();
  const navigate = useNavigate();
  const rolesApi = apiClient.roles;
  const tasksApi = 'tasks' in apiClient ? apiClient.tasks : null;
  const { showToast } = useToast();
  const { user } = useAuth();
  const canControlWorkspaceAgent = String(user?.role || '') === 'owner';
  const {
    jobs,
    processJobs,
    trackRole,
    trackRoleProcess,
  } = useJobStatus() ?? {};
  void onViewCandidate;

  const numericRoleId = Number(roleId);
  const { currentRoleIdRef, currentRoleScopeRef, roleRenderGenerationRef, roleScopeKey } = useRoleWorkspaceRouteIdentity(numericRoleId);
  const loadedRoleIdRef = useRef(null);
  const loadRoleWorkspaceRef = useRef(null);
  const batchScoreProgress = jobs?.[numericRoleId] ?? EMPTY_PROGRESS;
  // Live status is polled every 30s and pauses when the tab is hidden.
  const {
    status: agentStatus,
    phase: agentStatusPhase,
    setStatus: setAgentStatus,
    refetch: refetchAgentStatus,
    mutateStatus: mutateAgentStatus,
  } = useAgentStatus(Number.isFinite(numericRoleId) ? numericRoleId : null);
  const canControlRoleAgent = agentStatus != null
    && agentStatus.can_control_agent !== false;
  const roleAgentControlDisabledReason = agentStatus == null
    ? (agentStatusPhase === 'error'
      ? 'Role actions are temporarily unavailable because the current permissions could not be loaded.'
      : 'Checking your role permissions…')
    : canControlRoleAgent
      ? null
      : 'Only workspace owners, hiring managers, and recruiters assigned to this role can make changes.';
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
  const [role, setRoleState] = useState(null);
  const setRole = useCallback((nextRole) => {
    setRoleState((current) => {
      if (currentRoleScopeRef.current !== roleScopeKey) return current;
      return typeof nextRole === 'function' ? nextRole(current) : nextRole;
    });
  }, [currentRoleScopeRef, roleScopeKey]);
  // Workspace chips also power the role editor's suppressed-chip view.
  const [workspaceCriteria, setWorkspaceCriteria] = useState([]);
  const [criteriaBusy, setCriteriaBusy] = useState(false);
  const [criteriaSyncing, setCriteriaSyncing] = useState(false);
  const [criteriaResetting, setCriteriaResetting] = useState(false);
  const [roleTasks, setRoleTasks] = useState([]);
  const [roleTasksFetchKnown, setRoleTasksFetchKnown] = useState(false);
  const [roleTasksLoadError, setRoleTasksLoadError] = useState('');
  const [assessmentContextTasks, setAssessmentContextTasks] = useState([]);
  const [assessmentContextTasksFetchKnown, setAssessmentContextTasksFetchKnown] = useState(false);
  const [assessmentContextTasksLoadError, setAssessmentContextTasksLoadError] = useState('');
  const [roleApplications, setRoleApplications] = useState([]);
  const {
    decisionAdvanceToConfirm,
    decisionApprovalToConfirm,
    fetchPendingDecisions,
    handleApproveDecision,
    handleOverrideDecision,
    pendingAgentDecisions,
    resolvingDecisionId,
    setDecisionAdvanceToConfirm,
    setDecisionApprovalToConfirm,
  } = usePipelineDecisionControls({
    agentApi: apiClient.agent, canControlRoleAgent, currentRoleIdRef, loadRoleWorkspaceRef,
    numericRoleId, organizationsApi: apiClient.organizations, role, roleRenderGenerationRef,
    setRoleApplications, showToast,
  });
  const [applicationsLoading, setApplicationsLoading] = useState(false);
  const [applicationsLoadError, setApplicationsLoadError] = useState('');
  const [fetchCvsProgress, setFetchCvsProgress] = useState(EMPTY_FETCH_PROGRESS);
  const [preScreenProgress, setPreScreenProgress] = useState(EMPTY_PRE_SCREEN_PROGRESS);
  const [sisterScoringStatus, setSisterScoringStatus] = useState(null);
  const [sisterRescoring, setSisterRescoring] = useState(false);
  const [sisterRescoreToConfirm, setSisterRescoreToConfirm] = useState(null);
  const [sisterPollVersion, setSisterPollVersion] = useState(0);
  const [processDialogOpen, setProcessDialogOpen] = useState(false);
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
    [setRole, showToast],
  );
  const handleAutonomyChange = useRoleAutonomyChange({
    numericRoleId,
    role,
    rolesApi,
    setRole,
    showToast,
  });

  useRelatedRoleScoringPolling(role?.role_kind === 'sister', numericRoleId, rolesApi, sisterPollVersion, setSisterScoringStatus);
  const { scope: relatedRoleRecoveryScope, loading: relatedRoleRecoveryScopeLoading, error: relatedRoleRecoveryScopeError } = useRelatedRoleRecoveryScope(
    role?.role_kind === 'sister' && Boolean(agentStatus?.workspace_paused) && canControlWorkspaceAgent, numericRoleId, apiClient.agent, `${sisterPollVersion}:${role?.version || ''}:${agentStatus?.workspace_control_version || ''}`);
  const relatedRoleRecoveryScopeReady = Boolean(relatedRoleRecoveryAuthorization(role, relatedRoleRecoveryScope));
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
  }, [handleRoleVersionConflict, numericRoleId, role, rolesApi, setRole, showToast]);
  const {
    clients,
    savingClient,
    savingJobStatus,
    setClient: handleSetClient,
    setJobStatus: handleSetJobStatus,
  } = useRoleBriefControls({
    canControlRole: canControlRoleAgent,
    handleRoleVersionConflict,
    role,
    roleId: numericRoleId,
    rolesApi,
    setRole,
    showToast,
  });
  const [, setRefreshTick] = useState(0);
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const [activeView, setActiveView] = useRoleView();
  const taskCatalogue = useTaskCatalogue({
    // Related roles are score-only and intentionally omit task management.
    // Wait for the shell before loading the organisation catalogue so a direct
    // ?view=role-fit link cannot make an unused request while role kind is unknown.
    enabled: activeView === 'role-fit'
      && Number(role?.id) === numericRoleId
      && role?.role_kind !== 'sister',
    listTasks: tasksApi?.list,
  });
  const [tableStageFilter, setTableStageFilter] = useState('all');
  // Only the Sourced lens supports selection; sending outreach is its HITL.
  const [selectedSourcedAppIds, setSelectedSourcedAppIds] = useState(() => new Set());
  const [reachOutOpen, setReachOutOpen] = useState(false);
  const canReachOutToSourcedCandidates = canControlRoleAgent && role?.role_kind !== 'sister';
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
    if (tableStageFilter !== 'sourced' || !canReachOutToSourcedCandidates) {
      setSelectedSourcedAppIds(new Set());
      setReachOutOpen(false);
    }
  }, [canReachOutToSourcedCandidates, tableStageFilter]);
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
  const [relatedSpecChangeToConfirm, setRelatedSpecChangeToConfirm] = useState(null);
  const [turnOffOpen, setTurnOffOpen] = useState(false);
  const [turnOffDiscard, setTurnOffDiscard] = useState(false);

  const canEditJobSpec = Boolean(role)
    && canControlRoleAgent;
  // Only the most recent workspace load may write state.
  const loadSeqRef = useRef(0);
  const taskLoadSeqRef = useRef(0);
  const hoverPrefetchRef = useRef(null);
  if (!hoverPrefetchRef.current) hoverPrefetchRef.current = makeCandidateCvHoverPrefetch();
  useRoleWorkspaceRouteReset(
    numericRoleId,
    { loadSeqRef, taskLoadSeqRef, loadedRoleIdRef },
    {
      setRole, setUsageBreakdown, setRoleTasks, setRoleTasksFetchKnown, setRoleTasksLoadError, setAssessmentContextTasks, setAssessmentContextTasksFetchKnown,
      setAssessmentContextTasksLoadError, setRoleApplications, setWorkspaceCriteria, setFetchCvsProgress, setPreScreenProgress, setSisterScoringStatus,
      setSisterRescoreToConfirm, setSisterRescoring, setSuggestedThreshold, setThresholdDraft, setSelectedSourcedAppIds, setReachOutOpen, setFocusCampaignId,
      setProcessDialogOpen, setRelatedSpecChangeToConfirm, setPendingRoleView, setEditingSpec, setSpecEditorDirty, setJobSpecError, setJobSpecConflict,
      setTurnOffOpen, setTurnOffDiscard, setLoadError, setApplicationsLoadError, setRoleDetailLoadError, setLoading, setRoleDetailLoading, setApplicationsLoading,
    },
  );
  const { refreshAssessmentTasks, refreshRoleAndTasks } = useRoleTaskRefresh({
    currentRoleIdRef, currentRoleScopeRef, numericRoleId, scopeKey: roleScopeKey,
    role,
    rolesApi,
    setAssessmentContextTasks,
    setAssessmentContextTasksFetchKnown,
    setAssessmentContextTasksLoadError,
    setRole,
    setRoleTasks,
    setRoleTasksFetchKnown,
    setRoleTasksLoadError,
    taskLoadSeqRef,
  });

  const loadRoleWorkspace = useCallback(async () => {
    if (!Number.isFinite(numericRoleId) || currentRoleIdRef.current !== numericRoleId) return;
    const seq = (loadSeqRef.current += 1);
    const taskSeq = (taskLoadSeqRef.current += 1);
    const isCurrentWorkspace = () => (
      seq === loadSeqRef.current && currentRoleIdRef.current === numericRoleId
    );
    const cacheKey = `role-workspace:${numericRoleId}`;
    const isColdForRole = loadedRoleIdRef.current !== numericRoleId;
    const cached = isColdForRole ? readCache(cacheKey) : null;
    const shouldMergeShell = Boolean(cached?.data?.role) || !isColdForRole;
    setLoadError('');
    setApplicationsLoadError('');
    setRoleDetailLoadError('');
    setRoleTasksFetchKnown(false);
    setRoleTasksLoadError('');
    setAssessmentContextTasksFetchKnown(false);
    setAssessmentContextTasksLoadError('');
    if (cached?.data) {
      const c = cached.data;
      setRole(c.role || null);
      setRoleTasks(Array.isArray(c.roleTasks) ? c.roleTasks : []);
      setAssessmentContextTasks(
        Array.isArray(c.assessmentContextTasks)
          ? c.assessmentContextTasks
          : (Array.isArray(c.roleTasks) ? c.roleTasks : []),
      );
      setRoleApplications(Array.isArray(c.roleApplications) ? c.roleApplications : []);
      setWorkspaceCriteria(Array.isArray(c.workspaceCriteria) ? c.workspaceCriteria : []);
      setLoading(false);
      setRoleDetailLoading(false);
      loadedRoleIdRef.current = numericRoleId;
    } else if (isColdForRole) {
      setRoleTasks([]);
      setAssessmentContextTasks([]);
      setLoading(true);
      setRoleDetailLoading(true);
    }
    setApplicationsLoading(true);
    let rolePainted = Boolean(cached?.data);
    try {
      // Paint from a bounded, single-query shell endpoint. The ordinary role
      // detail endpoint computes funnel and decision aggregates and can be
      // slow on large roles; none of that should block the first useful paint.
      const readsRoleShell = typeof rolesApi.getShell === 'function';
      const shellRes = readsRoleShell
        ? await rolesApi.getShell(numericRoleId)
        : await rolesApi.get(numericRoleId);
      if (!isCurrentWorkspace()) return;
      loadedRoleIdRef.current = numericRoleId;
      let nextRole = shellRes?.data || null;
      setRole((current) => (
        readsRoleShell && current && shouldMergeShell
          ? mergeRoleShell(current, nextRole)
          : nextRole
      ));
      rolePainted = Boolean(nextRole);
      setThresholdDraft(nextRole?.score_threshold != null ? String(nextRole.score_threshold) : '');
      setLoading(false);

      try {
        const roleRes = await rolesApi.get(numericRoleId);
        if (!isCurrentWorkspace()) return;
        nextRole = roleRes?.data || nextRole;
        setRole(nextRole);
        setThresholdDraft(nextRole?.score_threshold != null ? String(nextRole.score_threshold) : '');
      } catch (error) {
        if (!isCurrentWorkspace()) return;
        setRoleDetailLoadError(getErrorMessage(error, 'Pipeline summary could not be loaded.'));
      } finally {
        if (isCurrentWorkspace()) setRoleDetailLoading(false);
      }

      const isRelatedAssessmentContext = nextRole?.role_kind === 'sister';
      const assessmentContextOwnerRoleId = Number(nextRole?.ats_owner_role_id);
      const hasAssessmentContextOwner = Number.isFinite(assessmentContextOwnerRoleId)
        && assessmentContextOwnerRoleId > 0;
      const assessmentContextRoleId = isRelatedAssessmentContext && hasAssessmentContextOwner
        ? assessmentContextOwnerRoleId
        : numericRoleId;
      const missingAssessmentOwnerError = {
        response: { data: { detail: 'This related role is not linked to an original role, so its assessment tasks cannot be confirmed.' } },
      };
      const [tasksResult, contextTasksResult, batchStatusRes, fetchStatusRes, preScreenStatusRes, orgCriteriaRes] = await Promise.all([
        isRelatedAssessmentContext
          ? Promise.resolve({ response: { data: [] }, error: null })
          : captureTaskRequest(rolesApi.listTasks(numericRoleId)),
        isRelatedAssessmentContext
          ? (hasAssessmentContextOwner
            ? captureTaskRequest(rolesApi.listTasks(assessmentContextRoleId))
            : Promise.resolve({ response: null, error: missingAssessmentOwnerError }))
          : Promise.resolve(null),
        rolesApi.batchScoreStatus(numericRoleId).catch(() => ({ data: null })),
        rolesApi.fetchCvsStatus(numericRoleId).catch(() => ({ data: EMPTY_FETCH_PROGRESS })),
        rolesApi.batchPreScreenStatus(numericRoleId).catch(() => ({ data: EMPTY_PRE_SCREEN_PROGRESS })),
        Promise.resolve(apiClient.organizations?.listCriteria?.() ?? { data: [] })
          .catch(() => ({ data: [] })),
      ]);
      if (!isCurrentWorkspace()) return;
      const roleTasksKnown = tasksResult.error == null;
      const nextTasks = roleTasksKnown
        ? (Array.isArray(tasksResult.response?.data) ? tasksResult.response.data : []) : null;
      const nextAssessmentContextTasks = contextTasksResult == null
        ? nextTasks
        : (contextTasksResult.error == null
          ? (Array.isArray(contextTasksResult.response?.data) ? contextTasksResult.response.data : []) : null);
      const assessmentContextKnown = contextTasksResult == null
        ? roleTasksKnown
        : contextTasksResult.error == null;
      const assessmentContextError = contextTasksResult == null
        ? tasksResult.error
        : contextTasksResult.error;
      const nextCriteria = Array.isArray(orgCriteriaRes?.data) ? orgCriteriaRes.data : [];
      if (taskSeq === taskLoadSeqRef.current) {
        if (nextTasks != null) setRoleTasks(nextTasks);
        if (nextAssessmentContextTasks != null) setAssessmentContextTasks(nextAssessmentContextTasks);
        setRoleTasksFetchKnown(roleTasksKnown);
        setRoleTasksLoadError(roleTasksKnown ? '' : getErrorMessage(tasksResult.error, 'Assessment tasks could not be loaded.'));
        setAssessmentContextTasksFetchKnown(assessmentContextKnown);
        setAssessmentContextTasksLoadError(assessmentContextKnown
          ? ''
          : getErrorMessage(assessmentContextError, 'Assessment tasks could not be loaded.'));
      }
      setWorkspaceCriteria(nextCriteria);
      setFetchCvsProgress(fetchStatusRes?.data || EMPTY_FETCH_PROGRESS);
      setPreScreenProgress(preScreenStatusRes?.data || EMPTY_PRE_SCREEN_PROGRESS);

      const roster = await loadApplicationRoster({
        rolesApi,
        roleId: numericRoleId,
        isSister: nextRole?.role_kind === 'sister',
        isCurrent: isCurrentWorkspace,
        onProgress: setRoleApplications,
      });
      if (roster.cancelled || !isCurrentWorkspace()) return;
      const applicationPayloads = roster.applications;
      if (roster.error) setApplicationsLoadError(
        getErrorMessage(roster.error, 'Some candidates could not be loaded.'),
      );
      // Fetch the agent's threshold recommendation when the role is
      // in auto mode so the panel shows it without waiting for click.
      if (nextRole?.auto_reject_threshold_mode === 'auto' && Number.isFinite(numericRoleId)) {
        rolesApi.suggestedAutoRejectThreshold(numericRoleId)
          .then((res) => {
            if (isCurrentWorkspace()) setSuggestedThreshold(res?.data || null);
          })
          .catch(() => {
            if (isCurrentWorkspace()) setSuggestedThreshold(null);
          });
      } else setSuggestedThreshold(null);
      const nextApps = applicationPayloads;
      setRoleApplications(nextApps);
      if (taskSeq === taskLoadSeqRef.current
        && nextTasks != null && nextAssessmentContextTasks != null) writeCache(cacheKey, {
        role: nextRole, roleTasks: nextTasks, assessmentContextTasks: nextAssessmentContextTasks,
        roleApplications: nextApps.slice(0, APPLICATION_ROSTER_PAGE_SIZE), workspaceCriteria: nextCriteria,
      });
      const initBatchStatus = String(batchStatusRes?.data?.status || '').toLowerCase();
      if (['running', 'cancelling', 'cancelled', 'completed'].includes(initBatchStatus)) {
        trackRole?.(numericRoleId);
      }
    } catch (error) {
      if (!isCurrentWorkspace()) return;
      // Preserve any shell/cache paint when a background request fails.
      if (!rolePainted && isColdForRole && !cached?.data) {
        setRole(null);
        setRoleTasks([]);
        setAssessmentContextTasks([]);
        setRoleApplications([]);
        setLoadError(getErrorMessage(error, 'Failed to load this job.'));
        showToast(getErrorMessage(error, 'Failed to load role pipeline.'), 'error');
      } else if (rolePainted) {
        setApplicationsLoadError(getErrorMessage(error, 'Some job data could not be loaded.'));
      }
    } finally {
      if (isCurrentWorkspace()) {
        setLoading(false);
        setRoleDetailLoading(false);
        setApplicationsLoading(false);
      }
    }
  }, [currentRoleIdRef, numericRoleId, rolesApi, setRole, showToast, trackRole]);
  useEffect(() => {
    loadRoleWorkspaceRef.current = loadRoleWorkspace;
  }, [loadRoleWorkspace]);

  const patchApplicationRow = useApplicationRowPatch({
    currentRoleIdRef, loadRoleWorkspace, loadSeqRef, numericRoleId,
    roleKind: role?.role_kind, rolesApi, setRole, setRoleApplications,
  });

  useEffect(() => {
    void loadRoleWorkspace();
  }, [loadRoleWorkspace]);

  useEffect(() => {
    const previous = previousSisterScoringStateRef.current;
    const current = sisterScoringStatus?.status || null;
    previousSisterScoringStateRef.current = current;
    if (shouldRefreshRelatedRoleWorkspace(previous, current)) {
      void loadRoleWorkspace();
    }
  }, [loadRoleWorkspace, sisterScoringStatus?.status]);

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
      title: role?.role_kind === 'sister' ? 'Original-role assessment' : 'Assessment',
      description: (role?.role_kind === 'sister' ? assessmentContextTasksFetchKnown : roleTasksFetchKnown)
        ? ((role?.role_kind === 'sister' ? assessmentContextTasks : roleTasks)
          .filter((task) => task?.is_active === true)
          .map((task) => task.name).join(' · ') || 'No active assessment task linked')
        : ((role?.role_kind === 'sister' ? assessmentContextTasksLoadError : roleTasksLoadError)
          || 'Checking current task assignment…'),
    },
  ]), [assessmentContextTasks, assessmentContextTasksFetchKnown, assessmentContextTasksLoadError,
    role?.role_kind, roleFactValues, roleTasks, roleTasksFetchKnown, roleTasksLoadError]);

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
  // a full workspace refetch would needlessly walk every candidate page per edit.
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
  }, [handleRoleVersionConflict, numericRoleId, role?.version, rolesApi, setRole, showToast]);

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
  }, [handleRoleVersionConflict, numericRoleId, role?.version, rolesApi, setRole, showToast]);

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
  }, [handleRoleVersionConflict, loadRoleWorkspace, numericRoleId, role?.version, rolesApi, setRole, showToast]);

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
  }, [handleRoleVersionConflict, numericRoleId, role?.version, rolesApi, setRole, showToast]);

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
  }, [handleRoleVersionConflict, numericRoleId, role?.version, rolesApi, setRole, showToast]);

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
  }, [handleRoleVersionConflict, numericRoleId, role, rolesApi, setRole, showToast]);

  const handleJobSpecSubmit = async ({ name, jobSpecText }, { confirmed = false } = {}) => {
    if (!Number.isFinite(numericRoleId) || !canEditJobSpec) return false;
    if (role?.role_kind === 'sister' && !confirmed) {
      setRelatedSpecChangeToConfirm({ name, jobSpecText });
      return false;
    }
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
      if (role?.role_kind === 'sister') {
        // A successful related-role spec save marks prior evaluations stale.
        // Completed polling has no timer left to observe that transition, so
        // explicitly re-arm it after the mutation commits.
        setSisterPollVersion((value) => value + 1);
      }
      setRefreshTick((value) => value + 1);
      const affectedCount = Number(response?.data?.would_rescreen?.count || 0);
      const estimatedCost = Number(response?.data?.would_rescreen?.est_cost_usd || 0);
      showToast(
        role?.role_kind === 'sister' && affectedCount > 0
          ? `Job spec saved. ${formatCount(affectedCount)} related-role score${affectedCount === 1 ? '' : 's'} now need re-score approval${estimatedCost > 0 ? ` · estimated model cost $${estimatedCost.toFixed(2)}` : ''}. No model spend was started.`
          : affectedCount > 0
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
    if (
      !Number.isFinite(numericRoleId)
      || !canControlRoleAgent
      || role?.role_kind === 'sister'
    ) return false;
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
      await refreshRoleAndTasks();
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
      await refreshRoleAndTasks();
      throw error;
    } finally {
      setSavingAssessmentTask(false);
    }
  }, [canControlRoleAgent, handleRoleVersionConflict, numericRoleId, refreshRoleAndTasks,
    role, roleTasks, rolesApi, showToast]);

  /*
   * Job-spec text and linked assessments intentionally have separate owners:
   * this editor updates the screening document atomically, while Agent settings
   * owns the assessment set. Keeping those workflows separate prevents a text
   * edit from silently changing candidate assignment behavior.
   */

  const handleRescoreSister = useCallback(() => {
    if (!Number.isFinite(numericRoleId) || sisterRescoring || !canControlRoleAgent) return;
    const approval = relatedRoleRescoreAuthorization(role, sisterScoringStatus);
    if (approval) setSisterRescoreToConfirm(approval);
    else {
      showToast('Refresh the related-role scoring preview before approving paid work.', 'warning');
      setSisterPollVersion((value) => value + 1);
    }
  }, [canControlRoleAgent, numericRoleId, role, showToast, sisterRescoring, sisterScoringStatus]);

  const confirmRescoreSister = useRelatedRoleRescoreApproval({
    approval: sisterRescoreToConfirm,
    canControlRoleAgent,
    loadRoleWorkspace,
    numericRoleId,
    rolesApi,
    setApproval: setSisterRescoreToConfirm,
    setPollingVersion: setSisterPollVersion,
    setRescoring: setSisterRescoring,
    setStatus: setSisterScoringStatus,
    showToast,
  });

  const handleStartRelatedRole = useCallback(async () => {
    if (!role?.id || startingRelatedRole || !canControlRoleAgent) return;
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
  }, [canControlRoleAgent, navigate, role?.id, showToast, startingRelatedRole]);

  const handleOpenRoleSettings = () => {
    document.getElementById('role-scoring-panel')?.scrollIntoView({ behavior: motionSafeScrollBehavior('smooth'), block: 'start' });
  };

  const handleProcessCandidates = useCallback(async (body) => {
    if (!Number.isFinite(numericRoleId) || !canControlRoleAgent) return;
    try {
      await rolesApi.processRole(numericRoleId, body);
      trackRoleProcess?.(numericRoleId);
      setProcessDialogOpen(false);
      showToast('Candidate processing started. Progress will stay visible while you work.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to start candidate processing.'), 'error');
      throw error;
    }
  }, [canControlRoleAgent, numericRoleId, rolesApi, showToast, trackRoleProcess]);

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
    scopeKey: roleScopeKey,
    role, roleApplications, roleTasks: assessmentContextTasks,
    roleTasksFetchKnown: assessmentContextTasksFetchKnown,
    roleTasksLoadError: assessmentContextTasksLoadError,
    onRetryRoleTasks: refreshAssessmentTasks, canMutate: canControlRoleAgent,
    loadRoleWorkspace, patchApplicationRow, showToast, rolesApi, viewCandidateReport,
  });

  const {
    controlAction: roleAgentControlAction,
    pauseAgent: handlePauseAgent,
    resumeAgent: handleResumeAgent,
  } = useRoleAgentControls({
    roleId: numericRoleId, scopeKey: roleScopeKey, role, agentStatus, canControlAgent: canControlRoleAgent,
    mutateAgentStatus, setAgentStatus, setRole, loadRoleWorkspace, handleRoleVersionConflict, showToast,
  });
  const handleResumeLegacyWorkspace = useEffectiveRelatedAgentResume({ agentStatus, canResumeWorkspace: canControlWorkspaceAgent, onResumeRole: handleResumeAgent,
    recoverRelatedRole: apiClient.agent.recoverRelatedRole, recoveryScope: relatedRoleRecoveryScope, refetchAgentStatus, reloadRole: loadRoleWorkspace, role, setPollingVersion: setSisterPollVersion, showToast });

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
  const persistedActivationStatus = String(persistedActivationIntent?.status || '').toLowerCase();
  const activationIsPending = ['pending', 'retry_wait'].includes(persistedActivationStatus)
    && !role?.agentic_mode_enabled;
  const activationIsBlocked = persistedActivationStatus === 'blocked'
    && !role?.agentic_mode_enabled;

  const handleActivationTasksLoaded = useCallback((tasks) => {
    taskLoadSeqRef.current += 1;
    setRoleTasks(tasks);
    setRoleTasksFetchKnown(true);
    setRoleTasksLoadError('');
    if (role?.role_kind !== 'sister') {
      setAssessmentContextTasks(tasks);
      setAssessmentContextTasksFetchKnown(true);
      setAssessmentContextTasksLoadError('');
    }
  }, [role?.role_kind]);
  const {
    activationPreflight, activationReview, ordinaryActivationAllowed,
    setActivationPreflight, setActivationReview, activateAgentWithAssessmentChoice,
    requestAgentActivationWhenReady, handleActivateAgent, confirmAgentActivation,
  } = useRoleActivationFlow({
    canControlRoleAgent, handleRoleVersionConflict, numericRoleId, scopeKey: roleScopeKey,
    onTasksLoaded: handleActivationTasksLoaded, refetchAgentStatus, role, roleTasks,
    roleTasksFetchKnown, rolesApi, setRole, showToast, refreshRoleAndTasks,
  });

  // Never paint the previous role after React Router has changed the URL.
  const renderedRoleIsStale = Boolean(role) && Number(role?.id) !== numericRoleId;
  if (renderedRoleIsStale || (loading && !role)) {
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
          void fetchPendingDecisions({ force: true });
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
  const sisterRescoreCandidateCount = Math.max(
    0,
    Number(sisterRescoreToConfirm?.scoreableCount
      ?? sisterScoringStatus?.scoreable_total ?? sisterScoringStatus?.total ?? 0),
  );
  const sisterRescoreEstimatedCost = Math.max(
    0,
    Number(sisterRescoreToConfirm?.estimatedCostUsd
      ?? sisterScoringStatus?.estimated_rescore_cost_usd ?? 0),
  );
  const openJobSpecEditor = () => {
    setJobSpecError('');
    setJobSpecConflict(null);
    setSpecEditorDirty(false);
    setEditingSpec(true);
    setActiveView('activity');
  };
  const intakeLifecycleCopy = agentIntakeLifecycleCopy(role);
  const manualPauseLifecycleCopy = externalProvider
    ? `A manual Pause also stops Taali processing until you Resume; it does not change the ${externalProviderLabel} posting.`
    : 'A manual Pause uses the same native-intake hold and waits for you to Resume.';

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
      <JobPipelineRoleHeader
        canEditJobSpec={canEditJobSpec}
        canMutateRole={canControlRoleAgent}
        controlsDisabledReason={roleAgentControlDisabledReason}
        externalProvider={externalProvider}
        externalProviderLabel={externalProviderLabel}
        navigate={navigate}
        onActivateAgent={handleActivateAgent}
        onAgentSettings={goToAgentSettings}
        onEditJobSpec={openJobSpecEditor}
        onOpenProcessDialog={() => setProcessDialogOpen(true)}
        onPauseAgent={handlePauseAgent}
        onRescoreSister={handleRescoreSister}
        onResumeAgent={handleResumeAgent}
        onStartRelatedRole={handleStartRelatedRole}
        onTurnOffAgent={handleTurnOffAgent}
        processStatus={processJobs?.[numericRoleId]?.status}
        role={role}
        roleAgent={roleAgent}
        roleFactValues={roleFactValues}
        rolePendingReviewTitle={rolePendingReviewTitle}
        roleTasks={roleTasks}
        sisterRescoring={sisterRescoring}
        sisterScoringStatus={sisterScoringStatus}
        startingRelatedRole={startingRelatedRole}
      />
      {role?.role_kind === 'sister' ? (
        <RelatedRoleContextBanner
          role={role}
          providerLabel={externalProviderLabel}
          status={sisterScoringStatus}
          agentStatus={agentStatus}
          canResumeWorkspace={canControlWorkspaceAgent}
          recoveryScope={relatedRoleRecoveryScope}
          recoveryScopeError={relatedRoleRecoveryScopeError} recoveryScopeLoading={relatedRoleRecoveryScopeLoading} recoveryScopeReady={relatedRoleRecoveryScopeReady}
          onResumeWorkspace={handleResumeLegacyWorkspace}
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
          <div className="mb-4 flex min-h-[88px] items-center justify-center rounded-xl border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] text-sm text-[var(--taali-muted)]" role="status">
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

        <RoleViewTabs
          activeView={activeView}
          onBeforeNavigate={handleRoleViewNavigate}
          scoreOnly={role?.role_kind === 'sister'}
        />

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
                      const relatedRoleLocked = isRelatedRoleApplicationLocked(application);
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
                      const decisionResolving = pendingDecision?.status === 'processing'
                        || (pendingDecision?.id != null && resolvingDecisionId === pendingDecision.id);
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
                                      void handleApproveDecision(pendingDecision);
                                    }}
                                    disabled={!canControlRoleAgent || decisionResolving}
                                    title={!canControlRoleAgent ? roleAgentControlDisabledReason : undefined}
                                  >
                                    {decisionResolving ? '…' : 'Approve'}
                                  </AgentLoop>
                                  <button
                                    type="button"
                                    className="btn btn-outline btn-xs"
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      void handleOverrideDecision(pendingDecision);
                                    }}
                                    disabled={!canControlRoleAgent || decisionResolving}
                                    title={!canControlRoleAgent ? roleAgentControlDisabledReason : undefined}
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
            roleTasksFetchKnown={roleTasksFetchKnown}
            roleTasksLoadError={roleTasksLoadError}
            onRetryTasks={refreshAssessmentTasks}
            allTasks={taskCatalogue.items}
            taskCatalogueLoading={taskCatalogue.loading}
            taskCatalogueError={taskCatalogue.error}
            taskCatalogueHasMore={taskCatalogue.hasMore}
            onTaskCatalogueSearchChange={taskCatalogue.setQuery}
            onRetryTaskCatalogue={taskCatalogue.retry}
            onLoadMoreTaskCatalogue={taskCatalogue.loadMore}
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
                      setRelatedSpecChangeToConfirm(null);
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
                            disabled={!canControlRoleAgent}
                            disabledReason={roleAgentControlDisabledReason}
                          />
                        ) : null}
                        {(clients.length > 0 || role?.client_id) ? (
                          <ClientControl
                            clientId={role?.client_id ?? null}
                            clientName={role?.client_name ?? null}
                            clients={clients}
                            onChange={handleSetClient}
                            busy={savingClient}
                            disabled={!canControlRoleAgent}
                            disabledReason={roleAgentControlDisabledReason}
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
              {tableStageFilter === 'sourced' && canReachOutToSourcedCandidates && selectedSourcedAppIds.size > 0 ? (
                <button
                  type="button"
                  className="btn btn-purple btn-sm"
                  onClick={() => { if (canReachOutToSourcedCandidates) setReachOutOpen(true); }}
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
            {applicationsLoadError && roleApplications.length > 0 ? (
              <div className="mc-agent-warn" role="alert" style={{ marginBottom: '0.75rem' }}>
                <div>
                  <div className="mc-agent-warn-title">Candidate list partially loaded</div>
                  <div className="mc-agent-warn-body">{applicationsLoadError}</div>
                </div>
                <Button type="button" variant="secondary" size="sm" onClick={() => { void loadRoleWorkspace(); }}>
                  Retry
                </Button>
              </div>
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
              const sourcingSelection = tableStageFilter === 'sourced' && canReachOutToSourcedCandidates;
              const visibleIds = sourcingSelection ? visible.map((a) => a.id) : [];
              const allSelected = visibleIds.length > 0
                && visibleIds.every((id) => selectedSourcedAppIds.has(id));
              const someSelected = visibleIds.some((id) => selectedSourcedAppIds.has(id));
              const toggleAllSourced = (checked) => {
                if (!canReachOutToSourcedCandidates) return;
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
                              className={`${isAgentRow ? 'agent-row ' : ''}${isRelatedRoleApplicationLocked(application) ? 'related-role-locked' : ''}`.trim()}
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
                                      if (!canReachOutToSourcedCandidates) return;
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

        <ConfirmActionDialog
          open={relatedSpecChangeToConfirm != null}
          title="Save related-role job spec?"
          description={`Saving updates ${roleReferenceLabel({ id: role?.id, name: role?.name }) || 'this related role'}'s independent scoring specification over the shared pool linked across ${linkedRoleTargetCopy(role, role?.role_family)}. Existing scores on this related role will be marked stale.`}
          warning="No model spend starts on save. Review the affected roster and explicitly approve Re-score roster when you are ready."
          confirmLabel="Save and mark scores stale"
          onClose={() => setRelatedSpecChangeToConfirm(null)}
          onConfirm={() => {
            const draft = relatedSpecChangeToConfirm;
            setRelatedSpecChangeToConfirm(null);
            void handleJobSpecSubmit(draft, { confirmed: true }).then((ok) => {
              if (ok) setEditingSpec(false);
            });
          }}
        />

        <ConfirmActionDialog
          open={sisterRescoreToConfirm != null}
          title="Approve related-role re-score?"
          description={`Re-score the full scoreable roster${sisterRescoreCandidateCount > 0 ? `: ${formatCount(sisterRescoreCandidateCount)} candidate${sisterRescoreCandidateCount === 1 ? '' : 's'}` : ''} for ${roleReferenceLabel({ id: role?.id, name: role?.name }) || role?.name || 'this related role'}.`}
          warning={sisterRescoreEstimatedCost > 0
            ? `Estimated model cost: $${sisterRescoreEstimatedCost.toFixed(2)}. This paid work starts only after you confirm.`
            : 'This starts paid model work. The backend will apply the current roster and budget controls when you confirm.'}
          confirmLabel="Approve re-score roster"
          onClose={() => setSisterRescoreToConfirm(null)}
          onConfirm={() => { void confirmRescoreSister(); }}
        />

        <ConfirmActionDialog
          open={decisionApprovalToConfirm != null}
          title="Reject across linked roles?"
          description={`Approving this recommendation rejects the shared application for ${linkedRoleTargetCopy(
            role,
            decisionApprovalToConfirm?.role_family || role?.role_family,
          )}.`}
          warning="This rejection cannot be limited to only one role in the shared candidate pool."
          confirmLabel="Reject across all linked roles"
          variant="danger"
          onClose={() => setDecisionApprovalToConfirm(null)}
          onConfirm={() => {
            const decision = decisionApprovalToConfirm;
            setDecisionApprovalToConfirm(null);
            void handleApproveDecision(decision, { confirmed: true });
          }}
        />

        {decisionAdvanceToConfirm ? (
          <OverrideModal
            decision={decisionAdvanceToConfirm.decision}
            alternative={decisionAdvanceToConfirm.alternative}
            workableStages={decisionAdvanceToConfirm.workableStages}
            onClose={() => setDecisionAdvanceToConfirm(null)}
            onRoleFamilyChanged={async () => {
              if (currentRoleIdRef.current !== decisionAdvanceToConfirm.requestRoleId) return;
              showToast('The recommendation changed before approval. The latest decision is being reloaded; review it before trying again.', 'warning');
              await Promise.all([fetchPendingDecisions({ force: true }), loadRoleWorkspaceRef.current?.()]);
            }}
            onSubmitted={() => {
              if (currentRoleIdRef.current !== decisionAdvanceToConfirm.requestRoleId) return;
              const decisionId = decisionAdvanceToConfirm.decision.id;
              showToast('Recommendation approved.', 'success');
              setRoleApplications((apps) => apps.map((app) => (app?.pending_decision?.id === decisionId ? { ...app, pending_decision: null } : app)));
              void fetchPendingDecisions({ force: true });
            }}
          />
        ) : null}

        <ProcessCandidatesDialog
          open={processDialogOpen}
          roleId={numericRoleId}
          onClose={() => setProcessDialogOpen(false)}
          onConfirm={handleProcessCandidates}
        />

        <ReachOutDialog
          open={canReachOutToSourcedCandidates && reachOutOpen}
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
          title="Turn on agent?"
          description="Review what the agent can do without asking you."
          footer={(
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setActivationPreflight(null)}>Cancel</Button>
              {role?.role_kind === 'sister' ? null : (
                <>
                  <Button
                    onClick={() => confirmAgentActivation('skip_assessment')}
                    title={!canControlRoleAgent ? roleAgentControlDisabledReason : undefined}
                    type="button" variant="secondary" disabled={!canControlRoleAgent}
                  >Skip assessment &amp; turn on</Button>
                  <Button
                    onClick={() => confirmAgentActivation('approve_when_ready')}
                    title={!canControlRoleAgent ? roleAgentControlDisabledReason : undefined}
                    type="button" variant={ordinaryActivationAllowed ? 'secondary' : 'primary'} disabled={!canControlRoleAgent}
                  >Generate, validate &amp; approve, then turn on</Button>
                </>
              )}
              {ordinaryActivationAllowed ? (
                <Button
                  onClick={() => confirmAgentActivation(null)}
                  title={!canControlRoleAgent ? roleAgentControlDisabledReason : undefined}
                  type="button" variant="primary" disabled={!canControlRoleAgent}
                >Turn on agent</Button>
              ) : null}
            </div>
          )}
        >
          <div className="space-y-3 text-sm">
            <div className="mc-agent-settings-card-help">
              <strong>Candidate-action safeguards</strong>
              <ul style={{ margin: '8px 0 0', paddingLeft: 20 }}>
                <li>
                  Assessment invitations {resolvedRoleAutomation(role, 'auto_send_assessment') ? 'send automatically' : 'require your approval'}.
                </li>
                <li>
                  Assessment retries {resolvedRoleAutomation(role, 'auto_resend_assessment') ? 'send automatically' : 'require your approval'}.
                </li>
                <li>
                  Candidate advancement {resolvedRoleAutomation(role, 'auto_advance') ? 'runs automatically' : 'requires your approval'}.
                </li>
                <li>
                  Pre-screen failures {resolvedDeterministicReject(role) ? 'reject automatically' : 'require your approval'}. Full CV-score and assessment rejections always require approval.
                </li>
                <li>
                  Current assessment policy: {
                    role?.role_kind === 'sister'
                      ? 'this related role is score-only, so candidate assessments remain on the original role'
                      : String(role?.assessment_task_provisioning?.reconfiguration?.status || '').toLowerCase() === 'blocked'
                        ? 'Turn on confirms the preserved assessment and resumes durable validation'
                      : role?.auto_skip_assessment
                      ? 'explicitly skipped for this role'
                      : roleTasksFetchKnown && hasActiveAssessmentTask(roleTasks)
                        ? 'uses the active approved task already assigned to this role'
                        : roleTasksFetchKnown
                          ? 'no active task exists; choose Generate or Skip below'
                          : 'the current task assignment is unavailable; choose Generate or Skip rather than inferring it'
                  }.
                </li>
              </ul>
              {role?.role_kind === 'sister' ? null : (
                <p style={{ margin: '8px 0 0' }}>
                  Generate, validate, and approve a role-specific assessment, or explicitly skip the assessment stage. Turn on never guesses between those choices.
                </p>
              )}
            </div>
            <div className="mc-agent-settings-card-help" role="status">
              <div>
                <strong>
                  Monthly AI-usage cap: ${Math.round(Number(activationPreflight?.monthlyBudgetCents || 0) / 100)}
                </strong>
                <div>Pausing stops new AI processing and spend until you resume.</div>
              </div>
            </div>
          </div>
        </Dialog>

        <Dialog
          open={Boolean(activationReview)}
          onClose={() => setActivationReview(null)}
          title={activationReview?.terminalStatus === 'succeeded'
            ? 'Agent turned on'
            : activationReview?.terminalStatus === 'blocked'
              ? 'Turn-on needs input'
              : activationReview?.terminalStatus === 'cancelled'
                ? 'Turn-on cancelled'
                : 'Preparing the assessment and turning on'}
          description={activationReview?.terminalStatus === 'succeeded'
            ? 'The assessment policy is ready and the Agent is on.'
            : activationReview?.terminalStatus === 'blocked'
              ? 'The saved Turn-on request needs input before it can continue.'
              : activationReview?.terminalStatus === 'cancelled'
                ? 'The saved Turn-on request was cancelled and the Agent remains off.'
                : activationReview?.activationSubmitting
            ? 'Saving Turn-on… The agent remains off until the backend confirms this request.'
            : activationReview?.activationRequested
              ? 'Your Turn-on request is saved. The agent will validate the generated task and turn on automatically, even after you close this dialog.'
              : activationReview?.activationError
                ? 'The Turn-on request was not saved. The agent remains off; retry when ready.'
                : 'The Turn-on request has not been saved.'}
          footer={(
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setActivationReview(null)}>Close</Button>
              {!['succeeded', 'cancelled'].includes(activationReview?.terminalStatus) ? (
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => activateAgentWithAssessmentChoice(activationReview?.monthlyBudgetCents, 'skip_assessment')}
                  disabled={!canControlRoleAgent || Boolean(activationReview?.activationSubmitting)}
                  title={!canControlRoleAgent ? roleAgentControlDisabledReason : undefined}
                >Skip assessment &amp; turn on</Button>
              ) : null}
              {activationReview?.activationError && activationReview?.terminalStatus !== 'cancelled' ? (
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
            {activationReview?.terminalStatus === 'succeeded' ? (
              <div className="mc-agent-settings-card-help" role="status">
                <strong>Agent turned on</strong>
                <div>{activationReview.terminalMessage}</div>
              </div>
            ) : activationReview?.terminalStatus === 'cancelled' ? (
              <div className="mc-agent-warn" role="status">
                <div>
                  <div className="mc-agent-warn-title">Turn-on request cancelled</div>
                  <div className="mc-agent-warn-body">{activationReview.terminalMessage}</div>
                </div>
              </div>
            ) : activationReview?.activationError ? (
              <div className="mc-agent-warn" role="alert">
                <div>
                  <div className="mc-agent-warn-title">
                    {activationReview?.terminalStatus === 'blocked' ? 'Turn-on needs input' : 'Turn-on request failed'}
                  </div>
                  <div className="mc-agent-warn-body">{activationReview.activationError}</div>
                </div>
              </div>
            ) : null}
            {activationReview?.terminalStatus ? null : activationReview?.draft ? (
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
                  <p style={{ margin: 0, color: 'var(--taali-success-ink-strong)' }}>
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
