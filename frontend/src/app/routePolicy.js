const publicPrefixes = [
  '/login', '/register', '/forgot-password', '/reset-password', '/verify-email', '/accept-invite',
  '/demo', '/blog', '/developers', '/terms', '/privacy', '/c/', '/share/', '/report/',
  '/submittal/', '/assess/', '/assessment/', '/job/', '/careers/', '/intake/', '/unsubscribe/',
  '/outreach/thanks', '/showcase/',
];

const protectedExact = new Set([
  '/dashboard', '/home', '/jobs', '/requisitions', '/assessments', '/analytics',
  '/reporting', '/tasks', '/tasks/bespoke', '/candidate-detail',
]);

const protectedPrefixes = [
  '/analytics/', '/jobs/', '/assessments/', '/candidates/', '/settings', '/chat',
  '/tasks/', '/admin', '/ats-admin',
];

const isPublicCandidateSharePath = (pathname, search = '') => {
  if (pathname.startsWith('/c/')) return true;
  if (pathname.startsWith('/submittal/')) return true;
  if (pathname.startsWith('/unsubscribe/')) return true;
  if (pathname.startsWith('/outreach/thanks')) return true;
  const params = new URLSearchParams(search || '');
  const hasInterviewToken = params.get('view') === 'interview' && Boolean(String(params.get('k') || '').trim());
  if (pathname.startsWith('/candidates/') && hasInterviewToken) return true;
  return /^\/candidates\/shr_[^/]+$/.test(pathname);
};

export const isProtectedRecruiterPath = (pathname, search = '') => {
  if (isPublicCandidateSharePath(pathname, search)) return false;
  return protectedExact.has(pathname) || protectedPrefixes.some((prefix) => pathname.startsWith(prefix));
};

export const isPublicPath = (pathname = '') => {
  if (pathname === '/' || pathname === '/showcase') return true;
  if (pathname.endsWith('-preview') || pathname === '/landing-preview') return true;
  return publicPrefixes.some((prefix) => pathname.startsWith(prefix));
};
