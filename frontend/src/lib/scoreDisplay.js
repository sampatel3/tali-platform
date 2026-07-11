export const normalizeScore = (value, scaleHint = '') => {
  // Number(null) === 0 passes Number.isFinite, so an explicit JSON null would
  // normalise to 0 (unscored candidates reading as a genuine 0/100). Guard the
  // nullish case first — same pattern as fluency4d's num().
  if (value == null) return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;

  const normalizedHint = String(scaleHint || '').trim().toLowerCase();
  if (normalizedHint.includes('100')) {
    return Math.max(0, Math.min(100, numeric));
  }

  if (normalizedHint.includes('10')) {
    return Math.max(0, Math.min(10, numeric));
  }

  if (numeric <= 10) {
    return Math.max(0, Math.min(100, numeric * 10));
  }

  return Math.max(0, Math.min(100, numeric));
};

const formatNumeric = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  return numeric.toFixed(1);
};

export const formatScale100Score = (value, scaleHint = '') => {
  const normalized = normalizeScore(value, scaleHint);
  return formatNumeric(normalized);
};

// HANDOFF v2 §6 — every score in the recruiter app is 0–100. The
// formatScale10Score / scoreTone10 helpers were retired with that
// migration; see scoreTone100 for the canonical tone bucket.
export const scoreTone100 = (value) => {
  const numeric = normalizeScore(value, '0-100');
  if (numeric == null) return 'var(--taali-muted)';
  if (numeric >= 75) return 'var(--taali-success)';
  if (numeric >= 55) return 'var(--taali-warning)';
  return 'var(--taali-danger)';
};
