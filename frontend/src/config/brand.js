export const BRAND = {
  name: 'TAALI',
  domain: 'taali.ai',
  productTagline: 'Technical assessments for AI-native engineering teams.',
  appTitle: 'AI Technical Assessments That Tally Real Skill',
};

export const getDocumentTitle = (pageLabel) => {
  if (!pageLabel) return `${BRAND.name} - ${BRAND.appTitle}`;
  return `${pageLabel} | ${BRAND.name}`;
};
