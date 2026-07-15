// Agent-authored timeline payloads are untrusted input. Navigation affordances
// may target product routes, never a different origin or executable scheme.
export const safeInternalRoute = (value) => {
  const href = String(value || '').trim();
  if (!href.startsWith('/') || href.startsWith('//') || href.includes('\\')) return null;
  try {
    const base = new URL('https://taali.local');
    const parsed = new URL(href, base);
    return parsed.origin === base.origin
      ? `${parsed.pathname}${parsed.search}${parsed.hash}`
      : null;
  } catch {
    return null;
  }
};

export default safeInternalRoute;
