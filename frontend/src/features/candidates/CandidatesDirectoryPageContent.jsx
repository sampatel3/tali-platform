import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  AlertCircle,
  ArrowRight,
  RefreshCw,
  Search,
} from 'lucide-react';

import * as apiClient from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import {
  CANDIDATES_DIRECTORY_SHOWCASE,
  CANDIDATES_DIRECTORY_STAGE_COUNTS,
  JOBS_SHOWCASE,
} from '../demo/productWalkthroughModels';
import {
  Button,
  EmptyState,
  Panel,
  Select,
  Sheet,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import { getErrorMessage } from './candidatesUiUtils';
import { BackgroundJobsToaster } from './BackgroundJobsToaster';
import { CandidateSheet } from './CandidateSheet';
import { RetakeAssessmentDialog } from './RetakeAssessmentDialog';
import {
  CandidateTriageDrawer,
  candidateReportHref,
} from './CandidateTriageDrawer';
import {
  CandidateAvatar,
  WorkableTagSm,
  formatRelativeDateTime,
} from '../../shared/ui/RecruiterDesignPrimitives';

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
const SORT_OPTIONS = [
  { value: 'cv_match_scored_at:desc', label: 'Recently scored (newest first)' },
  { value: 'cv_match_score:desc', label: 'CV match score (high to low)' },
  { value: 'cv_match_score:asc', label: 'CV match score (low to high)' },
  { value: 'pre_screen_score:desc', label: 'Pre-screen score (high to low)' },
  { value: 'pre_screen_score:asc', label: 'Pre-screen score (low to high)' },
  { value: 'taali_score:desc', label: 'TAALI score (high to low)' },
  { value: 'taali_score:asc', label: 'TAALI score (low to high)' },
  { value: 'created_at:desc', label: 'Submitted (newest first)' },
  { value: 'created_at:asc', label: 'Submitted (oldest first)' },
  { value: 'pipeline_stage_updated_at:desc', label: 'Recent activity' },
];
const STAGE_COUNT_DEFAULTS = {
  all: 0,
  applied: 0,
  invited: 0,
  in_assessment: 0,
  review: 0,
};
const STAGE_FILTER_OPTIONS = STAGE_OPTIONS.filter((option) => option.value !== 'all');

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

const resolvePreScreenScore = (application) => application?.pre_screen_score ?? null;

const resolveTaaliScore = (application) => (
  application?.taali_score
  ?? application?.score_summary?.taali_score
  ?? null
);

const resolveUnifiedScore = (application) => {
  const taali = resolveTaaliScore(application);
  const preScreen = resolvePreScreenScore(application);
  const mode = application?.score_mode || application?.score_summary?.score_mode || null;
  const isComposite = Boolean(mode) && mode !== 'role_fit_only';
  if (isComposite && taali != null) return { value: taali, kind: 'composite' };
  if (preScreen != null) return { value: preScreen, kind: 'cv' };
  if (taali != null) return { value: taali, kind: 'cv' };
  return { value: null, kind: null };
};

const resolveOptionalPercent = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.max(0, Math.min(100, Math.round(numeric)));
};

const isVersionConflictError = (error) => {
  const status = Number(error?.response?.status || 0);
  if (status !== 409) return false;
  const detail = String(error?.response?.data?.detail || '').toLowerCase();
  return detail.includes('version mismatch');
};

const eventReasonToLabel = (reason) => {
  const normalized = String(reason || '').trim().toLowerCase();
  if (!normalized) return '';
  if (normalized.includes('workflow v2')) return '';
  if (normalized.includes('imported from workable')) return 'Imported from Workable';
  if (normalized.includes('assessment invite created')) return 'Task sent';
  if (normalized.includes('assessment retake created')) return 'Task retake sent';
  if (normalized.includes('candidate started assessment')) return 'Candidate started task';
  if (normalized.includes('assessment completed')) return 'Candidate completed task';
  if (normalized.includes('auto-completed on timeout')) return 'Task auto-completed on timeout';
  return reason;
};

const formatTimelineEvent = (event) => {
  const eventType = String(event?.event_type || '').trim().toLowerCase();
  const safeFromStage = formatTitleCase(event?.from_stage);
  const safeToStage = formatTitleCase(event?.to_stage);
  const safeFromOutcome = formatTitleCase(event?.from_outcome);
  const safeToOutcome = formatTitleCase(event?.to_outcome);
  const reasonLabel = eventReasonToLabel(event?.reason);

  if (eventType === 'pipeline_initialized') {
    if (reasonLabel === 'Imported from Workable') {
      return {
        title: 'Imported from Workable',
        detail: safeToStage && safeToOutcome ? `${safeToStage} · ${safeToOutcome}` : 'Application added to pipeline',
      };
    }
    return {
      title: 'Pipeline initialized',
      detail: safeToStage && safeToOutcome ? `${safeToStage} · ${safeToOutcome}` : 'Application created',
    };
  }

  if (eventType === 'pipeline_stage_changed') {
    return {
      title: safeToStage ? `Stage moved to ${safeToStage}` : 'Pipeline stage updated',
      detail: reasonLabel || `${safeFromStage || 'Unknown'} -> ${safeToStage || 'Unknown'}`,
    };
  }

  if (eventType === 'application_outcome_changed') {
    return {
      title: safeToOutcome ? `Outcome changed to ${safeToOutcome}` : 'Outcome updated',
      detail: reasonLabel || `${safeFromOutcome || 'Unknown'} -> ${safeToOutcome || 'Unknown'}`,
    };
  }

  if (eventType === 'assessment_invite_sent') {
    return { title: 'Task sent', detail: reasonLabel || 'Assessment invite sent to candidate' };
  }
  if (eventType === 'assessment_retake_sent') {
    return { title: 'Task retake sent', detail: reasonLabel || 'Assessment retake sent to candidate' };
  }
  if (eventType === 'assessment_invite_resent') {
    return { title: 'Task invite resent', detail: reasonLabel || 'Assessment invite resent to candidate' };
  }
  if (eventType === 'workable_candidate_imported') {
    return { title: 'Imported from Workable', detail: reasonLabel || 'Candidate imported from Workable' };
  }
  if (eventType === 'workable_disqualified') {
    return { title: 'Rejected in Workable', detail: reasonLabel || 'Candidate disqualified in Workable' };
  }
  if (eventType === 'workable_reverted') {
    return { title: 'Reopened in Workable', detail: reasonLabel || 'Candidate disqualification reverted in Workable' };
  }
  if (eventType === 'workable_writeback_failed') {
    return { title: 'Workable sync failed', detail: reasonLabel || 'TAALI could not update Workable for this action' };
  }
  if (eventType === 'auto_rejected') {
    return { title: 'Auto-rejected', detail: reasonLabel || 'Candidate auto-rejected from TAALI pre-screening' };
  }
  if (eventType === 'auto_reject_failed') {
    return { title: 'Auto-reject failed', detail: reasonLabel || 'TAALI could not complete the Workable auto-reject write-back' };
  }

  return {
    title: formatTitleCase(event?.event_type),
    detail: reasonLabel || `${safeFromStage || 'Unknown'} -> ${safeToStage || 'Unknown'}`,
  };
};

const candidateApplicationKey = (application) => {
  const candidateId = Number(application?.candidate_id || 0);
  if (candidateId > 0) return `candidate-id:${candidateId}`;
  const email = String(application?.candidate_email || '').trim().toLowerCase();
  if (email) return `candidate-email:${email}`;
  return `application:${application?.id || 'unknown'}`;
};

const applicationDisplayName = (application) => (
  application?.candidate_name
  || application?.candidate_email
  || `Application ${application?.id || ''}`.trim()
);

const isWorkableLinkedApplication = (application) => (
  Boolean(String(application?.workable_candidate_id || '').trim())
);

const workableOutcomeSyncAction = (application, targetOutcome) => {
  if (!isWorkableLinkedApplication(application)) return null;
  const currentOutcome = String(application?.application_outcome || '').trim().toLowerCase();
  const nextOutcome = String(targetOutcome || '').trim().toLowerCase();
  if (currentOutcome === 'open' && nextOutcome === 'rejected') return 'reject';
  if (currentOutcome === 'rejected' && nextOutcome === 'open') return 'reopen';
  return null;
};

