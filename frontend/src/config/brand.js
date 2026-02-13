export const BRAND = {
  name: 'TALI',
  domain: 'tali.dev',
  productTagline: 'AI-augmented technical assessments for modern engineering teams.',
  appTitle: 'AI-Augmented Technical Assessments',
};

export const getDocumentTitle = (pageLabel) => {
  if (!pageLabel) return `${BRAND.name} - ${BRAND.appTitle}`;
  return `${pageLabel} | ${BRAND.name}`;
};
