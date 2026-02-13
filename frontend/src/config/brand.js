export const BRAND = {
  name: 'TAALI',
  domain: 'taali.ai',
  productTagline: 'Arabic-inspired, tally-precise technical assessments for modern engineering teams.',
  appTitle: 'AI Technical Assessments That Tally Real Skill',
};

export const getDocumentTitle = (pageLabel) => {
  if (!pageLabel) return `${BRAND.name} - ${BRAND.appTitle}`;
  return `${pageLabel} | ${BRAND.name}`;
};
