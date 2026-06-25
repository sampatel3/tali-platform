import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

import { isPreviewNavSurface } from '../../lib/previewNav';

// Preview surfaces — the public /demo and the pitch-deck iframes — render the
// real, auth-bearing app with NO auth session. Any click that navigates to a
// DIFFERENT in-app page hits an auth-gated route and bounces the iframe to the
// sign-in page (Sam: "I could click Home and go to the sign-in page"). Locking
// each link per-component is whack-a-mole (header, breadcrumb, candidate cards,
// role cards, …), so this guard blocks cross-page link navigation centrally.
//
// It runs in the capture phase, so it also cancels React Router <Link>s (their
// onClick sees defaultPrevented and skips the history push). It deliberately
// leaves alone: same-page tab/query/hash changes (e.g. ?tab=notes), external
// links, and anything that isn't an <a href> — so the surfaces stay interactive.
export const PreviewNavGuard = () => {
  const location = useLocation();
  useEffect(() => {
    if (!isPreviewNavSurface()) return undefined;
    const onClick = (event) => {
      const target = event.target;
      const anchor = target && target.closest ? target.closest('a[href]') : null;
      if (!anchor) return;
      let url;
      try {
        url = new URL(anchor.getAttribute('href'), window.location.origin);
      } catch {
        return;
      }
      if (url.origin !== window.location.origin) return; // external link — allow
      if (url.pathname === window.location.pathname) return; // same page (tabs/query) — allow
      // Cross-page in-app navigation → would escape to an auth-gated route.
      event.preventDefault();
      event.stopPropagation();
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, [location.pathname, location.search]);
  return null;
};

export default PreviewNavGuard;
