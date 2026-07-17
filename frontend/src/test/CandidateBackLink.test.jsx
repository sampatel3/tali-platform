import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

// Mirrors the api surface the standing report touches. Kept in sync with
// SecureCandidateShareLinks.test.jsx — the report renders the same way
// whether reached via /share/:token or the authenticated /candidates/:id.
vi.mock('../shared/api', () => ({
  viewShareLink: vi.fn(),
  auth: {
    login: vi.fn(),
    register: vi.fn(),
    me: vi.fn(),
    ssoCheck: vi.fn(),
    verifyEmail: vi.fn(),
    resendVerification: vi.fn(),
    forgotPassword: vi.fn(),
    resetPassword: vi.fn(),
  },
  assessments: {
    list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
    get: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    resend: vi.fn(),
    downloadReport: vi.fn(),
    addNote: vi.fn(),
    uploadCv: vi.fn(),
    postToWorkable: vi.fn(),
  },
  billing: { usage: vi.fn(), costs: vi.fn(), credits: vi.fn(), createCheckoutSession: vi.fn() },
  organizations: {
    get: vi.fn().mockResolvedValue({ data: { workable_connected: false } }),
    update: vi.fn(),
  },
  analytics: { get: vi.fn().mockResolvedValue({ data: {} }) },
  tasks: {
    list: vi.fn().mockResolvedValue({ data: [] }),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    generate: vi.fn(),
  },
  candidates: {
    list: vi.fn().mockResolvedValue({ data: { items: [] } }),
    get: vi.fn(),
    create: vi.fn(),
    createWithCv: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    uploadCv: vi.fn(),
    uploadJobSpec: vi.fn(),
    downloadDocument: vi.fn(),
  },
  roles: {
    list: vi.fn().mockResolvedValue({ data: [] }),
    get: vi.fn(),
    update: vi.fn(),
    listApplicationsGlobal: vi.fn().mockResolvedValue({ data: { items: [], total: 0, limit: 50, offset: 0 } }),
    listPipeline: vi.fn().mockResolvedValue({
      data: {
        role_id: 0,
        role_name: '',
        stage: 'all',
        stage_counts: { applied: 0, invited: 0, in_assessment: 0, review: 0 },
        active_candidates_count: 0,
        items: [],
        total: 0,
        limit: 50,
        offset: 0,
      },
    }),
    listApplications: vi.fn().mockResolvedValue({ data: [] }),
    getApplication: vi.fn(),
    listApplicationShareLinks: vi.fn().mockResolvedValue({ data: { links: [] } }),
    createApplicationShareLink: vi.fn(),
    revokeShareLink: vi.fn(),
    listApplicationEvents: vi.fn().mockResolvedValue({ data: [] }),
    listTasks: vi.fn().mockResolvedValue({ data: [] }),
    batchScoreStatus: vi.fn(),
    fetchCvsStatus: vi.fn(),
    batchScore: vi.fn(),
    fetchCvs: vi.fn(),
    regenerateInterviewFocus: vi.fn(),
    generateApplicationInterviewDebrief: vi.fn(),
    downloadApplicationReport: vi.fn(),
  },
  team: { list: vi.fn().mockResolvedValue({ data: [] }), invite: vi.fn() },
  // The standing report fetches the candidate's pending agent decision for the
  // header strip (apiClient.agent.listDecisions) during its load effect, and
  // wires the decision-strip actions to the other agent methods. Vitest throws
  // on access to an undeclared export of a mocked module, so the optional chain
  // (apiClient.agent?.listDecisions) would *throw* rather than no-op without
  // this — failing the load and stranding the page on its error state.
  agent: {
    listDecisions: vi.fn().mockResolvedValue({ data: [] }),
    orgStatus: vi.fn().mockResolvedValue({ data: {
      active_role_count: 0,
      paused_role_count: 0,
      pending_decisions: 0,
    } }),
    approveDecision: vi.fn(),
    snoozeDecision: vi.fn(),
    reEvaluateDecision: vi.fn(),
  },
  default: {
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    get: vi.fn(),
    post: vi.fn(),
    create: vi.fn().mockReturnValue({
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    }),
  },
}));

vi.mock('../shared/api/authClient', () => ({
  auth: { me: vi.fn(), login: vi.fn(), register: vi.fn() },
}));

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  RadarChart: () => <div data-testid="radar-chart" />,
  PolarGrid: () => <div />,
  PolarAngleAxis: () => <div />,
  PolarRadiusAxis: () => <div />,
  Radar: () => <div />,
  LineChart: () => <div data-testid="line-chart" />,
  Line: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  CartesianGrid: () => <div />,
  Tooltip: () => <div />,
}));

vi.mock('@monaco-editor/react', () => ({
  default: () => <div data-testid="code-editor" />,
}));

import { auth } from '../shared/api/authClient';
import { agent as agentApi, roles as rolesApi } from '../shared/api';
import App from '../App';
import { AuthProvider } from '../context/AuthContext';

const mockUser = {
  id: 1,
  email: 'admin@taali.ai',
  full_name: 'Admin User',
  organization_id: 1,
  role: 'admin',
};

const roleBearingApplication = {
  id: 77,
  candidate_id: 207,
  candidate_email: 'rami@example.com',
  candidate_name: 'Rami Reddy',
  candidate_position: 'AI Engineer',
  role_id: 31,
  role_name: 'AI Engineer',
  pipeline_stage: 'advanced',
  application_outcome: 'open',
  status: 'applied',
  cv_filename: 'rami.pdf',
  cv_match_score: 68,
  cv_match_details: {
    score_scale: '0-100',
    summary: 'Strong enough CV evidence to review before sending an assessment.',
    requirements_match_score_100: 68,
  },
  assessment_history: [],
  created_at: '2026-01-10T10:00:00Z',
  updated_at: '2026-01-10T10:00:00Z',
};

