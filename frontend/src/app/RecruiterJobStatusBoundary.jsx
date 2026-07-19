import { lazy, Suspense } from 'react';
import { useLocation } from 'react-router-dom';

import { useAuth } from '../context/AuthContext';
import { JobStatusProvider } from '../contexts/JobStatusContext';
import { isProtectedRecruiterPath } from './routePolicy';

const BackgroundJobsToaster = lazy(() =>
  import('../features/candidates/BackgroundJobsToaster').then((module) => ({
    default: module.BackgroundJobsToaster,
  })),
);

// Batch and sync discovery is recruiter-only infrastructure. Public, auth,
// candidate-share, and preview routes should not load or poll it.
export function RecruiterJobStatusBoundary({ children }) {
  const { isAuthenticated } = useAuth();
  const location = useLocation();
  const enabled = isAuthenticated
    && isProtectedRecruiterPath(location.pathname, location.search);

  if (!enabled) return children;
  return (
    <JobStatusProvider>
      {children}
      <Suspense fallback={null}>
        <BackgroundJobsToaster />
      </Suspense>
    </JobStatusProvider>
  );
}
