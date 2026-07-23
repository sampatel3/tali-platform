import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
    updateManualEvaluation: vi.fn(),
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
    addApplicationNote: vi.fn().mockResolvedValue({ data: {} }),
    listTasks: vi.fn().mockResolvedValue({ data: [] }),
    batchScoreStatus: vi.fn(),
    fetchCvsStatus: vi.fn(),
    batchScore: vi.fn(),
    scoreSelected: vi.fn(),
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
    overrideDecision: vi.fn(),
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

vi.mock('../shared/api/orgClient', () => ({
  organizations: {
    get: vi.fn().mockResolvedValue({ data: { id: 1, name: 'Taali' } }),
  },
}));

// Candidate report tests do not exercise background batch/sync discovery.
// AppShell mounts JobStatusProvider on every authenticated recruiter route;
// leaving the real provider active starts low-level API polling outside the
// mocked report client and leaks XMLHttpRequests into jsdom.
vi.mock('../contexts/JobStatusContext', () => ({
  JobStatusProvider: ({ children }) => children,
  useJobStatus: () => null,
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
  Legend: () => <div />,
}));

vi.mock('@monaco-editor/react', () => ({
  default: () => <div data-testid="code-editor" />,
}));

import { auth } from '../shared/api/authClient';
import { agent as agentApi, assessments as assessmentsApi, roles as rolesApi } from '../shared/api';
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

const navigateAppTo = async (path) => {
  await act(async () => {
    window.history.pushState(null, '', path);
    window.dispatchEvent(new PopStateEvent('popstate'));
    await Promise.resolve();
  });
};

const deferred = () => {
  let resolve;
  const promise = new Promise((settle) => { resolve = settle; });
  return { promise, resolve };
};

