import { formatScale100Score, normalizeScore, scoreTone100 } from '../../lib/scoreDisplay';

export const parseCollection = (data) => (Array.isArray(data) ? data : (data?.items || []));
export const formatDateTime = (value) => (value ? new Date(value).toLocaleString() : '—');

export const trimOrUndefined = (value) => {
  const trimmed = String(value || '').trim();
  return trimmed.length > 0 ? trimmed : undefined;
};

export const normalizeStatusKey = (value) => String(value || '')
  .trim()
  .toLowerCase()
  .replace(/[_-]+/g, ' ')
  .replace(/\s+/g, ' ');

export const formatStatusLabel = (value) => {
  const normalized = normalizeStatusKey(value);
  if (!normalized) return '—';
  return normalized
    .split(' ')
    .map((chunk) => chunk.charAt(0).toUpperCase() + chunk.slice(1))
    .join(' ');
};

export const buildApplicationStatusMeta = (status, workableStage) => {
  const pipelineStatus = trimOrUndefined(status);
  const workable = trimOrUndefined(workableStage);
  const items = [];

  if (pipelineStatus) {
    items.push({
      label: 'Pipeline status',
      value: formatStatusLabel(pipelineStatus),
    });
  }

  if (workable && normalizeStatusKey(workable) !== normalizeStatusKey(pipelineStatus)) {
    items.push({
      label: 'Workable stage',
      value: formatStatusLabel(workable),
    });
  }

  return items;
};

export const statusVariant = (status) => {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'pending') return 'muted';
  if (normalized === 'in_progress' || normalized === 'completed_due_to_timeout') return 'warning';
  if (normalized === 'completed') return 'purple';
  if (normalized === 'expired') return 'danger';
  if (normalized.includes('interview') || normalized.includes('review')) return 'purple';
  if (normalized.includes('reject') || normalized.includes('decline')) return 'warning';
  if (normalized.includes('offer') || normalized.includes('hired')) return 'success';
  return 'muted';
};

export const getErrorMessage = (err, fallback) => {
  const d = err?.response?.data?.detail;
  if (d != null) {
    if (typeof d === 'string') return d;
    if (Array.isArray(d) && d.length) {
      const first = d[0] || {};
      const msg = first?.msg ?? String(first);
      const locParts = Array.isArray(first?.loc)
        ? first.loc.filter((segment) => String(segment).toLowerCase() !== 'body')
        : [];
      if (locParts.length) {
        const loc = locParts.join('.').replace(/_/g, ' ');
        return `${loc}: ${msg}`;
      }
      return msg;
    }
  }
  return fallback;
};

export const toCvScore100 = (score, details = null) => {
  return normalizeScore(score, details?.score_scale || '');
};

export const formatCvScore100 = (score, details = null) => {
  return formatScale100Score(score, details?.score_scale || '');
};

export const cvScoreColor = (score, details = null) => {
  return scoreTone100(toCvScore100(score, details));
};

// Picks the best primary score for a row. Returns { score, details } where
// score is null when nothing is available. Mirrors the candidate-table cell
// resolution: pre-screen > taali > cv_match.
export const getPrimaryScorePayload = (application) => {
  if (!application) return { score: null, details: null };
  if (typeof application.pre_screen_score === 'number') {
    return { score: application.pre_screen_score, details: { score_scale: '0-100' } };
  }
  if (typeof application.taali_score === 'number') {
    return { score: application.taali_score, details: { score_scale: '0-100' } };
  }
  if (typeof application.score_summary?.taali_score === 'number') {
    return { score: application.score_summary.taali_score, details: { score_scale: '0-100' } };
  }
  if (typeof application.cv_match_score === 'number') {
    return { score: application.cv_match_score, details: application.cv_match_details };
  }
  return { score: null, details: null };
};

// Renders the cell text shown in the "Pre-screen" column. Active scoring
// jobs (pending/running) take precedence over a stale prior score so the
// recruiter visibly sees an in-flight rescore rather than an old number.
export const renderPrimaryScoreCell = (application) => {
  const payload = getPrimaryScorePayload(application);
  const status = application?.score_status;
  if (status === 'pending' || status === 'running') {
    return 'Scoring…';
  }
  if (typeof payload.score === 'number') {
    if (status === 'stale') {
      return `${formatCvScore100(payload.score, payload.details)} · out of date`;
    }
    return formatCvScore100(payload.score, payload.details);
  }
  if (status === 'error') return 'Score error';
  if (status === 'stale') return 'Out of date';
  if (!application?.cv_filename) return '—';
  return 'Pending';
};

// ---------------------------------------------------------------------------
// CV match details resolution + per-requirement evidence extraction
//
// Three prompt versions can write into the candidate-application JSON blob,
// each with a different field name:
//   - cv_match_v3.0  → application.cv_match_details          (current)
//   - cv_match_v4    → application.cv_job_match_details      (legacy)
//   - free-text v3   → application.cv_job_match_details with `evidence` instead of `cv_quote`/`evidence_quote`
//
// These helpers normalize over all three so the candidate page renders
// correctly during cutover. Pure functions; tested in candidatesUiUtils.test.js.
// ---------------------------------------------------------------------------

export const resolveCvMatchDetails = ({
  application,
  completedAssessment,
  fallback,
} = {}) => {
  const empty = {};
  const candidate = (
    completedAssessment?.cv_job_match_details
    || application?.cv_match_details                  // v3 (current)
    || application?.cv_job_match_details              // v4 / legacy
    || fallback
    || empty
  );
  return candidate && typeof candidate === 'object' ? candidate : empty;
};

export const extractRequirementEvidence = (item) => {
  if (!item || typeof item !== 'object') return '';
  return String(
    item.evidence_quote
    || item.cv_quote
    || item.evidence
    || ''
  ).trim();
};

export const extractRequirementKey = (item, fallbackIndex = 0) => {
  if (!item) return String(fallbackIndex);
  if (item.requirement_id != null) return String(item.requirement_id);
  if (item.criterion_id != null) return String(item.criterion_id);
  const label = (item.requirement || '').toString();
  return label ? `${label}-${fallbackIndex}` : String(fallbackIndex);
};
