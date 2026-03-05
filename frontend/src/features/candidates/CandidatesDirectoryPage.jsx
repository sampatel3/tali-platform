import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  ArrowUpDown,
  CheckCircle2,
  CircleDot,
  RefreshCw,
  Search,
} from 'lucide-react';

import * as apiClient from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import {
  Badge,
  Button,
  EmptyState,
  Input,
  PageContainer,
  PageHeader,
  Panel,
  Select,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import { formatDateTime, getErrorMessage } from './candidatesUiUtils';
import { CandidateCvSidebar } from './CandidateCvSidebar';
import { CandidateScoreSummarySheet } from './CandidateScoreSummarySheet';
import { RetakeAssessmentDialog } from './RetakeAssessmentDialog';

const PAGE_SIZE = 50;
const STAGE_OPTIONS = [
  { value: 'all', label: 'All stages' },
  { value: 'applied', label: 'Applied' },
  { value: 'invited', label: 'Invited' },
  { value: 'in_assessment', label: 'In assessment' },
  { value: 'review', label: 'Review' },
];
const OUTCOME_OPTIONS = [
  { value: 'open', label: 'Open' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'withdrawn', label: 'Withdrawn' },
  { value: 'hired', label: 'Hired' },
];
const STAGE_COUNT_DEFAULTS = {
  all: 0,
  applied: 0,
  invited: 0,
  in_assessment: 0,
  review: 0,
};

const formatTitleCase = (value) => String(value || '')
  .replace(/_/g, ' ')
  .trim()
  .split(/\s+/)
  .filter(Boolean)
  .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
  .join(' ');

const resolveAssessmentId = (application) => (
  application?.score_summary?.assessment_id
  || application?.valid_assessment_id
  || null
);

const stageBadgeVariant = (stage) => {
  if (stage === 'review') return 'purple';
  if (stage === 'in_assessment') return 'warning';
  if (stage === 'invited') return 'info';
  return 'muted';
};

const outcomeBadgeVariant = (outcome) => {
  if (outcome === 'hired') return 'success';
  if (outcome === 'rejected' || outcome === 'withdrawn') return 'danger';
  return 'muted';
};

const buildIdempotencyKey = (eventType, applicationId, version) => (
  `v2-${eventType}-${applicationId}-${version || 'na'}-${Date.now()}`
);

const isVersionConflictError = (error) => {
  const status = Number(error?.response?.status || 0);
  if (status !== 409) return false;
  const detail = String(error?.response?.data?.detail || '').toLowerCase();
  return detail.includes('version mismatch');
};

export const CandidatesDirectoryPage = ({
  onNavigate,
  NavComponent = null,
  initialRoleId = null,
  lockRoleId = null,
  useRolePipelineEndpoint = false,
  navCurrentPage = 'candidates',
  title = 'Candidates',
  subtitle = 'Global candidate directory across all roles and stages.',
}) => {
  const rolesApi = apiClient.roles;
  const assessmentsApi = apiClient.assessments;
  const { showToast } = useToast();
  const lockedRoleValue = lockRoleId != null && String(lockRoleId).trim() ? String(lockRoleId).trim() : null;
  const defaultRoleFilter = lockedRoleValue
    || (initialRoleId != null && String(initialRoleId).trim() ? String(initialRoleId).trim() : 'all');
  const roleFilterLocked = Boolean(lockedRoleValue);
  const rolePipelineMode = Boolean(useRolePipelineEndpoint && roleFilterLocked && lockedRoleValue);

  const [roles, setRoles] = useState([]);
  const [loadingRoles, setLoadingRoles] = useState(true);
  const [roleFilter, setRoleFilter] = useState(defaultRoleFilter);
  const [stageFilter, setStageFilter] = useState('all');
  const [outcomeFilter, setOutcomeFilter] = useState('open');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);

  const [applicationsPayload, setApplicationsPayload] = useState({
    items: [],
    total: 0,
    limit: PAGE_SIZE,
    offset: 0,
  });
  const [loadingApplications, setLoadingApplications] = useState(true);
  const [applicationsError, setApplicationsError] = useState('');
  const [selectedApplicationId, setSelectedApplicationId] = useState(null);
  const [stageCounts, setStageCounts] = useState({ ...STAGE_COUNT_DEFAULTS });
  const [loadingStageCounts, setLoadingStageCounts] = useState(false);
  const [rolePipelineName, setRolePipelineName] = useState('');

  const [applicationDetailsById, setApplicationDetailsById] = useState({});
  const [loadingDetailId, setLoadingDetailId] = useState(null);
  const [eventsByApplicationId, setEventsByApplicationId] = useState({});
  const [loadingEventsId, setLoadingEventsId] = useState(null);
  const [roleTasksByRoleId, setRoleTasksByRoleId] = useState({});

  const [pendingStage, setPendingStage] = useState('');
  const [pendingOutcome, setPendingOutcome] = useState('');
  const [updatingStage, setUpdatingStage] = useState(false);
  const [updatingOutcome, setUpdatingOutcome] = useState(false);

  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [creatingAssessmentId, setCreatingAssessmentId] = useState(null);
  const [retakeDialogState, setRetakeDialogState] = useState({ applicationId: null, defaultTaskId: '' });

  const [cvSidebarApplicationId, setCvSidebarApplicationId] = useState(null);
  const [scoreSheetApplicationId, setScoreSheetApplicationId] = useState(null);
  const [assessmentDetailsById, setAssessmentDetailsById] = useState({});
  const [loadingAssessmentId, setLoadingAssessmentId] = useState(null);

  const applications = useMemo(() => (
    Array.isArray(applicationsPayload.items) ? applicationsPayload.items : []
  ), [applicationsPayload]);

  const selectedApplicationFromList = useMemo(() => (
    applications.find((application) => Number(application.id) === Number(selectedApplicationId)) || null
  ), [applications, selectedApplicationId]);

  const selectedApplication = useMemo(() => {
    if (!selectedApplicationFromList) return null;
    const detail = applicationDetailsById[String(selectedApplicationFromList.id)];
    return detail ? { ...selectedApplicationFromList, ...detail } : selectedApplicationFromList;
  }, [applicationDetailsById, selectedApplicationFromList]);

  const selectedEvents = useMemo(() => (
    eventsByApplicationId[String(selectedApplication?.id)] || []
  ), [eventsByApplicationId, selectedApplication?.id]);

  const selectedRoleTasks = useMemo(() => (
    roleTasksByRoleId[String(selectedApplication?.role_id)] || []
  ), [roleTasksByRoleId, selectedApplication?.role_id]);

  const selectedAssessmentId = useMemo(() => resolveAssessmentId(selectedApplication), [selectedApplication]);
  const selectedCompletedAssessment = useMemo(() => (
    selectedAssessmentId ? (assessmentDetailsById[String(selectedAssessmentId)] || null) : null
  ), [assessmentDetailsById, selectedAssessmentId]);

  const totalPages = Math.max(1, Math.ceil(Number(applicationsPayload.total || 0) / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages - 1);

  useEffect(() => {
    if (!roleFilterLocked) return;
    setRoleFilter(lockedRoleValue);
  }, [lockedRoleValue, roleFilterLocked]);

  useEffect(() => {
    if (!rolePipelineMode) return;
    if (outcomeFilter !== 'open') {
      setOutcomeFilter('open');
    }
  }, [outcomeFilter, rolePipelineMode]);

  const buildListQueryParams = useCallback(() => {
    const resolvedRoleFilter = roleFilterLocked ? lockedRoleValue : roleFilter;
    const params = {
      limit: PAGE_SIZE,
      offset: currentPage * PAGE_SIZE,
    };
    if (!rolePipelineMode) {
      params.application_outcome = outcomeFilter || 'open';
      if (resolvedRoleFilter !== 'all') params.role_id = Number(resolvedRoleFilter);
      if (stageFilter !== 'all') params.pipeline_stage = stageFilter;
    } else if (stageFilter !== 'all') {
      params.stage = stageFilter;
    }
    const trimmed = search.trim();
    if (trimmed) params.search = trimmed;
    return params;
  }, [
    currentPage,
    lockedRoleValue,
    outcomeFilter,
    roleFilter,
    roleFilterLocked,
    rolePipelineMode,
    search,
    stageFilter,
  ]);

  const buildStageCountQueryParams = useCallback((stage = 'all') => {
    if (rolePipelineMode) return null;
    const resolvedRoleFilter = roleFilterLocked ? lockedRoleValue : roleFilter;
    const params = {
      limit: 1,
      offset: 0,
      application_outcome: outcomeFilter || 'open',
    };
    if (resolvedRoleFilter !== 'all') params.role_id = Number(resolvedRoleFilter);
    if (stage !== 'all') params.pipeline_stage = stage;
    const trimmed = search.trim();
    if (trimmed) params.search = trimmed;
    return params;
  }, [lockedRoleValue, outcomeFilter, roleFilter, roleFilterLocked, rolePipelineMode, search]);

  const upsertApplicationInCache = useCallback((updatedApplication) => {
    if (!updatedApplication || !updatedApplication.id) return;
    setApplicationsPayload((prev) => ({
      ...prev,
      items: (Array.isArray(prev.items) ? prev.items : []).map((item) => (
        Number(item.id) === Number(updatedApplication.id)
          ? { ...item, ...updatedApplication }
          : item
      )),
    }));
    setApplicationDetailsById((prev) => ({
      ...prev,
      [String(updatedApplication.id)]: {
        ...(prev[String(updatedApplication.id)] || {}),
        ...updatedApplication,
      },
    }));
  }, []);

  const ensureRoleTasks = useCallback(async (roleId) => {
    const key = String(roleId || '');
    if (!key) return [];
    if (Object.prototype.hasOwnProperty.call(roleTasksByRoleId, key)) {
      return roleTasksByRoleId[key] || [];
    }
    try {
      const res = await rolesApi.listTasks(Number(roleId));
      const tasks = Array.isArray(res?.data) ? res.data : [];
      setRoleTasksByRoleId((prev) => ({ ...prev, [key]: tasks }));
      return tasks;
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to load role tasks.'), 'error');
      setRoleTasksByRoleId((prev) => ({ ...prev, [key]: [] }));
      return [];
    }
  }, [roleTasksByRoleId, rolesApi, showToast]);

  const loadRoles = useCallback(async () => {
    setLoadingRoles(true);
    try {
      const res = await rolesApi.list();
      setRoles(Array.isArray(res?.data) ? res.data : []);
    } catch {
      setRoles([]);
    } finally {
      setLoadingRoles(false);
    }
  }, [rolesApi]);

  const loadApplications = useCallback(async ({ preferredApplicationId = null } = {}) => {
    setLoadingApplications(true);
    setApplicationsError('');
    try {
      const queryParams = buildListQueryParams();
      const res = rolePipelineMode
        ? await rolesApi.listPipeline(Number(lockedRoleValue), queryParams)
        : await rolesApi.listApplicationsGlobal(queryParams);
      const payload = res?.data || {};
      const items = Array.isArray(payload.items) ? payload.items : [];
      setApplicationsPayload({
        items,
        total: Number(payload.total || 0),
        limit: Number(payload.limit || PAGE_SIZE),
        offset: Number(payload.offset || 0),
      });
      if (rolePipelineMode) {
        setRolePipelineName(String(payload.role_name || '').trim());
        const rawCounts = payload.stage_counts && typeof payload.stage_counts === 'object'
          ? payload.stage_counts
          : {};
        const nextCounts = {
          ...STAGE_COUNT_DEFAULTS,
          applied: Number(rawCounts.applied || 0),
          invited: Number(rawCounts.invited || 0),
          in_assessment: Number(rawCounts.in_assessment || 0),
          review: Number(rawCounts.review || 0),
        };
        nextCounts.all = Number(
          payload.active_candidates_count
          || nextCounts.applied + nextCounts.invited + nextCounts.in_assessment + nextCounts.review
        );
        setStageCounts(nextCounts);
      } else if (rolePipelineName) {
        setRolePipelineName('');
      }
      setSelectedApplicationId((current) => {
        const target = preferredApplicationId != null ? Number(preferredApplicationId) : Number(current);
        if (target && items.some((item) => Number(item.id) === target)) return target;
        return items.length > 0 ? Number(items[0].id) : null;
      });
    } catch (error) {
      setApplicationsPayload({ items: [], total: 0, limit: PAGE_SIZE, offset: 0 });
      setApplicationsError(getErrorMessage(error, 'Failed to load candidates.'));
      setSelectedApplicationId(null);
      if (rolePipelineMode) {
        setRolePipelineName('');
      }
    } finally {
      setLoadingApplications(false);
    }
  }, [buildListQueryParams, lockedRoleValue, rolePipelineMode, rolePipelineName, rolesApi]);

  const loadStageCounts = useCallback(async () => {
    if (rolePipelineMode) {
      setLoadingStageCounts(false);
      return;
    }
    setLoadingStageCounts(true);
    try {
      const stages = ['all', 'applied', 'invited', 'in_assessment', 'review'];
      const responses = await Promise.all(
        stages.map((stage) => {
          const params = buildStageCountQueryParams(stage);
          return rolesApi.listApplicationsGlobal(params || {});
        })
      );
      const nextCounts = { ...STAGE_COUNT_DEFAULTS };
      stages.forEach((stage, index) => {
        nextCounts[stage] = Number(responses[index]?.data?.total || 0);
      });
      setStageCounts(nextCounts);
    } catch {
      setStageCounts({ ...STAGE_COUNT_DEFAULTS });
    } finally {
      setLoadingStageCounts(false);
    }
  }, [buildStageCountQueryParams, rolePipelineMode, rolesApi]);

  const loadApplicationDetail = useCallback(async (applicationId, { includeCvText = false, force = false } = {}) => {
    if (!applicationId) return null;
    const key = String(applicationId);
    const cached = applicationDetailsById[key];
    if (!force && cached && (!includeCvText || cached.cv_text)) return cached;

    setLoadingDetailId(Number(applicationId));
    try {
      const res = await rolesApi.getApplication(Number(applicationId), {
        params: { include_cv_text: includeCvText },
      });
      const detail = res?.data || null;
      if (detail) {
        setApplicationDetailsById((prev) => ({ ...prev, [key]: detail }));
        upsertApplicationInCache(detail);
      }
      return detail;
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to load candidate details.'), 'error');
      return null;
    } finally {
      setLoadingDetailId((current) => (
        Number(current) === Number(applicationId) ? null : current
      ));
    }
  }, [applicationDetailsById, rolesApi, showToast, upsertApplicationInCache]);

  const loadApplicationEvents = useCallback(async (applicationId) => {
    if (!applicationId) return;
    setLoadingEventsId(Number(applicationId));
    try {
      const res = await rolesApi.listApplicationEvents(Number(applicationId), { limit: 8, offset: 0 });
      setEventsByApplicationId((prev) => ({
        ...prev,
        [String(applicationId)]: Array.isArray(res?.data) ? res.data : [],
      }));
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to load activity timeline.'), 'error');
      setEventsByApplicationId((prev) => ({ ...prev, [String(applicationId)]: [] }));
    } finally {
      setLoadingEventsId((current) => (
        Number(current) === Number(applicationId) ? null : current
      ));
    }
  }, [rolesApi, showToast]);

  const loadCompletedAssessment = useCallback(async (assessmentId, { force = false } = {}) => {
    if (!assessmentId) return null;
    const key = String(assessmentId);
    if (!force && assessmentDetailsById[key]) return assessmentDetailsById[key];
    setLoadingAssessmentId(Number(assessmentId));
    try {
      const res = await assessmentsApi.get(Number(assessmentId));
      const detail = res?.data || null;
      if (detail) {
        setAssessmentDetailsById((prev) => ({ ...prev, [key]: detail }));
      }
      return detail;
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to load completed assessment.'), 'error');
      return null;
    } finally {
      setLoadingAssessmentId((current) => (
        Number(current) === Number(assessmentId) ? null : current
      ));
    }
  }, [assessmentDetailsById, assessmentsApi, showToast]);

  const refreshAll = useCallback(async () => {
    await Promise.all([
      loadRoles(),
      loadStageCounts(),
      loadApplications({ preferredApplicationId: selectedApplicationId }),
    ]);
  }, [loadApplications, loadRoles, loadStageCounts, selectedApplicationId]);

  useEffect(() => {
    loadRoles();
  }, [loadRoles]);

  useEffect(() => {
    loadApplications();
  }, [loadApplications]);

  useEffect(() => {
    loadStageCounts();
  }, [loadStageCounts]);

  useEffect(() => {
    if (!selectedApplicationId) return;
    let cancelled = false;
    const loadContext = async () => {
      const detail = await loadApplicationDetail(selectedApplicationId, { includeCvText: false });
      if (cancelled) return;
      await loadApplicationEvents(selectedApplicationId);
      if (cancelled) return;
      const roleId = detail?.role_id || selectedApplicationFromList?.role_id;
      if (roleId) {
        await ensureRoleTasks(roleId);
      }
    };
    loadContext();
    return () => {
      cancelled = true;
    };
  }, [
    ensureRoleTasks,
    loadApplicationDetail,
    loadApplicationEvents,
    selectedApplicationFromList?.role_id,
    selectedApplicationId,
  ]);

  useEffect(() => {
    setPage(0);
  }, [outcomeFilter, roleFilter, search, stageFilter]);

  useEffect(() => {
    if (!selectedApplication) {
      setPendingStage('');
      setPendingOutcome('');
      setSelectedTaskId('');
      return;
    }
    setPendingStage(selectedApplication.pipeline_stage || 'applied');
    setPendingOutcome(selectedApplication.application_outcome || 'open');

    const tasks = roleTasksByRoleId[String(selectedApplication.role_id)] || [];
    if (tasks.length === 1) {
      setSelectedTaskId(String(tasks[0].id));
      return;
    }
    if (tasks.some((task) => String(task.id) === String(selectedTaskId))) {
      return;
    }
    setSelectedTaskId('');
  }, [roleTasksByRoleId, selectedApplication, selectedTaskId]);

  const refreshAfterConflict = useCallback(async (applicationId) => {
    const targetId = Number(applicationId || selectedApplicationId || 0);
    await Promise.all([
      loadApplications({ preferredApplicationId: targetId || null }),
      loadStageCounts(),
    ]);
    if (targetId) {
      await Promise.all([
        loadApplicationDetail(targetId, { force: true }),
        loadApplicationEvents(targetId),
      ]);
    }
  }, [
    loadApplicationDetail,
    loadApplicationEvents,
    loadApplications,
    loadStageCounts,
    selectedApplicationId,
  ]);

  const applyStageUpdate = async () => {
    if (!selectedApplication || !pendingStage) return;
    if (selectedApplication.application_outcome !== 'open') {
      showToast('Re-open candidate outcome before moving stage.', 'error');
      return;
    }
    if (pendingStage === selectedApplication.pipeline_stage) return;
    setUpdatingStage(true);
    try {
      const res = await rolesApi.updateApplicationStage(selectedApplication.id, {
        pipeline_stage: pendingStage,
        expected_version: selectedApplication.version,
        reason: 'Updated from candidates directory',
        idempotency_key: buildIdempotencyKey('stage', selectedApplication.id, selectedApplication.version),
      });
      const updated = res?.data || null;
      if (updated) {
        upsertApplicationInCache(updated);
        setPendingStage(updated.pipeline_stage);
        setPendingOutcome(updated.application_outcome);
      }
      showToast('Pipeline stage updated.', 'success');
      await Promise.all([
        loadApplications({ preferredApplicationId: selectedApplication.id }),
        loadStageCounts(),
      ]);
      await loadApplicationEvents(selectedApplication.id);
    } catch (error) {
      if (isVersionConflictError(error)) {
        showToast('Candidate changed in another session. Refreshed latest data.', 'error');
        await refreshAfterConflict(selectedApplication.id);
        return;
      }
      showToast(getErrorMessage(error, 'Failed to update pipeline stage.'), 'error');
    } finally {
      setUpdatingStage(false);
    }
  };

  const applyOutcomeUpdate = async () => {
    if (!selectedApplication || !pendingOutcome) return;
    if (pendingOutcome === selectedApplication.application_outcome) return;
    setUpdatingOutcome(true);
    try {
      const res = await rolesApi.updateApplicationOutcome(selectedApplication.id, {
        application_outcome: pendingOutcome,
        expected_version: selectedApplication.version,
        reason: 'Updated from candidates directory',
        idempotency_key: buildIdempotencyKey('outcome', selectedApplication.id, selectedApplication.version),
      });
      const updated = res?.data || null;
      if (updated) {
        upsertApplicationInCache(updated);
        setPendingStage(updated.pipeline_stage);
        setPendingOutcome(updated.application_outcome);
      }
      showToast('Candidate outcome updated.', 'success');
      await Promise.all([
        loadApplications({ preferredApplicationId: selectedApplication.id }),
        loadStageCounts(),
      ]);
      await loadApplicationEvents(selectedApplication.id);
    } catch (error) {
      if (isVersionConflictError(error)) {
        showToast('Candidate changed in another session. Refreshed latest data.', 'error');
        await refreshAfterConflict(selectedApplication.id);
        return;
      }
      showToast(getErrorMessage(error, 'Failed to update candidate outcome.'), 'error');
    } finally {
      setUpdatingOutcome(false);
    }
  };

  const createOrRetakeAssessment = async (application, taskId, { retake = false, reason = '' } = {}) => {
    if (!application?.id || !taskId) return false;
    setCreatingAssessmentId(application.id);
    try {
      if (retake) {
        await rolesApi.retakeAssessment(application.id, {
          task_id: Number(taskId),
          duration_minutes: 30,
          void_reason: reason || 'Retake requested from candidates directory',
        });
      } else {
        await rolesApi.createAssessment(application.id, {
          task_id: Number(taskId),
          duration_minutes: 30,
        });
      }
      showToast(retake ? 'Retake assessment created.' : 'Assessment invite sent.', 'success');
      await Promise.all([
        loadApplications({ preferredApplicationId: application.id }),
        loadStageCounts(),
      ]);
      await Promise.all([
        loadApplicationDetail(application.id, { force: true }),
        loadApplicationEvents(application.id),
      ]);
      return true;
    } catch (error) {
      showToast(getErrorMessage(error, retake ? 'Failed to create retake.' : 'Failed to send assessment.'), 'error');
      return false;
    } finally {
      setCreatingAssessmentId(null);
    }
  };

  const openCvSidebar = async (application) => {
    if (!application?.id) return;
    await loadApplicationDetail(application.id, { includeCvText: true });
    setCvSidebarApplicationId(application.id);
  };

  const openScoreSheet = async (application) => {
    if (!application?.id) return;
    const detail = await loadApplicationDetail(application.id, { includeCvText: false });
    const assessmentId = resolveAssessmentId(detail || application);
    if (assessmentId) {
      await loadCompletedAssessment(assessmentId);
    }
    setScoreSheetApplicationId(application.id);
  };

  const viewFullPage = (application, assessmentId) => {
    if (!application) return;
    if (assessmentId) {
      onNavigate('candidate-detail', { candidateDetailAssessmentId: assessmentId });
      return;
    }
    onNavigate('candidate-report', { candidateApplicationId: application.id });
  };

  const retakeDialogApplication = useMemo(() => (
    applications.find((item) => Number(item.id) === Number(retakeDialogState.applicationId))
    || applicationDetailsById[String(retakeDialogState.applicationId)]
    || null
  ), [applicationDetailsById, applications, retakeDialogState.applicationId]);
  const headerTitle = rolePipelineMode && rolePipelineName
    ? `${rolePipelineName} pipeline`
    : title;
  const headerSubtitle = rolePipelineMode
    ? 'Active candidates for this role across applied, invited, in assessment, and review.'
    : subtitle;

  return (
    <div>
      {NavComponent ? <NavComponent currentPage={navCurrentPage} onNavigate={onNavigate} /> : null}
      <PageContainer density="compact" width="wide">
        <PageHeader
          title={headerTitle}
          subtitle={headerSubtitle}
          actions={(
            <Button
              type="button"
              variant="secondary"
              onClick={refreshAll}
              disabled={loadingApplications || loadingRoles}
            >
              <RefreshCw size={14} className={loadingApplications ? 'animate-spin' : ''} />
              Refresh
            </Button>
          )}
        />

        <Panel className="mb-4 grid gap-3 p-3 md:grid-cols-4">
          <label className="block">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Search</span>
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--taali-muted)]" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="pl-9"
                placeholder="Name, email, position"
              />
            </div>
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Role</span>
            <Select
              value={roleFilter}
              onChange={(event) => setRoleFilter(event.target.value)}
              disabled={loadingRoles || roleFilterLocked}
            >
              <option value="all">All roles</option>
              {roles.map((role) => (
                <option key={role.id} value={role.id}>{role.name}</option>
              ))}
            </Select>
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Pipeline stage</span>
            <Select value={stageFilter} onChange={(event) => setStageFilter(event.target.value)}>
              {STAGE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </Select>
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Outcome</span>
            <Select
              value={outcomeFilter}
              onChange={(event) => setOutcomeFilter(event.target.value)}
              disabled={rolePipelineMode}
            >
              {rolePipelineMode ? (
                <option value="open">Open</option>
              ) : (
                <>
                  <option value="all">All outcomes</option>
                  {OUTCOME_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </>
              )}
            </Select>
          </label>
        </Panel>

        <Panel className="mb-4 p-3">
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Stage counts</p>
          <div className="flex flex-wrap gap-2">
            {STAGE_OPTIONS.map((option) => {
              const isActive = stageFilter === option.value;
              const count = Number(stageCounts[option.value] || 0);
              return (
                <Button
                  key={option.value}
                  type="button"
                  size="xs"
                  variant={isActive ? 'secondary' : 'ghost'}
                  onClick={() => setStageFilter(option.value)}
                >
                  {option.label}
                  <Badge variant={isActive ? 'purple' : 'muted'}>
                    {loadingStageCounts ? '...' : count}
                  </Badge>
                </Button>
              );
            })}
          </div>
        </Panel>

        {loadingApplications ? (
          <div className="flex min-h-[260px] items-center justify-center">
            <Spinner size={22} />
          </div>
        ) : applicationsError ? (
          <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {applicationsError}
          </Panel>
        ) : applications.length === 0 ? (
          <EmptyState
            title="No candidates found"
            description="Try changing filters or add candidates from role pipelines."
          />
        ) : (
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
            <Panel className="p-0">
              <div className="border-b border-[var(--taali-border-soft)] px-4 py-3 text-xs text-[var(--taali-muted)]">
                {applicationsPayload.total} candidates
              </div>
              <div className="max-h-[72vh] divide-y divide-[var(--taali-border-soft)] overflow-y-auto">
                {applications.map((application) => {
                  const selected = Number(application.id) === Number(selectedApplicationId);
                  return (
                    <button
                      key={application.id}
                      type="button"
                      className={[
                        'w-full px-4 py-3 text-left transition-colors',
                        selected
                          ? 'bg-[var(--taali-surface-subtle)]'
                          : 'hover:bg-[var(--taali-surface-subtle)]',
                      ].join(' ')}
                      onClick={() => setSelectedApplicationId(Number(application.id))}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-[var(--taali-text)]">
                            {application.candidate_name || application.candidate_email}
                          </p>
                          <p className="truncate text-xs text-[var(--taali-muted)]">
                            {application.candidate_email}
                          </p>
                          <p className="truncate text-xs text-[var(--taali-muted)]">
                            {application.role_name || application.candidate_position || 'Role'}
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="text-sm font-semibold text-[var(--taali-text)]">
                            {application.taali_score != null ? Number(application.taali_score).toFixed(1) : '—'}
                          </p>
                          <p className="text-[11px] text-[var(--taali-muted)]">TAALI</p>
                        </div>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <Badge variant={stageBadgeVariant(application.pipeline_stage)}>
                          {formatTitleCase(application.pipeline_stage)}
                        </Badge>
                        <Badge variant={outcomeBadgeVariant(application.application_outcome)}>
                          {formatTitleCase(application.application_outcome)}
                        </Badge>
                        {application.pipeline_external_drift ? (
                          <Badge variant="warning">External drift</Badge>
                        ) : null}
                        <span className="text-[11px] text-[var(--taali-muted)]">
                          Updated {formatDateTime(application.pipeline_stage_updated_at || application.updated_at || application.created_at)}
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
              {applicationsPayload.total > PAGE_SIZE ? (
                <div className="flex items-center justify-between border-t border-[var(--taali-border-soft)] px-4 py-3 text-xs text-[var(--taali-muted)]">
                  <span>Page {currentPage + 1} of {totalPages}</span>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      disabled={currentPage <= 0}
                      onClick={() => setPage((prev) => Math.max(0, prev - 1))}
                    >
                      Previous
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      disabled={currentPage >= totalPages - 1}
                      onClick={() => setPage((prev) => Math.min(totalPages - 1, prev + 1))}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              ) : null}
            </Panel>

            <Panel className="h-fit p-4 lg:sticky lg:top-[5.8rem]">
              {!selectedApplication ? (
                <div className="min-h-[260px] text-sm text-[var(--taali-muted)]">
                  Select a candidate to open the decision pane.
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="border-b border-[var(--taali-border-soft)] pb-3">
                    <p className="text-base font-semibold text-[var(--taali-text)]">
                      {selectedApplication.candidate_name || selectedApplication.candidate_email}
                    </p>
                    <p className="text-xs text-[var(--taali-muted)]">{selectedApplication.candidate_email}</p>
                    <p className="text-xs text-[var(--taali-muted)]">
                      {selectedApplication.role_name || selectedApplication.candidate_position || 'Role'}
                    </p>
                  </div>

                  <div className="grid gap-3 md:grid-cols-2">
                    <label className="block">
                      <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Stage</span>
                      <div className="flex items-center gap-2">
                        <Select
                          value={pendingStage}
                          onChange={(event) => setPendingStage(event.target.value)}
                          disabled={selectedApplication.application_outcome !== 'open'}
                        >
                          {STAGE_OPTIONS.filter((item) => item.value !== 'all').map((option) => (
                            <option key={option.value} value={option.value}>{option.label}</option>
                          ))}
                        </Select>
                        <Button
                          type="button"
                          size="xs"
                          variant="secondary"
                          onClick={applyStageUpdate}
                          disabled={updatingStage || pendingStage === selectedApplication.pipeline_stage}
                        >
                          <ArrowUpDown size={12} />
                          {updatingStage ? 'Updating...' : 'Move'}
                        </Button>
                      </div>
                    </label>

                    <label className="block">
                      <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Outcome</span>
                      <div className="flex items-center gap-2">
                        <Select value={pendingOutcome} onChange={(event) => setPendingOutcome(event.target.value)}>
                          {OUTCOME_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>{option.label}</option>
                          ))}
                        </Select>
                        <Button
                          type="button"
                          size="xs"
                          variant="secondary"
                          onClick={applyOutcomeUpdate}
                          disabled={updatingOutcome || pendingOutcome === selectedApplication.application_outcome}
                        >
                          <CheckCircle2 size={12} />
                          {updatingOutcome ? 'Saving...' : 'Apply'}
                        </Button>
                      </div>
                    </label>
                  </div>

                  <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] p-3">
                    <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Actions</p>
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Select
                          value={selectedTaskId}
                          onChange={(event) => setSelectedTaskId(event.target.value)}
                          className="min-w-[220px]"
                        >
                          <option value="">Select task...</option>
                          {selectedRoleTasks.map((task) => (
                            <option key={task.id} value={task.id}>{task.name}</option>
                          ))}
                        </Select>
                        <Button
                          type="button"
                          variant="primary"
                          size="sm"
                          disabled={!selectedTaskId || creatingAssessmentId === selectedApplication.id}
                          onClick={() => {
                            if (resolveAssessmentId(selectedApplication)) {
                              setRetakeDialogState({
                                applicationId: selectedApplication.id,
                                defaultTaskId: selectedTaskId,
                              });
                              return;
                            }
                            createOrRetakeAssessment(selectedApplication, selectedTaskId, { retake: false });
                          }}
                        >
                          {creatingAssessmentId === selectedApplication.id
                            ? 'Creating...'
                            : (resolveAssessmentId(selectedApplication) ? 'Retake assessment' : 'Send assessment')}
                        </Button>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => openScoreSheet(selectedApplication)}
                        >
                          Open summary
                        </Button>
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => openCvSidebar(selectedApplication)}
                        >
                          Open CV
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => viewFullPage(selectedApplication, selectedAssessmentId)}
                        >
                          Full page
                        </Button>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] p-3">
                    <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Timeline</p>
                    {loadingEventsId === selectedApplication.id ? (
                      <div className="flex items-center gap-2 text-xs text-[var(--taali-muted)]">
                        <Spinner size={14} />
                        Loading events...
                      </div>
                    ) : selectedEvents.length > 0 ? (
                      <div className="space-y-2">
                        {selectedEvents.map((event) => (
                          <div key={event.id} className="rounded-[var(--taali-radius-control)] border border-[var(--taali-border-soft)] p-2">
                            <div className="flex items-center gap-2 text-[11px] text-[var(--taali-muted)]">
                              <CircleDot size={11} />
                              {formatDateTime(event.created_at)}
                            </div>
                            <p className="mt-1 text-xs font-semibold text-[var(--taali-text)]">
                              {formatTitleCase(event.event_type)}
                            </p>
                            <p className="mt-0.5 text-xs text-[var(--taali-muted)]">
                              {event.reason || `${formatTitleCase(event.from_stage)} -> ${formatTitleCase(event.to_stage)}`}
                            </p>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 text-xs text-[var(--taali-muted)]">
                        <AlertCircle size={12} />
                        No activity yet.
                      </div>
                    )}
                  </div>

                  <div className="text-xs text-[var(--taali-muted)]">
                    {loadingDetailId === selectedApplication.id ? (
                      <span className="inline-flex items-center gap-2"><Spinner size={12} />Refreshing candidate details...</span>
                    ) : (
                      <span>Version {selectedApplication.version}</span>
                    )}
                  </div>
                </div>
              )}
            </Panel>
          </div>
        )}
      </PageContainer>

      <CandidateCvSidebar
        open={Boolean(cvSidebarApplicationId)}
        application={
          cvSidebarApplicationId
            ? (applicationDetailsById[String(cvSidebarApplicationId)] || applications.find((item) => Number(item.id) === Number(cvSidebarApplicationId)) || null)
            : null
        }
        onClose={() => setCvSidebarApplicationId(null)}
      />

      <CandidateScoreSummarySheet
        open={Boolean(scoreSheetApplicationId)}
        loading={loadingDetailId === Number(scoreSheetApplicationId)}
        application={
          scoreSheetApplicationId
            ? (applicationDetailsById[String(scoreSheetApplicationId)] || applications.find((item) => Number(item.id) === Number(scoreSheetApplicationId)) || null)
            : null
        }
        completedAssessment={selectedCompletedAssessment}
        completedAssessmentLoading={loadingAssessmentId === Number(selectedAssessmentId)}
        roleTasks={selectedRoleTasks}
        creatingAssessmentId={creatingAssessmentId}
        onClose={() => setScoreSheetApplicationId(null)}
        onLaunchAssessment={(application, taskId) => createOrRetakeAssessment(application, taskId, { retake: false })}
        onOpenRetakeDialog={(application, taskId) => {
          setRetakeDialogState({
            applicationId: application?.id || null,
            defaultTaskId: String(taskId || ''),
          });
        }}
        onOpenCvSidebar={openCvSidebar}
        onViewFullPage={viewFullPage}
      />

      <RetakeAssessmentDialog
        open={Boolean(retakeDialogState.applicationId)}
        application={retakeDialogApplication}
        roleTasks={selectedRoleTasks}
        loading={creatingAssessmentId === retakeDialogState.applicationId}
        defaultTaskId={retakeDialogState.defaultTaskId}
        onClose={() => setRetakeDialogState({ applicationId: null, defaultTaskId: '' })}
        onConfirm={async ({ taskId, reason }) => {
          const application = retakeDialogApplication;
          if (!application) return;
          const ok = await createOrRetakeAssessment(application, taskId, { retake: true, reason });
          if (ok) {
            setRetakeDialogState({ applicationId: null, defaultTaskId: '' });
          }
        }}
      />
    </div>
  );
};

export default CandidatesDirectoryPage;
