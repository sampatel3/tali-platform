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
