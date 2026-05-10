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
import { ToastProvider } from './context/ToastContext';
import { JobStatusProvider } from './contexts/JobStatusContext';
import {
  assessments as assessmentsApi,
  organizations as organizationsApi,
} from './shared/api';
import { pathForPage } from './app/routing';
import { ErrorBoundary } from './shared/ui/ErrorBoundary';

import { LandingPage } from './features/marketing/LandingPage';
import {
  ForgotPasswordPage,
  LoginPage,
  RegisterPage,
  ResetPasswordPage,
  VerifyEmailPage,
} from './features/auth';
import { Shell as DashboardNav } from './shared/layout/Shell';
import {
  ConnectWorkableButton,
  WorkableCallbackPage,
} from './features/integrations/WorkableConnection';
import { StatsCard, StatusBadge } from './shared/ui/DashboardAtoms';

const HomePage = lazy(() =>
  import('./features/home/HomePage').then((m) => ({ default: m.HomePage }))
);
const CandidateWelcomePage = lazy(() =>
  import('./features/assessment_runtime/CandidateWelcomePage').then((m) => ({ default: m.CandidateWelcomePage }))
);
const BackgroundJobsToaster = lazy(() =>
  import('./features/candidates/BackgroundJobsToaster').then((m) => ({ default: m.BackgroundJobsToaster }))
);
const ToastShowcasePage = lazy(() =>
  import('./features/dev/ToastShowcasePage').then((m) => ({ default: m.ToastShowcasePage }))
);

const AssessmentPage = lazy(() => import('./features/assessment_runtime/AssessmentPage'));
const CandidateFeedbackPage = lazy(() =>
  import('./features/assessment_runtime/CandidateFeedbackPage').then((m) => ({ default: m.CandidateFeedbackPage }))
);
const DemoExperiencePage = lazy(() =>
  import('./features/demo/DemoExperiencePage').then((m) => ({ default: m.DemoExperiencePage }))
);
const DemoLeadPage = lazy(() =>
  import('./features/marketing/DemoLeadPage').then((m) => ({ default: m.DemoLeadPage }))
);
const DemoShowcasePage = lazy(() =>
  import('./features/marketing/DemoShowcasePage').then((m) => ({ default: m.DemoShowcasePage }))
);
const LazyAssessmentResultsPage = lazy(() =>
  import('./features/assessments/AssessmentResultsPage').then((m) => ({ default: m.AssessmentResultsPage }))
);
const AssessmentsPage = lazy(() =>
  import('./features/assessments/AssessmentsPage').then((m) => ({ default: m.AssessmentsPage }))
);
const ChatPage = lazy(() =>
  import('./features/chat/ChatPage').then((m) => ({ default: m.ChatPage }))
);
const ChatShowcaseView = lazy(() =>
  import('./features/chat/ChatShowcaseView').then((m) => ({ default: m.ChatShowcaseView }))
);
const HomeShowcaseView = lazy(() =>
  import('./features/home/HomeShowcaseView').then((m) => ({ default: m.HomeShowcaseView }))
);
const CandidateStandingReportPage = lazy(() =>
  import('./features/candidates/CandidateStandingReportPage').then((m) => ({ default: m.CandidateStandingReportPage }))
);
const JobsPage = lazy(() =>
  import('./features/jobs/JobsPage').then((m) => ({ default: m.JobsPage }))
);
const JobPipelinePage = lazy(() =>
  import('./features/jobs/JobPipelinePage').then((m) => ({ default: m.JobPipelinePage }))
);
const TasksPage = lazy(() =>
  import('./features/tasks/TasksPage').then((m) => ({ default: m.TasksPage }))
);
const TaskPreviewPage = lazy(() =>
  import('./features/tasks/TasksPage').then((m) => ({ default: m.TaskPreviewPage }))
);
const BespokeTaskRequestPage = lazy(() =>
  import('./features/tasks/BespokeTaskRequestPage').then((m) => ({ default: m.BespokeTaskRequestPage }))
);
const SettingsPage = lazy(() =>
  import('./features/settings/SettingsPage').then((m) => ({ default: m.SettingsPage }))
);
const DecisionPolicyPage = lazy(() =>
  import('./features/decision_policy/DecisionPolicyPage')
);
const InvestmentDeckPage = lazy(() =>
  import('./features/dev/InvestmentDeckPage')
);

