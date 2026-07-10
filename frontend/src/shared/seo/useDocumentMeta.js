import { useEffect } from 'react';

// Set a per-page document title (and optional og:title / description) for
// data-driven public pages (blog posts, public job pages, careers boards) that
// RouteMeta can't cover — RouteMeta only knows static routes, so it folds every
// /blog/:slug onto the /blog index meta. Call this once the page's data loads;
// the previous values are restored on unmount so the next route isn't stuck
// with a stale title.
export function useDocumentMeta({ title, description } = {}) {
  useEffect(() => {
    if (typeof document === 'undefined' || !title) return undefined;
    const prevTitle = document.title;
    const ogTitleEl = document.head.querySelector('meta[property="og:title"]');
    const descEl = document.head.querySelector('meta[name="description"]');
    const ogDescEl = document.head.querySelector('meta[property="og:description"]');
    const prevOgTitle = ogTitleEl?.getAttribute('content') ?? null;
    const prevDesc = descEl?.getAttribute('content') ?? null;
    const prevOgDesc = ogDescEl?.getAttribute('content') ?? null;

    document.title = title;
    if (ogTitleEl) ogTitleEl.setAttribute('content', title);
    if (description != null) {
      if (descEl) descEl.setAttribute('content', description);
      if (ogDescEl) ogDescEl.setAttribute('content', description);
    }

    return () => {
      document.title = prevTitle;
      if (ogTitleEl && prevOgTitle != null) ogTitleEl.setAttribute('content', prevOgTitle);
      if (descEl && prevDesc != null) descEl.setAttribute('content', prevDesc);
      if (ogDescEl && prevOgDesc != null) ogDescEl.setAttribute('content', prevOgDesc);
    };
  }, [title, description]);
}

export default useDocumentMeta;
