export const parseCollection = (data) => (Array.isArray(data) ? data : (data?.items || []));
export const formatDateTime = (value) => (value ? new Date(value).toLocaleString() : '—');

export const trimOrUndefined = (value) => {
  const trimmed = String(value || '').trim();
  return trimmed.length > 0 ? trimmed : undefined;
};

export const statusVariant = (status) => {
  const normalized = String(status || '').toLowerCase();
  if (normalized.includes('interview') || normalized.includes('review')) return 'purple';
  if (normalized.includes('reject') || normalized.includes('decline')) return 'warning';
  if (normalized.includes('offer') || normalized.includes('hired')) return 'success';
  return 'muted';
};

export const getErrorMessage = (err, fallback) => {
  const d = err?.response?.data?.detail;
  if (d != null) {
    if (typeof d === 'string') return d;
    if (Array.isArray(d) && d.length) return d[0]?.msg ?? d[0]?.loc?.join?.('. ') ?? String(d[0]);
  }
  return fallback;
};
