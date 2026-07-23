import React, { Suspense, useEffect, useMemo, useState } from 'react';
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
import { useAuth } from './context/AuthContext';
import { ToastProvider } from './context/ToastContext';
import { JobStatusProvider } from './contexts/JobStatusContext';
import { assessments as assessmentsApi } from './shared/api/assessmentsClient';
import { createPageNavigator } from './app/pageNavigation';
import { legalRoutes } from './app/legalRoutes';
import {
  AssessmentLiveRoute,
  CandidateWelcomeRoute,
  CandidateWelcomeWithIdRoute,
} from './app/AssessmentRoutes';
import { ErrorBoundary } from './shared/ui/ErrorBoundary';
import { Button, Panel, Spinner } from './shared/ui/TaaliPrimitives';
import { ScrollToTop } from './shared/ui/ScrollToTop';
import { RouteMeta } from './shared/seo/RouteMeta';
import { KeyboardShortcutsModal } from './shared/ui/KeyboardShortcutsModal';
import { useKeyboardShortcut } from './shared/hooks/useKeyboardShortcut';
import { MotionSystemProvider } from './shared/motion';
import { recoverCandidateRuntimeToken } from './shared/assessment/candidateProofBinding';
import { PreviewNavGuard } from './shared/layout/PreviewNavGuard';
import { StatsCard, StatusBadge } from './shared/ui/DashboardAtoms';
import {
  AcceptInvitePage,
  AgentPromptPreviewPage,
  AnalyticsMotionPreview,
  AnalyticsPage,
  AssessmentsPage,
  AtsAdminPage,
  BackgroundJobsToaster,
  BespokeTaskRequestPage,
  BlogIndexPage,
  BlogPostPage,
  ButtonShowcasePage,
  CandidateStandingReportPage,
  CareersPage,
  ChatPage,
  ChatShowcaseView,
  ChatDesignSystemView,
  ClientIntakePage,
  ConnectWorkableButton,
  DashboardNav,
  DecisionPolicyPage,
  DeckIframe,
  DeckLinksPage,
  DemoExperiencePage,
  DemoLeadPage,
  DemoShowcasePage,
  DeveloperPortalPage,
  ForgotPasswordPage,
  HomeMotionPreview,
  HomePage,
  HomeShowcaseView,
  JobPipelinePage,
  JobsMotionPreview,
  JobsPage,
  LandingPage,
  LandingPreviewPage,
  LoginPage,
  MotionShowcasePage,
  NotFoundPage,
  OutreachThanksPage,
  PipelineAnalyticsPage,
  PublicJobPage,
  RegisterPage,
  ReportMotionPreview,
  RequisitionTemplatePage,
  RequisitionsPage,
  ResetPasswordPage,
  SettingsPage,
  SubmittalPackPage,
  TaskPreviewPage,
  TasksPage,
  ToastShowcasePage,
  TokenGate,
  TopReportPage,
  UnsubscribePage,
  VerifyEmailPage,
  WorkableCallbackPage,
} from './app/lazyPages';

const isPublicCandidateSharePath = (pathname, search = '') => {
  // /c/:applicationId is a recruiter route. The only public /c/* surface is
  // the fixture-backed showcase report; real external reports use the opaque
  // /share/:shareToken route below. Do not revive the retired ?k or shr_*
  // compatibility formats here because they bypass recruiter session scoping.
  const params = new URLSearchParams(search || '');
  if (pathname === '/c/demo' && params.get('showcase') === '1') return true;
  if (pathname.startsWith('/submittal/')) return true;
  if (pathname.startsWith('/unsubscribe/')) return true;
  if (pathname.startsWith('/outreach/thanks')) return true;
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
  return pathname === '/jobs';
};

