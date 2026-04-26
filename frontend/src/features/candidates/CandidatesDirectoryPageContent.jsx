import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  ArrowUpDown,
  CheckCircle2,
  CircleDot,
  RefreshCw,
  Search,
} from 'lucide-react';

import * as apiClient from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import {
  Button,
  EmptyState,
  Panel,
  Select,
  Sheet,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import { formatDateTime, getErrorMessage } from './candidatesUiUtils';
import { CandidateSheet } from './CandidateSheet';
import { CandidateCvSidebar } from './CandidateCvSidebar';
import { CandidateScoreSummarySheet } from './CandidateScoreSummarySheet';
import { RetakeAssessmentDialog } from './RetakeAssessmentDialog';
import {
  CandidateAvatar,
  WorkableScorePip,
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
  { value: 'pre_screen_score:desc', label: 'Pre-screen score (high to low)' },
  { value: 'pre_screen_score:asc', label: 'Pre-screen score (low to high)' },
  { value: 'pipeline_stage_updated_at:desc', label: 'Recent activity' },
  { value: 'taali_score:desc', label: 'TAALI score (high to low)' },
  { value: 'taali_score:asc', label: 'TAALI score (low to high)' },
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

const resolveOptionalPercent = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.max(0, Math.min(100, Math.round(numeric)));
};

const formatScoreCell = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  return `${Math.round(numeric)}/100`;
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

const buildOutcomeChangeConfirmationMessage = (application, targetOutcome) => {
  const syncAction = workableOutcomeSyncAction(application, targetOutcome);
  if (!syncAction) return '';
  const candidateLabel = applicationDisplayName(application);
  if (syncAction === 'reject') {
    return [
      `Reject ${candidateLabel}?`,
      '',
      'This candidate is linked to Workable, so TAALI will also disqualify them in Workable.',
      "Any rejection email will come from Workable's disqualification automation/template.",
    ].join('\n');
  }
  return [
    `Reopen ${candidateLabel}?`,
    '',
    'This candidate is linked to Workable, so TAALI will also revert the Workable disqualification.',
  ].join('\n');
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
  const taaliScore = Number(resolveTaaliScore(application));
  const preScreenScore = Number(resolvePreScreenScore(application));
  const score = Number.isFinite(taaliScore) ? taaliScore : preScreenScore;
  if (!Number.isFinite(score)) return { label: 'Pending', tone: '' };
  if (score >= 80) return { label: 'Strong hire', tone: 'green' };
  if (score >= 65) return { label: 'Maybe', tone: 'amber' };
  return { label: 'No hire', tone: 'red' };
};

const getAiCollabSignal = (application) => {
  const taaliScore = Number(resolveTaaliScore(application));
  if (!Number.isFinite(taaliScore)) {
    const stage = String(application?.pipeline_stage || '').toLowerCase();
    if (stage === 'in_assessment') return { label: 'live', suffix: 'In progress', tone: 'c' };
    return { label: '—', suffix: '', tone: '' };
  }
  if (taaliScore >= 90) return { label: 'A+', suffix: `${Math.round(taaliScore)}`, tone: 'a' };
  if (taaliScore >= 80) return { label: 'A', suffix: `${Math.round(taaliScore)}`, tone: 'a' };
  if (taaliScore >= 70) return { label: 'B', suffix: `${Math.round(taaliScore)}`, tone: 'b' };
  if (taaliScore >= 60) return { label: 'C', suffix: `${Math.round(taaliScore)}`, tone: 'c' };
  return { label: 'D', suffix: `${Math.round(taaliScore)}`, tone: 'd' };
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
  onNavigate,
  NavComponent = null,
  initialRoleId = null,
  lockRoleId = null,
  useRolePipelineEndpoint = false,
  navCurrentPage = 'candidates',
  title = 'Candidates',
  subtitle = 'Every person across every role, scored and filterable. Click a row to open their assessment report.',
  prelude = null,
  externalRefreshKey = 0,
  embedded = false,
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

  const [pendingStage, setPendingStage] = useState('');
  const [pendingOutcome, setPendingOutcome] = useState('');
  const [updatingStage, setUpdatingStage] = useState(false);
  const [updatingOutcome, setUpdatingOutcome] = useState(false);
  const [selectedApplicationIds, setSelectedApplicationIds] = useState([]);
  const [bulkRejecting, setBulkRejecting] = useState(false);
  const [bulkRejectProgress, setBulkRejectProgress] = useState(null);
  const [bulkRejectSummary, setBulkRejectSummary] = useState(null);

  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [creatingAssessmentId, setCreatingAssessmentId] = useState(null);
  const [retakeDialogState, setRetakeDialogState] = useState({ applicationId: null, defaultTaskId: '' });
  const [inviteRolePickerOpen, setInviteRolePickerOpen] = useState(false);
  const [inviteRoleId, setInviteRoleId] = useState('');
  const [candidateSheetOpen, setCandidateSheetOpen] = useState(false);
  const [candidateSheetError, setCandidateSheetError] = useState('');
  const [addingCandidate, setAddingCandidate] = useState(false);

  const [cvSidebarApplicationId, setCvSidebarApplicationId] = useState(null);
  const [scoreSheetApplicationId, setScoreSheetApplicationId] = useState(null);
  const [assessmentDetailsById, setAssessmentDetailsById] = useState({});
  const [loadingAssessmentId, setLoadingAssessmentId] = useState(null);

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

  const selectedRoleApplicationCount = useMemo(() => (
    selectedApplication ? Number(roleApplicationsByCandidateKey[candidateApplicationKey(selectedApplication)] || 1) : 1
  ), [roleApplicationsByCandidateKey, selectedApplication]);

  const selectedRoleTasks = useMemo(() => (
    roleTasksByRoleId[String(selectedApplication?.role_id)] || []
  ), [roleTasksByRoleId, selectedApplication?.role_id]);

  const selectedAssessmentId = useMemo(() => resolveAssessmentId(selectedApplication), [selectedApplication]);
  const selectedCompletedAssessment = useMemo(() => (
    selectedAssessmentId ? (assessmentDetailsById[String(selectedAssessmentId)] || null) : null
  ), [assessmentDetailsById, selectedAssessmentId]);

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
        return items.length > 0 ? Number(items[0].id) : null;
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
  }, [buildListQueryParams, lockedRoleValue, rolePipelineMode, rolePipelineName, rolesApi]);

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
      loadApplications({ preferredApplicationId: selectedApplicationId }),
    ]);
  }, [loadApplications, loadRoles, selectedApplicationId]);

  useEffect(() => {
    loadRoles();
  }, [loadRoles]);

  useEffect(() => {
    loadApplications();
  }, [loadApplications]);

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
      await loadApplications({ preferredApplicationId: selectedApplication.id });
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
      if (Number(application.id) === Number(selectedApplicationId)) {
        setPendingStage(updated.pipeline_stage);
        setPendingOutcome(updated.application_outcome);
      }
    }
    return updated;
  }, [rolesApi, selectedApplicationId, upsertApplicationInCache]);

  const applyOutcomeUpdate = async () => {
    if (!selectedApplication || !pendingOutcome) return;
    if (pendingOutcome === selectedApplication.application_outcome) return;
    const confirmationMessage = buildOutcomeChangeConfirmationMessage(selectedApplication, pendingOutcome);
    if (!requestConfirmation(confirmationMessage)) return;
    setUpdatingOutcome(true);
    try {
      const updated = await submitOutcomeUpdate(selectedApplication, pendingOutcome);
      if (updated) {
        setPendingStage(updated.pipeline_stage);
        setPendingOutcome(updated.application_outcome);
      }
      showToast('Candidate outcome updated.', 'success');
      await loadApplications({ preferredApplicationId: selectedApplication.id });
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
  const selectedOutcomeSyncAction = workableOutcomeSyncAction(selectedApplication, pendingOutcome);
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

  return (
    <div>
      {NavComponent ? <NavComponent currentPage={navCurrentPage} onNavigate={onNavigate} /> : null}
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

          {effectiveRoleFilters.slice(0, 2).map((roleId) => {
            const label = roleFilterOptions.find((option) => option.value === roleId)?.label || roleId;
            return (
              <button key={roleId} type="button" className="filter-chip on" onClick={() => setRoleFilters([roleId])}>
                {label}
              </button>
            );
          })}

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

        <div className={`candidate-bulk-bar ${selectedRejectableApplications.length > 0 ? 'on' : ''}`}>
          <span className="count">{selectedRejectableApplications.length} selected</span>
          <span className="label">{bulkBarLabel}</span>
          <button type="button" disabled title="Inline bulk notes are not wired on this surface yet.">Add note</button>
          <button type="button" disabled title="Bulk stage moves are not wired on this surface yet.">Move stage</button>
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
                  <div title="CV scored against job spec + recruiter requirements">CV match</div>
                  <div>Taali score</div>
                  <div>AI collab</div>
                  <div>Hire signal</div>
                  <div>Status</div>
                  <div>Submitted</div>
                </div>

                {applications.map((application) => {
                  const selected = Number(application.id) === Number(selectedApplicationId);
                  const selectableForBulkReject = application?.application_outcome === 'open';
                  const roleApplicationCount = Number(roleApplicationsByCandidateKey[candidateApplicationKey(application)] || 1);
                  const preScreenScore = resolvePreScreenScore(application);
                  const taaliScore = resolveTaaliScore(application);
                  const updatedAt = application.pipeline_stage_updated_at || application.updated_at || application.created_at;
                  const cvTone = getCvMatchTone(preScreenScore, thresholdRoleValue);
                  const aiSignal = getAiCollabSignal(application);
                  const hireSignal = getHireSignal(application);
                  const statusChip = getStatusChip(application);
                  const belowThreshold = Number.isFinite(thresholdRoleValue) && Number.isFinite(Number(preScreenScore)) && Number(preScreenScore) < thresholdRoleValue;
                  return (
                    <div
                      key={application.id}
                      className={`cand-row ${selected ? 'selected' : ''} ${belowThreshold ? 'below' : ''}`}
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

                      <button type="button" className="c-name text-left" onClick={() => setSelectedApplicationId(Number(application.id))}>
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
                      </button>

                      <div className="c-role">
                        {application.role_name || application.candidate_position || 'Role'}
                        <div className="r-meta">
                          {application.candidate_location || 'Location not captured'}
                          {roleApplicationCount > 1 ? ` · ${roleApplicationCount} applications` : ''}
                        </div>
                      </div>

                      <div className="c-cv" title={preScreenScore == null ? 'Not scored yet' : `${Math.round(Number(preScreenScore))}%`}>
                        <span className={`pct ${cvTone}`}>{preScreenScore == null ? '—' : `${Math.round(Number(preScreenScore))}%`}</span>
                        <div className="meter">
                          <i className={cvTone} style={{ width: `${Math.max(0, Math.min(100, Number(preScreenScore || 0)))}%` }} />
                          {hasThresholdRoleValue ? (
                            <span className="thr" style={{ left: `${Math.max(0, Math.min(100, thresholdRoleValue))}%` }} />
                          ) : null}
                        </div>
                      </div>

                      <div className={`c-score ${Number(taaliScore) >= 80 ? 'high' : Number(taaliScore) >= 60 ? 'mid' : Number.isFinite(Number(taaliScore)) ? 'low' : ''}`}>
                        {taaliScore != null ? Math.round(Number(taaliScore)) : <span className="dash">—</span>}
                        {taaliScore != null ? <span className="dash">/100</span> : null}
                        {application.workable_score_raw != null && taaliScore != null ? (
                          <span className="wk-pip">WK <b>{Math.round(Number(application.workable_score_raw))}</b></span>
                        ) : null}
                      </div>

                      <div>
                        <span className={`candidate-ai-pill ${aiSignal.tone}`}>
                          {aiSignal.label}{aiSignal.suffix ? ` · ${aiSignal.suffix}` : ''}
                        </span>
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

            {selectedApplication ? (
              <Panel className="candidate-decision-pane h-fit p-4">
                <div className="space-y-4">
                  <div
                    className="overflow-hidden rounded-[20px] border border-[var(--taali-line)] p-4"
                    style={{
                      background:
                        'radial-gradient(circle at top right, rgba(45,140,255,0.12), transparent 26%), linear-gradient(155deg, rgba(255,255,255,0.98), rgba(245,241,255,0.94))',
                    }}
                  >
                    <div className="flex items-start gap-3">
                      <CandidateAvatar
                        name={selectedApplication.candidate_name || selectedApplication.candidate_email}
                        imageUrl={selectedApplication.candidate_image_url}
                        size={44}
                      />
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-base font-semibold text-[var(--taali-text)]">
                            {selectedApplication.candidate_name || selectedApplication.candidate_email}
                          </p>
                          {selectedApplication.workable_sourced ? <WorkableTagSm /> : null}
                        </div>
                        <p className="text-xs text-[var(--taali-muted)]">{selectedApplication.candidate_email}</p>
                        <p className="text-xs text-[var(--taali-muted)]">
                          {selectedApplication.role_name || selectedApplication.candidate_position || 'Role'}
                        </p>
                      </div>
                    </div>

                    {selectedApplication.candidate_headline ? (
                      <p className="mt-3 text-xs text-[var(--taali-muted)]">{selectedApplication.candidate_headline}</p>
                    ) : null}
                    {selectedApplication.candidate_location ? (
                      <p className="mt-1 text-xs text-[var(--taali-muted)]">{selectedApplication.candidate_location}</p>
                    ) : null}
                    {selectedRoleApplicationCount > 1 ? (
                      <p className="mt-2 text-[11px] text-[var(--taali-muted)]">
                        This candidate has {selectedRoleApplicationCount} role applications in the current results.
                      </p>
                    ) : null}
                    {selectedApplication.candidate_summary ? (
                      <p className="mt-3 line-clamp-3 text-xs text-[var(--taali-muted)]">
                        {selectedApplication.candidate_summary}
                      </p>
                    ) : null}

                    <div className="mt-4 grid grid-cols-3 gap-2">
                      <div className="rounded-[16px] border border-[var(--taali-line)] bg-[var(--taali-surface-elevated)] px-3 py-3">
                        <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Pre-screen</div>
                        <div className="mt-1 font-mono text-sm font-semibold text-[var(--taali-text)]">
                          {formatScoreCell(resolvePreScreenScore(selectedApplication))}
                        </div>
                      </div>
                      <div className="rounded-[16px] border border-[var(--taali-line)] bg-[var(--taali-surface-elevated)] px-3 py-3">
                        <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Taali</div>
                        <div className="mt-1 font-mono text-sm font-semibold text-[var(--taali-text)]">
                          {formatScoreCell(resolveTaaliScore(selectedApplication))}
                        </div>
                      </div>
                      <div className="rounded-[16px] border border-[var(--taali-line)] bg-[var(--taali-surface-elevated)] px-3 py-3">
                        <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Workable</div>
                        <div className="mt-1 flex items-center gap-2">
                          {selectedApplication.workable_score_raw != null ? (
                            <WorkableScorePip value={selectedApplication.workable_score_raw} />
                          ) : (
                            <span className="font-mono text-sm font-semibold text-[var(--taali-text)]">—</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="grid gap-3 md:grid-cols-2">
                    <label className="block">
                      <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Stage</span>
                      <div className="flex items-center gap-2">
                        <Select
                          aria-label="Candidate stage"
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
                        <Select aria-label="Candidate outcome" value={pendingOutcome} onChange={(event) => setPendingOutcome(event.target.value)}>
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
                      {selectedOutcomeSyncAction === 'reject' ? (
                        <p className="mt-2 text-[11px] text-[var(--taali-muted)]">
                          Rejecting this Workable-linked candidate will also disqualify them in Workable. Any rejection email is sent by Workable&apos;s disqualification automation/template.
                        </p>
                      ) : null}
                      {selectedOutcomeSyncAction === 'reopen' ? (
                        <p className="mt-2 text-[11px] text-[var(--taali-muted)]">
                          Reopening this Workable-linked candidate will also revert the Workable disqualification.
                        </p>
                      ) : null}
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
                          Open standing report
                        </Button>
                        {selectedAssessmentId ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => onNavigate('candidate-detail', { candidateDetailAssessmentId: selectedAssessmentId })}
                          >
                            Open candidate detail
                          </Button>
                        ) : null}
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
                        {selectedEvents.map((event) => {
                          const formatted = formatTimelineEvent(event);
                          return (
                            <div key={event.id} className="rounded-[var(--taali-radius-control)] border border-[var(--taali-border-soft)] p-2">
                              <div className="flex items-center gap-2 text-[11px] text-[var(--taali-muted)]">
                                <CircleDot size={11} />
                                {formatDateTime(event.created_at)}
                              </div>
                              <p className="mt-1 text-xs font-semibold text-[var(--taali-text)]">
                                {formatted.title}
                              </p>
                              <p className="mt-0.5 text-xs text-[var(--taali-muted)]">
                                {formatted.detail}
                              </p>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 text-xs text-[var(--taali-muted)]">
                        <AlertCircle size={12} />
                        No activity yet.
                      </div>
                    )}
                  </div>

                  {loadingDetailId === selectedApplication.id && (
                    <div className="text-xs text-[var(--taali-muted)]">
                      <span className="inline-flex items-center gap-2"><Spinner size={12} />Refreshing candidate details...</span>
                    </div>
                  )}
                </div>
              </Panel>
            ) : null}
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
