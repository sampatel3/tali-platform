const normalizeOrgKey = (value = '') => String(value || '')
  .trim()
  .replace(/&/g, ' and ')
  .replace(/[^a-zA-Z0-9]+/g, '_')
  .replace(/^_+|_+$/g, '')
  .replace(/_{2,}/g, '_')
  .toLowerCase();

const KNOWN_HEADER_ORG_NAMES = new Map([
  ['deeplight', 'DeepLight AI'],
  ['deeplight_ai', 'DeepLight AI'],
  ['deeplightai', 'DeepLight AI'],
  ['deep_light_ai', 'DeepLight AI'],
]);

const humanizeOrgName = (value = '') => {
  const raw = String(value || '').trim();
  const knownName = KNOWN_HEADER_ORG_NAMES.get(normalizeOrgKey(raw));
  if (knownName) return knownName;

  const cleaned = raw.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (!cleaned) return '';

  const shouldHumanize = /[_-]/.test(raw) || (cleaned === cleaned.toUpperCase() && /[A-Z]/.test(cleaned));
  if (!shouldHumanize) return cleaned;

  return cleaned.split(' ').map((word) => {
    const upper = word.toUpperCase();
    if (upper.length <= 3) return upper;
    return `${word.charAt(0).toUpperCase()}${word.slice(1).toLowerCase()}`;
  }).join(' ');
};

export const normalizeHeaderOrgName = (value, fallback = 'No company') => {
  const fallbackName = humanizeOrgName(fallback) || 'No company';
  const raw = String(value || '').trim();
  if (!raw) return fallbackName;
  return humanizeOrgName(raw) || fallbackName;
};

export const formatHeaderOrgLabel = (value, fallback = 'No company') => normalizeHeaderOrgName(value, fallback);