const buildBulkRejectConfirmationMessage = (applications) => {
  const selectedCount = Array.isArray(applications) ? applications.length : 0;
  const workableLinkedCount = (applications || []).filter((application) => workableOutcomeSyncAction(application, 'rejected') === 'reject').length;
  if (selectedCount <= 0) return '';
  if (workableLinkedCount <= 0) {
    return `Reject ${selectedCount} selected candidate${selectedCount === 1 ? '' : 's'}?`;
  }
  return [
    `Reject ${selectedCount} selected candidate${selectedCount === 1 ? '' : 's'}?`,
    '',
    `${workableLinkedCount} linked candidate${workableLinkedCount === 1 ? '' : 's'} will also be disqualified in Workable.`,
    "Workable's disqualification automation/template handles any rejection email.",
  ].join('\n');
};

const uniqueStringValues = (values) => {
  const seen = new Set();
  const ordered = [];
  (values || []).forEach((value) => {
    const normalized = String(value || '').trim();
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    ordered.push(normalized);
  });
  return ordered;
};

const getCvMatchTone = (score, thresholdValue) => {
  const numeric = Number(score);
  if (!Number.isFinite(numeric)) return 'un';
  if (Number.isFinite(Number(thresholdValue)) && numeric < Number(thresholdValue)) return 'lo';
  if (numeric >= 80) return 'hi';
  if (numeric >= 60) return 'md';
  return 'lo';
};

const getHireSignal = (application) => {
  const score = Number(resolveUnifiedScore(application).value);
  if (!Number.isFinite(score)) return { label: 'Pending', tone: '' };
  if (score >= 80) return { label: 'Strong hire', tone: 'green' };
  if (score >= 65) return { label: 'Maybe', tone: 'amber' };
  return { label: 'No hire', tone: 'red' };
};

const getStatusChip = (application) => {
  const stage = String(application?.pipeline_stage || '').toLowerCase();
  if (stage === 'in_assessment') return { label: 'live', tone: 'live' };
  if (stage === 'invited') return { label: 'invited', tone: 'invited' };
  if (stage === 'review') return { label: 'review', tone: 'review' };
  return { label: 'submitted', tone: 'submitted' };
};

const toCsvValue = (value) => `"${String(value ?? '').replaceAll('"', '""')}"`;