const isProtectedRecruiterPath = (pathname, search = '') => {
  if (isPublicCandidateSharePath(pathname, search)) return false;
  if (isShowcaseRecruiterPath(pathname, search)) return false;
  return (
    [
    '/dashboard',
    '/home',
    '/jobs',
    '/requisitions',
    '/assessments',
    '/analytics',
    '/reporting',
    '/tasks',
    '/tasks/bespoke',
    '/candidate-detail',
    // Minting deck links needs a real owner session, not just the dev token.
    // Exact match only — '/deck' and '/deck/<prospect-token>' stay open.
    '/deck/links',
    ].includes(pathname)
    || pathname.startsWith('/analytics/')
    || pathname.startsWith('/c/')
    || pathname.startsWith('/jobs/')
    || pathname.startsWith('/assessments/')
    || pathname.startsWith('/candidates/')
    || pathname.startsWith('/settings')
    // Recruiter-only routes that render full chrome before any API call, so a
    // logged-out visit must be caught here before any protected API call.
    || pathname.startsWith('/chat')
    || pathname.startsWith('/tasks/')
    || pathname.startsWith('/admin')
    || pathname.startsWith('/ats-admin')
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

// Batch/sync discovery is recruiter-only infrastructure. Keeping it outside
// public, auth, candidate-share, and preview routes avoids background probes
// and prevents the toaster chunk from loading where it can never render.
function RecruiterJobStatusBoundary({ children }) {
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

const lazyFallback = (
  <div className="min-h-screen flex items-center justify-center">
    <Spinner size={28} />
  </div>
);

// Preserves the conversation id when redirecting from the v1
// ``/copilot/:id`` URL to ``/chat/:id``.
function RedirectCopilotConvo() {
  const { conversationId } = useParams();
  return <Navigate to={`/chat/${conversationId}`} replace />;
}

// Stable component identity keeps route-level navigation mounted across
// AppContent updates (for example opening global recruiter UI).
function DashboardNavWithMode(props) {
  return <DashboardNav {...props} />;
}

function AppContent() {
  const { isAuthenticated, loading: authLoading } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [loadingCandidateDetail, setLoadingCandidateDetail] = useState(false);
  // Only show the "Assessment unavailable" panel after a CONFIRMED fetch
  // failure — otherwise every deep link flashes the error for a frame before
  // the spinner appears.
  const [candidateDetailFetchFailed, setCandidateDetailFetchFailed] = useState(false);
  const [startedAssessmentData, setStartedAssessmentData] = useState(null);
  const [shortcutsModalOpen, setShortcutsModalOpen] = useState(false);

  const recruiterShortcutsEnabled = isAuthenticated
    && isProtectedRecruiterPath(location.pathname, location.search);

  useKeyboardShortcut(
    (e) => e.key === '?' && !e.metaKey && !e.ctrlKey && !e.altKey,
    () => setShortcutsModalOpen(true),
    { enabled: recruiterShortcutsEnabled },
  );

  useEffect(() => {
    if (!recruiterShortcutsEnabled) setShortcutsModalOpen(false);
  }, [recruiterShortcutsEnabled]);

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
      return searchParams.get('token') || recoverCandidateRuntimeToken() || null;
    }
    return null;
  }, [assessmentIdFromLink, location.pathname, searchParams]);

  const resetPasswordToken = location.pathname === '/reset-password'
    ? (searchParams.get('token') || '')
    : '';
  const verifyEmailToken = location.pathname === '/verify-email'
    ? (searchParams.get('token') || '')
    : '';
  const acceptInviteToken = location.pathname === '/accept-invite'
    ? (searchParams.get('token') || '')
    : '';

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
      const liveParams = new URLSearchParams(window.location.search || '');
      const livePath = window.location.pathname || '';
      if (
        liveParams.get('showcase') === '1'
        && liveParams.get('demo') === '1'
        && livePath === '/jobs'
      ) {
        return;
      }
    }
    if (isProtectedRecruiterPath(location.pathname, location.search)) {
      const nextPath = `${location.pathname}${location.search}${location.hash}`;
      navigate(`/login?next=${encodeURIComponent(nextPath)}`, { replace: true });
    }
  }, [isAuthenticated, authLoading, location.hash, location.pathname, location.search, navigate]);

  const navigateToPage = createPageNavigator(navigate, {
    activeAssessmentToken,
    assessmentIdFromLink,
    candidateDetailAssessmentId,
    resetPasswordToken,
    verifyEmailToken,
  });

  const handleCandidateStarted = (startData) => {
    setStartedAssessmentData(startData);
  };

  const navigateToCandidate = (candidate) => {
    // Back navigation now lives in the ?from= breadcrumb model in
    // CandidateStandingReportPage — the old candidateDetailBackTo state was
    // written here but never read, so it (and its setters) are gone.
    setSelectedCandidate(candidate);
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
    setCandidateDetailFetchFailed(false);
    assessmentsApi.get(candidateDetailAssessmentId)
      .then(async (res) => {
        const { mapAssessmentToCandidateView } = await import('./features/candidates/assessmentViewModels');
        if (cancelled) return;
        setSelectedCandidate(mapAssessmentToCandidateView(res.data || {}));
        setLoadingCandidateDetail(false);
      })
      .catch(() => {
        if (cancelled) return;
        setSelectedCandidate(null);
        setCandidateDetailFetchFailed(true);
        setLoadingCandidateDetail(false);
      });

    return () => {
      cancelled = true;
    };
  }, [location.pathname, candidateDetailAssessmentId, isAuthenticated, selectedCandidate]);

  // Public marketing, candidate, and preview routes do not depend on recruiter
  // identity. Let them paint while a cached token is validated instead of
  // serialising public content behind the /me request.
  if (authLoading && (
    isAuthenticated
    || isProtectedRecruiterPath(location.pathname, location.search)
  )) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Spinner size={32} />
      </div>
    );
  }

  // workflowModeLoading removed — there is no per-org workflow probe anymore.

  return (
    <>
      <ScrollToTop />
      <PreviewNavGuard />
      <RouteMeta />
      <KeyboardShortcutsModal
        open={shortcutsModalOpen}
        onClose={() => setShortcutsModalOpen(false)}
      />
      <Routes>
      <Route
        path="/"
        element={
          <Suspense fallback={lazyFallback}>
            <LandingPage onNavigate={navigateToPage} />
          </Suspense>
        }
      />
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
      {/* Internal landing-design preview. Public, no-auth — not in
          isProtectedRecruiterPath and it calls no APIs. ?v=a|b picks the variant. */}
      <Route
        path="/landing-preview"
        element={(
          <Suspense fallback={lazyFallback}>
            <LandingPreviewPage onNavigate={navigateToPage} />
          </Suspense>
        )}
      />
      {/* Internal Motion preview of the real Home Hub. Public, no-auth — not in
          isProtectedRecruiterPath and backed only by fixture data. */}
      <Route
        path="/home-preview"
        element={(
          <Suspense fallback={lazyFallback}>
            <HomeMotionPreview />
          </Suspense>
        )}
      />
      {/* Public, fixture-only comparison of four agent-prompt interaction
          directions. `?v=a|b|c|d` makes each concept directly shareable. */}
      <Route
        path="/agent-prompts-preview"
        element={(
          <Suspense fallback={lazyFallback}>
            <AgentPromptPreviewPage />
          </Suspense>
        )}
      />
      {/* Motion previews of the rest of the app — public, no-auth, fixtures
          only. Tour: /home-preview · /jobs-preview · /report-preview ·
          /analytics-preview · /landing-preview. */}
      <Route
        path="/jobs-preview"
        element={(
          <Suspense fallback={lazyFallback}>
            <JobsMotionPreview />
          </Suspense>
        )}
      />
      <Route
        path="/report-preview"
        element={(
          <Suspense fallback={lazyFallback}>
            <ReportMotionPreview />
          </Suspense>
        )}
      />
      <Route
        path="/analytics-preview"
        element={(
          <Suspense fallback={lazyFallback}>
            <AnalyticsMotionPreview />
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
      <Route
        path="/developers"
        element={(
          <Suspense fallback={lazyFallback}>
            <DeveloperPortalPage />
          </Suspense>
        )}
      />
      <Route
        path="/blog"
        element={(
          <Suspense fallback={lazyFallback}>
            <BlogIndexPage onNavigate={navigateToPage} />
          </Suspense>
        )}
      />
      <Route
        path="/blog/:slug"
        element={(
          <Suspense fallback={lazyFallback}>
            <BlogPostPage onNavigate={navigateToPage} />
          </Suspense>
        )}
      />
      {/* Public, no-auth legal pages — /privacy, /terms, /subprocessors.
          Declared as a Route fragment (see app/legalRoutes) so this file stays
          within its ratcheted line cap. */}
      {legalRoutes}

      <Route path="/login" element={<Suspense fallback={lazyFallback}><LoginPage onNavigate={navigateToPage} /></Suspense>} />
      <Route path="/register" element={<Suspense fallback={lazyFallback}><RegisterPage onNavigate={navigateToPage} /></Suspense>} />
      <Route path="/forgot-password" element={<Suspense fallback={lazyFallback}><ForgotPasswordPage onNavigate={navigateToPage} /></Suspense>} />
      <Route path="/reset-password" element={<Suspense fallback={lazyFallback}><ResetPasswordPage onNavigate={navigateToPage} token={resetPasswordToken} /></Suspense>} />
      <Route path="/verify-email" element={<Suspense fallback={lazyFallback}><VerifyEmailPage onNavigate={navigateToPage} token={verifyEmailToken} /></Suspense>} />
      <Route path="/accept-invite" element={<Suspense fallback={lazyFallback}><AcceptInvitePage onNavigate={navigateToPage} token={acceptInviteToken} /></Suspense>} />

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
        path="/requisitions"
        element={(
          <Suspense fallback={lazyFallback}>
            <RequisitionsPage
              onNavigate={navigateToPage}
              NavComponent={DashboardNavWithMode}
            />
          </Suspense>
        )}
      />

      {/* Clients are managed directly in Settings → Clients (embedded
          ClientsManager); there is no standalone /clients page. Per-client
          pipeline lives on the Jobs page's client filter. */}

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

      {/* Candidates live per-job — each role's pipeline is the working
          surface, and the cross-job "what needs a decision" view is the Home
          hub. There is no top-level candidate list. The drill-down route
          /candidates/:id below is the standing report, reached by
          application id. */}

      {/* Taali Chat — agentic chat over the same MCP tools served at /mcp.
          Backend at /api/v1/taali-chat/*. The "Agents" sub-routes surface
          the per-role agent threads (same backend as the Home dock, so the
          two surfaces stay in sync) alongside the regular Ask chats. The
          static ``/chat/agents*`` paths outrank ``/chat/:conversationId`` in
          React-router's ranking, so order here doesn't matter. */}
      <Route
        path="/chat/agents"
        element={(
          <Suspense fallback={lazyFallback}>
            <ChatPage mode="agents" onNavigate={navigateToPage} NavComponent={DashboardNavWithMode} />
          </Suspense>
        )}
      />
      <Route
        path="/chat/agents/:roleId"
        element={(
          <Suspense fallback={lazyFallback}>
            <ChatPage mode="agents" onNavigate={navigateToPage} NavComponent={DashboardNavWithMode} />
          </Suspense>
        )}
      />
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
      {/* Public, fixture-only living reference for the complete chat design
          language. It exercises production chat primitives without APIs. */}
      <Route
        path="/showcase/chat-system"
        element={(
          <Suspense fallback={lazyFallback}>
            <ChatDesignSystemView />
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

      {/* Public share-link route — recipient lands here via the URL
          generated by ShareModal. The page detects share mode from
          the ``:token`` URL param, fetches the application via the
          unauth /share/:token endpoint, and renders in client or
          recruiter view based on the link's mode. No /api/v1 prefix
          and no recruiter session required. */}
      <Route
        path="/share/:shareToken"
        element={(
          <Suspense fallback={lazyFallback}>
            <CandidateStandingReportPage
              onNavigate={navigateToPage}
              NavComponent={DashboardNavWithMode}
            />
          </Suspense>
        )}
      />

      {/* Public, no-auth shareable "top candidates" report — renders a
          persisted find_top_candidates snapshot by token. */}
      <Route
        path="/report/:reportToken"
        element={(
          <Suspense fallback={lazyFallback}>
            <TopReportPage />
          </Suspense>
        )}
      />

      {/* Public, no-auth curated client submittal — renders a persisted,
          role-scoped shortlist snapshot (client-safe candidate cards) by
          token. No /api/v1 prefix and no recruiter session required. */}
      <Route
        path="/submittal/:submittalToken"
        element={(
          <Suspense fallback={lazyFallback}>
            <SubmittalPackPage />
          </Suspense>
        )}
      />

      {/* Public, no-auth one-click unsubscribe. GET validates + shows the org
          name and masked email; the Unsubscribe button POSTs the opt-out. No
          /api/v1 prefix path here and no recruiter session required. */}
      <Route
        path="/unsubscribe/:token"
        element={(
          <Suspense fallback={lazyFallback}>
            <UnsubscribePage />
          </Suspense>
        )}
      />

      {/* Public, no-auth outreach thanks page — the CTA landing when a campaign
          has no job page. Interest is recorded by the backend before redirect. */}
      <Route
        path="/outreach/thanks"
        element={(
          <Suspense fallback={lazyFallback}>
            <OutreachThanksPage />
          </Suspense>
        )}
      />

      <Route
        path="/candidate-detail"
        element={<Navigate replace to={candidateDetailAssessmentId ? `/assessments/${candidateDetailAssessmentId}` : '/assessments'} />}
      />

      {/* Consolidation cutover: /assessments/:id is retired in favour of the
          canonical candidate file. Once the assessment is loaded we redirect
          to /candidates/:applicationId?tab=assessment (the migrated Assessment
          tab). Assessments with no linked application (legacy) fall back to the
          old page so nothing 404s during the transition. */}
      <Route
        path="/assessments/:assessmentId"
        element={
          selectedCandidate?._raw?.application_id ? (
            <Navigate
              replace
              to={`/candidates/${selectedCandidate._raw.application_id}?tab=assessment`}
            />
          ) : (loadingCandidateDetail || (selectedCandidate == null && !candidateDetailFetchFailed)) ? (
            // Still fetching (nothing loaded yet and no failure) is a loading
            // state, not an error — so a deep link doesn't flash the panel on
            // first paint. Once the fetch resolves (a legacy assessment with no
            // application, or a confirmed failure) we fall through to the panel.
            <div className="min-h-screen flex items-center justify-center">
              <Spinner size={28} />
            </div>
          ) : (
            <div>
              <DashboardNavWithMode currentPage="jobs" onNavigate={navigateToPage} />
              <div className="page">
                <Panel style={{ padding: 24, marginTop: 16 }}>
                  <h2 className="taali-display text-xl font-semibold text-[var(--taali-text)]">Assessment unavailable</h2>
                  <p className="mt-2 text-sm text-[var(--taali-muted)]">
                    This assessment couldn’t be opened in the candidate file — it isn’t linked to a
                    candidate application, or it no longer exists.
                  </p>
                  <Button type="button" variant="secondary" className="mt-4" onClick={() => navigateToPage('jobs')}>
                    Back to Jobs
                  </Button>
                </Panel>
              </div>
            </div>
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

      {/* Analytics is its own page now (the agent reporting layer, off the home
          review loop). /reporting is a legacy alias that lands on it. */}
      <Route
        path="/analytics"
        element={(
          <Suspense fallback={lazyFallback}>
            <AnalyticsPage
              onNavigate={navigateToPage}
              NavComponent={DashboardNavWithMode}
            />
          </Suspense>
        )}
      />
      <Route
        path="/analytics/pipeline"
        element={(
          <Suspense fallback={lazyFallback}>
            <PipelineAnalyticsPage
              onNavigate={navigateToPage}
              NavComponent={DashboardNavWithMode}
            />
          </Suspense>
        )}
      />
      <Route
        path="/reporting"
        element={<Navigate replace to="/analytics" />}
      />
      <Route
        path="/ats-admin"
        element={(
          <Suspense fallback={lazyFallback}>
            <AtsAdminPage onNavigate={navigateToPage} NavComponent={DashboardNavWithMode} />
          </Suspense>
        )}
      />

      {/* Requisition spec template editor. A dedicated page (not a tab in the
          tabbed SettingsPage), so it gets a specific route ABOVE the
          /settings/* splat — React Router ranks the static path higher, so it
          wins over the catch-all. */}
      <Route
        path="/settings/requisition-template"
        element={(
          <Suspense fallback={lazyFallback}>
            <RequisitionTemplatePage
              onNavigate={navigateToPage}
              NavComponent={DashboardNavWithMode}
            />
          </Suspense>
        )}
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
          <Suspense fallback={lazyFallback}>
            <WorkableCallbackPage
              code={searchParams.get('code')}
              state={searchParams.get('state')}
              error={searchParams.get('error')}
              errorDescription={searchParams.get('error_description')}
              onNavigate={navigateToPage}
            />
          </Suspense>
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

      {/* Internal investor deck. Reach via /deck?k=<VITE_DEV_TOKEN>.
          See features/_dev/TokenGate.jsx and public/_deck/index.html. */}
      <Route
        path="/deck"
        element={(
          <Suspense fallback={lazyFallback}>
            <TokenGate>
              <DeckIframe />
            </TokenGate>
          </Suspense>
        )}
      />

      {/* Mint / audit / revoke per-prospect deck links. Owner-gated
          server-side; TokenGate here is obscurity + noindex only. */}
      <Route
        path="/deck/links"
        element={(
          <Suspense fallback={lazyFallback}>
            <TokenGate>
              <DeckLinksPage />
            </TokenGate>
          </Suspense>
        )}
      />

      <Route
        path="/dev/toasters"
        element={(
          <Suspense fallback={lazyFallback}>
            <TokenGate>
              <ToastShowcasePage />
            </TokenGate>
          </Suspense>
        )}
      />

      <Route
        path="/dev/motion"
        element={(
          <Suspense fallback={lazyFallback}>
            <TokenGate>
              <MotionShowcasePage />
            </TokenGate>
          </Suspense>
        )}
      />

      <Route
        path="/dev/buttons"
        element={(
          <Suspense fallback={lazyFallback}>
            <TokenGate>
              <ButtonShowcasePage />
            </TokenGate>
          </Suspense>
        )}
      />

      <Route
        path="/assess/:token"
        element={(
          <CandidateWelcomeRoute
            onNavigate={navigateToPage}
            onStarted={handleCandidateStarted}
          />
        )}
      />

      {/* Public, no-auth careers-style job posting. The shareable link a
          published requisition produces. Like /assess/:token, it renders
          WITHOUT a NavComponent and without a recruiter session — the page
          fetches its snapshot through the unauthenticated public job
          endpoint. */}
      <Route
        path="/job/:token"
        element={(
          <Suspense fallback={lazyFallback}>
            <PublicJobPage />
          </Suspense>
        )}
      />

      {/* Public, no-auth CAREERS BOARD. The per-org page listing all of an
          org's published jobs, reached via the org's careers_url. Like
          /job/:token, it renders WITHOUT a NavComponent and without a recruiter
          session — the page fetches the board through the unauthenticated
          public careers endpoint. */}
      <Route
        path="/careers/:slug"
        element={(
          <Suspense fallback={lazyFallback}>
            <CareersPage />
          </Suspense>
        )}
      />

      {/* Public, no-auth CLIENT INTAKE. A consultancy recruiter shares this
          link with their client, who describes the role to the same
          conversational agent (company/economics hidden). Like /job/:token and
          /assess/:token, it renders WITHOUT a NavComponent and without a
          recruiter session — every call goes through the unauthenticated
          public intake endpoints. */}
      <Route
        path="/intake/:token"
        element={(
          <Suspense fallback={lazyFallback}>
            <ClientIntakePage />
          </Suspense>
        )}
      />

      <Route
        path="/assessment/:assessmentId"
        element={(
          <CandidateWelcomeWithIdRoute
            onNavigate={navigateToPage}
            onStarted={handleCandidateStarted}
          />
        )}
      />
      <Route
        path="/assessment/live"
        element={<AssessmentLiveRoute startData={startedAssessmentData} />}
      />

      {/* Unknown URL → a real 404 with a way back, instead of silently
          teleporting to "/" (which then bounced authed users to /home and hid
          the fact that the link was broken). Legacy aliases are individually
          routed above, so they never reach this. */}
      <Route
        path="*"
        element={(
          <Suspense fallback={lazyFallback}>
            <NotFoundPage />
          </Suspense>
        )}
      />
      </Routes>
    </>
  );
}

function SessionScopedAppTree({ sessionScope }) {
  const location = useLocation();
  // Public candidate/client flows use auth that is independent of a recruiter
  // session. Keep those routes mounted when another tab logs in or out, while
  // remounting the complete private recruiter subtree at every account change.
  const contentScope = isProtectedRecruiterPath(location.pathname, location.search)
    ? sessionScope
    : 'public';

  return (
    <ToastProvider key={contentScope}>
      <RecruiterJobStatusBoundary>
        <ErrorBoundary>
          <AppContent />
        </ErrorBoundary>
      </RecruiterJobStatusBoundary>
    </ToastProvider>
  );
}

function App() {
  const { isAuthenticated, sessionBoundary } = useAuth();
  // Recruiter UI state is private to the session that created it. Remount the
  // toast/activity store, job tracking provider, and route state together when
  // the authenticated boundary changes so a new account cannot inherit stale
  // toasts, selected candidates, or async callbacks from the previous account.
  // Logged-out/public browsing intentionally shares one stable scope.
  const sessionScope = isAuthenticated
    ? `authenticated:${sessionBoundary || 'pending'}`
    : 'anonymous';

  return (
    <MotionSystemProvider>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <SessionScopedAppTree sessionScope={sessionScope} />
      </BrowserRouter>
    </MotionSystemProvider>
  );
}

export default App;
