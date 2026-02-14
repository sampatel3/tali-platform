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

export const getErrorMessage = (err, fallback) => err?.response?.data?.detail || fallback;