export const CandidatesDirectoryPage = ({
  onNavigate: rawOnNavigate,
  NavComponent = null,
  initialRoleId = null,
  lockRoleId = null,
  useRolePipelineEndpoint = false,
  navCurrentPage = 'candidates',
  title = 'Candidates',
  subtitle = 'Every person across every role, scored and filterable. Click a row to triage; Cmd/Ctrl-click opens the full report.',
  prelude = null,
  externalRefreshKey = 0,
  embedded = false,
}) => {
  const rolesApi = apiClient.roles;
  const { showToast } = useToast();
  const [searchParams] = useSearchParams();
  const isShowcase = searchParams.get('demo') === '1' && searchParams.get('showcase') === '1';
  const onNavigate = isShowcase ? () => {} : rawOnNavigate;
  const lockedRoleValue = lockRoleId != null && String(lockRoleId).trim() ? String(lockRoleId).trim() : null;
  const defaultRoleFilter = lockedRoleValue
    || (initialRoleId != null && String(initialRoleId).trim() ? String(initialRoleId).trim() : 'all');
  const roleFilterLocked = Boolean(lockedRoleValue);
  const rolePipelineMode = Boolean(useRolePipelineEndpoint && roleFilterLocked && lockedRoleValue);

  const [roles, setRoles] = useState([]);
  const [loadingRoles, setLoadingRoles] = useState(true);
  const [roleFilters, setRoleFilters] = useState(() => (
    defaultRoleFilter === 'all' ? [] : [String(defaultRoleFilter)]
  ));
  const [stageFilters, setStageFilters] = useState([]);
  const [outcomeFilters, setOutcomeFilters] = useState(['open']);
  const [sortOption, setSortOption] = useState(SORT_OPTIONS[0].value);
  const [minPreScreenScore, setMinPreScreenScore] = useState('');
  const [search, setSearch] = useState('');
  const [workableOnly, setWorkableOnly] = useState(false);
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

  const [updatingStageId, setUpdatingStageId] = useState(null);
  const [updatingOutcomeId, setUpdatingOutcomeId] = useState(null);
  const [selectedApplicationIds, setSelectedApplicationIds] = useState([]);
  const [bulkRejecting, setBulkRejecting] = useState(false);
  const [bulkRejectProgress, setBulkRejectProgress] = useState(null);
  const [bulkRejectSummary, setBulkRejectSummary] = useState(null);

  const [creatingAssessmentId, setCreatingAssessmentId] = useState(null);
  const [retakeDialogState, setRetakeDialogState] = useState({ applicationId: null, defaultTaskId: '' });
  const [inviteRolePickerOpen, setInviteRolePickerOpen] = useState(false);
  const [inviteRoleId, setInviteRoleId] = useState('');
  const [candidateSheetOpen, setCandidateSheetOpen] = useState(false);
  const [candidateSheetError, setCandidateSheetError] = useState('');
  const [addingCandidate, setAddingCandidate] = useState(false);

  const applications = useMemo(() => (
    Array.isArray(applicationsPayload.items) ? applicationsPayload.items : []
  ), [applicationsPayload]);

  const selectedApplicationIdSet = useMemo(() => (
    new Set(selectedApplicationIds.map((value) => Number(value)))
  ), [selectedApplicationIds]);

  const rejectableApplications = useMemo(() => (
    applications.filter((application) => application?.application_outcome === 'open')
  ), [applications]);

  const selectedRejectableApplications = useMemo(() => (
    applications.filter((application) => (
      selectedApplicationIdSet.has(Number(application.id))
      && application?.application_outcome === 'open'
    ))
  ), [applications, selectedApplicationIdSet]);

  const allRejectableApplicationsSelected = useMemo(() => (
    rejectableApplications.length > 0
    && selectedRejectableApplications.length === rejectableApplications.length
  ), [rejectableApplications, selectedRejectableApplications]);

  const roleApplicationsByCandidateKey = useMemo(() => {
    const next = {};
    applications.forEach((application) => {
      const key = candidateApplicationKey(application);
      next[key] = Number(next[key] || 0) + 1;
    });
    return next;
  }, [applications]);

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

  const totalPages = Math.max(1, Math.ceil(Number(applicationsPayload.total || 0) / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages - 1);

  const roleFilterOptions = useMemo(() => (
    roles.map((role) => ({ value: String(role.id), label: role.name }))
  ), [roles]);

  const effectiveRoleFilters = useMemo(() => {
    const normalized = uniqueStringValues(
      roleFilterLocked ? [lockedRoleValue] : roleFilters
    );
    if (!normalized.length) return [];
    if (roleFilterLocked) return normalized;
    if (!roleFilterOptions.length) return normalized;
    const allRoleValues = roleFilterOptions.map((option) => option.value);
    if (normalized.length >= allRoleValues.length) return [];
    return normalized.filter((value) => allRoleValues.includes(value));
  }, [lockedRoleValue, roleFilterLocked, roleFilterOptions, roleFilters]);

  const effectiveStageFilters = useMemo(() => {
    const normalized = uniqueStringValues(stageFilters).filter(
      (value) => STAGE_FILTER_OPTIONS.some((option) => option.value === value)
    );
    if (normalized.length >= STAGE_FILTER_OPTIONS.length) return [];
    return normalized;
  }, [stageFilters]);

  const effectiveOutcomeFilters = useMemo(() => {
    const normalized = uniqueStringValues(outcomeFilters).filter(
      (value) => OUTCOME_OPTIONS.some((option) => option.value === value)
    );
    if (rolePipelineMode) return ['open'];
    if (!normalized.length || normalized.length >= OUTCOME_OPTIONS.length) return [];
    return normalized;
  }, [outcomeFilters, rolePipelineMode]);

  const thresholdRole = useMemo(() => {
    if (rolePipelineMode) {
      return roles.find((role) => Number(role.id) === Number(lockedRoleValue)) || null;
    }
    if (effectiveRoleFilters.length !== 1) return null;
    return roles.find((role) => String(role.id) === String(effectiveRoleFilters[0])) || null;
  }, [effectiveRoleFilters, lockedRoleValue, rolePipelineMode, roles]);
  const inviteRole = useMemo(() => (
    roles.find((role) => String(role.id) === String(inviteRoleId)) || null
  ), [inviteRoleId, roles]);

  const thresholdRoleValue = useMemo(
    () => resolveOptionalPercent(thresholdRole?.auto_reject_threshold_100),
    [thresholdRole?.auto_reject_threshold_100]
  );
  const hasThresholdRoleValue = thresholdRoleValue != null;
  const belowThresholdCount = useMemo(() => {
    if (thresholdRoleValue == null) return 0;
    return applications.filter((application) => {
      const score = Number(resolvePreScreenScore(application));
      return Number.isFinite(score) && score < thresholdRoleValue;
    }).length;
  }, [applications, thresholdRoleValue]);
  const selectedBelowThresholdCount = useMemo(() => {
    if (thresholdRoleValue == null) return 0;
    return selectedRejectableApplications.filter((application) => {
      const score = Number(resolvePreScreenScore(application));
      return Number.isFinite(score) && score < thresholdRoleValue;
    }).length;
  }, [selectedRejectableApplications, thresholdRoleValue]);

  useEffect(() => {
    if (!roleFilterLocked) return;
    setRoleFilters(lockedRoleValue ? [lockedRoleValue] : []);
  }, [lockedRoleValue, roleFilterLocked]);

  useEffect(() => {
    if (!rolePipelineMode) return;
    if (outcomeFilters.length !== 1 || outcomeFilters[0] !== 'open') {
      setOutcomeFilters(['open']);
    }
  }, [outcomeFilters, rolePipelineMode]);

  const buildListQueryParams = useCallback(() => {
    const [sortBy, sortOrder] = String(sortOption || SORT_OPTIONS[0].value).split(':');
    const parsedMinPreScreen = Number(minPreScreenScore);
    const params = {
      limit: PAGE_SIZE,
      offset: currentPage * PAGE_SIZE,
      sort_by: sortBy || 'pipeline_stage_updated_at',
      sort_order: sortOrder || 'desc',
      include_stage_counts: true,
    };
    if (minPreScreenScore !== '' && Number.isFinite(parsedMinPreScreen)) {
      params.min_pre_screen_score = Math.max(0, Math.min(100, parsedMinPreScreen));
    }
    if (workableOnly) {
      params.source = 'workable';
    }
    if (!rolePipelineMode) {
      if (!effectiveOutcomeFilters.length) {
        params.application_outcome = 'all';
      } else if (effectiveOutcomeFilters.length === 1) {
        params.application_outcome = effectiveOutcomeFilters[0];
      } else {
        params.application_outcomes = effectiveOutcomeFilters.join(',');
      }
      if (effectiveRoleFilters.length === 1) {
        params.role_id = Number(effectiveRoleFilters[0]);
      } else if (effectiveRoleFilters.length > 1) {
        params.role_ids = effectiveRoleFilters.join(',');
      }
      if (effectiveStageFilters.length === 1) {
        params.pipeline_stage = effectiveStageFilters[0];
      } else if (effectiveStageFilters.length > 1) {
        params.pipeline_stages = effectiveStageFilters.join(',');
      }
    } else if (effectiveStageFilters.length === 1) {
      params.stage = effectiveStageFilters[0];
    } else if (effectiveStageFilters.length > 1) {
      params.stages = effectiveStageFilters.join(',');
    }
    const trimmed = search.trim();
    if (trimmed) params.search = trimmed;
    return params;
  }, [
    currentPage,
    effectiveOutcomeFilters,
    effectiveRoleFilters,
    effectiveStageFilters,
    minPreScreenScore,
    rolePipelineMode,
    search,
    sortOption,
    workableOnly,
  ]);

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
    if (isShowcase) return [];
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
  }, [isShowcase, roleTasksByRoleId, rolesApi, showToast]);

  const loadRoles = useCallback(async () => {
    if (isShowcase) {
      setRoles(JOBS_SHOWCASE);
      setLoadingRoles(false);
      return;
    }
    setLoadingRoles(true);
    try {
      const res = await rolesApi.list();
      setRoles(Array.isArray(res?.data) ? res.data : []);
    } catch {
      setRoles([]);
    } finally {
      setLoadingRoles(false);
    }
  }, [isShowcase, rolesApi]);

  const loadApplications = useCallback(async ({ preferredApplicationId = null } = {}) => {
    if (isShowcase) {
      setApplicationsPayload({
        items: CANDIDATES_DIRECTORY_SHOWCASE,
        total: CANDIDATES_DIRECTORY_SHOWCASE.length,
        limit: PAGE_SIZE,
        offset: 0,
      });
      setStageCounts({ ...CANDIDATES_DIRECTORY_STAGE_COUNTS });
      setApplicationsError('');
      setLoadingApplications(false);
      setLoadingStageCounts(false);
      setSelectedApplicationId(null);
      return;
    }
    setLoadingApplications(true);
    setLoadingStageCounts(true);
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
      } else {
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
          rawCounts.all
          || nextCounts.applied + nextCounts.invited + nextCounts.in_assessment + nextCounts.review
        );
        setStageCounts(nextCounts);
        if (rolePipelineName) {
          setRolePipelineName('');
        }
      }
      setSelectedApplicationId((current) => {
        const target = preferredApplicationId != null ? Number(preferredApplicationId) : Number(current);
        if (target && items.some((item) => Number(item.id) === target)) return target;
        return null;
      });
    } catch (error) {
      setApplicationsPayload({ items: [], total: 0, limit: PAGE_SIZE, offset: 0 });
      setApplicationsError(getErrorMessage(error, 'Failed to load candidates.'));
      setSelectedApplicationId(null);
      if (rolePipelineMode) {
        setRolePipelineName('');
      }
      setStageCounts({ ...STAGE_COUNT_DEFAULTS });
    } finally {
      setLoadingApplications(false);
      setLoadingStageCounts(false);
    }
  }, [buildListQueryParams, isShowcase, lockedRoleValue, rolePipelineMode, rolePipelineName, rolesApi]);

  const loadApplicationDetail = useCallback(async (applicationId, { includeCvText = false, force = false } = {}) => {
    if (!applicationId) return null;
    if (isShowcase) return null;
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
  }, [applicationDetailsById, isShowcase, rolesApi, showToast, upsertApplicationInCache]);

  const loadApplicationEvents = useCallback(async (applicationId) => {
    if (!applicationId) return;
    if (isShowcase) {
      setEventsByApplicationId((prev) => ({ ...prev, [String(applicationId)]: [] }));
      return;
    }
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
  }, [isShowcase, rolesApi, showToast]);

  const refreshAll = useCallback(async () => {
    await Promise.all([
      loadRoles(),
      loadApplications({ preferredApplicationId: selectedApplicationId }),
    ]);
  }, [loadApplications, loadRoles, selectedApplicationId]);

  useEffect(() => {
    loadRoles();
  }, [loadRoles]);

  useEffect(() => {
    loadApplications();
  }, [loadApplications]);

  // Auto-poll while any visible application has an in-flight scoring job.
  // Stops as soon as every score_status settles to done/error/stale.
  useEffect(() => {
    if (isShowcase) return undefined;
    const hasInflight = applications.some((app) => {
      const status = app?.score_status;
      return status === 'pending' || status === 'running';
    });
    if (!hasInflight) return undefined;
    const handle = setInterval(() => {
      loadApplications({ preferredApplicationId: selectedApplicationId || null });
    }, 8000);
    return () => clearInterval(handle);
  }, [applications, isShowcase, loadApplications, selectedApplicationId]);

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
  }, [effectiveOutcomeFilters, effectiveRoleFilters, effectiveStageFilters, minPreScreenScore, search, sortOption, workableOnly]);

  useEffect(() => {
    if (externalRefreshKey === 0) return;
    void refreshAll();
  }, [externalRefreshKey, refreshAll]);

  useEffect(() => {
    const visibleRejectableIds = new Set(
      applications
        .filter((application) => application?.application_outcome === 'open')
        .map((application) => Number(application.id))
    );
    setSelectedApplicationIds((current) => current.filter((value) => visibleRejectableIds.has(Number(value))));
  }, [applications]);

  const refreshAfterConflict = useCallback(async (applicationId) => {
    const targetId = Number(applicationId || selectedApplicationId || 0);
    await loadApplications({ preferredApplicationId: targetId || null });
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
    selectedApplicationId,
  ]);

  const requestConfirmation = useCallback((message) => {
    if (!message) return true;
    if (typeof window === 'undefined' || typeof window.confirm !== 'function') return true;
    return window.confirm(message);
  }, []);

  const submitOutcomeUpdate = useCallback(async (
    application,
    targetOutcome,
    {
      reason = 'Updated from candidates directory',
      idempotencyPrefix = 'outcome',
    } = {},
  ) => {
    const res = await rolesApi.updateApplicationOutcome(application.id, {
      application_outcome: targetOutcome,
      expected_version: application.version,
      reason,
      idempotency_key: buildIdempotencyKey(idempotencyPrefix, application.id, application.version),
    });
    const updated = res?.data || null;
    if (updated) {
      upsertApplicationInCache(updated);
    }
    return updated;
  }, [rolesApi, upsertApplicationInCache]);

  const removeApplicationFromVisibleList = useCallback((application) => {
    const stageKey = String(application?.pipeline_stage || '').toLowerCase();
    setApplicationsPayload((prev) => ({
      ...prev,
      total: Math.max(0, Number(prev.total || 0) - 1),
      items: (Array.isArray(prev.items) ? prev.items : []).filter((item) => Number(item.id) !== Number(application.id)),
    }));
    setSelectedApplicationIds((current) => current.filter((value) => Number(value) !== Number(application.id)));
    setStageCounts((prev) => ({
      ...prev,
      all: Math.max(0, Number(prev.all || 0) - 1),
      ...(stageKey && Object.prototype.hasOwnProperty.call(prev, stageKey)
        ? { [stageKey]: Math.max(0, Number(prev[stageKey] || 0) - 1) }
        : {}),
    }));
  }, []);

  const moveApplicationStage = useCallback(async (application, nextStage) => {
    if (!application?.id || !nextStage) return;
    if (application.application_outcome !== 'open') {
      showToast('Re-open candidate outcome before moving stage.', 'error');
      return;
    }
    if (String(nextStage) === String(application.pipeline_stage || 'applied')) return;

    const previousApplication = { ...application };
    setUpdatingStageId(application.id);
    upsertApplicationInCache({
      ...application,
      pipeline_stage: nextStage,
      pipeline_stage_updated_at: new Date().toISOString(),
    });
    try {
      const res = await rolesApi.updateApplicationStage(application.id, {
        pipeline_stage: nextStage,
        expected_version: application.version,
        reason: 'Updated from candidate triage drawer',
        idempotency_key: buildIdempotencyKey('stage', application.id, application.version),
      });
      const updated = res?.data || null;
      if (updated) {
        upsertApplicationInCache(updated);
      }
      showToast('Pipeline stage updated.', 'success');
      await loadApplicationEvents(application.id);
    } catch (error) {
      upsertApplicationInCache(previousApplication);
      if (isVersionConflictError(error)) {
        showToast('Candidate changed in another session. Refreshed latest data.', 'error');
        await refreshAfterConflict(application.id);
        return;
      }
      showToast(getErrorMessage(error, 'Failed to update pipeline stage.'), 'error');
    } finally {
      setUpdatingStageId(null);
    }
  }, [
    loadApplicationEvents,
    refreshAfterConflict,
    rolesApi,
    showToast,
    upsertApplicationInCache,
  ]);

  const rejectApplicationFromDrawer = useCallback(async (application) => {
    if (!application?.id) return;
    if (application.application_outcome !== 'open') {
      showToast('Candidate is already closed.', 'info');
      return;
    }
    setUpdatingOutcomeId(application.id);
    try {
      const updated = await submitOutcomeUpdate(application, 'rejected', {
        reason: 'Rejected from candidate triage drawer',
        idempotencyPrefix: 'triage-reject',
      });
      showToast('Candidate rejected.', 'success');
      setSelectedApplicationId(null);
      if (rolePipelineMode || effectiveOutcomeFilters.includes('open')) {
        removeApplicationFromVisibleList(updated || application);
      }
    } catch (error) {
      if (isVersionConflictError(error)) {
        showToast('Candidate changed in another session. Refreshed latest data.', 'error');
        await refreshAfterConflict(application.id);
        return;
      }
      showToast(getErrorMessage(error, 'Failed to reject candidate.'), 'error');
    } finally {
      setUpdatingOutcomeId(null);
    }
  }, [
    effectiveOutcomeFilters,
    refreshAfterConflict,
    removeApplicationFromVisibleList,
    rolePipelineMode,
    showToast,
    submitOutcomeUpdate,
  ]);

  const toggleApplicationSelection = useCallback((applicationId) => {
    setSelectedApplicationIds((current) => {
      const normalizedId = Number(applicationId || 0);
      if (!normalizedId) return current;
      if (current.some((value) => Number(value) === normalizedId)) {
        return current.filter((value) => Number(value) !== normalizedId);
      }
      return [...current, normalizedId];
    });
  }, []);

  const handleSelectVisibleRejectable = useCallback(() => {
    setSelectedApplicationIds(rejectableApplications.map((application) => Number(application.id)));
  }, [rejectableApplications]);

  const handleBulkRejectSelected = async () => {
    if (!selectedRejectableApplications.length) {
      showToast('Select at least one open candidate to reject.', 'info');
      return;
    }
    const confirmationMessage = buildBulkRejectConfirmationMessage(selectedRejectableApplications);
    if (!requestConfirmation(confirmationMessage)) return;

    setBulkRejecting(true);
    setBulkRejectProgress({ current: 0, total: selectedRejectableApplications.length });
    setBulkRejectSummary(null);

    const succeeded = [];
    const failed = [];

    for (let index = 0; index < selectedRejectableApplications.length; index += 1) {
      const application = selectedRejectableApplications[index];
      setBulkRejectProgress({ current: index + 1, total: selectedRejectableApplications.length });
      try {
        await submitOutcomeUpdate(application, 'rejected', {
          reason: 'Bulk rejected from candidates directory',
          idempotencyPrefix: 'bulk-outcome',
        });
        succeeded.push({
          id: application.id,
          label: applicationDisplayName(application),
        });
      } catch (error) {
        if (isVersionConflictError(error)) {
          await refreshAfterConflict(application.id);
        }
        failed.push({
          id: application.id,
          label: applicationDisplayName(application),
          message: isVersionConflictError(error)
            ? 'Candidate changed in another session. Latest data loaded.'
            : getErrorMessage(error, 'Failed to update candidate outcome.'),
        });
      }
    }

    setBulkRejectSummary({
      action: 'reject',
      succeeded,
      failed,
      completedAt: new Date().toISOString(),
    });
    setSelectedApplicationIds([]);

    const total = selectedRejectableApplications.length;
    if (failed.length > 0) {
      showToast(`Bulk reject finished. ${succeeded.length}/${total} updated, ${failed.length} failed.`, 'error');
    } else {
      showToast(`Rejected ${succeeded.length} candidate${succeeded.length === 1 ? '' : 's'}.`, 'success');
    }

    try {
      await loadApplications({ preferredApplicationId: selectedApplicationId });
      if (selectedApplicationId) {
        await loadApplicationEvents(selectedApplicationId);
      }
    } finally {
      setBulkRejectProgress(null);
      setBulkRejecting(false);
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
      await loadApplications({ preferredApplicationId: application.id });
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

  // Per-candidate Score / Rescore — calls the orchestrator (cache hits make
  // unchanged candidates instant; misses run cv_match_v4 in the background).
  const [generatingTaaliId, setGeneratingTaaliId] = useState(null);
  const handleGenerateTaaliCvAi = useCallback(async (application) => {
    if (!rolesApi?.generateTaaliCvAi || !application?.id) return;
    setGeneratingTaaliId(application.id);
    try {
      const res = await rolesApi.generateTaaliCvAi(application.id);
      const updated = res?.data;
      if (updated && updated.id) upsertApplicationInCache(updated);
      showToast('CV scoring started.', 'success');
      await loadApplications({ preferredApplicationId: application.id });
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to score candidate.'), 'error');
    } finally {
      setGeneratingTaaliId(null);
    }
  }, [loadApplications, rolesApi, showToast, upsertApplicationInCache]);

  // Per-application Refresh interview guidance — re-derives the screening
  // pack, summaries, and the cv_match_v4-derived candidate kit. No Claude call.
  const [refreshingInterviewGuidanceId, setRefreshingInterviewGuidanceId] = useState(null);
  const handleRefreshInterviewGuidance = useCallback(async (application) => {
    if (!rolesApi?.refreshInterviewSupport || !application?.id) return;
    setRefreshingInterviewGuidanceId(application.id);
    try {
      const res = await rolesApi.refreshInterviewSupport(application.id);
      const updated = res?.data;
      if (updated && updated.id) upsertApplicationInCache(updated);
      showToast('Interview guidance refreshed.', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to refresh interview guidance.'), 'error');
    } finally {
      setRefreshingInterviewGuidanceId(null);
    }
  }, [rolesApi, showToast, upsertApplicationInCache]);

  // Bulk Score selected — only enqueues candidates whose inputs have changed
  // since their last score (the orchestrator caches the rest as no-ops).
  const [bulkScoreInFlight, setBulkScoreInFlight] = useState(false);
  const handleScoreSelected = useCallback(async () => {
    const ids = Array.from(selectedApplicationIdSet).map(Number);
    if (ids.length === 0) return;
    // The bulk endpoint is per-role, but selection can span roles in this view.
    // Group by role_id and call once per role.
    const byRole = new Map();
    for (const app of applications) {
      const id = Number(app?.id);
      if (!ids.includes(id)) continue;
      const roleId = app?.role_id;
      if (!roleId) continue;
      if (!byRole.has(roleId)) byRole.set(roleId, []);
      byRole.get(roleId).push(id);
    }
    if (byRole.size === 0 || !rolesApi?.scoreSelected) return;
    setBulkScoreInFlight(true);
    try {
      let totalEnqueued = 0;
      let totalSkipped = 0;
      let totalNotEligible = 0;
      let totalAutoFetching = 0;
      for (const [roleId, applicationIds] of byRole) {
        const res = await rolesApi.scoreSelected(roleId, applicationIds);
        const data = res?.data || {};
        totalEnqueued += Number(data.enqueued || 0);
        totalSkipped += Number(data.skipped_unchanged || 0);
        totalNotEligible += Number(data.not_eligible || 0);
        totalAutoFetching += Number(data.auto_fetching || 0);
      }
      const skippedSuffix = totalSkipped > 0 ? `; ${totalSkipped} already up to date` : '';
      const fetchingSuffix = totalAutoFetching > 0
        ? `; fetching CVs and scoring ${totalAutoFetching} more in the background`
        : '';
      const notEligibleSuffix = totalNotEligible > 0
        ? `; ${totalNotEligible} skipped (no CV available)`
        : '';
      if (totalEnqueued > 0 || totalAutoFetching > 0) {
        const headline = totalEnqueued > 0
          ? `Scoring ${totalEnqueued} candidate(s)`
          : `Fetching CVs and scoring ${totalAutoFetching} candidate(s)`;
        const tail = totalEnqueued > 0
          ? `${skippedSuffix}${fetchingSuffix}${notEligibleSuffix}`
          : `${skippedSuffix}${notEligibleSuffix}`;
        showToast(`${headline}${tail}.`, 'success');
        loadApplications({});
      } else if (totalNotEligible > 0) {
        showToast(
          `No CV available for ${totalNotEligible} candidate(s) and they aren't linked to Workable. Upload CVs first, then re-score.`,
          'info',
        );
      } else if (totalSkipped > 0) {
        showToast(`No changes since last score — ${totalSkipped} candidate(s) already up to date.`, 'info');
      } else {
        showToast('Nothing to score for the selected candidates.', 'info');
      }
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to score selected candidates.'), 'error');
    } finally {
      setBulkScoreInFlight(false);
    }
  }, [selectedApplicationIdSet, applications, rolesApi, showToast, loadApplications]);

  const [bulkFetchCvsInFlight, setBulkFetchCvsInFlight] = useState(false);
  const handleFetchCvsSelected = useCallback(async () => {
    const ids = Array.from(selectedApplicationIdSet).map(Number);
    if (ids.length === 0 || !rolesApi?.fetchCvsSelected) return;

    const byRole = new Map();
    for (const app of applications) {
      const id = Number(app?.id);
      if (!ids.includes(id)) continue;
      const roleId = app?.role_id;
      if (!roleId) continue;
      if (!byRole.has(roleId)) byRole.set(roleId, []);
      byRole.get(roleId).push(id);
    }
    if (byRole.size === 0) return;
    setBulkFetchCvsInFlight(true);
    try {
      let totalFetching = 0;
      let totalAlreadyPresent = 0;
      for (const [roleId, applicationIds] of byRole) {
        const res = await rolesApi.fetchCvsSelected(roleId, applicationIds);
        const data = res?.data || {};
        totalFetching += Number(data.fetching || 0);
        totalAlreadyPresent += Number(data.already_present || 0);
      }
      if (totalFetching > 0) {
        const suffix = totalAlreadyPresent > 0
          ? `; ${totalAlreadyPresent} already had CVs`
          : '';
        showToast(`Fetching ${totalFetching} CV(s) from Workable${suffix}.`, 'success');
        // Poll-friendly: refresh after a short delay so the user sees CV columns populate.
        setTimeout(() => loadApplications({}), 4000);
      } else if (totalAlreadyPresent > 0) {
        showToast(`All selected candidates already have CVs (${totalAlreadyPresent}).`, 'info');
      } else {
        showToast('No fetchable CVs in the selection (not linked to Workable).', 'info');
      }
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to fetch CVs for selected candidates.'), 'error');
    } finally {
      setBulkFetchCvsInFlight(false);
    }
  }, [selectedApplicationIdSet, applications, rolesApi, showToast, loadApplications]);

  const [bulkGuidanceInFlight, setBulkGuidanceInFlight] = useState(false);
  const handleRefreshGuidanceSelected = useCallback(async () => {
    const ids = Array.from(selectedApplicationIdSet).map(Number);
    if (ids.length === 0 || !rolesApi?.refreshInterviewSupportBulk) return;
    const byRole = new Map();
    for (const app of applications) {
      const id = Number(app?.id);
      if (!ids.includes(id)) continue;
      const roleId = app?.role_id;
      if (!roleId) continue;
      if (!byRole.has(roleId)) byRole.set(roleId, []);
      byRole.get(roleId).push(id);
    }
    if (byRole.size === 0) return;
    setBulkGuidanceInFlight(true);
    try {
      let totalRefreshed = 0;
      for (const [roleId, applicationIds] of byRole) {
        const res = await rolesApi.refreshInterviewSupportBulk(roleId, applicationIds);
        totalRefreshed += Number(res?.data?.refreshed || 0);
      }
      showToast(`Refreshed interview guidance for ${totalRefreshed} candidate(s).`, 'success');
      loadApplications({});
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to refresh interview guidance.'), 'error');
    } finally {
      setBulkGuidanceInFlight(false);
    }
  }, [selectedApplicationIdSet, applications, rolesApi, showToast, loadApplications]);

  const viewFullPage = (application, assessmentId) => {
    if (!application) return;
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
  const activeStageSegment = effectiveStageFilters.length === 1 ? effectiveStageFilters[0] : 'all';
  const segmentOptions = [
    { value: 'all', label: `All · ${stageCounts.all || applicationsPayload.total || 0}` },
    { value: 'in_assessment', label: `In assessment · ${stageCounts.in_assessment || 0}` },
    { value: 'review', label: `Review · ${stageCounts.review || 0}` },
    { value: 'invited', label: `Invited · ${stageCounts.invited || 0}` },
  ];
  const showPageHead = Boolean(String(headerTitle || '').trim() || String(headerSubtitle || '').trim());
  const activeFilterDescription = hasThresholdRoleValue && Number(minPreScreenScore) === thresholdRoleValue
    ? `Below threshold · ${belowThresholdCount || 0}`
    : '';
  const showInitialLoadingState = loadingApplications && applications.length === 0 && !applicationsError;
  const bulkBarLabel = selectedRejectableApplications.length > 0 && hasThresholdRoleValue && selectedBelowThresholdCount === selectedRejectableApplications.length
    ? `Bulk action — all ${selectedRejectableApplications.length} are below the ${thresholdRoleValue}% CV threshold for ${thresholdRole?.name || 'this role'}.`
    : 'Bulk action — all selected candidates stay synced to pipeline outcomes. Workable-linked rows will disqualify back to Workable.';

  const handleExportCsv = () => {
    const rows = [
      ['Candidate', 'Email', 'Role', 'Stage', 'Outcome', 'Pre-screen', 'Taali', 'Updated'],
      ...applications.map((application) => [
        applicationDisplayName(application),
        application?.candidate_email || '',
        application?.role_name || application?.candidate_position || '',
        application?.pipeline_stage || '',
        application?.application_outcome || '',
        resolvePreScreenScore(application) ?? '',
        resolveTaaliScore(application) ?? '',
        application?.pipeline_stage_updated_at || application?.updated_at || application?.created_at || '',
      ]),
    ];
    const csv = rows.map((row) => row.map(toCsvValue).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'taali-candidates.csv';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  const handleOpenInviteCandidate = () => {
    const directRoleId = roleFilterLocked
      ? lockedRoleValue
      : (effectiveRoleFilters.length === 1 ? effectiveRoleFilters[0] : '');
    setCandidateSheetError('');
    if (directRoleId) {
      setInviteRoleId(String(directRoleId));
      setCandidateSheetOpen(true);
      return;
    }
    setInviteRoleId((current) => current || roleFilterOptions[0]?.value || '');
    setInviteRolePickerOpen(true);
  };

  const handleCandidateSubmit = async ({ email, name, position, cvFile }) => {
    if (!inviteRole || !rolesApi?.createApplication) return;
    setAddingCandidate(true);
    setCandidateSheetError('');
    try {
      const res = await rolesApi.createApplication(inviteRole.id, {
        candidate_email: email,
        candidate_name: name,
        candidate_position: position || undefined,
      });
      if (cvFile && rolesApi.uploadApplicationCv && res?.data?.id) {
        await rolesApi.uploadApplicationCv(res.data.id, cvFile);
      }
      setCandidateSheetOpen(false);
      setInviteRolePickerOpen(false);
      await Promise.all([
        loadApplications(),
        loadRoles(),
      ]);
      showToast('Candidate added.', 'success');
    } catch (err) {
      setCandidateSheetError(getErrorMessage(err, 'Failed to add candidate.'));
    } finally {
      setAddingCandidate(false);
    }
  };

  const selectedActivityLabel = useMemo(() => {
    if (selectedEvents.length > 0) {
      const latestEvent = selectedEvents[0];
      const formatted = formatTimelineEvent(latestEvent);
      return `${formatRelativeDateTime(latestEvent.created_at)} · ${formatted.title}`;
    }
    const fallbackDate = selectedApplication?.pipeline_stage_updated_at
      || selectedApplication?.updated_at
      || selectedApplication?.created_at;
    return fallbackDate ? `Last activity ${formatRelativeDateTime(fallbackDate)}` : '';
  }, [selectedApplication?.created_at, selectedApplication?.pipeline_stage_updated_at, selectedApplication?.updated_at, selectedEvents]);

  const handleTriageSendAssessment = useCallback((application, taskId) => {
    if (!application?.id || !taskId) return;
    if (resolveAssessmentId(application)) {
      setRetakeDialogState({
        applicationId: application.id,
        defaultTaskId: String(taskId),
      });
      return;
    }
    void createOrRetakeAssessment(application, taskId, { retake: false });
  }, []);

  const openCandidateReportInNewTab = useCallback((application) => {
    if (isShowcase) return;
    if (!application?.id || typeof window === 'undefined') return;
    window.open(candidateReportHref(application), '_blank', 'noopener,noreferrer');
  }, [isShowcase]);

  const isInteractiveRowTarget = (target) => (
    target instanceof Element
    && Boolean(target.closest('input, button, a, select, textarea, label'))
  );

  const toggleApplicationDrawer = useCallback((application) => {
    if (!application?.id) return;
    setSelectedApplicationId((current) => (
      Number(current) === Number(application.id) ? null : Number(application.id)
    ));
  }, []);

  const handleCandidateRowClick = useCallback((event, application) => {
    if (isInteractiveRowTarget(event.target)) return;
    if (event.metaKey || event.ctrlKey) {
      openCandidateReportInNewTab(application);
      return;
    }
    toggleApplicationDrawer(application);
  }, [openCandidateReportInNewTab, toggleApplicationDrawer]);

  const handleCandidateRowAuxClick = useCallback((event, application) => {
    if (event.button !== 1 || isInteractiveRowTarget(event.target)) return;
    event.preventDefault();
    openCandidateReportInNewTab(application);
  }, [openCandidateReportInNewTab]);

  const handleCandidateRowKeyDown = useCallback((event, application) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    if (isInteractiveRowTarget(event.target)) return;
    event.preventDefault();
    toggleApplicationDrawer(application);
  }, [toggleApplicationDrawer]);

  useEffect(() => {
    if (!selectedApplicationId) return undefined;
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        setSelectedApplicationId(null);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [selectedApplicationId]);

  // Active role for the toaster — only meaningful when a single role
  // is filtered (otherwise the role-scoped batch-status endpoints don't
  // apply). Toaster renders nothing when roleId is falsy.
  const toasterRoleId = (() => {
    if (effectiveRoleFilters.length !== 1) return null;
    const id = Number(effectiveRoleFilters[0]);
    return Number.isFinite(id) && id > 0 ? id : null;
  })();

  return (
    <div>
      {NavComponent ? <NavComponent currentPage={navCurrentPage} onNavigate={onNavigate} /> : null}
      {!embedded && toasterRoleId ? <BackgroundJobsToaster roleId={toasterRoleId} /> : null}
      <div className={embedded ? '' : 'page'}>
        {showPageHead ? (
          <div className="page-head">
            <div className="tally-bg" />
            <div>
              <div className="kicker">{rolePipelineMode ? 'ROLE PIPELINE' : '02 · RECRUITER WORKSPACE'}</div>
              <h1>{headerTitle || 'Candidates'}<em>.</em></h1>
              <p className="sub">{headerSubtitle}</p>
            </div>
            <div className="row">
              {!rolePipelineMode ? (
                <button type="button" className="btn btn-outline btn-sm" onClick={handleExportCsv}>
                  Export CSV
                </button>
              ) : null}
              <button
                type="button"
                className="btn btn-purple btn-sm"
                onClick={handleOpenInviteCandidate}
                disabled={loadingRoles || roles.length === 0}
              >
                + Invite candidate
              </button>
            </div>
          </div>
        ) : null}

        {prelude ? <div className="mb-4 space-y-4">{prelude}</div> : null}

        <div className="candidate-toolbar">
          <div className="segset">
            {segmentOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                className={activeStageSegment === option.value ? 'on' : ''}
                onClick={() => {
                  if (option.value === 'all') {
                    setStageFilters([]);
                    return;
                  }
                  setStageFilters([option.value]);
                }}
              >
                {option.label}
              </button>
            ))}
          </div>

          <div className="relative grow min-w-[280px]">
            <Search size={14} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[var(--mute)]" />
            <input
              className="search pl-10"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search by name, email, or role…"
              aria-label="Search candidates"
            />
          </div>

          {!roleFilterLocked && roleFilterOptions.length > 0 ? (
            <label className="filter-chip" style={{ cursor: 'pointer', padding: 0 }}>
              <span style={{ padding: '0 6px 0 12px', fontSize: 11, color: 'var(--mute)', whiteSpace: 'nowrap' }}>Role:</span>
              <select
                value={effectiveRoleFilters[0] || ''}
                onChange={(event) => {
                  const value = event.target.value;
                  setRoleFilters(value ? [value] : []);
                }}
                aria-label="Filter by role"
                style={{
                  background: 'transparent',
                  border: 'none',
                  outline: 'none',
                  fontSize: 12,
                  padding: '6px 12px 6px 0',
                  cursor: 'pointer',
                  color: 'inherit',
                  appearance: 'none',
                  maxWidth: 200,
                }}
              >
                <option value="">All roles</option>
                {roleFilterOptions.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
          ) : null}

          {!rolePipelineMode ? (
            <label className="filter-chip" style={{ cursor: 'pointer', padding: 0 }}>
              <span style={{ padding: '0 6px 0 12px', fontSize: 11, color: 'var(--mute)', whiteSpace: 'nowrap' }}>Outcome:</span>
              <select
                value={outcomeFilters.length === 1 ? outcomeFilters[0] : (outcomeFilters.length === 0 ? 'all' : 'open')}
                onChange={(event) => {
                  const value = event.target.value;
                  if (value === 'all') {
                    setOutcomeFilters([]);
                  } else {
                    setOutcomeFilters([value]);
                  }
                }}
                aria-label="Filter by application outcome"
                style={{
                  background: 'transparent',
                  border: 'none',
                  outline: 'none',
                  fontSize: 12,
                  padding: '6px 12px 6px 0',
                  cursor: 'pointer',
                  color: 'inherit',
                  appearance: 'none',
                }}
              >
                <option value="all">All outcomes</option>
                {OUTCOME_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
          ) : null}
          {roleFilterLocked
            ? effectiveRoleFilters.slice(0, 2).map((roleId) => {
                const label = roleFilterOptions.find((option) => option.value === roleId)?.label || roleId;
                return (
                  <span key={roleId} className="filter-chip on" title={`Locked to ${label}`}>
                    {label}
                  </span>
                );
              })
            : null}

          {activeFilterDescription ? (
            <button
              type="button"
              className="filter-chip on"
              style={{
                background: 'color-mix(in oklab, var(--red) 10%, transparent)',
                borderColor: 'color-mix(in oklab, var(--red) 30%, var(--line))',
                color: 'var(--red)',
              }}
              onClick={() => setMinPreScreenScore('')}
            >
              <AlertCircle size={10} />
              {activeFilterDescription}
            </button>
          ) : null}

          <button type="button" className={`filter-chip ${workableOnly ? 'on' : ''}`} onClick={() => setWorkableOnly((current) => !current)}>
            <ArrowRight size={10} />
            From Workable
          </button>

          <label className="filter-chip" style={{ cursor: 'pointer', padding: 0 }}>
            <span style={{ padding: '0 6px 0 12px', fontSize: 11, color: 'var(--mute)', whiteSpace: 'nowrap' }}>Sort:</span>
            <select
              value={sortOption}
              onChange={(event) => setSortOption(event.target.value)}
              aria-label="Sort candidates"
              style={{
                background: 'transparent',
                border: 'none',
                outline: 'none',
                fontSize: 12,
                padding: '6px 12px 6px 0',
                cursor: 'pointer',
                color: 'inherit',
                appearance: 'none',
              }}
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>

          <button type="button" className="filter-chip" disabled title="Additional recruiter filters are coming next.">
            + Add filter
          </button>

          {hasThresholdRoleValue ? (
            <button
              type="button"
              className="threshold-control"
              onClick={() => setMinPreScreenScore(Number(minPreScreenScore) === thresholdRoleValue ? '' : String(thresholdRoleValue))}
              title="Toggle the saved role threshold filter"
            >
              <span className="dot" />
              <span className="role">{thresholdRole?.name || 'Role'} threshold:</span>
              <b>{thresholdRoleValue}%</b>
              <span style={{ color: 'var(--mute)', marginLeft: 4 }}>{belowThresholdCount} below</span>
            </button>
          ) : null}

          {!rolePipelineMode ? (
            <button type="button" className="filter-chip" onClick={refreshAll}>
              <RefreshCw size={10} className={loadingApplications ? 'animate-spin' : ''} />
              Refresh
            </button>
          ) : null}

          {(search || workableOnly || minPreScreenScore !== '' || effectiveStageFilters.length || effectiveRoleFilters.length || (!rolePipelineMode && effectiveOutcomeFilters.length)) ? (
            <button
              type="button"
              className="filter-chip"
              onClick={() => {
                setSearch('');
                setWorkableOnly(false);
                setMinPreScreenScore('');
                setStageFilters([]);
                if (!roleFilterLocked) setRoleFilters([]);
                if (!rolePipelineMode) setOutcomeFilters(['open']);
              }}
            >
              Clear filters
            </button>
          ) : null}
        </div>
        {hasThresholdRoleValue && thresholdRole && !rolePipelineMode ? (
          <div className="candidate-toolbar-note">
            CV scored manually ·{' '}
            <button
              type="button"
              className="candidate-toolbar-link"
              onClick={() => onNavigate('job-pipeline', { roleId: thresholdRole.id })}
            >
              Run scoring on this role <span className="arrow">→</span>
            </button>
          </div>
        ) : null}

        <div className={`candidate-bulk-bar ${selectedApplicationIds.length > 0 ? 'on' : ''}`}>
          <span className="count">{selectedApplicationIds.length} selected</span>
          <span className="label">{bulkBarLabel}</span>
          {rolesApi?.scoreSelected ? (
            <button
              type="button"
              onClick={handleScoreSelected}
              disabled={selectedApplicationIds.length === 0 || bulkScoreInFlight}
              title="Re-score CV match for selected candidates. CVs missing from Workable get fetched automatically."
            >
              {bulkScoreInFlight ? 'Scoring…' : `Score selected (${selectedApplicationIds.length})`}
            </button>
          ) : null}
          {rolesApi?.fetchCvsSelected ? (
            <button
              type="button"
              onClick={handleFetchCvsSelected}
              disabled={selectedApplicationIds.length === 0 || bulkFetchCvsInFlight}
              title="Pull CVs from Workable for the selected candidates without scoring."
            >
              {bulkFetchCvsInFlight ? 'Fetching…' : `Fetch CVs (${selectedApplicationIds.length})`}
            </button>
          ) : null}
          {rolesApi?.refreshInterviewSupportBulk ? (
            <button
              type="button"
              onClick={handleRefreshGuidanceSelected}
              disabled={selectedApplicationIds.length === 0 || bulkGuidanceInFlight}
              title="Refresh interview guidance kit + screening pack for selected candidates (no extra Claude call)"
            >
              {bulkGuidanceInFlight ? 'Refreshing…' : `Refresh interview guidance (${selectedApplicationIds.length})`}
            </button>
          ) : null}
          <button type="button" onClick={() => setSelectedApplicationIds([])}>Clear</button>
          <button type="button" className="danger" onClick={handleBulkRejectSelected} disabled={selectedRejectableApplications.length === 0 || bulkRejecting}>
            {selectedRejectableApplications.length > 0
              ? `Reject selected (${selectedRejectableApplications.length})`
              : 'Reject selected'}
          </button>
        </div>

        {bulkRejectSummary ? (
          <Panel className={`mb-4 p-3 ${bulkRejectSummary.failed.length > 0 ? 'border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)]' : 'border-[var(--taali-success-border)] bg-[var(--taali-success-soft)]'}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-[var(--taali-text)]">Bulk reject finished</p>
                <p className="text-xs text-[var(--taali-muted)]">
                  {bulkRejectSummary.succeeded.length} updated, {bulkRejectSummary.failed.length} failed
                </p>
              </div>
              <Button type="button" size="xs" variant="ghost" onClick={() => setBulkRejectSummary(null)}>
                Dismiss
              </Button>
            </div>
            {bulkRejectSummary.succeeded.length > 0 ? (
              <p className="mt-2 text-xs text-[var(--taali-text)]">
                Rejected: {bulkRejectSummary.succeeded.map((item) => item.label).join(', ')}
              </p>
            ) : null}
            {bulkRejectSummary.failed.length > 0 ? (
              <p className="mt-2 text-xs text-[var(--taali-text)]">
                Failed: {bulkRejectSummary.failed.map((item) => `${item.label} (${item.message})`).join(' · ')}
              </p>
            ) : null}
          </Panel>
        ) : null}

        {showInitialLoadingState ? (
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
          <div>
            <div className="candidate-table">
              <div className="candidate-table-wrap">
                <div className="cand-head">
                  <div>
                    <input
                      type="checkbox"
                      aria-label="Select all open candidates on this page"
                      checked={allRejectableApplicationsSelected}
                      onChange={() => {
                        if (allRejectableApplicationsSelected) {
                          setSelectedApplicationIds([]);
                          return;
                        }
                        handleSelectVisibleRejectable();
                      }}
                    />
                  </div>
                  <div>Candidate</div>
                  <div>Role</div>
                  <div title="CV match before assessment, blended composite after">Taali AI Score</div>
                  <div>Hire signal</div>
                  <div>Status</div>
                  <div>Submitted</div>
                </div>

                {applications.map((application) => {
                  const selected = Number(application.id) === Number(selectedApplicationId);
                  const selectableForBulkReject = application?.application_outcome === 'open';
                  const roleApplicationCount = Number(roleApplicationsByCandidateKey[candidateApplicationKey(application)] || 1);
                  const preScreenScore = resolvePreScreenScore(application);
                  const unifiedScore = resolveUnifiedScore(application);
                  const updatedAt = application.pipeline_stage_updated_at || application.updated_at || application.created_at;
                  const scoreTone = getCvMatchTone(unifiedScore.value, thresholdRoleValue);
                  const hireSignal = getHireSignal(application);
                  const statusChip = getStatusChip(application);
                  const belowThreshold = Number.isFinite(thresholdRoleValue) && Number.isFinite(Number(preScreenScore)) && Number(preScreenScore) < thresholdRoleValue;
                  return (
                    <React.Fragment key={application.id}>
                      <div
                        className={`cand-row ${selected ? 'selected' : ''} ${belowThreshold ? 'below' : ''}`}
                        role="button"
                        tabIndex={0}
                        aria-expanded={selected}
                        aria-controls={selected ? `candidate-triage-${application.id}` : undefined}
                        onClick={(event) => handleCandidateRowClick(event, application)}
                        onAuxClick={(event) => handleCandidateRowAuxClick(event, application)}
                        onKeyDown={(event) => handleCandidateRowKeyDown(event, application)}
                      >
                        <div>
                          <input
                            type="checkbox"
                            aria-label={`Select ${applicationDisplayName(application)}`}
                            checked={selectedApplicationIdSet.has(Number(application.id))}
                            disabled={!selectableForBulkReject || bulkRejecting}
                            onChange={() => toggleApplicationSelection(application.id)}
                          />
                        </div>

                        <div className="c-name text-left">
                          <CandidateAvatar
                            name={application.candidate_name || application.candidate_email}
                            imageUrl={application.candidate_image_url}
                            size={34}
                          />
                          <div className="min-w-0">
                            <div className="n">
                              {application.candidate_name || application.candidate_email}
                              {application.workable_sourced ? <WorkableTagSm className="ml-2" /> : null}
                            </div>
                            <div className="e">{application.candidate_email}</div>
                            {belowThreshold ? (
                              <div className="below-pill">
                                <AlertCircle size={8} />
                                Below threshold
                              </div>
                            ) : null}
                          </div>
                        </div>

                        <div className="c-role">
                          {application.role_name || application.candidate_position || 'Role'}
                          <div className="r-meta">
                            {application.candidate_location || 'Location not captured'}
                            {roleApplicationCount > 1 ? ` · ${roleApplicationCount} applications` : ''}
                          </div>
                        </div>

                        <div className="c-score-unified" title={unifiedScore.value == null ? 'Not scored yet' : `${Math.round(Number(unifiedScore.value))}%`}>
                          <span className={`pct ${scoreTone}`}>
                            {(() => {
                              const status = application?.score_status;
                              if (status === 'pending' || status === 'running') return 'Scoring…';
                              if (unifiedScore.value == null) {
                                if (status === 'error') return 'Error';
                                if (status === 'stale') return 'Stale';
                                return '—';
                              }
                              const formatted = `${Math.round(Number(unifiedScore.value))}%`;
                              return status === 'stale' ? `${formatted} · stale` : formatted;
                            })()}
                            {application.workable_score_raw != null && unifiedScore.value != null ? (
                              <span className="wk-pip">WK <b>{Math.round(Number(application.workable_score_raw))}</b></span>
                            ) : null}
                          </span>
                          <div className="meter">
                            <i className={scoreTone} style={{ width: `${Math.max(0, Math.min(100, Number(unifiedScore.value || 0)))}%` }} />
                            {hasThresholdRoleValue ? (
                              <span className="thr" style={{ left: `${Math.max(0, Math.min(100, thresholdRoleValue))}%` }} />
                            ) : null}
                          </div>
                          <div className="score-meta">
                            {unifiedScore.kind ? (
                              <span className={`score-kind-pill ${unifiedScore.kind}`}>
                                {unifiedScore.kind === 'composite' ? 'Composite' : 'CV'}
                              </span>
                            ) : null}
                            {rolesApi?.generateTaaliCvAi && application.cv_filename ? (() => {
                              const status = application?.score_status;
                              const inFlight =
                                Number(generatingTaaliId) === Number(application.id)
                                || status === 'pending'
                                || status === 'running';
                              const label = inFlight
                                ? 'Scoring…'
                                : status === 'error'
                                  ? 'Retry'
                                  : (status === 'stale' || preScreenScore != null)
                                    ? 'Rescore'
                                    : 'Score';
                              return (
                                <button
                                  type="button"
                                  className="cv-rescore-link"
                                  disabled={inFlight}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    handleGenerateTaaliCvAi(application);
                                  }}
                                  title="Score this candidate's CV match"
                                >
                                  {label}
                                </button>
                              );
                            })() : null}
                          </div>
                        </div>

                        <div>
                          <span className={`chip ${hireSignal.tone}`}>{hireSignal.label}</span>
                        </div>

                        <div className={`candidate-status-chip ${statusChip.tone}`}>
                          <span className="dot" />{statusChip.label}
                        </div>

                        <div className="candidate-submitted-cell">
                          <span className="c-time">{formatRelativeDateTime(updatedAt)}</span>
                        </div>
                      </div>
                      {selected ? (
                        <div id={`candidate-triage-${application.id}`}>
                          <CandidateTriageDrawer
                            application={selectedApplication || application}
                            roleTasks={selectedRoleTasks}
                            activityLabel={selectedActivityLabel}
                            loadingActivity={loadingEventsId === application.id}
                            stageBusy={updatingStageId === application.id}
                            assessmentBusy={creatingAssessmentId === application.id}
                            rejectBusy={updatingOutcomeId === application.id}
                            onClose={() => setSelectedApplicationId(null)}
                            onMoveStage={moveApplicationStage}
                            onSendAssessment={handleTriageSendAssessment}
                            onViewFullReport={viewFullPage}
                            onReject={rejectApplicationFromDrawer}
                          />
                        </div>
                      ) : null}
                    </React.Fragment>
                  );
                })}
              </div>

              {applicationsPayload.total > PAGE_SIZE ? (
                <div className="pagination">
                  <div>
                    Showing {currentPage * PAGE_SIZE + 1}–{Math.min(applicationsPayload.total, ((currentPage + 1) * PAGE_SIZE))} of {applicationsPayload.total}
                  </div>
                  <div className="row">
                    <button type="button" className="btn btn-outline btn-sm" disabled={currentPage <= 0} onClick={() => setPage((prev) => Math.max(0, prev - 1))}>
                      Previous
                    </button>
                    <button type="button" className="btn btn-outline btn-sm" disabled={currentPage >= totalPages - 1} onClick={() => setPage((prev) => Math.min(totalPages - 1, prev + 1))}>
                      Next
                    </button>
                  </div>
                </div>
              ) : (
                <div className="pagination">
                  <div>Showing {applicationsPayload.total} candidates in the current result set</div>
                  <div className="row">
                    <button type="button" className="btn btn-ghost btn-sm" disabled={rejectableApplications.length === 0 || allRejectableApplicationsSelected || bulkRejecting} onClick={handleSelectVisibleRejectable}>
                      Select page
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" disabled={selectedRejectableApplications.length === 0 || bulkRejecting} onClick={() => setSelectedApplicationIds([])}>
                      Clear
                    </button>
                  </div>
                </div>
              )}
            </div>

          </div>
        )}
      </div>

      <Sheet
        open={inviteRolePickerOpen}
        onClose={() => setInviteRolePickerOpen(false)}
        title="Choose role"
        description="Select which role this candidate should be added to."
        footer={(
          <div className="flex items-center justify-between gap-2">
            <Button type="button" variant="secondary" onClick={() => setInviteRolePickerOpen(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="primary"
              disabled={!inviteRoleId}
              onClick={() => {
                if (!inviteRoleId) return;
                setCandidateSheetError('');
                setInviteRolePickerOpen(false);
                setCandidateSheetOpen(true);
              }}
            >
              Continue
            </Button>
          </div>
        )}
      >
        <label className="block">
          <span className="mb-2 block text-sm font-semibold text-[var(--taali-text)]">Role</span>
          <Select
            value={inviteRoleId}
            onChange={(event) => setInviteRoleId(event.target.value)}
          >
            <option value="">Select a role</option>
            {roleFilterOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </label>
      </Sheet>

      <CandidateSheet
        open={candidateSheetOpen}
        role={inviteRole}
        saving={addingCandidate}
        error={candidateSheetError}
        onClose={() => setCandidateSheetOpen(false)}
        onSubmit={handleCandidateSubmit}
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
