// Preview surfaces — the public /demo and the pitch-deck iframes — render the
// real app chrome but have no auth session. Any nav that escapes to an
// auth-gated route lands the iframe on the sign-in page. This predicate marks
// those surfaces so the nav chrome (Shell header, breadcrumbs) can render
// non-interactive there, while the page content stays fully interactive.
//
// Detected surfaces: /showcase/*, public candidate shares /c/*, and any route
// loaded with ?showcase=1 (e.g. /jobs, /candidates, /assessment/live).
export const isPreviewNavSurface = () => {
  if (typeof window === 'undefined') return false;
  const { pathname, search } = window.location;
  if (pathname.startsWith('/showcase/') || pathname.startsWith('/c/')) return true;
  return new URLSearchParams(search || '').get('showcase') === '1';
};

export default isPreviewNavSurface;
