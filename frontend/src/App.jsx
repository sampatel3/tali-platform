import React, { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from 'react-router-dom';
import { Loader2 } from 'lucide-react';

import { useAuth } from './context/AuthContext';
import { assessments as assessmentsApi } from './shared/api';
import { pathForPage } from './app/routing';

import { LandingPage } from './features/marketing/LandingPage';
import {
  ForgotPasswordPage,
  LoginPage,
  RegisterPage,
  ResetPasswordPage,
  VerifyEmailPage,
} from './features/auth';
import { DashboardNav } from './features/dashboard/DashboardNav';
import { AnalyticsPage } from './features/analytics/AnalyticsPage';
import { CandidateWelcomePage } from './features/assessment_runtime/CandidateWelcomePage';
import {
  ConnectWorkableButton,
  WorkableCallbackPage,
} from './features/integrations/WorkableConnection';
import { StatsCard, StatusBadge } from './shared/ui/DashboardAtoms';

const AssessmentPage = lazy(() => import('./features/assessment_runtime/AssessmentPage'));
const DemoExperiencePage = lazy(() =>
  import('./features/demo/DemoExperiencePage').then((m) => ({ default: m.DemoExperiencePage }))
);
const LazyCandidateDetailPage = lazy(() =>
  import('./features/candidates/CandidateDetailPage').then((m) => ({ default: m.CandidateDetailPage }))
);
const DashboardPage = lazy(() =>
  import('./features/dashboard/DashboardPage').then((m) => ({ default: m.DashboardPage }))
);
const CandidatesPage = lazy(() =>
  import('./features/candidates/CandidatesPage').then((m) => ({ default: m.CandidatesPage }))
);
const TasksPage = lazy(() =>
  import('./features/tasks/TasksPage').then((m) => ({ default: m.TasksPage }))
);
const SettingsPage = lazy(() =>
  import('./features/settings/SettingsPage').then((m) => ({ default: m.SettingsPage }))
);

function AppContent() {
  const { isAuthenticated, loading: authLoading } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [loadingCandidateDetail, setLoadingCandidateDetail] = useState(false);
  const [startedAssessmentData, setStartedAssessmentData] = useState(null);

  const candidateDetailAssessmentId = searchParams.get('assessmentId')
    ? Number(searchParams.get('assessmentId'))
    : null;

  const assessmentIdFromLink = useMemo(() => {
    const m = location.pathname.match(/^\/assessment\/(\d+)$/);
    return m ? Number(m[1]) : null;
  }, [location.pathname]);

  const activeAssessmentToken = useMemo(() => {
    const fromAssessPath = location.pathname.match(/^\/assess\/(.+)$/);
    if (fromAssessPath?.[1]) {
      return decodeURIComponent(fromAssessPath[1]);
    }
    if (assessmentIdFromLink) {
      return searchParams.get('token') || null;
    }
    if (location.pathname === '/assessment/live') {
      return searchParams.get('token') || null;
    }
    return null;
  }, [assessmentIdFromLink, location.pathname, searchParams]);

  const resetPasswordToken = location.pathname === '/reset-password'
    ? (searchParams.get('token') || '')
    : '';
  const verifyEmailToken = location.pathname === '/verify-email'
    ? (searchParams.get('token') || '')
    : '';

  const mapAssessmentToCandidateView = (assessment) => ({
    id: assessment.id,
    name: (assessment.candidate_name || assessment.candidate?.full_name || assessment.candidate_email || '').trim() || 'Unknown',
    email: assessment.candidate_email || assessment.candidate?.email || '',
    task: assessment.task_name || assessment.task?.name || 'Assessment',
    status: assessment.status || 'pending',
    score: assessment.score ?? assessment.overall_score ?? null,
    time: assessment.duration_taken ? `${Math.round(assessment.duration_taken / 60)}m` : '—',
    position: assessment.role_name || assessment.candidate?.position || '',
    completedDate: assessment.completed_at ? new Date(assessment.completed_at).toLocaleDateString() : null,
    breakdown: assessment.breakdown || null,
    prompts: assessment.prompt_count ?? 0,
    promptsList: assessment.prompts_list || [],
    timeline: assessment.timeline || [],
    results: assessment.results || [],
    token: assessment.token,
    _raw: assessment,
  });

  useEffect(() => {
    setStartedAssessmentData(null);
  }, [activeAssessmentToken]);

  useEffect(() => {
    if (isAuthenticated && ['/', '/login', '/forgot-password'].includes(location.pathname)) {
      navigate('/dashboard', { replace: true });
    }
  }, [isAuthenticated, location.pathname, navigate]);

  useEffect(() => {
    if (
      !authLoading &&
      !isAuthenticated &&
      ['/dashboard', '/candidates', '/analytics', '/settings', '/tasks', '/candidate-detail'].includes(location.pathname)
    ) {
      navigate('/', { replace: true });
    }
  }, [isAuthenticated, authLoading, location.pathname, navigate]);

  const navigateToPage = (page, options = {}) => {
    const nextPath = pathForPage(page, {
      assessmentToken: Object.prototype.hasOwnProperty.call(options, 'assessmentToken')
        ? options.assessmentToken
        : activeAssessmentToken,
      assessmentIdFromLink: Object.prototype.hasOwnProperty.call(options, 'assessmentIdFromLink')
        ? options.assessmentIdFromLink
        : assessmentIdFromLink,
      candidateDetailAssessmentId: Object.prototype.hasOwnProperty.call(options, 'candidateDetailAssessmentId')
        ? options.candidateDetailAssessmentId
        : candidateDetailAssessmentId,
      resetPasswordToken: Object.prototype.hasOwnProperty.call(options, 'resetPasswordToken')
        ? options.resetPasswordToken
        : resetPasswordToken,
      verifyEmailToken: Object.prototype.hasOwnProperty.call(options, 'verifyEmailToken')
        ? options.verifyEmailToken
        : verifyEmailToken,
    });

    if (nextPath) {
      navigate(nextPath, { replace: Boolean(options.replace) });
    }
    window.scrollTo(0, 0);
  };

  const handleCandidateStarted = (startData) => {
    setStartedAssessmentData(startData);
  };

  const navigateToCandidate = (candidate) => {
    setSelectedCandidate(candidate);
    navigateToPage('candidate-detail', {
      candidateDetailAssessmentId: candidate?.id || candidate?._raw?.id || null,
    });
  };

  useEffect(() => {
    if (location.pathname !== '/candidate-detail' || !candidateDetailAssessmentId || !isAuthenticated) {
      return;
    }
    if (selectedCandidate && Number(selectedCandidate.id) === Number(candidateDetailAssessmentId)) {
      setLoadingCandidateDetail(false);
      return;
    }

    let cancelled = false;
    setLoadingCandidateDetail(true);
    assessmentsApi.get(candidateDetailAssessmentId)
      .then((res) => {
        if (cancelled) return;
        setSelectedCandidate(mapAssessmentToCandidateView(res.data || {}));
        setLoadingCandidateDetail(false);
      })
      .catch(() => {
        if (cancelled) return;
        setSelectedCandidate(null);
        setLoadingCandidateDetail(false);
      });

    return () => {
      cancelled = true;
    };
  }, [location.pathname, candidateDetailAssessmentId, isAuthenticated, selectedCandidate]);

  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 size={32} className="animate-spin" style={{ color: '#9D00FF' }} />
      </div>
    );
  }

  const lazyFallback = (
    <div className="min-h-screen flex items-center justify-center">
      <Loader2 size={28} className="animate-spin" style={{ color: '#9D00FF' }} />
    </div>
  );

  const CandidateWelcomeRoute = () => {
    const { token } = useParams();
    return (
      <CandidateWelcomePage
        token={token || null}
        assessmentId={null}
        onNavigate={navigateToPage}
        onStarted={handleCandidateStarted}
      />
    );
  };

  const CandidateWelcomeWithIdRoute = () => {
    const { assessmentId } = useParams();
    const token = searchParams.get('token');
    if (!token) return <Navigate to="/" replace />;
    return (
      <CandidateWelcomePage
        token={token}
        assessmentId={Number(assessmentId)}
        onNavigate={navigateToPage}
        onStarted={handleCandidateStarted}
      />
    );
  };

  const AssessmentLiveRoute = () => {
    const token = searchParams.get('token');
    return (
      <Suspense fallback={lazyFallback}>
        <AssessmentPage token={token} startData={startedAssessmentData} />
      </Suspense>
    );
  };

  return (
    <Routes>
      <Route path="/" element={<LandingPage onNavigate={navigateToPage} />} />
      <Route
        path="/demo"
        element={(
          <Suspense fallback={lazyFallback}>
            <DemoExperiencePage onNavigate={navigateToPage} />
          </Suspense>
        )}
      />
      <Route path="/login" element={<LoginPage onNavigate={navigateToPage} />} />
      <Route path="/register" element={<RegisterPage onNavigate={navigateToPage} />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage onNavigate={navigateToPage} />} />
      <Route path="/reset-password" element={<ResetPasswordPage onNavigate={navigateToPage} token={resetPasswordToken} />} />
      <Route path="/verify-email" element={<VerifyEmailPage onNavigate={navigateToPage} token={verifyEmailToken} />} />

      <Route
        path="/dashboard"
        element={(
          <Suspense fallback={lazyFallback}>
            <DashboardPage
              onNavigate={navigateToPage}
              onViewCandidate={navigateToCandidate}
              NavComponent={DashboardNav}
              StatsCardComponent={StatsCard}
              StatusBadgeComponent={StatusBadge}
            />
          </Suspense>
        )}
      />

      <Route
        path="/candidates"
        element={(
          <Suspense fallback={lazyFallback}>
            <CandidatesPage
              onNavigate={navigateToPage}
              onViewCandidate={navigateToCandidate}
              NavComponent={DashboardNav}
            />
          </Suspense>
        )}
      />

      <Route
        path="/candidate-detail"
        element={
          loadingCandidateDetail ? (
            <div className="min-h-screen flex items-center justify-center">
              <Loader2 size={28} className="animate-spin" style={{ color: '#9D00FF' }} />
            </div>
          ) : (
            <Suspense fallback={lazyFallback}>
              <LazyCandidateDetailPage
                candidate={selectedCandidate}
                assessmentId={candidateDetailAssessmentId}
                onNavigate={navigateToPage}
                onDeleted={() => setSelectedCandidate(null)}
                onNoteAdded={(timeline) =>
                  setSelectedCandidate((prev) => (prev ? { ...prev, timeline } : prev))
                }
                NavComponent={DashboardNav}
              />
            </Suspense>
          )
        }
      />

      <Route
        path="/tasks"
        element={(
          <Suspense fallback={lazyFallback}>
            <TasksPage onNavigate={navigateToPage} NavComponent={DashboardNav} />
          </Suspense>
        )}
      />

      <Route
        path="/analytics"
        element={<AnalyticsPage onNavigate={navigateToPage} NavComponent={DashboardNav} />}
      />

      <Route
        path="/settings"
        element={(
          <Suspense fallback={lazyFallback}>
            <SettingsPage
              onNavigate={navigateToPage}
              NavComponent={DashboardNav}
              ConnectWorkableButton={ConnectWorkableButton}
            />
          </Suspense>
        )}
      />

      <Route
        path="/settings/workable/callback"
        element={<WorkableCallbackPage code={searchParams.get('code')} onNavigate={navigateToPage} />}
      />

      <Route path="/assess/:token" element={<CandidateWelcomeRoute />} />
      <Route path="/assessment/:assessmentId" element={<CandidateWelcomeWithIdRoute />} />
      <Route path="/assessment/live" element={<AssessmentLiveRoute />} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AppContent />
    </BrowserRouter>
  );
}

export default App;
export { CandidateDetailPage } from './features/candidates/CandidateDetailPage';