const renderAppAt = (path) => {
  window.history.replaceState(null, '', path);
  return render(
    <AuthProvider>
      <App />
    </AuthProvider>
  );
};

describe('Candidate report back link', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('taali_access_token', 'fake-jwt-token');
    localStorage.setItem('taali_user', JSON.stringify(mockUser));
    auth.me.mockResolvedValue({ data: mockUser });
  });

  afterEach(() => {
    window.history.replaceState(null, '', '/');
    localStorage.clear();
  });

  // The back-link button was unified into the AgentHeader breadcrumb trail
  // (no more "Back to job"/"Back to home" buttons). The origin logic is
  // unchanged and now surfaces as the breadcrumb: ?from=jobs/<id> or a
  // role-bearing application → "Jobs / <role> / <candidate>"; explicit
  // ?from=home → "Home / <candidate>". Assertions are scoped to the
  // breadcrumb <nav> so they don't collide with the dashboard nav's own
  // Jobs/Home links.
  it('falls back to the candidate role when ?from is absent (job-opened report)', async () => {
    // Reaching the report from a job board without the ?from tag must not
    // strand the recruiter on "Home" — the candidate belongs to a role, so
    // the breadcrumb offers that role.
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });

    renderAppAt('/candidates/77');

    const crumb = await screen.findByRole('navigation', { name: /breadcrumb/i }, { timeout: 5000 });
    // Wait for the application to load (candidate name lands in the trail).
    await waitFor(
      () => expect(within(crumb).getByText('Rami Reddy')).toBeInTheDocument(),
      { timeout: 5000 },
    );
    expect(within(crumb).getByRole('link', { name: /^Jobs$/ })).toBeInTheDocument();
    expect(within(crumb).getByRole('link', { name: /^AI Engineer$/ })).toBeInTheDocument();
    expect(within(crumb).queryByText(/^Home$/)).not.toBeInTheDocument();
    await waitFor(() => {
      expect(agentApi.listDecisions).toHaveBeenCalledWith({
        application_id: 77,
        role_id: 31,
        status: 'current',
        limit: 1,
      });
    });
  });

  it('still honours an explicit ?from=home (Hub-opened report)', async () => {
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });

    renderAppAt('/candidates/77?from=home');

    const crumb = await screen.findByRole('navigation', { name: /breadcrumb/i }, { timeout: 5000 });
    await waitFor(
      () => expect(within(crumb).getByText('Rami Reddy')).toBeInTheDocument(),
      { timeout: 5000 },
    );
    expect(within(crumb).getByRole('link', { name: /^Home$/ })).toBeInTheDocument();
    expect(within(crumb).queryByRole('link', { name: /^Jobs$/ })).not.toBeInTheDocument();
  });

  it('loads a Home-opened report in the decision role context', async () => {
    rolesApi.getApplication.mockResolvedValue({
      data: {
        ...roleBearingApplication,
        role_id: 135,
        cv_match_score: 77,
        taali_score: 77,
      },
    });

    renderAppAt('/candidates/77?from=home&view_role_id=135');

    const crumb = await screen.findByRole('navigation', { name: /breadcrumb/i }, { timeout: 5000 });
    await waitFor(
      () => expect(within(crumb).getByText('Rami Reddy')).toBeInTheDocument(),
      { timeout: 5000 },
    );
    expect(rolesApi.getApplication).toHaveBeenCalledWith(77, {
      params: { view_role_id: 135 },
    });
    expect(agentApi.listDecisions).toHaveBeenCalledWith({
      application_id: 77,
      role_id: 135,
      status: 'current',
      limit: 1,
    });
    expect(screen.getByLabelText('77 of 100')).toBeInTheDocument();
  });

  it('keeps the decision aligned if a viewed role can no longer be projected', async () => {
    rolesApi.getApplication.mockResolvedValue({
      data: {
        ...roleBearingApplication,
        role_id: 31,
        cv_match_score: 67,
        taali_score: 67,
      },
    });
    agentApi.listDecisions
      .mockResolvedValueOnce({ data: [{ id: 900, application_id: 77, role_id: 135 }] })
      .mockResolvedValueOnce({ data: [{
        id: 901,
        application_id: 77,
        role_id: 31,
        candidate_name: 'Rami Reddy',
        status: 'pending',
        decision_type: 'reject',
        reasoning: 'Below the role-fit bar.',
        evidence: {},
      }] })
      .mockResolvedValueOnce({ data: [] });

    renderAppAt('/candidates/77?from=home&view_role_id=135');

    expect(await screen.findByLabelText('67 of 100', {}, { timeout: 5000 })).toBeInTheDocument();
    await waitFor(() => {
      expect(agentApi.listDecisions).toHaveBeenCalledWith({
        application_id: 77,
        role_id: 31,
        status: 'current',
        limit: 1,
      });
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Snooze' }));
    await waitFor(() => {
      expect(agentApi.snoozeDecision).toHaveBeenCalledWith(901);
      expect(agentApi.listDecisions).toHaveBeenLastCalledWith({
        application_id: 77,
        role_id: 31,
        status: 'current',
        limit: 1,
      });
    });
  });
});
