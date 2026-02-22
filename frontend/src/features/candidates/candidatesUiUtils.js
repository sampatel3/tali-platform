export const parseCollection = (data) => (Array.isArray(data) ? data : (data?.items || []));
export const formatDateTime = (value) => (value ? new Date(value).toLocaleString() : '—');

export const trimOrUndefined = (value) => {
  const trimmed = String(value || '').trim();
  return trimmed.length > 0 ? trimmed : undefined;
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
  if (score == null) return null;
  const numeric = Number(score);
  if (!Number.isFinite(numeric) || numeric < 0) return null;
  const scaleHint = String(details?.score_scale || '').trim().toLowerCase();
  const normalized = scaleHint.includes('100')
    ? numeric
    : (numeric <= 10 ? numeric * 10 : numeric);
  return Math.max(0, Math.min(100, normalized));
};

export const formatCvScore100 = (score, details = null) => {
  const normalized = toCvScore100(score, details);
  if (normalized == null) return '—';
  const rounded = Math.round(normalized * 10) / 10;
  const text = Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1);
  return `${text}/100`;
};

export const cvScoreColor = (score, details = null) => {
  const normalized = toCvScore100(score, details);
  if (normalized == null) return 'var(--taali-muted)';
  if (normalized >= 75) return 'var(--taali-success)';
  if (normalized >= 55) return 'var(--taali-warning)';
  return 'var(--taali-danger)';
};
