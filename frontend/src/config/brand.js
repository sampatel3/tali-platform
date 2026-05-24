export const BRAND = {
  name: 'Taali',
  wordmark: 'taali',
  domain: 'taali.ai',
  productTagline: 'Agentic hiring that assesses how engineers use AI.',
  appTitle: 'Agentic hiring & AI-fluency assessments',
};

export const getDocumentTitle = (pageLabel) => {
  if (!pageLabel) return `${BRAND.name} — ${BRAND.appTitle}`;
  return `${pageLabel} | ${BRAND.name}`;
};
