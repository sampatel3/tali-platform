export const demoReportViewMode = (applicationKey, searchParams) => {
  if (String(applicationKey || '').trim() !== 'demo') return null;
  const params = searchParams instanceof URLSearchParams
    ? searchParams
    : new URLSearchParams(searchParams || '');
  return params.get('showcase') === '1'
    && params.get('view') === 'client'
    && params.get('k') === 'demo-token'
    ? 'client'
    : null;
};