const isPublicCandidateSharePath = (pathname, search = '') => {
  if (pathname.startsWith('/c/')) return true;
  const params = new URLSearchParams(search || '');
  const hasInterviewToken = params.get('view') === 'interview' && Boolean(String(params.get('k') || '').trim());
  if (pathname.startsWith('/candidates/') && hasInterviewToken) return true;
  if (/^\/candidates\/shr_[^/]+$/.test(pathname)) return true;
  return false;
};

const isShowcaseRecruiterPath = (pathname, search = '') => {
  // Belt-and-braces: also peek at the live browser URL. We've seen the
  // React-router `location.search` come through empty on the first render
  // after a hard navigation, which made the auth-redirect useEffect
  // misfire and bounce the iframe to /login even though the URL clearly
  // had ?demo=1&showcase=1. Falling back to window.location keeps the
  // bypass honest in that race.
  let effectiveSearch = search || '';
  if (typeof window !== 'undefined') {
    const liveSearch = window.location.search || '';
    const livePath = window.location.pathname || '';
    if (livePath === pathname && liveSearch && !effectiveSearch.includes('showcase=')) {
      effectiveSearch = liveSearch;
    }
  }
  const params = new URLSearchParams(effectiveSearch);
  if (params.get('demo') !== '1' || params.get('showcase') !== '1') return false;
  return pathname === '/jobs' || pathname === '/candidates';
};

const isProtectedRecruiterPath = (pathname, search = '') => {
  if (isPublicCandidateSharePath(pathname, search)) return false;
  if (isShowcaseRecruiterPath(pathname, search)) return false;
  return (
    [
    '/dashboard',
    '/home',
    '/jobs',
    '/assessments',
    '/candidates',
    '/analytics',
    '/reporting',
    '/tasks',
    '/tasks/bespoke',
    '/candidate-detail',
    ].includes(pathname)
    || pathname.startsWith('/jobs/')
    || pathname.startsWith('/assessments/')
    || pathname.startsWith('/candidates/')
    || pathname.startsWith('/settings')
  );
};

const resolveSafeNextPath = (rawValue) => {
  if (typeof rawValue !== 'string') return '';
  const nextPath = rawValue.trim();
  if (!nextPath.startsWith('/') || nextPath.startsWith('//') || nextPath.includes('://')) {
    return '';
  }
  return nextPath;
};

