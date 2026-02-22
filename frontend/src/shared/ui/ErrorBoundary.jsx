import React from 'react';

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    // Keep this visible in dev/prod console until centralized logging is wired.
    // eslint-disable-next-line no-console
    console.error('ErrorBoundary caught:', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex items-center justify-center px-6 py-10 bg-[var(--taali-bg)]">
          <div className="max-w-md w-full border-2 border-[var(--taali-border)] bg-[var(--taali-surface)] p-6 text-center">
            <h1 className="text-2xl font-bold text-[var(--taali-text)] mb-2">Something went wrong</h1>
            <p className="text-sm text-[var(--taali-muted)] mb-4">
              An unexpected error occurred. Refresh to try again, or contact support if the issue persists.
            </p>
            <button
              type="button"
              className="font-mono text-sm text-[var(--taali-purple)] underline underline-offset-2 hover:text-[var(--taali-primary)]"
              onClick={() => window.location.reload()}
            >
              Refresh page
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

