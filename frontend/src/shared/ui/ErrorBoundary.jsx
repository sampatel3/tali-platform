import React from 'react';
import { ErrorBoundary as SharedErrorBoundary } from '@mainspring/ui';

// The error-catching machinery (getDerivedStateFromError / componentDidCatch /
// scoped-fallback support) is the shared @mainspring/ui primitive. Only the
// brand's default full-screen crash card stays local — it uses Taali's
// token-namespaced classes, which the substrate's generic card markup does not
// carry. Callers that pass their own `fallback` (e.g. a scoped scoring pane)
// keep that behaviour unchanged.
function TaaliDefaultFallback() {
  return (
    <div className="min-h-screen flex items-center justify-center px-6 py-10 bg-[var(--taali-bg)]">
      <div className="max-w-md w-full border-2 border-[var(--taali-border)] bg-[var(--taali-surface)] p-6 text-center">
        <h1 className="text-2xl font-bold text-[var(--taali-text)] mb-2">Something went wrong</h1>
        <p className="text-sm text-[var(--taali-muted)] mb-4">
          An unexpected error occurred. Refresh to try again, or contact support if the issue persists.
        </p>
        <button
          type="button"
          className="font-mono text-sm text-[var(--taali-purple)] underline underline-offset-2 hover:text-[var(--taali-purple-hover)]"
          onClick={() => window.location.reload()}
        >
          Refresh page
        </button>
      </div>
    </div>
  );
}

export function ErrorBoundary({ fallback, children }) {
  return (
    <SharedErrorBoundary fallback={fallback ?? <TaaliDefaultFallback />}>
      {children}
    </SharedErrorBoundary>
  );
}
