import { prefetchDocumentBlob } from '../../shared/api/documentCache';
import { applicationFunnelBucket } from '../../shared/metrics';

export const EMPTY_PROGRESS = {
  status: 'idle', total: 0, scored: 0, errors: 0, include_scored: false,
};
export const EMPTY_FETCH_PROGRESS = {
  status: 'idle', total: 0, fetched: 0, errors: 0,
};
export const EMPTY_PRE_SCREEN_PROGRESS = {
  status: 'idle', total: 0, processed: 0, errors: 0, refresh: false,
};

// Mirror of backend settings.PRE_SCREEN_THRESHOLD (config.py). Candidates
// below this cutoff were screened out and should not be re-scored by default.
export const PRE_SCREEN_FILTER_THRESHOLD = 30;

export const summarizeUnscoredApplications = (applications) => {
  let scoreable = 0;
  let preScreenFiltered = 0;
  let noCv = 0;
  for (const application of applications) {
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
};

export const PIPELINE_STAGE_ORDER = [
  { key: 'sourced', label: 'Sourced' },
  { key: 'applied', label: 'Applied' },
  { key: 'scored', label: 'Scored' },
  { key: 'invited', label: 'Invited' },
  { key: 'advanced', label: 'Advanced' },
];

export const matchesPipelineStage = (application, stageKey) => {
  const bucket = applicationFunnelBucket(application);
  return stageKey === 'invited'
    ? bucket === 'invited' || bucket === 'completed'
    : bucket === stageKey;
};

// Only prefetch a CV after hover intent and cap concurrent downloads.
const HOVER_INTENT_MS = 200;
const HOVER_PREFETCH_MAX = 3;
let hoverPrefetchActive = 0;

export const makeHoverPrefetch = () => {
  let timer = null;
  const start = (applicationId) => {
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      timer = null;
      if (hoverPrefetchActive >= HOVER_PREFETCH_MAX) return;
      hoverPrefetchActive += 1;
      Promise.resolve(prefetchDocumentBlob({ applicationId, docType: 'cv' }))
        .catch(() => {})
        .finally(() => {
          hoverPrefetchActive = Math.max(0, hoverPrefetchActive - 1);
        });
    }, HOVER_INTENT_MS);
  };
  const cancel = () => {
    if (timer) {
      window.clearTimeout(timer);
      timer = null;
    }
  };
  return { start, cancel };
};

export const normalizeThreshold = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '';
  return String(Math.max(0, Math.min(100, Math.round(numeric))));
};

export const formatRelativeShort = (value) => {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  const minutes = Math.round((Date.now() - parsed.getTime()) / 60000);
  if (minutes < 60) return `${Math.max(1, minutes)}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
};

export const buildApplicationTitle = (application) => (
  application?.candidate_name
  || application?.candidate_email
  || `Candidate #${application?.candidate_id || application?.id || '—'}`
);

export const resolveOptionalPercent = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.max(0, Math.min(100, Math.round(numeric)));
};

const PIPELINE_STAGE_LABELS = {
  sourced: 'Sourced',
  applied: 'Applied',
  invited: 'Invited',
  in_assessment: 'In assessment',
  review: 'Review',
  advanced: 'Advanced',
};

export const formatStageLabel = (stage) => (
  PIPELINE_STAGE_LABELS[stage]
  || (stage ? stage.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : '—')
);

export const GRANULAR_AUTOMATION_KEYS = Object.freeze([
  'auto_send_assessment',
  'auto_resend_assessment',
  'auto_advance',
]);

const hasConfiguredGranularAutomation = (role) => GRANULAR_AUTOMATION_KEYS
  .some((key) => role?.[key] != null);

export const resolvedRoleAutomation = (role, key) => {
  const effective = role?.agent_effective_policy || {};
  if (effective[key] != null) return Boolean(effective[key]);
  if (role?.[key] != null) return Boolean(role[key]);
  // Nullable fields identify roles created before action-level controls were
  // introduced. Their historical aggregate remains authoritative until the
  // recruiter explicitly materializes a granular policy.
  return Boolean(role?.auto_promote);
};

export const resolvedDeterministicReject = (role) => {
  const configured = role?.agent_effective_policy?.auto_reject_pre_screen
    ?? role?.auto_reject_pre_screen;
  return configured == null ? true : Boolean(configured);
};

export const resolvedRoleAutoSkipAssessment = (role) => {
  const effective = role?.agent_effective_policy?.auto_skip_assessment;
  return effective == null ? Boolean(role?.auto_skip_assessment) : Boolean(effective);
};

export const hasActiveAssessmentTask = (tasks) => (
  Array.isArray(tasks) && tasks.some((task) => task?.is_active === true)
);

export const resolvedScoredReject = (role) => Boolean(
  role?.agent_effective_policy?.auto_reject ?? role?.auto_reject
);

export const activationAutonomyPayload = (role) => {
  // Do not rewrite a legacy all-null policy merely because the recruiter turns
  // the Agent on. The backend resolves those rows through auto_promote and
  // preserves their existing power. New roles already store concrete safe
  // granular defaults.
  if (!hasConfiguredGranularAutomation(role)) return {};
  const payload = {};
  for (const key of GRANULAR_AUTOMATION_KEYS) {
    if (role?.[key] != null) payload[key] = Boolean(role[key]);
  }
  payload.auto_promote = GRANULAR_AUTOMATION_KEYS.every((key) => (
    role?.[key] != null ? Boolean(role[key]) : Boolean(role?.auto_promote)
  ));
  return payload;
};

const DECISION_LABELS = {
  advance_to_interview: 'Advance to interview',
  send_assessment: 'Send assessment',
  resend_assessment_invite: 'Resend invite',
  reject: 'Reject',
  skip_assessment_reject: 'Reject',
  escalate_low_confidence: 'Needs your review',
};

export const formatDecisionLabel = (recommendation) => {
  const key = String(recommendation || '').toLowerCase();
  if (!key) return null;
  return DECISION_LABELS[key]
    || key.replace(/_/g, ' ').replace(/^./, (character) => character.toUpperCase());
};
