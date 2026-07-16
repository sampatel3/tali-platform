// Accept only same-origin absolute paths for post-auth redirects.
export function resolveSafeNextPath(rawValue) {
  if (typeof rawValue !== 'string') return '';
  const nextPath = rawValue.trim();
  if (!nextPath.startsWith('/') || nextPath.startsWith('//') || nextPath.includes('://')) {
    return '';
  }
  return nextPath;
}

export default resolveSafeNextPath;
