import { applicationFunnelBucket } from '../../shared/metrics';

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
  if (!hasConfiguredGranularAutomation(role)) return true;
  const effective = role?.agent_effective_policy || {};
  if (effective[key] != null) return Boolean(effective[key]);
  if (role?.[key] != null) return Boolean(role[key]);
  return Boolean(role?.auto_promote);
};

export const resolvedDeterministicReject = (role) => Boolean(
  role?.agent_effective_policy?.auto_reject_pre_screen
  ?? role?.auto_reject_pre_screen
) || Boolean(role?.auto_reject);

export const activationAutonomyPayload = (role) => {
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
  return DECISION_LABELS[key] || key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
};
