import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

// Standard React Router pattern — restore the scroll position on every
// path change so SPA navigations behave like full page loads. Lives at
// the router level (mounted inside <BrowserRouter />) so it fires for
// both <Link> clicks and programmatic navigate() calls.
export function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

export default ScrollToTop;