function AppContent() {
  const { isAuthenticated, loading: authLoading } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [candidateDetailBackTo, setCandidateDetailBackTo] = useState({ page: 'assessments', label: 'Back to Assessments' });
  const [loadingCandidateDetail, setLoadingCandidateDetail] = useState(false);
  const [startedAssessmentData, setStartedAssessmentData] = useState(null);

  // The Hub (/home) is the agent-first landing — see docs/HOME_HUB_DESIGN.md.
  // Replaces /reporting as the default route after sign-in.
  const defaultRecruiterRoute = '/home';
  const nextRedirectPath = useMemo(
    () => resolveSafeNextPath(searchParams.get('next')),
    [searchParams]
  );

  const candidateDetailAssessmentId = useMemo(() => {
    const recruiterAssessmentMatch = location.pathname.match(/^\/assessments\/(\d+)$/);
    if (recruiterAssessmentMatch?.[1]) {
      return Number(recruiterAssessmentMatch[1]);
    }
    const legacyAssessmentId = searchParams.get('assessmentId');
    return legacyAssessmentId ? Number(legacyAssessmentId) : null;
  }, [location.pathname, searchParams]);

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

  // Workflow-mode probe removed — v2 is the only path; nothing to fetch.

  useEffect(() => {
    if (
      isAuthenticated
      && ['/', '/login', '/forgot-password'].includes(location.pathname)
    ) {
      navigate(location.pathname === '/login' && nextRedirectPath ? nextRedirectPath : defaultRecruiterRoute, { replace: true });
    }
  }, [defaultRecruiterRoute, isAuthenticated, location.pathname, navigate, nextRedirectPath]);

  useEffect(() => {
    if (authLoading || isAuthenticated) return;
    // Hard bypass for the showcase routes loaded inside the marketing demo
    // iframes. The structured `isProtectedRecruiterPath` check above is
    // supposed to handle this, but in practice the React-router `location`
    // can be a render behind on a hard navigation, which makes the bypass
    // miss and bounces the iframe to /login. Looking at the live browser
    // URL is the only thing that's consistently correct on first paint.
    if (typeof window !== 'undefined') {
      const liveSearch = window.location.search || '';
      const livePath = window.location.pathname || '';
      if (
        liveSearch.includes('showcase=1')
        && liveSearch.includes('demo=1')
        && (livePath === '/jobs' || livePath === '/candidates')
      ) {
        return;
      }
    }
    if (isProtectedRecruiterPath(location.pathname, location.search)) {
      const nextPath = `${location.pathname}${location.search}${location.hash}`;
      navigate(`/login?next=${encodeURIComponent(nextPath)}`, { replace: true });
    }
  }, [isAuthenticated, authLoading, location.hash, location.pathname, location.search, navigate]);

  const navigateToPage = (page, options = {}) => {
    const nextPath = pathForPage(page, {
      assessmentToken: Object.prototype.hasOwnProperty.call(options, 'assessmentToken')
        ? options.assessmentToken
        : activeAssessmentToken,
      assessmentIdFromLink: Object.prototype.hasOwnProperty.call(options, 'assessmentIdFromLink')
        ? options.assessmentIdFromLink
        : assessmentIdFromLink,
      candidateApplicationId: Object.prototype.hasOwnProperty.call(options, 'candidateApplicationId')
        ? options.candidateApplicationId
        : null,
      candidateDetailAssessmentId: Object.prototype.hasOwnProperty.call(options, 'candidateDetailAssessmentId')
        ? options.candidateDetailAssessmentId
        : candidateDetailAssessmentId,
      resetPasswordToken: Object.prototype.hasOwnProperty.call(options, 'resetPasswordToken')
        ? options.resetPasswordToken
        : resetPasswordToken,
      verifyEmailToken: Object.prototype.hasOwnProperty.call(options, 'verifyEmailToken')
        ? options.verifyEmailToken
        : verifyEmailToken,
      roleId: Object.prototype.hasOwnProperty.call(options, 'roleId')
        ? options.roleId
        : null,
      chatInitialQuery: Object.prototype.hasOwnProperty.call(options, 'initialQuery')
        ? options.initialQuery
        : (Object.prototype.hasOwnProperty.call(options, 'chatInitialQuery')
          ? options.chatInitialQuery
          : null),
    });

    if (nextPath) {
      navigate(nextPath, { replace: Boolean(options.replace) });
    }
    window.scrollTo(0, 0);
  };

  const handleCandidateStarted = (startData) => {
    setStartedAssessmentData(startData);
  };

  const navigateToCandidate = (candidate, sourcePage = 'assessments') => {
    setSelectedCandidate(candidate);
    if (sourcePage === 'candidates') {
      setCandidateDetailBackTo({ page: 'candidates', label: 'Back to Candidates' });
    } else if (sourcePage === 'jobs') {
      setCandidateDetailBackTo({ page: 'jobs', label: 'Back to Jobs' });
    } else {
      setCandidateDetailBackTo({ page: 'assessments', label: 'Back to Assessments' });
    }
    navigateToPage('candidate-detail', {
      candidateDetailAssessmentId: candidate?.id || candidate?._raw?.id || null,
    });
  };

  useEffect(() => {
    const isAssessmentResultsRoute = location.pathname === '/candidate-detail' || /^\/assessments\/\d+$/.test(location.pathname);
    if (!isAssessmentResultsRoute || !candidateDetailAssessmentId || !isAuthenticated) {
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
        <Loader2 size={32} className="animate-spin" style={{ color: 'var(--purple)' }} />
      </div>
    );
  }

  const lazyFallback = (
    <div className="min-h-screen flex items-center justify-center">
      <Loader2 size={28} className="animate-spin" style={{ color: 'var(--purple)' }} />
    </div>
  );
  // workflowModeLoading removed — there is no per-org workflow probe anymore.

  const CandidateWelcomeRoute = () => {
    const { token } = useParams();
    return (
      <Suspense fallback={lazyFallback}>
        <CandidateWelcomePage
          token={token || null}
          assessmentId={null}
          onNavigate={navigateToPage}
          onStarted={handleCandidateStarted}
        />
      </Suspense>
    );
  };

  // Preserves the conversation id when redirecting from the v1
  // ``/copilot/:id`` URL to ``/chat/:id``.
  const RedirectCopilotConvo = () => {
    const { conversationId } = useParams();
    return <Navigate to={`/chat/${conversationId}`} replace />;
  };

  const CandidateWelcomeWithIdRoute = () => {
    const { assessmentId } = useParams();
    const token = searchParams.get('token');
    if (!token) return <Navigate to="/" replace />;
    return (
      <Suspense fallback={lazyFallback}>
        <CandidateWelcomePage
          token={token}
          assessmentId={Number(assessmentId)}
          onNavigate={navigateToPage}
          onStarted={handleCandidateStarted}
        />
      </Suspense>
    );
  };

  const AssessmentLiveRoute = () => {
    const token = searchParams.get('token');
    const demo = searchParams.get('demo') === '1';
    const [demoFixtures, setDemoFixtures] = useState(null);
    useEffect(() => {
      if (demo && !demoFixtures) {
        import('./features/demo/productWalkthroughModels').then((m) =>
          setDemoFixtures({
            startData: m.PRODUCT_WALKTHROUGH_START_DATA,
            runtime: m.PRODUCT_WALKTHROUGH.runtime,
          })
        );
      }
    }, [demo, demoFixtures]);
    if (demo && !demoFixtures) return lazyFallback;
    return (
      <Suspense fallback={lazyFallback}>
        <AssessmentPage
          token={demo ? null : token}
          startData={demo ? demoFixtures.startData : startedAssessmentData}
          demoMode={demo}
          demoProfile={demo ? {
            ...demoFixtures.runtime,
            output: demoFixtures.runtime.output,
          } : undefined}
        />
      </Suspense>
    );
  };

  const CandidateFeedbackRoute = () => {
    const { token } = useParams();
    return (
      <Suspense fallback={lazyFallback}>
        <CandidateFeedbackPage token={token || ''} />
      </Suspense>
    );
  };

  // Thin wrapper preserved so route-level <DashboardNav /> usage stays
  // consistent if we add cross-cutting props later (e.g. environment banners).
  const DashboardNavWithMode = (props) => <DashboardNav {...props} />;

  return (
    <>
      <Routes>
      <Route path="/" element={<LandingPage onNavigate={navigateToPage} />} />
      {/* /demo is the showcase. /showcase kept as an alias. The legacy
          DemoExperiencePage walkthrough lives at /demo-walkthrough until
          we decide to retire it entirely. */}
      <Route
        path="/demo"
        element={(
          <Suspense fallback={lazyFallback}>
            <DemoShowcasePage onNavigate={navigateToPage} />
          </Suspense>
        )}
      />
      <Route
        path="/showcase"
        element={(
          <Suspense fallback={lazyFallback}>
            <DemoShowcasePage onNavigate={navigateToPage} />
          </Suspense>
        )}
      />
      <Route
        path="/demo-walkthrough"
        element={(
          <Suspense fallback={lazyFallback}>
            <DemoExperiencePage onNavigate={navigateToPage} />
          </Suspense>
        )}
      />
      <Route
        path="/demo-lead"
        element={(
          <Suspense fallback={lazyFallback}>
            <DemoLeadPage onNavigate={navigateToPage} />
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
        element={<Navigate replace to={defaultRecruiterRoute} />}
      />

      <Route
        path="/home"
        element={(
          <Suspense fallback={lazyFallback}>
            <HomePage
              onNavigate={navigateToPage}
              NavComponent={DashboardNavWithMode}
            />
          </Suspense>
        )}
      />

      <Route
        path="/jobs"
        element={(
          <Suspense fallback={lazyFallback}>
            <JobsPage
              onNavigate={navigateToPage}
              NavComponent={DashboardNavWithMode}
            />
          </Suspense>
        )}
      />

      <Route
        path="/jobs/:roleId"
        element={(
          <Suspense fallback={lazyFallback}>
            <JobPipelinePage
              onNavigate={navigateToPage}
              onViewCandidate={(candidate) => navigateToCandidate(candidate, 'jobs')}
              NavComponent={DashboardNavWithMode}
            />
          </Suspense>
        )}
      />

      <Route
        path="/assessments"
        element={(
          <Suspense fallback={lazyFallback}>
            <AssessmentsPage
              onNavigate={navigateToPage}
              onViewCandidate={(candidate) => navigateToCandidate(candidate, 'assessments')}
              NavComponent={DashboardNavWithMode}
              StatsCardComponent={StatsCard}
              StatusBadgeComponent={StatusBadge}
            />
          </Suspense>
        )}
      />

      {/* The standalone /candidates directory is deprecated — the
          triage drawer now lives on the role page (JobPipelinePage), so
          there is no separate "all candidates" list. Redirect any stale
          bookmarks to /jobs. The drill-down route /candidates/:id
          stays mounted below; that's the standing report, still used. */}
      <Route path="/candidates" element={<Navigate to="/jobs" replace />} />

      {/* Taali Chat — agentic chat over the same MCP tools served at /mcp.
          Backend at /api/v1/taali-chat/*. */}
      <Route
        path="/chat"
        element={(
          <Suspense fallback={lazyFallback}>
            <ChatPage onNavigate={navigateToPage} NavComponent={DashboardNavWithMode} />
          </Suspense>
        )}
      />
      <Route
        path="/chat/:conversationId"
        element={(
          <Suspense fallback={lazyFallback}>
            <ChatPage onNavigate={navigateToPage} NavComponent={DashboardNavWithMode} />
          </Suspense>
        )}
      />
      {/* Public, auth-free chat preview used by the demo showcase to
          demonstrate graph topology + tool-call flow without a backend. */}
      <Route
        path="/showcase/chat"
        element={(
          <Suspense fallback={lazyFallback}>
            <ChatShowcaseView />
          </Suspense>
        )}
      />
      {/* Public, auth-free Hub snapshot — the agent narrator + decision
          feed surface, fed by fixture data. Used by the demo showcase
          so the "Workflow & decisions" tab can render without auth and
          without hitting the agent APIs. */}
      <Route
        path="/showcase/home"
        element={(
          <Suspense fallback={lazyFallback}>
            <HomeShowcaseView />
          </Suspense>
        )}
      />
      {/* Stale-bookmark redirects from the v1 ``/copilot`` URL. */}
      <Route path="/copilot" element={<Navigate to="/chat" replace />} />
      <Route path="/copilot/:conversationId" element={<RedirectCopilotConvo />} />

      <Route
        path="/candidates/:applicationId"
        element={(
          <Suspense fallback={lazyFallback}>
            <CandidateStandingReportPage
              onNavigate={navigateToPage}
              NavComponent={DashboardNavWithMode}
            />
          </Suspense>
        )}
      />

      <Route
        path="/c/:applicationId"
        element={(
          <Suspense fallback={lazyFallback}>
            <CandidateStandingReportPage
              onNavigate={navigateToPage}
              NavComponent={DashboardNavWithMode}
            />
          </Suspense>
        )}
      />

      <Route
        path="/candidate-detail"
        element={<Navigate replace to={candidateDetailAssessmentId ? `/assessments/${candidateDetailAssessmentId}` : '/assessments'} />}
      />

      <Route
        path="/assessments/:assessmentId"
        element={
          loadingCandidateDetail ? (
            <div className="min-h-screen flex items-center justify-center">
              <Loader2 size={28} className="animate-spin" style={{ color: 'var(--purple)' }} />
            </div>
          ) : (
            <Suspense fallback={lazyFallback}>
              <LazyAssessmentResultsPage
                candidate={selectedCandidate}
                assessmentId={candidateDetailAssessmentId}
                onNavigate={navigateToPage}
                backTo={candidateDetailBackTo}
                onDeleted={() => setSelectedCandidate(null)}
                onNoteAdded={(timeline) =>
                  setSelectedCandidate((prev) => (prev ? { ...prev, timeline } : prev))
                }
                NavComponent={DashboardNavWithMode}
              />
            </Suspense>
          )
        }
      />

      <Route
        path="/tasks"
        element={(
          <Suspense fallback={lazyFallback}>
            <TasksPage onNavigate={navigateToPage} NavComponent={DashboardNavWithMode} />
          </Suspense>
        )}
      />

      <Route
        path="/tasks/bespoke"
        element={(
          <Suspense fallback={lazyFallback}>
            <BespokeTaskRequestPage onNavigate={navigateToPage} NavComponent={DashboardNavWithMode} />
          </Suspense>
        )}
      />

      {/* Recruiter task preview renders the candidate-facing AssessmentPage
          in demo mode (full-screen IDE + chat + terminal). Intentionally
          chrome-less to match the runtime — the recruiter sees exactly what
          the candidate sees. Use the browser back button to return. */}
      <Route
        path="/tasks/:taskId/preview"
        element={(
          <Suspense fallback={lazyFallback}>
            <TaskPreviewPage />
          </Suspense>
        )}
      />

      {/* Reporting + analytics fold into the Hub bottom section now.
          Both routes 301 to /home — see docs/HOME_HUB_DESIGN.md §4. */}
      <Route
        path="/analytics"
        element={<Navigate replace to="/home" />}
      />
      <Route
        path="/reporting"
        element={<Navigate replace to="/home" />}
      />

      <Route
        path="/settings/*"
        element={(
          <Suspense fallback={lazyFallback}>
            <SettingsPage
              onNavigate={navigateToPage}
              NavComponent={DashboardNavWithMode}
              ConnectWorkableButton={ConnectWorkableButton}
            />
          </Suspense>
        )}
      />

      <Route
        path="/settings/workable/callback"
        element={(
          <WorkableCallbackPage
            code={searchParams.get('code')}
            error={searchParams.get('error')}
            errorDescription={searchParams.get('error_description')}
            onNavigate={navigateToPage}
          />
        )}
      />

      <Route
        path="/admin/decision-policy/*"
        element={(
          <Suspense fallback={lazyFallback}>
            <DecisionPolicyPage />
          </Suspense>
        )}
      />

      {/* Internal investment-deck screenshot canvas. Not linked from
          anywhere; reach by direct URL only. See
          features/dev/InvestmentDeckPage.jsx. */}
      <Route
        path="/deck"
        element={(
          <Suspense fallback={lazyFallback}>
            <InvestmentDeckPage />
          </Suspense>
        )}
      />

      <Route
        path="/dev/toasters"
        element={(
          <Suspense fallback={lazyFallback}>
            <ToastShowcasePage />
          </Suspense>
        )}
      />

      <Route path="/assess/:token" element={<CandidateWelcomeRoute />} />
      <Route path="/assessment/:token/feedback" element={<CandidateFeedbackRoute />} />
      <Route path="/assessment/:assessmentId" element={<CandidateWelcomeWithIdRoute />} />
      <Route path="/assessment/live" element={<AssessmentLiveRoute />} />

      <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}

function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <ToastProvider>
        <JobStatusProvider>
          <ErrorBoundary>
            <AppContent />
          </ErrorBoundary>
          {/* Global job panel — outside routes so it survives navigation */}
          <Suspense fallback={null}>
            <BackgroundJobsToaster />
          </Suspense>
        </JobStatusProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}

export default App;
export { CandidateDetailPage, AssessmentResultsPage } from './features/candidates/CandidateDetailPage';