describe('Candidate report back link', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('taali_access_token', 'fake-jwt-token');
    localStorage.setItem('taali_user', JSON.stringify(mockUser));
    auth.me.mockResolvedValue({ data: mockUser });
    agentApi.listDecisions.mockReset().mockResolvedValue({ data: [] });
    agentApi.approveDecision.mockReset();
    agentApi.overrideDecision.mockReset();
    agentApi.snoozeDecision.mockReset().mockResolvedValue({ data: {} });
    agentApi.reEvaluateDecision.mockReset().mockResolvedValue({ data: {} });
    rolesApi.addApplicationNote.mockReset().mockResolvedValue({ data: {} });
    rolesApi.scoreSelected.mockReset().mockResolvedValue({ data: {} });
  });

  afterEach(() => {
    vi.useRealTimers();
    window.history.replaceState(null, '', '/');
    localStorage.clear();
  });

  it('keeps the fixture-backed candidate showcase public, client-safe, and read-only', async () => {
    localStorage.clear();

    renderAppAt('/c/demo?view=client&showcase=1&tab=assessment');

    expect(await screen.findByText('Client view.')).toBeInTheDocument();
    expect(await screen.findAllByText('Priya Raman')).not.toHaveLength(0);
    expect(window.location.pathname).toBe('/c/demo');
    expect(screen.queryByRole('button', { name: 'Share internally' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Share with client' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Assessment' })).not.toBeInTheDocument();
    expect(rolesApi.getApplication).not.toHaveBeenCalled();
  });

  it('shows the completed Assessment pane in the read-only internal product preview', async () => {
    localStorage.clear();

    renderAppAt('/c/demo?view=internal&showcase=1&tab=assessment');

    expect(await screen.findByText('Product preview.')).toBeInTheDocument();
    const assessmentLink = await screen.findByRole('link', { name: 'Assessment' });
    expect(assessmentLink).toHaveAttribute('aria-current', 'page');
    expect(await screen.findByLabelText('Assessment scorecard — the 5 Ds')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Share internally' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Share with client' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Actions' })).not.toBeInTheDocument();
    expect(screen.queryByText('YOUR EVALUATION')).not.toBeInTheDocument();
    expect(assessmentsApi.remove).not.toHaveBeenCalled();
    expect(assessmentsApi.updateManualEvaluation).not.toHaveBeenCalled();
    expect(rolesApi.getApplication).not.toHaveBeenCalled();
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

  it('mints a share link in the logical role rendered by the report', async () => {
    rolesApi.getApplication.mockResolvedValue({
      data: {
        ...roleBearingApplication,
        role_id: 135,
        role_name: 'Related AI Engineer',
        cv_match_score: 77,
        taali_score: 77,
      },
    });
    rolesApi.createApplicationShareLink.mockResolvedValue({
      data: { token: 'shr_related_role_report' },
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    renderAppAt('/candidates/77?from=home&view_role_id=135');

    fireEvent.click(await screen.findByRole('button', { name: 'Share with client' }));
    await waitFor(() => {
      expect(rolesApi.createApplicationShareLink).toHaveBeenCalledWith(77, {
        mode: 'client',
        expiry: '7d',
        viewRoleId: 135,
      });
    });
    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/share/shr_related_role_report`,
    );
  });

  it('saves notes and supporting links in the viewed logical role', async () => {
    rolesApi.getApplication.mockResolvedValue({
      data: {
        ...roleBearingApplication,
        role_id: 135,
        role_name: 'Related AI Engineer',
      },
    });

    renderAppAt('/candidates/77?from=jobs/135&view_role_id=135');

    fireEvent.click(await screen.findByRole('link', { name: 'Notes & timeline' }));
    await waitFor(() => {
      expect(rolesApi.listApplicationEvents).toHaveBeenCalledWith(77, {
        role_id: 135,
      });
    });
    fireEvent.change(await screen.findByPlaceholderText('Write a note for the hiring team…'), {
      target: { value: 'Use the related-role interview rubric.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));

    await waitFor(() => {
      expect(rolesApi.addApplicationNote).toHaveBeenCalledWith(
        77,
        'Use the related-role interview rubric.',
        true,
        { role_id: 135 },
      );
    });

    fireEvent.click(screen.getByRole('button', { name: /add supporting link/i }));
    fireEvent.change(await screen.findByLabelText(/^URL$/), {
      target: { value: 'https://example.com/related-role-portfolio' },
    });
    fireEvent.change(screen.getByLabelText(/Label/), {
      target: { value: 'Portfolio' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add link' }));

    await waitFor(() => {
      expect(rolesApi.addApplicationNote).toHaveBeenLastCalledWith(
        77,
        'Portfolio',
        true,
        {
          kind: 'link',
          link_url: 'https://example.com/related-role-portfolio',
          link_label: 'Portfolio',
          role_id: 135,
        },
      );
    });
  });

  it('keeps the newest same-role report read when overlapping refreshes resolve backwards', async () => {
    const olderRefresh = deferred();
    const newerRefresh = deferred();
    const initialApplication = {
      ...roleBearingApplication,
      role_id: 135,
      role_name: 'Related AI Engineer',
    };
    rolesApi.getApplication
      .mockResolvedValueOnce({ data: initialApplication })
      .mockReturnValueOnce(olderRefresh.promise)
      .mockReturnValueOnce(newerRefresh.promise);

    renderAppAt('/candidates/77?from=home&view_role_id=135&tab=notes');
    const note = await screen.findByPlaceholderText('Write a note for the hiring team…');
    await waitFor(() => expect(rolesApi.getApplication).toHaveBeenCalledTimes(1));

    fireEvent.change(note, { target: { value: 'Start the older refresh' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    await waitFor(() => expect(rolesApi.getApplication).toHaveBeenCalledTimes(2));

    fireEvent.change(note, { target: { value: 'Start the newer refresh' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    await waitFor(() => expect(rolesApi.getApplication).toHaveBeenCalledTimes(3));

    await act(async () => {
      newerRefresh.resolve({
        data: { ...initialApplication, cv_match_score: 91, taali_score: 91 },
      });
      await newerRefresh.promise;
    });
    expect(await screen.findByLabelText('91 of 100')).toBeInTheDocument();

    await act(async () => {
      olderRefresh.resolve({
        data: { ...initialApplication, cv_match_score: 33, taali_score: 33 },
      });
      await olderRefresh.promise;
    });
    expect(screen.getByLabelText('91 of 100')).toBeInTheDocument();
    expect(screen.queryByLabelText('33 of 100')).not.toBeInTheDocument();
  });

  it('clears role-local drafts and ignores a stale note completion after a logical-role switch', async () => {
    const staleSave = deferred();
    rolesApi.addApplicationNote
      .mockReturnValueOnce(staleSave.promise)
      .mockResolvedValue({ data: {} });
    rolesApi.getApplication.mockImplementation((_applicationId, config = {}) => {
      const logicalRoleId = Number(config?.params?.view_role_id || 31);
      return Promise.resolve({
        data: {
          ...roleBearingApplication,
          role_id: logicalRoleId,
          role_name: `Role ${logicalRoleId}`,
        },
      });
    });

    renderAppAt('/candidates/77?from=home&view_role_id=135&tab=notes');
    const roleANote = await screen.findByPlaceholderText('Write a note for the hiring team…');
    fireEvent.change(roleANote, { target: { value: 'Guidance for role A' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    await waitFor(() => expect(rolesApi.addApplicationNote).toHaveBeenCalledWith(
      77,
      'Guidance for role A',
      true,
      { role_id: 135 },
    ));

    await navigateAppTo('/candidates/77?from=home&view_role_id=246&tab=notes');
    const roleBNote = await screen.findByPlaceholderText('Write a note for the hiring team…');
    expect(roleBNote).toHaveValue('');
    fireEvent.change(roleBNote, { target: { value: 'Draft for role B' } });
    expect(screen.getByRole('button', { name: 'Add note' })).toBeEnabled();

    await act(async () => {
      staleSave.resolve({ data: {} });
      await staleSave.promise;
    });
    expect(roleBNote).toHaveValue('Draft for role B');

    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    await waitFor(() => expect(rolesApi.addApplicationNote).toHaveBeenLastCalledWith(
      77,
      'Draft for role B',
      true,
      { role_id: 246 },
    ));
  });

  it('resets an active full-evaluation state when the logical role changes', async () => {
    rolesApi.getApplication.mockImplementation((_applicationId, config = {}) => {
      const logicalRoleId = Number(config?.params?.view_role_id || 31);
      return Promise.resolve({
        data: {
          ...roleBearingApplication,
          role_id: logicalRoleId,
          role_name: `Role ${logicalRoleId}`,
          cv_match_score: null,
          taali_score: null,
          pre_screen_recommendation: 'Below threshold',
          pre_screen_evidence: { decision: 'no', summary: 'Missing required evidence.' },
        },
      });
    });

    renderAppAt('/candidates/77?from=home&view_role_id=135');
    fireEvent.click(await screen.findByRole('button', { name: 'Run full evaluation' }));
    expect(await screen.findByRole('button', { name: 'Evaluating…' })).toBeDisabled();

    await navigateAppTo('/candidates/77?from=home&view_role_id=246');

    expect(await screen.findByRole('button', { name: 'Run full evaluation' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Evaluating…' })).not.toBeInTheDocument();
    expect(rolesApi.scoreSelected).toHaveBeenCalledWith(
      135,
      [77],
      { force: true, bypassPreScreen: true },
    );
  });

  it('ignores a full-evaluation completion from the previous logical role', async () => {
    const staleEvaluation = deferred();
    rolesApi.scoreSelected.mockReturnValueOnce(staleEvaluation.promise);
    rolesApi.getApplication.mockImplementation((_applicationId, config = {}) => {
      const logicalRoleId = Number(config?.params?.view_role_id || 31);
      return Promise.resolve({
        data: {
          ...roleBearingApplication,
          role_id: logicalRoleId,
          role_name: `Role ${logicalRoleId}`,
          cv_match_score: null,
          taali_score: null,
          pre_screen_recommendation: 'Below threshold',
          pre_screen_evidence: { decision: 'no', summary: 'Missing required evidence.' },
        },
      });
    });

    renderAppAt('/candidates/77?from=home&view_role_id=135');
    fireEvent.click(await screen.findByRole('button', { name: 'Run full evaluation' }));
    expect(await screen.findByRole('button', { name: 'Queuing…' })).toBeDisabled();

    await navigateAppTo('/candidates/77?from=home&view_role_id=246');
    expect(await screen.findByRole('button', { name: 'Run full evaluation' })).toBeEnabled();

    await act(async () => {
      staleEvaluation.resolve({ data: {} });
      await staleEvaluation.promise;
    });

    expect(screen.getByRole('button', { name: 'Run full evaluation' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Evaluating…' })).not.toBeInTheDocument();
  });

  it('fails closed if the application response does not belong to the requested role', async () => {
    rolesApi.getApplication.mockResolvedValue({
      data: {
        ...roleBearingApplication,
        role_id: 31,
        cv_match_score: 67,
        taali_score: 67,
      },
    });
    agentApi.listDecisions
      .mockResolvedValueOnce({ data: [{ id: 900, application_id: 77, role_id: 135 }] });

    renderAppAt('/candidates/77?from=home&view_role_id=135');

    expect(await screen.findByText('Candidate is not available in this role.'))
      .toBeInTheDocument();
    expect(screen.queryByLabelText('67 of 100')).not.toBeInTheDocument();
    expect(agentApi.listDecisions).toHaveBeenCalledWith({
      application_id: 77,
      role_id: 135,
      status: 'current',
      limit: 1,
    });
    expect(agentApi.listDecisions).not.toHaveBeenCalledWith(expect.objectContaining({
      role_id: 31,
    }));
    expect(agentApi.snoozeDecision).not.toHaveBeenCalled();
  });

  const pendingDecision = {
    id: 910,
    application_id: 77,
    role_id: 31,
    candidate_name: 'Rami Reddy',
    status: 'pending',
    decision_type: 'send_assessment',
    reasoning: 'Assessment recommended.',
    evidence: {},
  };

  it('ignores a slower prior-candidate response after route navigation', async () => {
    let resolveFirstApplication;
    const nextApplication = {
      ...roleBearingApplication,
      id: 88,
      candidate_id: 208,
      candidate_name: 'Bea Byte',
      candidate_email: 'bea@example.com',
    };
    rolesApi.getApplication.mockImplementation((applicationId) => {
      if (Number(applicationId) === 77) {
        return new Promise((resolve) => { resolveFirstApplication = resolve; });
      }
      return Promise.resolve({ data: nextApplication });
    });
    agentApi.listDecisions.mockImplementation(({ application_id: applicationId }) => (
      Number(applicationId) === 88
        ? Promise.reject(new Error('decision read unavailable'))
        : Promise.resolve({ data: [pendingDecision] })
    ));

    renderAppAt('/candidates/77');
    await waitFor(() => expect(rolesApi.getApplication).toHaveBeenCalledWith(77, {}));

    await act(async () => {
      window.history.pushState(null, '', '/candidates/88');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    const breadcrumb = await screen.findByRole('navigation', { name: /breadcrumb/i });
    await waitFor(() => expect(within(breadcrumb).getByText('Bea Byte')).toBeInTheDocument());

    await act(async () => {
      resolveFirstApplication({ data: roleBearingApplication });
      await Promise.resolve();
    });

    await waitFor(() => expect(within(breadcrumb).getByText('Bea Byte')).toBeInTheDocument());
    expect(within(breadcrumb).queryByText('Rami Reddy')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send assessment' })).not.toBeInTheDocument();
  });

  it('ignores an old candidate action that settles after route navigation', async () => {
    let resolveReEvaluate;
    const nextApplication = {
      ...roleBearingApplication,
      id: 88,
      candidate_id: 208,
      candidate_name: 'Bea Byte',
      candidate_email: 'bea@example.com',
    };
    rolesApi.getApplication.mockImplementation((applicationId) => Promise.resolve({
      data: Number(applicationId) === 88 ? nextApplication : roleBearingApplication,
    }));
    agentApi.listDecisions.mockImplementation(({ application_id: applicationId }) => Promise.resolve({
      data: Number(applicationId) === 77 ? [pendingDecision] : [],
    }));
    agentApi.reEvaluateDecision.mockImplementationOnce(() => new Promise((resolve) => {
      resolveReEvaluate = resolve;
    }));

    renderAppAt('/candidates/77');
    fireEvent.click(await screen.findByRole('button', { name: 'Re-evaluate' }));
    await waitFor(() => expect(agentApi.reEvaluateDecision).toHaveBeenCalledWith(910));

    await act(async () => {
      window.history.pushState(null, '', '/candidates/88');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    const breadcrumb = await screen.findByRole('navigation', { name: /breadcrumb/i });
    await waitFor(() => expect(within(breadcrumb).getByText('Bea Byte')).toBeInTheDocument());

    await act(async () => {
      resolveReEvaluate({ data: {} });
      await Promise.resolve();
    });

    expect(document.querySelector('.dossier')).not.toHaveAttribute('aria-busy');
    expect(screen.queryByText('Re-evaluating with fresh inputs…')).not.toBeInTheDocument();
    expect(rolesApi.getApplication.mock.calls.filter(([id]) => Number(id) === 77)).toHaveLength(1);
  });

  it('freezes a directly accepted decision when both refresh reads fail', async () => {
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });
    agentApi.listDecisions
      .mockResolvedValueOnce({ data: [pendingDecision] })
      .mockRejectedValue(new Error('refresh unavailable'));
    agentApi.approveDecision.mockResolvedValueOnce({
      data: { decision_id: 910, accepted: true },
    });
    renderAppAt('/candidates/77');

    fireEvent.click(await screen.findByRole('button', { name: 'Send assessment' }));

    expect(await screen.findByText('Processing', { selector: '.dr-decided-outcome' }))
      .toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send assessment' })).not.toBeInTheDocument();
    expect(agentApi.approveDecision).toHaveBeenCalledWith(910, {}, { force: false });
    expect(await screen.findByText('Accepted for processing.')).toBeInTheDocument();
  });

  it('blocks changed-input approval on the candidate report', async () => {
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });
    agentApi.listDecisions.mockResolvedValue({
      data: [{
        ...pendingDecision,
        is_stale: true,
        staleness_reasons: ['score_generation_changed'],
      }],
    });
    renderAppAt('/candidates/77');

    expect(await screen.findByRole('button', { name: 'Send assessment' })).toBeDisabled();
    expect(screen.getByText(/Inputs changed since this was decided/i)).toBeInTheDocument();
    expect(agentApi.approveDecision).not.toHaveBeenCalled();
  });

  it('forces only an unchanged old-engine decision from the candidate report', async () => {
    const oldEngineDecision = {
      ...pendingDecision,
      is_stale: true,
      staleness_reasons: ['engine_outdated'],
    };
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });
    agentApi.listDecisions
      .mockResolvedValueOnce({ data: [oldEngineDecision] })
      .mockRejectedValue(new Error('refresh unavailable'));
    agentApi.approveDecision.mockResolvedValueOnce({
      data: { decision_id: 910, accepted: true },
    });
    renderAppAt('/candidates/77');

    fireEvent.click(await screen.findByRole('button', { name: 'Send assessment' }));

    await waitFor(() => {
      expect(agentApi.approveDecision).toHaveBeenCalledWith(910, {}, { force: true });
    });
  });

  it('does not let an older pending read overwrite a newer terminal decision', async () => {
    let resolveApproval;
    let resolveTerminalRead;
    let resolveStaleRead;
    const approval = new Promise((resolve) => { resolveApproval = resolve; });
    const terminalRead = new Promise((resolve) => { resolveTerminalRead = resolve; });
    const staleRead = new Promise((resolve) => { resolveStaleRead = resolve; });
    let decisionReadCount = 0;
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });
    agentApi.listDecisions.mockImplementation(() => {
      decisionReadCount += 1;
      if (decisionReadCount === 1) return Promise.resolve({ data: [pendingDecision] });
      if (decisionReadCount === 2) return staleRead;
      if (decisionReadCount === 3) return terminalRead;
      return Promise.resolve({ data: [{ ...pendingDecision, status: 'approved' }] });
    });
    agentApi.approveDecision.mockReturnValueOnce(approval);
    renderAppAt('/candidates/77');

    fireEvent.click(await screen.findByRole('button', { name: 'Send assessment' }));
    vi.useFakeTimers();
    await act(async () => {
      resolveApproval({ data: { decision_id: 910, accepted: true } });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(agentApi.listDecisions).toHaveBeenCalledTimes(2);

    await act(async () => {
      vi.advanceTimersByTime(4000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(agentApi.listDecisions).toHaveBeenCalledTimes(3);

    await act(async () => {
      resolveTerminalRead({ data: [{ ...pendingDecision, status: 'approved' }] });
      await Promise.resolve();
    });
    expect(screen.getByText('Advanced', { selector: '.dr-decided-outcome' })).toBeInTheDocument();

    await act(async () => {
      resolveStaleRead({ data: [pendingDecision] });
      await Promise.resolve();
    });
    expect(screen.getByText('Advanced', { selector: '.dr-decided-outcome' }))
      .toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send assessment' })).not.toBeInTheDocument();
  });

  it('keeps a successful override read-only when both refresh reads fail', async () => {
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });
    agentApi.listDecisions
      .mockResolvedValueOnce({ data: [pendingDecision] })
      .mockRejectedValue(new Error('refresh unavailable'));
    agentApi.overrideDecision.mockResolvedValueOnce({
      data: { ...pendingDecision, status: 'processing' },
    });
    renderAppAt('/candidates/77');

    fireEvent.click(await screen.findByRole('button', { name: 'Reject' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/Why\?/i), {
      target: { value: 'Confirmed role mismatch' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reject' }));

    await waitFor(() => {
      expect(agentApi.overrideDecision).toHaveBeenCalledWith(910, {
        note: 'Confirmed role mismatch',
        override_action: 'reject',
      });
    });
    expect(await screen.findByText('Processing', { selector: '.dr-decided-outcome' }))
      .toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send assessment' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument();
    expect(await screen.findByText('Reject accepted for processing.')).toBeInTheDocument();
  });

  it('restores an override after a definitive rejection', async () => {
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });
    agentApi.listDecisions
      .mockResolvedValueOnce({ data: [pendingDecision] })
      .mockRejectedValue(new Error('refresh unavailable'));
    agentApi.overrideDecision.mockRejectedValueOnce({
      response: { status: 400, data: { detail: 'Override was rejected.' } },
    });
    renderAppAt('/candidates/77');

    fireEvent.click(await screen.findByRole('button', { name: 'Reject' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/Why\?/i), {
      target: { value: 'Confirmed role mismatch' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reject' }));

    expect(await within(dialog).findByText('Override was rejected.')).toBeInTheDocument();
    await waitFor(() => {
      expect(within(document.querySelector('.dossier-rail')).getByRole(
        'button',
        { name: 'Send assessment' },
      )).toBeEnabled();
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    expect(await screen.findByRole('button', { name: 'Send assessment' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeEnabled();
    expect(agentApi.listDecisions).toHaveBeenCalledTimes(2);
  });

  it('keeps an ambiguous override read-only while its outcome is checked', async () => {
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });
    agentApi.listDecisions
      .mockResolvedValueOnce({ data: [pendingDecision] })
      .mockResolvedValueOnce({ data: [pendingDecision] });
    agentApi.overrideDecision.mockRejectedValueOnce({ code: 'ERR_NETWORK' });
    renderAppAt('/candidates/77');

    fireEvent.click(await screen.findByRole('button', { name: 'Reject' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/Why\?/i), {
      target: { value: 'Confirmed role mismatch' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reject' }));

    expect(await screen.findByText('Checking status', { selector: '.dr-decided-outcome' }))
      .toBeInTheDocument();
    expect(await screen.findByText(
      "We couldn't confirm this action. Refresh before taking another action.",
    )).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send assessment' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument();
  });

  it('renders a same-id skip reclassification as the new pending decision', async () => {
    const reclassifiedDecision = {
      ...pendingDecision,
      status: 'pending',
      decision_type: 'advance_to_interview',
      evidence: { reclassified_by: 'recruiter_skip_assessment_advance' },
    };
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });
    agentApi.listDecisions
      .mockResolvedValueOnce({ data: [pendingDecision] })
      .mockRejectedValue(new Error('refresh unavailable'));
    agentApi.overrideDecision.mockResolvedValueOnce({ data: reclassifiedDecision });
    renderAppAt('/candidates/77');

    fireEvent.click(await screen.findByRole('button', { name: 'Skip & advance' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/Why\?/i), {
      target: { value: 'Pre-vetted internal referral' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Move to advance queue' }));

    await waitFor(() => {
      expect(agentApi.overrideDecision).toHaveBeenCalledWith(910, {
        note: 'Pre-vetted internal referral',
        override_action: 'skip_assessment_advance',
      });
    });
    expect(await screen.findByRole('button', { name: 'Advance to next stage' })).toBeEnabled();
    expect(screen.queryByText('Processing', { selector: '.dr-decided-outcome' }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Skip & advance' })).not.toBeInTheDocument();
  });

  it.each(['processing', 'approved'])(
    'reconciles a lost response at %s and keeps a processing receipt visible',
    async (status) => {
      rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });
      agentApi.listDecisions
        .mockResolvedValueOnce({ data: [pendingDecision] })
        .mockResolvedValueOnce({ data: [{ ...pendingDecision, status }] })
        .mockRejectedValue(new Error('refresh unavailable'));
      agentApi.approveDecision.mockRejectedValueOnce({ code: 'ERR_NETWORK' });
      renderAppAt('/candidates/77');

      fireEvent.click(await screen.findByRole('button', { name: 'Send assessment' }));

      expect(await screen.findByText('Processing', { selector: '.dr-decided-outcome' }))
        .toBeInTheDocument();
      expect(agentApi.listDecisions).toHaveBeenCalledWith(
        { application_id: 77, status: 'current', limit: 50 },
        { timeout: 10000 },
      );
      expect(await screen.findByText('Accepted for processing.')).toBeInTheDocument();
    },
  );

  it('keeps an unresolved outcome read-only with the safe message', async () => {
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });
    agentApi.listDecisions
      .mockResolvedValueOnce({ data: [pendingDecision] })
      .mockResolvedValueOnce({ data: [{ ...pendingDecision }] });
    agentApi.approveDecision.mockRejectedValueOnce({ code: 'ERR_NETWORK' });
    renderAppAt('/candidates/77');

    fireEvent.click(await screen.findByRole('button', { name: 'Send assessment' }));

    expect(await screen.findByText('Checking status', { selector: '.dr-decided-outcome' }))
      .toBeInTheDocument();
    expect(await screen.findByText(
      "We couldn't confirm this action. Refresh before taking another action.",
    )).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send assessment' })).not.toBeInTheDocument();
  });

  it('leaves a definitive failure actionable', async () => {
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });
    agentApi.listDecisions.mockResolvedValueOnce({ data: [pendingDecision] });
    agentApi.approveDecision.mockRejectedValueOnce({
      response: { status: 503, data: { detail: 'Nothing was sent; please try again.' } },
    });
    renderAppAt('/candidates/77');

    fireEvent.click(await screen.findByRole('button', { name: 'Send assessment' }));

    expect(await screen.findByText('Nothing was sent; please try again.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send assessment' })).toBeEnabled();
    expect(agentApi.listDecisions).toHaveBeenCalledTimes(1);
  });
});
