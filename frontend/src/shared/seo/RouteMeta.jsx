import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

// Per-route SEO metadata for the SPA.
//
// The static index.html head is optimized for the home page and is the only
// markup non-JS crawlers see. JS-capable crawlers (Googlebot, Bingbot) render
// the app, so this keeps their view correct as the route changes: a
// self-referential canonical, per-page title/description on the public
// marketing routes, and noindex on the signed-in app + auth flows (a
// belt-and-braces complement to public/robots.txt).
//
// Deliberately a standalone component rendered inside the router rather than
// logic in App.jsx, which is size- and behaviour-gated by the architecture
// check.

const ORIGIN = 'https://www.taali.ai';
const INDEXABLE_ROBOTS =
  'index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1';
const NOINDEX_ROBOTS = 'noindex, nofollow';

// Public, indexable routes. Anything not listed here is treated as part of
// the signed-in app (or an auth flow) and marked noindex.
const PUBLIC_META = {
  '/': {
    title: 'Taali — Agentic Hiring Platform & AI-Native Assessments',
    description:
      'Taali is the agentic hiring platform for AI-native teams. An autonomous agent runs your engineering pipeline 24/7, and AI-native assessments score how candidates actually use AI on the job.',
  },
  '/demo': {
    title: 'Product walkthrough — Taali agentic hiring',
    description:
      'Take an interactive, no-signup walkthrough of Taali: the autonomous hiring agent and the AI-native assessment runtime that scores how candidates use AI.',
  },
  '/demo-lead': {
    title: 'See Taali run — interactive walkthrough',
    description:
      'Take the interactive Taali walkthrough — the agentic hiring platform with AI-native assessments. No call, no card. We follow up by email.',
  },
  '/developers': {
    title: 'Developer Portal — Taali API',
    description:
      'Read Taali API documentation, authentication guidance, endpoint references, scopes, errors, and changelog details for building Taali integrations.',
  },
  '/terms': {
    title: 'Terms of Service — Taali',
    description: 'Terms governing use of the Taali hiring platform and website.',
  },
  '/privacy': {
    title: 'Privacy Notice — Taali',
    description: 'How Taali collects, uses, protects, retains, and shares personal data.',
  },
  '/blog': {
    title: 'Taali Blog — AI-native work',
    description:
      'Writing from Taali on AI-native work, agentic hiring, and how high-performing teams use AI in engineering and beyond.',
  },
};

// /showcase renders the same page as /demo; /demo-walkthrough is the legacy
// walkthrough. Treat them as the demo for metadata purposes.
const ALIAS = {
  '/showcase': '/demo',
  '/demo-walkthrough': '/demo',
};

const normalizePath = (pathname) => {
  if (!pathname || pathname === '/') return '/';
  return pathname.replace(/\/+$/, '') || '/';
};

const resolvePublicMetaPath = (path) => {
  if (path.startsWith('/blog/')) return '/blog';
  return ALIAS[path] || path;
};

const upsertMeta = (selector, attrs) => {
  let el = document.head.querySelector(selector);
  if (!el) {
    el = document.createElement('meta');
    for (const [k, v] of Object.entries(attrs)) {
      if (k !== 'content') el.setAttribute(k, v);
    }
    document.head.appendChild(el);
  }
  if (attrs.content != null) el.setAttribute('content', attrs.content);
  return el;
};

const upsertCanonical = (href) => {
  let el = document.head.querySelector('link[rel="canonical"]');
  if (!el) {
    el = document.createElement('link');
    el.setAttribute('rel', 'canonical');
    document.head.appendChild(el);
  }
  el.setAttribute('href', href);
};

export function RouteMeta() {
  const { pathname } = useLocation();

  useEffect(() => {
    if (typeof document === 'undefined') return;

    const path = normalizePath(pathname);
    const resolved = resolvePublicMetaPath(path);
    const meta = PUBLIC_META[resolved];
    // Articles inherit the blog's generic metadata until BlogPostPage replaces
    // title/description, but each article must remain self-canonical. Aliases
    // such as /showcase intentionally canonicalize to their primary route.
    const canonicalPath = path.startsWith('/blog/')
      ? path
      : (resolved === '/' ? '/' : resolved);
    const canonicalUrl = `${ORIGIN}${canonicalPath}`;

    upsertCanonical(canonicalUrl);
    upsertMeta('meta[property="og:url"]', { property: 'og:url', content: canonicalUrl });

    if (meta) {
      document.title = meta.title;
      upsertMeta('meta[name="description"]', { name: 'description', content: meta.description });
      upsertMeta('meta[name="robots"]', { name: 'robots', content: INDEXABLE_ROBOTS });
      upsertMeta('meta[property="og:title"]', { property: 'og:title', content: meta.title });
      upsertMeta('meta[property="og:description"]', {
        property: 'og:description',
        content: meta.description,
      });
    } else {
      // Signed-in app or auth flow — keep it out of the index. The page's own
      // title is left to the feature that owns it.
      upsertMeta('meta[name="robots"]', { name: 'robots', content: NOINDEX_ROBOTS });
    }
  }, [pathname]);

  return null;
}

export default RouteMeta;
