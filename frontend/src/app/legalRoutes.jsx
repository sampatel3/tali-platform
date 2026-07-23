import React, { Suspense } from 'react';
import { Route } from 'react-router-dom';

import { Spinner } from '../shared/ui/TaaliPrimitives';
import { PrivacyPage, SubprocessorsPage, TermsPage } from './lazyPages';

// Public, no-auth legal pages (/privacy, /terms, /subprocessors). Extracted as
// a Route fragment so AppShell's <Routes> stays close to its ratcheted line cap
// (`RATCHETED_SOURCE_LIMITS` in scripts/check-architecture.mjs) — React Router
// flattens the fragment's <Route> children as if they were declared inline.
// These pages make no API calls, so they are intentionally NOT listed in
// isProtectedRecruiterPath and render for logged-out visitors.

const legalFallback = (
  <div className="min-h-screen flex items-center justify-center">
    <Spinner size={28} />
  </div>
);

export const legalRoutes = (
  <>
    <Route
      path="/privacy"
      element={<Suspense fallback={legalFallback}><PrivacyPage /></Suspense>}
    />
    <Route
      path="/terms"
      element={<Suspense fallback={legalFallback}><TermsPage /></Suspense>}
    />
    <Route
      path="/subprocessors"
      element={<Suspense fallback={legalFallback}><SubprocessorsPage /></Suspense>}
    />
  </>
);

export default legalRoutes;
