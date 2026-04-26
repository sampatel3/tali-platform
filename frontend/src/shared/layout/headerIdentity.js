export const formatHeaderOrgLabel = (value, fallback = 'No company') => {
  const raw = String(value || '').trim() || fallback;
  const label = raw
    .replace(/&/g, ' and ')
    .replace(/[^a-zA-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/_{2,}/g, '_')
    .toUpperCase();

  return label || String(fallback || 'No company').toUpperCase().replace(/\s+/g, '_');
};
