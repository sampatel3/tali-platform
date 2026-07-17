import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { Link, Route, Routes, useNavigate } from 'react-router-dom';

import TestMemoryRouter from '../../test/TestMemoryRouter';

const showToast = vi.fn();
const authState = vi.hoisted(() => ({ user: { role: 'owner' } }));

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => authState,
}));

// Some role-page descendants (notably AgentNeedsInputCard) import the
// raw axios instance from `httpClient` directly instead of going
// through the `apiClient.*` namespace. Mock both so no real network
// dispatch happens — that's what was causing the jsdom undici flake.
vi.mock('../../shared/api/httpClient', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: null }),
    put: vi.fn().mockResolvedValue({ data: null }),
    patch: vi.fn().mockResolvedValue({ data: null }),
    delete: vi.fn().mockResolvedValue({ data: null }),
  },
}));

vi.mock('../../shared/api', () => ({
  roles: {
    get: vi.fn(),
    getShell: vi.fn(),
    getApplication: vi.fn(),
    listTasks: vi.fn(),
    listApplications: vi.fn(),
    updateApplicationOutcome: vi.fn(),
    updateApplicationStage: vi.fn(),
    createAssessment: vi.fn(),
    moveApplicationToWorkableStage: vi.fn(),
    moveApplicationToAtsStage: vi.fn(),
    batchScoreStatus: vi.fn(),
    fetchCvsStatus: vi.fn(),
    batchPreScreenStatus: vi.fn(),
    update: vi.fn(),
    updateJobSpec: vi.fn(),
    setJobStatus: vi.fn(),
    setClient: vi.fn(),
    processRole: vi.fn(),
    regenerateInterviewFocus: vi.fn(),
    fetchCvs: vi.fn(),
    batchPreScreen: vi.fn(),
    batchScore: vi.fn(),
    createCriterion: vi.fn(),
    updateCriterion: vi.fn(),
    deleteCriterion: vi.fn(),
    syncCriteriaWithWorkspace: vi.fn(),
    resetCriteriaToWorkspace: vi.fn(),
    listFeedbackNotes: vi.fn(),
    createFeedbackNote: vi.fn(),
    listScreeningQuestions: vi.fn(),
    createScreeningQuestion: vi.fn(),
    updateScreeningQuestion: vi.fn(),
    deleteScreeningQuestion: vi.fn(),
    addTask: vi.fn(),
    removeTask: vi.fn(),
    distribution: vi.fn(),
    sisterScoringStatus: vi.fn(),
    rescoreSister: vi.fn(),
    previewSister: vi.fn(),
    createSister: vi.fn(),
  },
  tasks: {
    list: vi.fn(),
  },
  organizations: {
    get: vi.fn().mockResolvedValue({ data: { default_role_requirements: [] } }),
    listCriteria: vi.fn().mockResolvedValue({ data: [] }),
    getWorkableStages: vi.fn().mockResolvedValue({ data: { stages: [] } }),
    getBullhornStageMap: vi.fn().mockResolvedValue({
      data: { mappings: [], resolved_write_targets: {} },
    }),
  },
  agent: {
    status: vi.fn().mockResolvedValue({ data: null }),
    usageBreakdown: vi.fn().mockResolvedValue({ data: null }),
    listDecisionExecutionSnapshots: vi.fn().mockResolvedValue({ data: [] }),
    approveDecision: vi.fn().mockResolvedValue({ data: null }),
    overrideDecision: vi.fn().mockResolvedValue({ data: null }),
    discardPending: vi.fn().mockResolvedValue({ data: null }),
    pause: vi.fn().mockResolvedValue({ data: null }),
    resume: vi.fn().mockResolvedValue({ data: null }),
    resumeAll: vi.fn().mockResolvedValue({ data: null }),
    recoverRelatedRole: vi.fn().mockResolvedValue({ data: null }),
    relatedRoleRecoveryScope: vi.fn().mockResolvedValue({ data: null }),
    runNow: vi.fn().mockResolvedValue({ data: null }),
  },
}));

vi.mock('../candidates/CandidateSheet', () => ({
  CandidateSheet: () => null,
}));

vi.mock('../requisitions/api', () => ({
  requisitionApi: {
    createRelated: vi.fn(),
  },
}));

vi.mock('../clients/api', () => ({
  clientApi: {
    list: vi.fn(),
  },
}));

// CRUD behavior has its own focused test. Keep this large pipeline suite from
// scheduling a second independent settings fetch on every Agent settings case.
vi.mock('./RoleScreeningQuestions', () => ({
  default: () => <div data-testid="screening-question-editor" />,
}));

import * as apiClient from '../../shared/api';
import { clearCache, readCache, writeCache } from '../../shared/api/resourceCache';
import { clientApi } from '../clients/api';
import { requisitionApi } from '../requisitions/api';
import { JobPipelinePage } from './JobPipelinePage';

const baseRole = {
  id: 101,
  version: 7,
  name: 'AI Native Engineer',
  source: 'workable',
  active_candidates_count: 2,
  score_threshold: null,
  stage_counts: {
    applied: 1,
    invited: 0,
    in_assessment: 0,
    review: 1,
  },
  interview_focus: { questions: [] },
  auto_promote: true,
};

const baseApplications = [
  {
    id: 1,
    candidate_id: 11,
    candidate_name: 'Sam Patel',
    candidate_email: 'sam@example.com',
    pipeline_stage: 'applied',
    application_outcome: 'open',
    pre_screen_score: 91,
    taali_score: 63,
    status: 'applied',
    created_at: '2026-04-26T02:00:00Z',
    updated_at: '2026-04-26T02:00:00Z',
    // Lower score (63) but the most recent activity — used to prove the
    // Last updated sort orders independently of score.
    last_activity_at: '2026-05-22T00:00:00Z',
  },
  {
    id: 2,
    candidate_id: 22,
    candidate_name: 'Priya Anand',
    candidate_email: 'priya@example.com',
    pipeline_stage: 'review',
    application_outcome: 'open',
    pre_screen_score: 88,
    taali_score: 64,
    status: 'completed',
    created_at: '2026-04-26T01:00:00Z',
    updated_at: '2026-04-26T01:00:00Z',
    // Higher score (64) but older activity than Sam.
    last_activity_at: '2026-04-20T00:00:00Z',
    score_summary: {
      taali_score: 64,
      assessment_id: 32,
    },
  },
];

const sourcedApplication = {
  ...baseApplications[0],
  pipeline_stage: 'sourced',
};

const deferred = () => {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
};

const PipelineRoute = ({ onNavigate }) => {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate('/jobs/101')}>Open role 101</button>
      <button type="button" onClick={() => navigate('/jobs/202')}>Open role 202</button>
      <JobPipelinePage onNavigate={onNavigate} />
    </>
  );
};

const renderPipeline = ({ onNavigate = vi.fn(), roleSwitcher = false } = {}) => ({
  onNavigate,
  ...render(
    <TestMemoryRouter initialEntries={['/jobs/101']}>
      {roleSwitcher ? <Link to="/jobs/202">Switch role</Link> : null}
      <Routes>
        <Route path="/jobs/:roleId" element={<PipelineRoute onNavigate={onNavigate} />} />
        <Route path="/chat/agents/:roleId" element={<div>Role agent chat route</div>} />
        <Route path="/requisitions" element={<div>Related role draft chat</div>} />
      </Routes>
    </TestMemoryRouter>
  ),
});

const RouteSwitchButton = () => {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate('/jobs/102')}>Switch role</button>;
};

describe('JobPipelinePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'owner' };
    clearCache();
    apiClient.roles.listApplicationsPage = undefined;
    apiClient.roles.get.mockResolvedValue({ data: baseRole });
    apiClient.roles.getShell.mockResolvedValue({ data: baseRole });
    apiClient.roles.update.mockResolvedValue({ data: baseRole });
    apiClient.roles.updateJobSpec.mockResolvedValue({
      data: {
        applied: true,
        role: baseRole,
        diff: { added: 0, removed: 0, criteria_count: 0 },
        would_rescreen: { count: 0, est_cost_usd: 0 },
      },
    });
    apiClient.roles.setJobStatus.mockResolvedValue({ data: baseRole });
    apiClient.roles.setClient.mockResolvedValue({ data: baseRole });
    apiClient.roles.processRole.mockResolvedValue({ data: { status: 'queued' } });
    apiClient.roles.listTasks.mockResolvedValue({ data: [] });
    apiClient.roles.listApplications.mockResolvedValue({ data: baseApplications });
    apiClient.roles.batchScoreStatus.mockResolvedValue({ data: { status: 'idle', total: 0, scored: 0, errors: 0 } });
    apiClient.roles.fetchCvsStatus.mockResolvedValue({ data: { status: 'idle', total: 0, fetched: 0, errors: 0 } });
    apiClient.roles.batchPreScreenStatus.mockResolvedValue({ data: { status: 'idle', total: 0, processed: 0, errors: 0 } });
    apiClient.roles.setJobStatus.mockResolvedValue({ data: null });
    apiClient.roles.listFeedbackNotes.mockResolvedValue({ data: [] });
    apiClient.roles.listScreeningQuestions.mockResolvedValue({ data: [] });
    apiClient.roles.distribution.mockResolvedValue({ data: { published: false } });
    apiClient.roles.sisterScoringStatus.mockResolvedValue({
      data: { status: 'completed', progress_percent: 100, counts: { done: 2 } },
    });
    apiClient.agent.listDecisionExecutionSnapshots.mockResolvedValue({ data: [] });
    apiClient.agent.status.mockResolvedValue({ data: { can_control_agent: true } });
    apiClient.agent.relatedRoleRecoveryScope.mockResolvedValue({ data: null });
    apiClient.tasks.list.mockResolvedValue({ data: [] });
    clientApi.list.mockResolvedValue([]);
    requisitionApi.createRelated.mockResolvedValue({ id: 44 });
  });

  // Default view is the candidates table; pipeline kanban is opt-in. Tests
  // that assert on kanban cards switch to the Pipeline tab first.
  const switchToPipelineView = async () => {
    fireEvent.click(await screen.findByRole('link', { name: /^Pipeline$/i }));
  };

  // Per HANDOFF v2 §4.3 / canvas jobs-detail-settings — CV scoring criteria
  // and Screening threshold live on the Agent settings tab now (the legacy
  // above-tabs score-panel was retired). Tests that assert on those
  // controls open the tab first.
  const openAgentSettingsTab = async (section = 'Decision rules') => {
    const tab = await screen.findByRole('link', { name: /^Agent settings$/i });
    await act(async () => {
      fireEvent.click(tab);
    });
    if (section) {
      const navigation = await screen.findByRole('navigation', { name: 'Agent settings sections' });
      fireEvent.click(within(navigation).getByRole('link', { name: new RegExp(`^${section}`, 'i') }));
    }
  };

  const confirmTurnOnPolicy = async () => {
    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
    expect(await screen.findByRole('heading', { name: /Turn on (?:the )?agent/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Turn on agent$/i }));
  };

  it('paints the job shell before a large candidate roster finishes loading', async () => {
    apiClient.roles.listApplications.mockReturnValue(new Promise(() => {}));

    renderPipeline();

    expect(await screen.findByRole('heading', { name: /AI Native Engineer/i })).toBeInTheDocument();
    expect(screen.getAllByRole('status').some((node) => (
      node.textContent?.includes('Loading candidates…')
    ))).toBe(true);
  });

  it('paints page one, loads every outcome sequentially, and bounds the SWR roster', async () => {
    const firstPage = Array.from({ length: 200 }, (_, index) => ({
      ...baseApplications[0],
      id: 1000 + index,
      candidate_id: 2000 + index,
      candidate_name: index === 0 ? 'First Page Leader' : `Paged Candidate ${index}`,
      candidate_email: `paged-${index}@example.com`,
      taali_score: index === 0 ? 100 : 50,
    }));
    const tail = {
      ...baseApplications[0], id: 5000, candidate_id: 5000,
      candidate_name: 'Open Page Tail', candidate_email: 'tail@example.com', taali_score: 99,
    };
    const rejected = {
      ...baseApplications[0], id: 6000, candidate_id: 6000,
      candidate_name: 'Rejected History', candidate_email: 'rejected@example.com',
      application_outcome: 'rejected', taali_score: 98,
    };
    let releaseSecondPage;
    const secondPage = new Promise((resolve) => { releaseSecondPage = resolve; });
    apiClient.roles.listApplicationsPage = vi.fn((_, params) => {
      if (params.application_outcome === 'open' && params.offset === 0) {
        return Promise.resolve({ data: { items: firstPage, total: 201, limit: 200, offset: 0 } });
      }
      if (params.application_outcome === 'open' && params.offset === 200) return secondPage;
      if (params.application_outcome === 'rejected' && params.offset === 0) {
        return Promise.resolve({ data: { items: [rejected], total: 1, limit: 200, offset: 0 } });
      }
      throw new Error(`Unexpected page ${params.application_outcome}:${params.offset}`);
    });

    renderPipeline();

    expect(await screen.findByText('First Page Leader')).toBeInTheDocument();
    expect(screen.queryByText('Open Page Tail')).not.toBeInTheDocument();
    await waitFor(() => expect(apiClient.roles.listApplicationsPage).toHaveBeenCalledTimes(2));

    await act(async () => {
      releaseSecondPage({ data: { items: [tail], total: null, limit: 200, offset: 200 } });
    });

    expect(await screen.findByText('Open Page Tail')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /Rejected, 1/i }));
    expect(await screen.findByText('Rejected History')).toBeInTheDocument();
    expect(apiClient.roles.listApplicationsPage.mock.calls.map(([, params]) => [
      params.application_outcome,
      params.offset,
      params.limit,
    ])).toEqual([
      ['open', 0, 200],
      ['open', 200, 200],
      ['rejected', 0, 200],
    ]);

    await waitFor(() => expect(readCache('role-workspace:101')?.data?.roleApplications).toHaveLength(200));
    const cachedIds = readCache('role-workspace:101').data.roleApplications.map((app) => app.id);
    expect(cachedIds).not.toContain(tail.id);
    expect(cachedIds).not.toContain(rejected.id);
  });

  it('paints the job shell while the aggregate role detail is still loading', async () => {
    apiClient.roles.get.mockReturnValue(new Promise(() => {}));

    renderPipeline();

    expect(await screen.findByRole('heading', { name: /AI Native Engineer/i })).toBeInTheDocument();
    expect(screen.getAllByRole('status').some((node) => (
      node.textContent?.includes('Loading pipeline summary…')
    ))).toBe(true);
    expect(apiClient.roles.getShell).toHaveBeenCalledWith(101);
  });

  it('preserves warm-cached role detail while its lightweight shell revalidates', async () => {
    const cachedRole = {
      ...baseRole,
      description: 'Cached source description',
      job_spec_text: 'Description\nCached full job specification survives the shell refresh.',
      criteria: [{ id: 4, text: 'Cached recruiter requirement', source: 'recruiter' }],
      requisition: { id: 31, title: 'Cached requisition' },
      client_id: 8,
      client_name: 'Cached client',
      tasks_count: 3,
      applications_count: 9,
      pending_decisions_by_type: { rejection: 2 },
      is_published: true,
    };
    writeCache('role-workspace:101', {
      role: cachedRole,
      roleTasks: [],
      assessmentContextTasks: [],
      roleApplications: baseApplications,
      workspaceCriteria: [],
    });
    apiClient.roles.getShell.mockResolvedValue({
      data: {
        ...baseRole,
        version: 8,
        description: null,
        job_spec_text: null,
        criteria: [],
        requisition: null,
        client_id: null,
        client_name: null,
        tasks_count: 0,
        applications_count: 0,
        stage_counts: {},
        pending_decisions_by_type: {},
        active_candidates_count: 0,
        is_published: false,
      },
    });
    apiClient.roles.get.mockReturnValue(new Promise(() => {}));

    renderPipeline();

    await waitFor(() => expect(apiClient.roles.getShell).toHaveBeenCalledWith(101));
    fireEvent.click(await screen.findByRole('link', { name: /^Job spec$/i }));
    fireEvent.click(await screen.findByRole('button', { name: /View description/i }));
    expect(await screen.findAllByText('Cached full job specification survives the shell refresh.'))
      .not.toHaveLength(0);
    expect(screen.getByText('Cached recruiter requirement')).toBeInTheDocument();
  });

  it('does not present the role agent as on before workspace status has loaded', async () => {
    const enabledRole = { ...baseRole, agentic_mode_enabled: true };
    const now = new Date().toISOString();
    let resolveStatus;
    apiClient.roles.getShell.mockResolvedValue({ data: enabledRole });
    apiClient.roles.get.mockResolvedValue({ data: enabledRole });
    apiClient.agent.status.mockImplementation(() => new Promise((resolve) => {
      resolveStatus = resolve;
    }));

    const { container } = renderPipeline();

    expect(await screen.findByLabelText('Agent status')).toBeInTheDocument();
    const bar = container.querySelector('.abar');
    expect(bar).toHaveClass('abar-loading');
    expect(bar).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByText('Checking role and workspace controls…')).toBeInTheDocument();
    expect(screen.queryByLabelText('Agent on')).not.toBeInTheDocument();
    expect(within(bar).queryByText('AI spend')).not.toBeInTheDocument();
    expect(within(bar).queryByRole('progressbar')).not.toBeInTheDocument();
    expect(bar.querySelector('.ab-actions')).toBeNull();

    await act(async () => {
      resolveStatus({ data: {
        enabled: true,
        paused: true,
        pause_scope: 'workspace',
        paused_at: now,
        paused_reason: 'workspace paused by recruiter',
        paused_by: { user_id: 7, name: 'Sam Patel', is_current_user: true },
        workspace_paused: true,
        workspace_control_version: 4,
        workspace_paused_at: now,
        workspace_paused_reason: 'workspace paused by recruiter',
        workspace_paused_by: { user_id: 7, name: 'Sam Patel', is_current_user: true },
        monthly_spent_cents: 1605,
        monthly_budget_cents: 50000,
        pending_decisions: 31,
      } });
    });

    expect(await screen.findByLabelText('All agents paused')).toBeInTheDocument();
    expect(screen.getByText('AI spend')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pause this role' })).toBeInTheDocument();
  });

  it('keeps controls unavailable after a failed status read and restores them on retry', async () => {
    const enabledRole = { ...baseRole, agentic_mode_enabled: true };
    const now = new Date().toISOString();
    apiClient.roles.getShell.mockResolvedValue({ data: enabledRole });
    apiClient.roles.get.mockResolvedValue({ data: enabledRole });
    apiClient.agent.status
      .mockRejectedValueOnce(new Error('status unavailable'))
      .mockResolvedValueOnce({ data: {
        enabled: true,
        paused: true,
        pause_scope: 'workspace',
        paused_at: now,
        paused_reason: 'workspace paused by recruiter',
        paused_by: { user_id: 7, name: 'Sam Patel', is_current_user: true },
        workspace_paused: true,
        workspace_control_version: 4,
        workspace_paused_at: now,
        workspace_paused_reason: 'workspace paused by recruiter',
        workspace_paused_by: { user_id: 7, name: 'Sam Patel', is_current_user: true },
        monthly_spent_cents: 1605,
        monthly_budget_cents: 50000,
        pending_decisions: 31,
      } });

    const { container } = renderPipeline();

    expect(await screen.findByLabelText('Agent status unavailable')).toBeInTheDocument();
    const bar = container.querySelector('.abar');
    expect(bar).toHaveClass('abar-unavailable');
    expect(bar).not.toHaveAttribute('aria-busy');
    expect(within(bar).getByRole('status')).toHaveAccessibleName('Agent status unavailable');
    expect(within(bar).queryByText('AI spend')).not.toBeInTheDocument();
    expect(bar.querySelector('.ab-actions')).toBeNull();

    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });

    await waitFor(() => expect(apiClient.agent.status).toHaveBeenCalledTimes(2));
    expect(await screen.findByLabelText('All agents paused')).toBeInTheDocument();
    expect(within(bar).getByRole('status')).toHaveAccessibleName('All agents paused');
    expect(screen.getByRole('button', { name: 'Pause this role' })).toBeInTheDocument();
  });

  it('renders the reject-threshold slider on the Agent settings tab without a spinbutton', async () => {
    renderPipeline();
    await openAgentSettingsTab();

    await screen.findByRole('heading', { name: /Screening threshold/i, level: 2 });

    expect(screen.getByRole('slider', { name: /Screening threshold percent/i })).toBeInTheDocument();
    // The threshold is a slider only — no spinbutton anywhere on the tab.
    // (The agent bar's budget input is its own spinbutton, outside scope.)
    const settingsRegion = document.querySelector('.mc-agent-settings');
    expect(settingsRegion).toBeInTheDocument();
    expect(within(settingsRegion).queryByRole('spinbutton')).not.toBeInTheDocument();
  });

  it('uses pressed filter semantics for the candidate stage lens', async () => {
    renderPipeline();

    const stageFilter = await screen.findByRole('group', { name: 'Filter candidates by stage' });
    const all = within(stageFilter).getByRole('button', { name: /^All/i });
    const applied = within(stageFilter).getByRole('button', { name: /^Applied/i });
    expect(all).toHaveAttribute('aria-pressed', 'true');
    expect(stageFilter).not.toHaveAttribute('role', 'tablist');

    fireEvent.click(applied);

    expect(applied).toHaveAttribute('aria-pressed', 'true');
    expect(all).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText('Sam Patel')).toBeInTheDocument();
    expect(screen.queryByText('Priya Anand')).not.toBeInTheDocument();
  });

  it('updates pre-screen rejection without changing scored rejection', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: { ...baseRole, auto_reject: true, auto_reject_pre_screen: true },
    });
    renderPipeline();
    await openAgentSettingsTab();

    fireEvent.click(await screen.findByRole('button', {
      name: 'Auto-reject pre-screen failures',
    }));
    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(101, {
      auto_reject_pre_screen: false,
      expected_version: 7,
    }));

    fireEvent.click(await screen.findByRole('button', {
      name: 'Auto-reject after scoring',
    }));
    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(101, {
      auto_reject: false,
      expected_version: 7,
    }));
  });

  it('materializes the complete visible policy when one untouched-role setting changes', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        auto_promote: false,
        auto_send_assessment: null,
        auto_resend_assessment: null,
        auto_advance: null,
        agent_effective_policy: {
          auto_send_assessment: false,
          auto_resend_assessment: false,
          auto_advance: false,
        },
      },
    });
    renderPipeline();
    await openAgentSettingsTab();

    const send = await screen.findByRole('button', { name: 'Auto-send assessments' });
    expect(send).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(send);

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(101, {
      auto_send_assessment: true,
      auto_resend_assessment: false,
      auto_advance: false,
      auto_promote: false,
      expected_version: 7,
    }));
  });

  it('does not disable untouched actions when one enabled legacy action changes', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        auto_promote: true,
        auto_send_assessment: null,
        auto_resend_assessment: null,
        auto_advance: null,
        agent_effective_policy: {
          auto_send_assessment: true,
          auto_resend_assessment: true,
          auto_advance: true,
        },
      },
    });
    renderPipeline();
    await openAgentSettingsTab();

    const send = await screen.findByRole('button', { name: 'Auto-send assessments' });
    expect(send).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(send);

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(101, {
      auto_send_assessment: false,
      auto_resend_assessment: true,
      auto_advance: true,
      auto_promote: false,
      expected_version: 7,
    }));
  });

  it('uses the committed role version for the next automatic-action save', async () => {
    const versionEight = {
      ...baseRole,
      version: 8,
      auto_promote: false,
      auto_send_assessment: false,
      auto_resend_assessment: true,
      auto_advance: true,
      agent_effective_policy: {
        auto_send_assessment: false,
        auto_resend_assessment: true,
        auto_advance: true,
      },
    };
    apiClient.roles.update
      .mockResolvedValueOnce({ data: versionEight })
      .mockResolvedValueOnce({
        data: {
          ...versionEight,
          version: 9,
          auto_advance: false,
          agent_effective_policy: {
            ...versionEight.agent_effective_policy,
            auto_advance: false,
          },
        },
      });
    renderPipeline();
    await openAgentSettingsTab();

    const send = await screen.findByRole('button', { name: 'Auto-send assessments' });
    fireEvent.click(send);
    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(send).toHaveAttribute('aria-pressed', 'false');
      expect(send).not.toBeDisabled();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Auto-advance qualified candidates' }));
    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledTimes(2));
    expect(apiClient.roles.update.mock.calls[1]).toEqual([
      101,
      expect.objectContaining({
        auto_advance: false,
        expected_version: 8,
      }),
    ]);
  });

  it('refetches the authoritative role once after a switch conflict and does not auto-retry', async () => {
    const openedRole = {
      ...baseRole,
      auto_promote: true,
      auto_send_assessment: true,
      auto_resend_assessment: true,
      auto_advance: true,
      agent_effective_policy: {
        auto_send_assessment: true,
        auto_resend_assessment: true,
        auto_advance: true,
      },
    };
    const authoritativeRole = {
      ...openedRole,
      version: 8,
      auto_send_assessment: true,
    };
    apiClient.roles.get
      .mockResolvedValueOnce({ data: openedRole })
      .mockResolvedValueOnce({ data: authoritativeRole });
    apiClient.roles.update
      .mockRejectedValueOnce({
        response: {
          status: 409,
          data: {
            detail: {
              code: 'ROLE_VERSION_CONFLICT',
              message: 'This job changed after you opened it.',
              current_role: {
                id: 101,
                version: 8,
                // Deliberately differs from the authoritative GET. The switch
                // must never hydrate from this partial conflict summary.
                auto_send_assessment: false,
              },
              current_version: 8,
              changed_by: { name: 'Aisha Khan' },
            },
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          ...authoritativeRole,
          version: 9,
          auto_send_assessment: false,
          agent_effective_policy: {
            ...authoritativeRole.agent_effective_policy,
            auto_send_assessment: false,
          },
        },
      });
    renderPipeline();
    await openAgentSettingsTab();

    const send = await screen.findByRole('button', { name: 'Auto-send assessments' });
    fireEvent.click(send);

    await waitFor(() => expect(apiClient.roles.get).toHaveBeenCalledTimes(2));
    await waitFor(() => {
      expect(send).toHaveAttribute('aria-pressed', 'true');
      expect(send).not.toBeDisabled();
    });
    expect(apiClient.roles.update).toHaveBeenCalledTimes(1);
    expect(apiClient.roles.update).toHaveBeenNthCalledWith(1, 101, expect.objectContaining({
      expected_version: 7,
    }));
    expect(showToast).toHaveBeenCalledTimes(1);
    expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('Aisha Khan saved a newer version'),
      'error',
    );

    // A retry remains an explicit user action and starts from the fresh GET's
    // revision; the failed request was never replayed automatically.
    fireEvent.click(send);
    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledTimes(2));
    expect(apiClient.roles.update).toHaveBeenNthCalledWith(2, 101, expect.objectContaining({
      auto_send_assessment: false,
      expected_version: 8,
    }));
  });

  it('marks the pipeline header with the role ATS mode', async () => {
    apiClient.roles.get.mockResolvedValue({ data: { ...baseRole, source: 'manual' } });
    renderPipeline();
    // A native role's header states it runs on Taali's own full ATS.
    expect(await screen.findByText('Full ATS')).toBeInTheDocument();
  });

  it('archives and reopens a Full ATS role with no initial job status', async () => {
    const nativeRole = {
      ...baseRole,
      source: 'manual',
      ats_provider: null,
      job_status: null,
    };
    apiClient.roles.getShell.mockResolvedValue({ data: nativeRole });
    apiClient.roles.get.mockResolvedValue({ data: nativeRole });
    apiClient.roles.setJobStatus
      .mockResolvedValueOnce({ data: { ...nativeRole, version: 8, job_status: 'cancelled' } })
      .mockResolvedValueOnce({ data: { ...nativeRole, version: 9, job_status: 'open' } });

    renderPipeline();
    fireEvent.click(await screen.findByRole('link', { name: /^Job spec$/i }));

    const lifecycle = await screen.findByRole('group', { name: 'Role lifecycle' });
    expect(within(lifecycle).getByText('Open')).toBeInTheDocument();
    expect(within(lifecycle).getByRole('button', { name: 'Mark filled by us' })).toBeInTheDocument();
    expect(within(lifecycle).getByRole('button', { name: 'Mark filled externally' })).toBeInTheDocument();

    fireEvent.click(within(lifecycle).getByRole('button', { name: 'Archive role' }));
    expect(apiClient.roles.setJobStatus).not.toHaveBeenCalled();
    let dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Archive this role?' })).toBeInTheDocument();
    expect(within(dialog).getByText(/Candidate history will stay available\. You can reopen the role later\./i))
      .toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Archive role' }));

    await waitFor(() => expect(apiClient.roles.setJobStatus).toHaveBeenNthCalledWith(
      1,
      101,
      'cancelled',
      undefined,
      7,
    ));
    const archivedLifecycle = await screen.findByRole('group', { name: 'Role lifecycle' });
    expect(within(archivedLifecycle).getByText('Archived')).toBeInTheDocument();

    fireEvent.click(within(archivedLifecycle).getByRole('button', { name: 'Reopen role' }));
    dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Reopen this role?' })).toBeInTheDocument();
    expect(within(dialog).getByText(/current agent and native job-page settings will still apply/i))
      .toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reopen role' }));

    await waitFor(() => expect(apiClient.roles.setJobStatus).toHaveBeenNthCalledWith(
      2,
      101,
      'open',
      undefined,
      8,
    ));
  });

  it('keeps an explicitly empty legacy Full ATS role as a draft that can be archived', async () => {
    const draftRole = {
      ...baseRole,
      source: 'manual',
      ats_provider: null,
      job_status: null,
      job_spec_present: false,
      applications_count: 0,
      active_candidates_count: 0,
    };
    apiClient.roles.getShell.mockResolvedValue({ data: draftRole });
    apiClient.roles.get.mockResolvedValue({ data: draftRole });

    renderPipeline();
    fireEvent.click(await screen.findByRole('link', { name: /^Job spec$/i }));
    const lifecycle = await screen.findByRole('group', { name: 'Role lifecycle' });

    expect(within(lifecycle).getByText('Draft')).toBeInTheDocument();
    expect(within(lifecycle).getByRole('button', { name: 'Open role' })).toBeInTheDocument();
    expect(within(lifecycle).getByRole('button', { name: 'Archive role' })).toBeInTheDocument();
  });

  it('clears a pending lifecycle confirmation when navigating to another role', async () => {
    const nativeRole = {
      ...baseRole,
      source: 'manual',
      ats_provider: null,
      job_status: 'open',
    };
    apiClient.roles.getShell.mockResolvedValue({ data: nativeRole });
    apiClient.roles.get.mockResolvedValue({ data: nativeRole });

    render(
      <TestMemoryRouter initialEntries={['/jobs/101']}>
        <Routes>
          <Route path="/jobs/:roleId" element={<><RouteSwitchButton /><JobPipelinePage onNavigate={vi.fn()} /></>} />
        </Routes>
      </TestMemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('link', { name: /^Job spec$/i }));
    const lifecycle = await screen.findByRole('group', { name: 'Role lifecycle' });
    fireEvent.click(within(lifecycle).getByRole('button', { name: 'Archive role' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Switch role' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(apiClient.roles.setJobStatus).not.toHaveBeenCalled();
  });

  it('keeps native filled outcomes and confirms before moving the role inactive', async () => {
    const nativeRole = {
      ...baseRole,
      source: 'manual',
      ats_provider: null,
      job_status: 'open',
    };
    apiClient.roles.getShell.mockResolvedValue({ data: nativeRole });
    apiClient.roles.get.mockResolvedValue({ data: nativeRole });
    apiClient.roles.setJobStatus.mockResolvedValue({
      data: { ...nativeRole, version: 8, job_status: 'filled_external' },
    });

    renderPipeline();
    fireEvent.click(await screen.findByRole('link', { name: /^Job spec$/i }));
    const lifecycle = await screen.findByRole('group', { name: 'Role lifecycle' });

    fireEvent.click(within(lifecycle).getByRole('button', { name: 'Mark filled externally' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Mark this role as filled externally?' }))
      .toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Mark filled externally' }));

    await waitFor(() => expect(apiClient.roles.setJobStatus).toHaveBeenCalledWith(
      101,
      'filled_external',
      undefined,
      7,
    ));
    const filledLifecycle = await screen.findByRole('group', { name: 'Role lifecycle' });
    expect(within(filledLifecycle).getByText('Filled · external')).toBeInTheDocument();
    expect(within(filledLifecycle).getByRole('button', { name: 'Reopen role' })).toBeInTheDocument();
  });

  it.each([
    ['Workable', { source: 'workable', ats_provider: 'workable', workable_job_state: 'archived' }, 'Archived'],
    ['Bullhorn', { source: 'bullhorn', ats_provider: 'bullhorn', external_job_state: 'on_hold_client' }, 'On Hold Client'],
  ])('shows %s lifecycle as ATS-managed and read-only', async (provider, providerFields, expectedState) => {
    const externalRole = {
      ...baseRole,
      ...providerFields,
      job_status: 'open',
    };
    apiClient.roles.getShell.mockResolvedValue({ data: externalRole });
    apiClient.roles.get.mockResolvedValue({ data: externalRole });

    renderPipeline();
    fireEvent.click(await screen.findByRole('link', { name: /^Job spec$/i }));
    const lifecycle = await screen.findByRole('group', { name: 'Role lifecycle' });

    expect(within(lifecycle).getByText(expectedState)).toBeInTheDocument();
    expect(within(lifecycle).getByText(`Managed in ${provider}`)).toBeInTheDocument();
    expect(within(lifecycle).getByText(new RegExp(`Archive or reopen this role in ${provider}`, 'i')))
      .toBeInTheDocument();
    expect(within(lifecycle).queryByRole('button', { name: /archive|reopen|filled/i }))
      .not.toBeInTheDocument();
    expect(apiClient.roles.setJobStatus).not.toHaveBeenCalled();
  });

  it('defers related-role lifecycle to its original role instead of an ATS provider', async () => {
    const relatedRole = {
      ...baseRole,
      source: 'sister',
      ats_provider: null,
      role_kind: 'sister',
      ats_owner_role_id: 77,
      ats_owner_role_name: 'AI Engineer',
      job_status: null,
    };
    apiClient.roles.getShell.mockResolvedValue({ data: relatedRole });
    apiClient.roles.get.mockResolvedValue({ data: relatedRole });

    renderPipeline();
    fireEvent.click(await screen.findByRole('link', { name: /^Job spec$/i }));
    const lifecycle = await screen.findByRole('group', { name: 'Role lifecycle' });

    expect(within(lifecycle).getByText('Managed on the original role')).toBeInTheDocument();
    expect(within(lifecycle).getByText(/Archive or reopen AI Engineer #77/i)).toBeInTheDocument();
    expect(within(lifecycle).queryByText(/Managed in Workable/i)).not.toBeInTheDocument();
    expect(within(lifecycle).queryByRole('button')).not.toBeInTheDocument();
    expect(apiClient.roles.setJobStatus).not.toHaveBeenCalled();
  });

  it('renders a related role in the ordinary job shell with an exact original-role control', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        role_kind: 'sister',
        source: 'sister',
        ats_owner_role_id: 77,
        ats_owner_role_name: 'AI Engineer',
        ats_provider: 'workable',
        role_family: {
          owner: { id: 77, name: 'AI Engineer' },
          related: [{ id: 101, name: 'AI Native Engineer' }],
        },
        effective_workable_job_id: 'AI-ENG',
      },
    });
    const sisterApps = {
      open: { ...baseApplications[0], taali_score: 91, source_role_score: 72, score_status: 'done' },
      rejected: {
        ...baseApplications[0], id: 3, candidate_id: 33, candidate_name: 'Rejected Sister',
        application_outcome: 'rejected', taali_score: 81,
      },
      hired: {
        ...baseApplications[0], id: 4, candidate_id: 44, candidate_name: 'Hired Sister',
        application_outcome: 'hired', taali_score: 80,
      },
      withdrawn: {
        ...baseApplications[0], id: 5, candidate_id: 55, candidate_name: 'Withdrawn Sister',
        application_outcome: 'withdrawn', related_role_availability: 'closed', taali_score: 79,
      },
    };
    apiClient.roles.listApplications.mockImplementation((_, params) => (
      params.application_outcome === 'hired'
        ? Promise.reject(new Error('Hired history unavailable'))
        : Promise.resolve({ data: [sisterApps[params.application_outcome]] })
    ));

    renderPipeline();

    expect((await screen.findAllByText('Workable')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Related · Workable')).not.toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent(
      'Shared Workable candidate pool with AI Engineer #77 (original). Rejecting and advancing apply to AI Engineer #77, AI Native Engineer #101.',
    );
    expect(screen.getByRole('columnheader', { name: /Original fit/i })).toBeInTheDocument();
    const row = screen.getByText('Sam Patel').closest('tr');
    expect(within(row).getByText('91')).toBeInTheDocument();
    expect(within(row).getByText('72')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Closed, 2/i }));
    expect(screen.getByText('Rejected Sister')).toBeInTheDocument();
    expect(screen.getByText('Withdrawn Sister')).toBeInTheDocument();
    expect(screen.getByText('Withdrawn Sister').closest('tr')).toHaveClass('related-role-locked');
    expect(screen.getByRole('alert')).toHaveTextContent('Some candidates could not be loaded.');
    expect(apiClient.roles.listApplications.mock.calls.map(([, params]) => (
      params.application_outcome
    ))).toEqual(['open', 'rejected', 'hired', 'withdrawn']);
    expect(screen.getByRole('button', { name: 'Open original role AI Engineer #77' }))
      .toHaveAttribute('data-motion-role-origin');
    expect(screen.getByRole('button', { name: /Ask agent/i })).toBeInTheDocument();
    expect(screen.queryByText(/Not published/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Process \d+ candidate/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Edit job spec$/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('link', { name: /^Job spec$/i }));
    expect(await screen.findByRole('heading', { name: /^Role specification$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Edit$/i })).toBeInTheDocument();
    expect(apiClient.roles.updateJobSpec).not.toHaveBeenCalled();

    expect(screen.queryByRole('link', { name: /^Scoring settings$/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: /^Agent settings$/i }));
    fireEvent.click(within(
      await screen.findByRole('navigation', { name: 'Agent settings sections' }),
    ).getByRole('link', { name: /^Decision rules/i }));
    expect(await screen.findByRole('heading', { name: 'Screening threshold' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Assessment tasks' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Auto-send assessments' })).not.toBeDisabled();
    expect(screen.getByRole('button', { name: 'Auto-retry assessment invites' })).not.toBeDisabled();
    expect(screen.getByRole('button', { name: 'Auto-advance qualified candidates' })).not.toBeDisabled();
    expect(screen.getByRole('button', { name: 'Auto-reject pre-screen failures' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Auto-reject after scoring' })).toBeDisabled();
    expect(screen.queryByText('Shared-pool candidate actions remain behind recruiter approval.')).not.toBeInTheDocument();

    fireEvent.click(within(
      screen.getByRole('navigation', { name: 'Agent settings sections' }),
    ).getByRole('link', { name: /^Guidance/i }));
    expect(screen.getByTestId('screening-question-editor')).toBeInTheDocument();
  });

  it('shows shared candidates while related scoring waits and labels the original pipeline clearly', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        role_kind: 'sister',
        source: 'sister',
        ats_owner_role_id: 77,
        ats_owner_role_name: 'AI Engineer',
        effective_workable_job_id: 'AI-ENG',
        stage_counts: { applied: 119, review: 172, invited: 8, advanced: 8, rejected: 498 },
      },
    });
    apiClient.roles.listApplications.mockResolvedValue({
      data: [{
        ...baseApplications[0],
        taali_score: null,
        source_role_score: 72,
        score_status: 'retry_wait',
      }],
    });
    apiClient.roles.sisterScoringStatus.mockResolvedValue({
      data: {
        status: 'waiting',
        waiting_reason: 'workspace_paused',
        total: 806,
        scoreable_total: 800,
        scored: 0,
        completed: 6,
        progress_percent: 0,
        counts: { pending: 0, running: 0, retry_wait: 800, done: 0, error: 0, unscorable: 6 },
      },
    });

    renderPipeline();

    expect(await screen.findByText(/Related-role scoring is waiting · 0%/i)).toBeInTheDocument();
    expect(screen.getByText(/legacy workspace-wide agent hold is blocking scoring/i)).toBeInTheDocument();
    expect(screen.getByText(/0 of 800 scoreable candidates/i)).toBeInTheDocument();
    expect(screen.getByText(/Related-role Taali pipeline · independent stages/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /turn on/i })).toBeInTheDocument();
    expect(screen.getByText('Shared candidates')).toBeInTheDocument();
    expect(screen.getByText('806')).toBeInTheDocument();
    expect(screen.getByText('Awaiting score')).toBeInTheDocument();
    expect(screen.getByText('Waiting…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Waiting 0%' })).toBeDisabled();
    expect(screen.queryByText('AGENT OFF')).not.toBeInTheDocument();
  });

  it('saves a related-role spec only after warning that scores become stale without spend', async () => {
    const originalSpec = 'Build production AI systems with reliable Python services, evaluation tooling, and model observability.';
    const updatedSpec = `${originalSpec} Own distributed tracing and safe model rollout practices.`;
    const relatedRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      ats_provider: 'workable',
      ats_owner_role_id: 77,
      ats_owner_role_name: 'AI Engineer',
      role_family: {
        owner: { id: 77, name: 'AI Engineer' },
        related: [{ id: 101, name: 'AI Native Engineer' }],
      },
      job_spec_text: originalSpec,
    };
    apiClient.roles.getShell.mockResolvedValue({ data: relatedRole });
    apiClient.roles.get.mockResolvedValue({ data: relatedRole });
    apiClient.roles.updateJobSpec.mockResolvedValue({
      data: {
        applied: true,
        role: { ...relatedRole, version: 8, job_spec_text: updatedSpec },
        diff: { added: [], removed: [], criteria_count: 0 },
        would_rescreen: { count: 2, est_cost_usd: 0.17 },
        rescore_dispatch_approved: false,
      },
    });

    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^Edit job spec$/i }));
    fireEvent.change(await screen.findByLabelText('Job description'), {
      target: { value: updatedSpec },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save job spec/i }));

    expect(await screen.findByRole('heading', { name: 'Save related-role job spec?' }))
      .toBeInTheDocument();
    expect(screen.getByText(/Existing scores on this related role will be marked stale/i)).toBeInTheDocument();
    expect(screen.getByText(/No model spend starts on save/i)).toBeInTheDocument();
    expect(apiClient.roles.updateJobSpec).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Save and mark scores stale' }));
    await waitFor(() => expect(apiClient.roles.updateJobSpec).toHaveBeenCalledWith(
      101,
      expect.objectContaining({
        job_spec_text: updatedSpec,
        expected_version: 7,
      }),
    ));
    await waitFor(() => expect(apiClient.roles.sisterScoringStatus).toHaveBeenCalledTimes(2));
    expect(showToast).toHaveBeenCalledWith(
      expect.stringMatching(/2 related-role scores now need re-score approval.*\$0\.17.*No model spend was started/i),
      'success',
    );
  });

  it('requires explicit paid approval before re-scoring a stale related roster', async () => {
    const relatedRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      ats_provider: 'workable',
      ats_owner_role_id: 77,
      ats_owner_role_name: 'AI Engineer',
      role_family: {
        owner: { id: 77, name: 'AI Engineer' },
        related: [{ id: 101, name: 'AI Native Engineer' }],
      },
    };
    apiClient.roles.getShell.mockResolvedValue({ data: relatedRole });
    apiClient.roles.get.mockResolvedValue({ data: relatedRole });
    apiClient.roles.sisterScoringStatus.mockResolvedValue({
      data: {
        status: 'stale',
        total: 10,
        scoreable_total: 8,
        scored: 0,
        stale_scored: 8,
        visible_scored: 8,
        estimated_rescore_cost_usd: 0.66,
        counts: { done: 0, stale: 8, unscorable: 2 },
      },
    });
    apiClient.roles.rescoreSister.mockResolvedValue({
      data: { status: 'running', total: 10, scoreable_total: 8, progress_percent: 0 },
    });

    renderPipeline();

    expect(await screen.findByText(/Related-role scores need re-score approval/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Re-score roster' }));
    expect(await screen.findByRole('heading', { name: 'Approve related-role re-score?' }))
      .toBeInTheDocument();
    expect(screen.getByText(/Re-score the full scoreable roster: 8 candidates/i)).toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByText(/Estimated model cost: \$0\.66/i))
      .toBeInTheDocument();
    expect(apiClient.roles.rescoreSister).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Approve re-score roster' }));
    await waitFor(() => expect(apiClient.roles.rescoreSister).toHaveBeenCalledWith(101, {
      approved_max_scoreable_count: 8,
      expected_version: 7,
    }));
  });

  it('shows legacy workspace recovery read-only to non-owner related-role viewers', async () => {
    authState.user = { role: 'member' };
    const sisterRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      ats_owner_role_id: 77,
      ats_owner_role_name: 'AI Engineer',
    };
    apiClient.roles.getShell.mockResolvedValue({ data: sisterRole });
    apiClient.roles.get.mockResolvedValue({ data: sisterRole });
    apiClient.agent.status.mockResolvedValue({
      data: {
        can_control_agent: true,
        enabled: true,
        paused: true,
        pause_scope: 'workspace',
        workspace_paused: true,
        workspace_control_version: 12,
      },
    });

    renderPipeline();

    const resume = await screen.findByRole('button', {
      name: 'Recover this related role',
    });
    expect(resume).toBeDisabled();
    expect(resume).toHaveAttribute(
      'title',
      'Only workspace owners can recover this related role.',
    );
    fireEvent.click(resume);
    expect(apiClient.agent.resumeAll).not.toHaveBeenCalled();
    expect(apiClient.agent.recoverRelatedRole).not.toHaveBeenCalled();
  });

  it('recovers only the viewed related-role cohort and never calls workspace resume-all', async () => {
    const family = {
      owner: { id: 77, name: 'AI Engineer' },
      related: [{ id: 101, name: 'AI Native Engineer' }],
    };
    const sisterRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      agentic_mode_enabled: true,
      ats_owner_role_id: 77,
      ats_owner_role_name: 'AI Engineer',
      role_family: family,
    };
    apiClient.roles.getShell.mockResolvedValue({ data: sisterRole });
    apiClient.roles.get.mockResolvedValue({ data: sisterRole });
    apiClient.roles.sisterScoringStatus.mockResolvedValue({
      data: {
        role_version: 7,
        status: 'waiting',
        waiting_reason: 'workspace_paused',
        total: 10,
        scoreable_total: 8,
        counts: { retry_wait: 8, unscorable: 2 },
      },
    });
    apiClient.agent.status.mockResolvedValue({
      data: {
        can_control_agent: true,
        enabled: true,
        paused: true,
        pause_scope: 'workspace',
        workspace_paused: true,
        workspace_control_version: 12,
      },
    });
    apiClient.agent.relatedRoleRecoveryScope.mockResolvedValue({
      data: {
        role_id: 101,
        role_version: 7,
        workspace_paused: true,
        workspace_control_version: 12,
        role_family: family,
        cohort_fingerprint: 'b'.repeat(64),
        cohort_total: 10,
        cohort_scoreable: 8,
        cohort_unscorable: 2,
        cohort_excluded: 0,
      },
    });

    renderPipeline();
    const recover = await screen.findByRole('button', { name: 'Recover this related role' });
    await waitFor(() => expect(recover).toBeEnabled());
    expect(screen.getByText(/Exact recovery scope checked: 8 scoreable of 10 shared candidates/i))
      .toBeInTheDocument();
    fireEvent.click(recover);

    expect(apiClient.agent.relatedRoleRecoveryScope).toHaveBeenCalledOnce();
    await waitFor(() => expect(apiClient.agent.recoverRelatedRole).toHaveBeenCalledWith(101, {
      expected_version: 7,
      expected_workspace_control_version: 12,
      expected_role_family: family,
      cohort_fingerprint: 'b'.repeat(64),
      approved_max_candidates_total: 10,
      approved_max_scoreable_count: 8,
    }));
    expect(apiClient.agent.resumeAll).not.toHaveBeenCalled();
  });

  it('opens related-role creation directly from the job header', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        job_spec_text: 'AI engineer role requiring Python, production machine learning systems, evaluation, observability, and reliable delivery.',
      },
    });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /Create related role/i }));

    expect(requisitionApi.createRelated).toHaveBeenCalledWith(baseRole.id);
    expect(await screen.findByText('Related role draft chat')).toBeInTheDocument();
  });

  it('links the job header directly to its role-agent chat', async () => {
    renderPipeline();
    const chatButton = await screen.findByRole('button', { name: /Ask agent/i });
    fireEvent.click(chatButton);
    expect(await screen.findByText('Role agent chat route')).toBeInTheDocument();
  });

  it('removes manual sourcing, processing, syncing and distribution work from the role page', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: { ...baseRole, agentic_mode_enabled: true, workable_job_id: 'AI-ENG' },
    });
    renderPipeline();

    await screen.findByRole('heading', { name: /AI Native Engineer/i });
    expect(screen.queryByRole('link', { name: /^Find candidates$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add sourced/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Process \d+ candidate/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Sync from Workable/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Invite candidate/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Not published/i)).not.toBeInTheDocument();
    expect(apiClient.roles.distribution).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('link', { name: /^Job spec$/i }));
    await screen.findByRole('heading', { name: /Role specification/i });
    expect(screen.queryByText(/Distribute this role/i)).not.toBeInTheDocument();
  });

  it('uses six canonical kanban columns and folds completed assessments into Invited', async () => {
    renderPipeline();
    await switchToPipelineView();

    await screen.findByText('Priya Anand');
    const columns = document.querySelectorAll('.kanban-col');
    expect(columns).toHaveLength(6);
    expect(Array.from(columns, (column) => column.dataset.stage)).toEqual([
      'sourced',
      'applied',
      'scored',
      'invited',
      'advanced',
      'rejected',
    ]);
    expect(screen.queryByText(/^Completed$/)).not.toBeInTheDocument();
    const invitedColumn = document.querySelector('.kanban-col[data-stage="invited"]');
    expect(within(invitedColumn).getByText('Priya Anand')).toBeInTheDocument();
  });

  it('previews the effective policy and preserves configured autonomy on Turn on', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        source: 'manual',
        auto_promote: false,
        auto_send_assessment: false,
        auto_resend_assessment: true,
        auto_advance: false,
        agent_effective_policy: {
          auto_send_assessment: false,
          auto_resend_assessment: true,
          auto_advance: false,
          auto_reject_pre_screen: false,
          auto_skip_assessment: false,
        },
      },
    });
    apiClient.roles.listTasks.mockResolvedValue({ data: [{ id: 700, name: 'Approved task', is_active: true }] });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
    expect(await screen.findByText(/Assessment invitations require your approval/i)).toBeInTheDocument();
    expect(screen.getByText(/Assessment retries send automatically/i)).toBeInTheDocument();
    expect(screen.getByText(/Deterministic rejects after CV and role-fit scoring/i)).toBeInTheDocument();
    expect(screen.getByText(/Assessment-stage and LLM-only rejects require approval/i)).toBeInTheDocument();
    expect(apiClient.roles.update).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /^Turn on agent$/i }));
    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(101, expect.objectContaining({
      agentic_mode_enabled: true,
      auto_promote: false,
      auto_send_assessment: false,
      auto_resend_assessment: true,
      auto_advance: false,
    })));
  });

  it.each([
    ['workable', 'Workable', 'WK-900'],
    ['bullhorn', 'Bullhorn', 'BH-900'],
  ])(
    'keeps the %s provider lifecycle out of the action-safety confirmation',
    async (provider, label, externalJobId) => {
      apiClient.roles.get.mockResolvedValue({
        data: {
          ...baseRole,
          source: null,
          ats_provider: provider,
          external_job_id: externalJobId,
          external_job_state: 'open',
          external_job_live: true,
        },
      });
      apiClient.roles.listTasks.mockResolvedValue({
        data: [{ id: 790, name: 'Approved task', is_active: true }],
      });
      renderPipeline();

      fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
      expect(await screen.findByText(/Candidate-action safeguards/i)).toBeInTheDocument();
      expect(screen.queryByText(new RegExp(`${label} remains the intake source`, 'i')))
        .not.toBeInTheDocument();
    },
  );

  it.each([
    [
      'workable',
      'Workable',
      { workable_stage: 'Phone screen', workable_candidate_id: 'WK-C-1' },
      'Phone Screen',
    ],
    [
      'bullhorn',
      'Bullhorn',
      { external_stage_raw: 'Interview Scheduled', bullhorn_job_submission_id: 'BH-S-1' },
      'Interview Scheduled',
    ],
  ])(
    'renders the raw %s candidate stage in its provider-owned column',
    async (provider, label, externalFields, expectedStage) => {
      apiClient.roles.get.mockResolvedValue({
        data: {
          ...baseRole,
          source: null,
          ats_provider: provider,
          external_job_id: `${provider}-job-1`,
          external_job_state: 'open',
          external_job_live: true,
        },
      });
      apiClient.roles.listApplications.mockResolvedValue({
        data: [{
          ...baseApplications[0],
          source: provider,
          ...externalFields,
        }],
      });
      renderPipeline();

      expect(await screen.findByRole('columnheader', { name: label })).toBeInTheDocument();
      const row = (await screen.findByText('Sam Patel')).closest('tr');
      expect(within(row).getByText(expectedStage)).toBeInTheDocument();
    },
  );

  it('offers a provider-neutral related scoring role for Bullhorn jobs', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        source: 'bullhorn',
        ats_provider: 'bullhorn',
        external_job_id: 'BH-900',
        bullhorn_job_order_id: 'BH-900',
      },
    });

    renderPipeline();

    const action = await screen.findByRole('button', { name: /create related role/i });
    expect(action).toHaveAttribute(
      'title',
      'Create a separate scoring role over this Bullhorn candidate pool',
    );
  });

  it('keeps an untouched first Turn on HITL-safe', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        auto_promote: false,
        auto_send_assessment: null,
        auto_resend_assessment: null,
        auto_advance: null,
        agent_effective_policy: {
          auto_send_assessment: false,
          auto_resend_assessment: false,
          auto_advance: false,
        },
      },
    });
    apiClient.roles.listTasks.mockResolvedValue({ data: [{ id: 701, name: 'Approved task', is_active: true }] });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
    expect(await screen.findByText(/Assessment invitations require your approval/i)).toBeInTheDocument();
    expect(screen.getByText(/Candidate advancement requires your approval/i)).toBeInTheDocument();
    expect(screen.getByText(/Pre-screen failures reject automatically/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Turn on agent$/i }));

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalled());
    const activationPayload = apiClient.roles.update.mock.calls.at(-1)[1];
    expect(activationPayload).toEqual(expect.objectContaining({
      agentic_mode_enabled: true,
      monthly_usd_budget_cents: 5000,
      expected_version: 7,
    }));
    expect(activationPayload).not.toHaveProperty('auto_promote');
    expect(activationPayload).not.toHaveProperty('auto_send_assessment');
    expect(activationPayload).not.toHaveProperty('auto_resend_assessment');
    expect(activationPayload).not.toHaveProperty('auto_advance');
  });

  it('requires an explicit Generate or Skip choice for a known taskless role', async () => {
    apiClient.roles.listTasks.mockResolvedValue({ data: [] });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
    expect(await screen.findByText(/no active task exists; choose Generate or Skip/i))
      .toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Turn on agent$/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Generate, validate & approve/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Skip assessment & turn on/i })).toBeInTheDocument();
    expect(apiClient.roles.update).not.toHaveBeenCalled();
  });

  it('allows ordinary Turn on when taskless skip is already configured', async () => {
    apiClient.roles.get.mockResolvedValue({ data: { ...baseRole, auto_skip_assessment: true } });
    apiClient.roles.listTasks.mockResolvedValue({ data: [] });
    renderPipeline();

    await confirmTurnOnPolicy();

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(101, expect.objectContaining({
      agentic_mode_enabled: true,
      expected_version: 7,
    })));
    const payload = apiClient.roles.update.mock.calls.at(-1)[1];
    expect(payload).not.toHaveProperty('activation_assessment_action');
  });

  it('does not treat taskless runtime effective skip as a configured Turn-on choice', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        auto_skip_assessment: false,
        agent_effective_policy: { auto_skip_assessment: true },
      },
    });
    apiClient.roles.listTasks.mockResolvedValue({ data: [] });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));

    expect(await screen.findByText(/no active task exists; choose Generate or Skip/i))
      .toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Turn on agent$/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Generate, validate & approve/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Skip assessment & turn on/i })).toBeInTheDocument();
    expect(apiClient.roles.update).not.toHaveBeenCalled();
  });

  it('keeps ordinary Turn on available to confirm a blocked republish', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        assessment_task_provisioning: { reconfiguration: { status: 'blocked' } },
      },
    });
    apiClient.roles.listTasks.mockRejectedValue(new Error('task list unavailable'));
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
    expect(await screen.findByText(/confirms the preserved assessment/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Turn on agent$/i }));

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalled());
    expect(apiClient.roles.update.mock.calls.at(-1)[1])
      .not.toHaveProperty('activation_assessment_action');
  });

  it('does not infer tasklessness or skip policy when the task fetch fails', async () => {
    apiClient.roles.listTasks.mockRejectedValue(new Error('temporary task catalogue failure'));
    renderPipeline();

    await openAgentSettingsTab();
    expect(await screen.findByText('Assessment tasks unavailable')).toBeInTheDocument();
    expect(screen.queryByText('No assessment task assigned')).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^turn on$/i }));
    expect(await screen.findByText(/current task assignment is unavailable/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Turn on agent$/i })).not.toBeInTheDocument();
    expect(apiClient.roles.update).not.toHaveBeenCalled();
  });

  it('offers an explicit durable generate-validate-approve activation path', async () => {
    apiClient.roles.listTasks.mockResolvedValue({ data: [] });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
    expect(await screen.findByRole('heading', { name: /Turn on agent/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {
      name: /Generate, validate & approve, then turn on/i,
    }));

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(
      101,
      expect.objectContaining({
        agentic_mode_enabled: true,
        monthly_usd_budget_cents: 5000,
        activation_assessment_action: 'approve_when_ready',
      }),
    ));
    expect(await screen.findByRole('heading', {
      name: /Preparing the assessment and turning on/i,
    })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Skip assessment & turn on/i })).toBeInTheDocument();
  });

  it('offers an explicit skip choice without consulting the fetched task list', async () => {
    apiClient.roles.listTasks.mockRejectedValue(new Error('temporary task catalogue failure'));
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
    expect(await screen.findByRole('heading', { name: /Turn on agent/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Skip assessment & turn on/i }));

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(
      101,
      expect.objectContaining({
        agentic_mode_enabled: true,
        auto_skip_assessment: true,
        activation_assessment_action: 'skip_assessment',
      }),
    ));
  });

  it('does not force assessment skip when turning on a related role with an active task', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        role_kind: 'sister',
        source: 'sister',
        ats_owner_role_id: 77,
        ats_owner_role_name: 'AI Engineer',
        auto_promote: false,
        auto_send_assessment: false,
        auto_resend_assessment: false,
        auto_advance: false,
        agent_effective_policy: {
          auto_send_assessment: false,
          auto_resend_assessment: false,
          auto_advance: false,
        },
      },
    });
    apiClient.roles.listTasks.mockResolvedValue({
      data: [{ id: 712, name: 'Production AI exercise', is_active: true }],
    });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
    expect(await screen.findByRole('heading', { name: /Turn on the agent for AI Native Engineer/i }))
      .toBeInTheDocument();
    expect(screen.getByText(/This role shares candidates with AI Engineer #77/i))
      .toBeInTheDocument();
    expect(screen.getByText(/The agent scores them separately for AI Native Engineer/i))
      .toBeInTheDocument();
    expect(screen.getByText(/Assessments: You approve invitations and retries/i))
      .toBeInTheDocument();
    expect(screen.getByText(/Candidate decisions: You approve advances and rejections/i))
      .toBeInTheDocument();
    expect(screen.queryByText(/Candidate-action safeguards/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Turn on agent$/i }));

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalled());
    const activationPayload = apiClient.roles.update.mock.calls.at(-1)[1];
    expect(activationPayload).toEqual(expect.objectContaining({
      agentic_mode_enabled: true,
      monthly_usd_budget_cents: 5000,
    }));
    expect(activationPayload).not.toHaveProperty('auto_skip_assessment');
    expect(activationPayload).not.toHaveProperty('activation_assessment_action');
  });

  it('explains that automatic related-role advances update every linked role', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        role_kind: 'sister',
        source: 'sister',
        ats_owner_role_id: 77,
        ats_owner_role_name: 'AI Engineer',
        auto_send_assessment: false,
        auto_resend_assessment: false,
        auto_advance: true,
        agent_effective_policy: {
          auto_send_assessment: false,
          auto_resend_assessment: false,
          auto_advance: true,
        },
      },
    });
    apiClient.roles.listTasks.mockResolvedValue({
      data: [{ id: 713, name: 'Production AI exercise', is_active: true }],
    });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));

    expect(await screen.findByText(
      /Advances happen automatically across the original role and every related role/i,
    )).toBeInTheDocument();
    expect(screen.getByText(/You approve rejections; an approved rejection applies across every role/i))
      .toBeInTheDocument();
  });

  it('says candidates skip assessment when a related role has no assessment', async () => {
    const relatedRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      ats_owner_role_id: 77,
      ats_owner_role_name: 'AI Engineer',
      auto_send_assessment: true,
      auto_resend_assessment: true,
      auto_advance: false,
      agent_effective_policy: {
        auto_send_assessment: true,
        auto_resend_assessment: true,
        auto_advance: false,
      },
    };
    apiClient.roles.getShell.mockResolvedValue({ data: relatedRole });
    apiClient.roles.get.mockResolvedValue({ data: relatedRole });
    apiClient.roles.listTasks.mockResolvedValue({ data: [] });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));

    expect(await screen.findByText(/No active assessment is assigned, so candidates skip that step for now/i))
      .toBeInTheDocument();
    expect(screen.queryByText(/Invitations and retries send automatically/i))
      .not.toBeInTheDocument();
  });

  it('keeps the agent off while first activation is pending, then remains off if rejected', async () => {
    apiClient.roles.listTasks.mockResolvedValue({ data: [{
      id: 706,
      name: 'Active assessment',
      is_active: true,
    }] });
    let rejectActivation;
    apiClient.roles.update.mockReturnValue(new Promise((resolve, reject) => {
      void resolve;
      rejectActivation = reject;
    }));
    renderPipeline();

    await confirmTurnOnPolicy();
    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(
      101,
      expect.objectContaining({ agentic_mode_enabled: true }),
    ));

    // A pending write is not an authoritative agent state.
    expect(screen.getByText('Agent off')).toBeInTheDocument();
    expect(screen.queryByText('Agent on')).not.toBeInTheDocument();
    expect(screen.queryByText('Agent starting')).not.toBeInTheDocument();

    await switchToPipelineView();
    const appliedCard = (await screen.findByText('Sam Patel')).closest('.kanban-card');
    fireEvent.click(within(appliedCard).getByRole('link', { name: /Open Sam Patel/i }));
    expect(await screen.findByText(/Closes the application/i)).toBeInTheDocument();
    expect(screen.queryByText(/The agent sends assessments automatically/i))
      .not.toBeInTheDocument();

    await act(async () => {
      rejectActivation({
        response: { data: { detail: 'Activation rejected by readiness gate.' } },
      });
    });
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      'Activation rejected by readiness gate.',
      'error',
    ));
    expect(screen.getByText('Agent off')).toBeInTheDocument();
    expect(screen.queryByText('Agent on')).not.toBeInTheDocument();
    expect(screen.queryByText('Agent starting')).not.toBeInTheDocument();
  });

  it('requires durable Generate confirmation when only an inactive draft exists', async () => {
    apiClient.roles.listTasks.mockResolvedValue({ data: [{
      id: 708,
      name: 'Pending generated exercise',
      description: 'Still validating.',
      duration_minutes: 30,
      is_active: false,
      generated: true,
      needs_review: true,
      battle_test: null,
    }] });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
    expect(screen.queryByRole('button', { name: /^Turn on agent$/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Generate, validate & approve/i }));
    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalled());
    const activationPayload = apiClient.roles.update.mock.calls.at(-1)[1];
    expect(activationPayload.agentic_mode_enabled).toBe(true);
    expect(activationPayload).not.toHaveProperty('auto_skip_assessment');
    expect(activationPayload.activation_assessment_action).toBe('approve_when_ready');
    expect(await screen.findByRole('heading', { name: /Preparing the assessment/i }))
      .toBeInTheDocument();
  });

  it('keeps explicit taskless Skip activation off until the authoritative PATCH succeeds', async () => {
    const pendingDraft = {
      id: 709,
      name: 'Generated systems exercise',
      description: 'Design and repair a queue worker.',
      duration_minutes: 45,
      is_active: false,
      generated: true,
      needs_review: true,
      battle_test: null,
    };
    apiClient.roles.listTasks.mockResolvedValue({ data: [pendingDraft] });
    apiClient.roles.get
      .mockResolvedValueOnce({ data: baseRole })
      .mockResolvedValue({
        data: {
          ...baseRole,
          agentic_mode_enabled: true,
          auto_skip_assessment: true,
        },
      });
    let resolveActivation;
    apiClient.roles.update.mockReturnValue(new Promise((resolve) => {
      resolveActivation = resolve;
    }));
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
    fireEvent.click(screen.getByRole('button', { name: /Skip assessment & turn on/i }));
    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalled());
    const activationPayload = apiClient.roles.update.mock.calls.at(-1)[1];
    expect(activationPayload.agentic_mode_enabled).toBe(true);
    expect(activationPayload.auto_skip_assessment).toBe(true);
    expect(activationPayload.activation_assessment_action).toBe('skip_assessment');
    expect(screen.getByText('Agent off')).toBeInTheDocument();

    await act(async () => {
      resolveActivation({
        data: {
          ...baseRole,
          agentic_mode_enabled: true,
          auto_skip_assessment: true,
        },
      });
    });
    await waitFor(() => expect(screen.getByText('Agent on')).toBeInTheDocument());
  });

  it('shows a failed durable taskless Turn-on request and leaves the agent off', async () => {
    apiClient.roles.listTasks.mockResolvedValue({ data: [{
      id: 710,
      name: 'Pending generated exercise',
      is_active: false,
      generated: true,
      needs_review: true,
      battle_test: null,
    }] });
    apiClient.roles.update.mockRejectedValue({
      response: { data: { detail: 'Activation authorization could not be persisted.' } },
    });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
    fireEvent.click(screen.getByRole('button', { name: /Generate, validate & approve/i }));

    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      'Activation authorization could not be persisted.',
      'error',
    ));
    expect(screen.getByText('Agent off')).toBeInTheDocument();
  });

  it('shows a persisted queued activation after page reload', async () => {
    const pendingRole = {
      ...baseRole,
      agentic_mode_enabled: false,
      assessment_task_provisioning: {
        status: 'succeeded',
        activation_intent: { status: 'pending', last_error: null },
      },
    };
    apiClient.roles.getShell.mockResolvedValue({ data: pendingRole });
    apiClient.roles.get.mockResolvedValue({ data: pendingRole });
    renderPipeline();

    expect(await screen.findByText('Agent turn-on is queued')).toBeInTheDocument();
    expect(screen.getByText(/You can leave this page/i)).toBeInTheDocument();
  });

  it('shows an honest blocked activation after page reload', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        agentic_mode_enabled: false,
        assessment_task_provisioning: {
          status: 'blocked',
          activation_intent: {
            status: 'blocked',
            last_error: 'Assessment task provisioning is blocked: job description is too thin',
          },
        },
      },
    });
    renderPipeline();

    expect(await screen.findByText('Agent turn-on needs input')).toBeInTheDocument();
    expect(screen.getByText(/job description is too thin/i)).toBeInTheDocument();
  });

  it('shows stage-aware card signals instead of pre-screen scores in early stages', async () => {
    renderPipeline();
    await switchToPipelineView();

    const appliedCard = (await screen.findByText('Sam Patel')).closest('.kanban-card');
    const reviewCard = (await screen.findByText('Priya Anand')).closest('.kanban-card');

    expect(appliedCard).toBeTruthy();
    expect(reviewCard).toBeTruthy();

    // Per HANDOFF v2 §4 / canvas jobs-detail-pipeline — early-stage cards
    // (applied / invited / in_assessment) hide the composite score
    // entirely until a review-stage signal exists. Review-stage cards
    // surface the composite score in the agent recommendation block.
    expect(within(appliedCard).queryByText('91')).not.toBeInTheDocument();
    expect(within(appliedCard).queryByText('64')).not.toBeInTheDocument();

    await waitFor(() => {
      expect(within(reviewCard).getByText('64')).toBeInTheDocument();
    });
  });

  it('renders an em dash rather than a fabricated zero for unscored kanban cards', async () => {
    apiClient.roles.listApplications.mockResolvedValue({
      data: [{
        ...baseApplications[0],
        taali_score: null,
        cv_match_score: null,
        pre_screen_score: null,
      }],
    });
    renderPipeline();
    await switchToPipelineView();

    const card = (await screen.findByText('Sam Patel')).closest('.kanban-card');
    expect(within(card).getByText('CV —')).toBeInTheDocument();
    expect(within(card).queryByText('0')).not.toBeInTheDocument();
  });

  it('keeps agent recommendations compact instead of rendering long reasoning in the kanban', async () => {
    const longReasoning = 'Strong technical profile with directly relevant skills. '.repeat(30);
    apiClient.agent.listDecisionExecutionSnapshots.mockResolvedValue({
      data: [{
        id: 501,
        application_id: 2,
        recommendation: 'reject',
        reasoning: longReasoning,
      }],
    });
    renderPipeline();
    await switchToPipelineView();

    const reviewCard = (await screen.findByText('Priya Anand')).closest('.kanban-card');
    expect(await within(reviewCard).findByText('Reject')).toBeInTheDocument();
    expect(within(reviewCard).getByRole('button', { name: /^Approve$/i })).toBeInTheDocument();
    expect(within(reviewCard).getByRole('button', { name: /^Override$/i })).toBeInTheDocument();
    expect(within(reviewCard).queryByText(longReasoning)).not.toBeInTheDocument();
  });

  it('names and snapshots every linked role before approving a reject recommendation', async () => {
    const roleFamily = {
      owner: { id: 77, name: 'AI Engineer' },
      related: [
        { id: 101, name: 'AI Native Engineer' },
        { id: 109, name: 'ML Platform Engineer' },
      ],
    };
    const relatedRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      ats_provider: 'workable',
      ats_owner_role_id: 77,
      ats_owner_role_name: 'AI Engineer',
      role_family: roleFamily,
    };
    apiClient.roles.getShell.mockResolvedValue({ data: relatedRole });
    apiClient.roles.get.mockResolvedValue({ data: relatedRole });
    apiClient.agent.listDecisionExecutionSnapshots.mockResolvedValue({
      data: [{
        id: 502,
        application_id: 2,
        decision_type: 'reject',
        recommendation: 'Do not proceed',
        role_family: roleFamily,
      }],
    });
    renderPipeline();
    await switchToPipelineView();

    const reviewCard = (await screen.findByText('Priya Anand')).closest('.kanban-card');
    fireEvent.click(await within(reviewCard).findByRole('button', { name: /^Approve$/i }));

    expect(await screen.findByRole('heading', { name: 'Reject across linked roles?' }))
      .toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByText(
      /AI Engineer #77, AI Native Engineer #101, ML Platform Engineer #109/,
    )).toBeInTheDocument();
    expect(apiClient.agent.approveDecision).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Reject across all linked roles' }));
    await waitFor(() => expect(apiClient.agent.approveDecision).toHaveBeenCalledWith(502, {
      expected_decision_type: 'reject',
      expected_role_family: roleFamily,
    }));
  });

  it('reloads the family and decision when linked-role authority changes before approval', async () => {
    const roleFamily = {
      owner: { id: 77, name: 'AI Engineer' },
      related: [{ id: 101, name: 'AI Native Engineer' }],
    };
    const refreshedFamily = {
      owner: { id: 77, name: 'AI Engineer' },
      related: [
        { id: 101, name: 'AI Native Engineer' },
        { id: 109, name: 'ML Platform Engineer' },
      ],
    };
    const relatedRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      ats_provider: 'workable',
      ats_owner_role_id: 77,
      ats_owner_role_name: 'AI Engineer',
      role_family: roleFamily,
    };
    apiClient.roles.getShell.mockResolvedValue({ data: relatedRole });
    apiClient.roles.get.mockResolvedValue({ data: relatedRole });
    apiClient.agent.listDecisionExecutionSnapshots.mockResolvedValueOnce({
      data: [{
        id: 503,
        application_id: 2,
        decision_type: 'reject',
        recommendation: 'Reject',
        role_family: roleFamily,
      }],
    }).mockResolvedValue({
      data: [{
        id: 503,
        application_id: 2,
        decision_type: 'reject',
        recommendation: 'Reject',
        role_family: refreshedFamily,
      }],
    });
    apiClient.agent.approveDecision.mockRejectedValueOnce({
      response: {
        status: 409,
        data: { detail: { code: 'ROLE_FAMILY_CHANGED' } },
      },
    });
    renderPipeline();
    await switchToPipelineView();

    const reviewCard = (await screen.findByText('Priya Anand')).closest('.kanban-card');
    fireEvent.click(await within(reviewCard).findByRole('button', { name: /^Approve$/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Reject across all linked roles' }));

    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('Linked roles changed before approval'),
      'warning',
    ));
    await waitFor(() => expect(apiClient.agent.listDecisionExecutionSnapshots.mock.calls.length).toBeGreaterThan(1));
    await waitFor(() => expect(apiClient.roles.get.mock.calls.length).toBeGreaterThan(1));

    fireEvent.click(await within(reviewCard).findByRole('button', { name: /^Approve$/i }));
    expect(await screen.findByRole('dialog')).toHaveTextContent(
      'ML Platform Engineer #109',
    );
  });

  it('loads the exact Workable job stages before approving an advance', async () => {
    apiClient.agent.listDecisionExecutionSnapshots.mockResolvedValue({
      data: [{
        id: 505,
        application_id: 2,
        candidate_name: 'Priya Anand',
        decision_type: 'advance_to_interview',
        recommendation: 'Advance',
        workable_job_id: 'AI-ENG',
        workable_stage: 'phone-screen',
      }],
    });
    apiClient.organizations.getWorkableStages.mockResolvedValue({
      data: { stages: [
        { slug: 'phone-screen', name: 'Phone screen' },
        { slug: 'technical-interview', name: 'Technical interview' },
      ] },
    });
    renderPipeline();
    await switchToPipelineView();

    const reviewCard = (await screen.findByText('Priya Anand')).closest('.kanban-card');
    fireEvent.click(await within(reviewCard).findByRole('button', { name: /^Approve$/i }));

    await waitFor(() => expect(apiClient.organizations.getWorkableStages).toHaveBeenCalledWith({
      shortcode: 'AI-ENG',
    }));
    const dialog = await screen.findByRole('dialog', { name: /Advance Priya Anand/i });
    expect(within(dialog).getByRole('radio', { name: /Phone screen.*Current/i })).toBeDisabled();
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Technical interview' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Advance' }));

    await waitFor(() => expect(apiClient.agent.approveDecision).toHaveBeenCalledWith(505, {
      note: null,
      expected_decision_type: 'advance_to_interview',
      workable_target_stage: 'technical-interview',
    }, { force: false }));
  });

  it('does not open an old decision dialog when stage lookup finishes after role navigation', async () => {
    const oldStageRequest = deferred();
    const otherRole = { ...baseRole, id: 202, name: 'Data Platform Lead' };
    const otherApplication = {
      ...baseApplications[0],
      id: 88,
      candidate_id: 880,
      candidate_name: 'New Role Candidate',
    };
    apiClient.agent.listDecisionExecutionSnapshots.mockImplementation(({ role_id: requestedRoleId }) => (
      Number(requestedRoleId) === 101
        ? Promise.resolve({
          data: [{
            id: 509,
            application_id: 2,
            candidate_name: 'Priya Anand',
            decision_type: 'advance_to_interview',
            recommendation: 'Advance',
            workable_job_id: 'AI-ENG',
            workable_stage: 'phone-screen',
          }],
        })
        : Promise.resolve({ data: [] })
    ));
    apiClient.organizations.getWorkableStages.mockReturnValue(oldStageRequest.promise);
    apiClient.roles.getShell.mockImplementation((requestedRoleId) => Promise.resolve({
      data: Number(requestedRoleId) === 202 ? otherRole : baseRole,
    }));
    apiClient.roles.get.mockImplementation((requestedRoleId) => Promise.resolve({
      data: Number(requestedRoleId) === 202 ? otherRole : baseRole,
    }));
    apiClient.roles.listApplications.mockImplementation((requestedRoleId) => Promise.resolve({
      data: Number(requestedRoleId) === 202 ? [otherApplication] : baseApplications,
    }));
    renderPipeline({ roleSwitcher: true });
    await switchToPipelineView();

    const reviewCard = (await screen.findByText('Priya Anand')).closest('.kanban-card');
    fireEvent.click(await within(reviewCard).findByRole('button', { name: /^Approve$/i }));
    await waitFor(() => expect(apiClient.organizations.getWorkableStages).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('link', { name: 'Switch role' }));
    expect(await screen.findByText('New Role Candidate')).toBeInTheDocument();

    await act(async () => {
      oldStageRequest.resolve({
        data: { stages: [
          { slug: 'phone-screen', name: 'Phone screen' },
          { slug: 'technical-interview', name: 'Technical interview' },
        ] },
      });
      await oldStageRequest.promise;
    });

    expect(screen.queryByRole('dialog', { name: /Advance Priya Anand/i }))
      .not.toBeInTheDocument();
    expect(screen.getByText('New Role Candidate')).toBeInTheDocument();
  });

  it('blocks a linked Workable advance when no forward stage exists', async () => {
    apiClient.agent.listDecisionExecutionSnapshots.mockResolvedValue({
      data: [{
        id: 506,
        application_id: 2,
        candidate_name: 'Priya Anand',
        decision_type: 'advance_to_interview',
        recommendation: 'Advance',
        workable_job_id: 'AI-ENG',
      }],
    });
    apiClient.organizations.getWorkableStages.mockResolvedValue({
      data: { stages: [{ slug: 'sourced', name: 'Sourced' }, { slug: 'applied', name: 'Applied' }] },
    });
    renderPipeline();
    await switchToPipelineView();

    const reviewCard = (await screen.findByText('Priya Anand')).closest('.kanban-card');
    fireEvent.click(await within(reviewCard).findByRole('button', { name: /^Approve$/i }));

    const dialog = await screen.findByRole('dialog', { name: /Advance Priya Anand/i });
    expect(within(dialog).getByRole('alert')).toHaveTextContent('no available advance stage');
    expect(within(dialog).getByRole('button', { name: 'Advance' })).toBeDisabled();
    expect(apiClient.agent.approveDecision).not.toHaveBeenCalled();
  });

  it('keeps a linked advance unsubmitted when its stage lookup fails', async () => {
    apiClient.agent.listDecisionExecutionSnapshots.mockResolvedValue({
      data: [{
        id: 507,
        application_id: 2,
        candidate_name: 'Priya Anand',
        decision_type: 'advance_to_interview',
        recommendation: 'Advance',
        workable_job_id: 'AI-ENG',
      }],
    });
    apiClient.organizations.getWorkableStages.mockRejectedValue(new Error('Workable offline'));
    renderPipeline();
    await switchToPipelineView();

    const reviewCard = (await screen.findByText('Priya Anand')).closest('.kanban-card');
    fireEvent.click(await within(reviewCard).findByRole('button', { name: /^Approve$/i }));

    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      "Couldn't load Workable stages. Try again.",
      'error',
    ));
    expect(screen.queryByRole('dialog', { name: /Advance Priya Anand/i })).not.toBeInTheDocument();
    expect(apiClient.agent.approveDecision).not.toHaveBeenCalled();
  });

  it('keeps internal and Bullhorn advances independent of Workable stage lookup', async () => {
    apiClient.agent.listDecisionExecutionSnapshots.mockResolvedValue({
      data: [{
        id: 508,
        application_id: 2,
        decision_type: 'advance_to_interview',
        recommendation: 'Advance',
        workable_job_id: null,
      }],
    });
    renderPipeline();
    await switchToPipelineView();

    const reviewCard = (await screen.findByText('Priya Anand')).closest('.kanban-card');
    fireEvent.click(await within(reviewCard).findByRole('button', { name: /^Approve$/i }));

    await waitFor(() => expect(apiClient.agent.approveDecision).toHaveBeenCalledWith(508, {
      expected_decision_type: 'advance_to_interview',
    }));
    expect(apiClient.organizations.getWorkableStages).not.toHaveBeenCalled();
  });

  it('binds override to the displayed decision type and refreshes changed decisions', async () => {
    apiClient.agent.listDecisionExecutionSnapshots.mockResolvedValue({
      data: [{
        id: 504,
        application_id: 2,
        decision_type: 'advance_to_interview',
        recommendation: 'Advance',
      }],
    });
    apiClient.agent.overrideDecision.mockRejectedValueOnce({
      response: {
        status: 409,
        data: { detail: { code: 'DECISION_CHANGED' } },
      },
    });
    renderPipeline();
    await switchToPipelineView();

    const reviewCard = (await screen.findByText('Priya Anand')).closest('.kanban-card');
    fireEvent.click(await within(reviewCard).findByRole('button', { name: /^Override$/i }));

    await waitFor(() => expect(apiClient.agent.overrideDecision).toHaveBeenCalledWith(504, {
      override_action: 'manual_review',
      expected_decision_type: 'advance_to_interview',
    }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('recommendation changed before override'),
      'warning',
    ));
    await waitFor(() => expect(apiClient.agent.listDecisionExecutionSnapshots.mock.calls.length).toBeGreaterThan(1));
    await waitFor(() => expect(apiClient.roles.get.mock.calls.length).toBeGreaterThan(1));
  });

  it('names every linked role before approving an advance recommendation', async () => {
    const roleFamily = {
      owner: { id: 77, name: 'AI Engineer' },
      related: [
        { id: 101, name: 'AI Native Engineer' },
        { id: 109, name: 'ML Platform Engineer' },
      ],
    };
    const relatedRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      ats_provider: 'workable',
      ats_owner_role_id: 77,
      ats_owner_role_name: 'AI Engineer',
      role_family: roleFamily,
    };
    apiClient.roles.getShell.mockResolvedValue({ data: relatedRole });
    apiClient.roles.get.mockResolvedValue({ data: relatedRole });
    apiClient.agent.listDecisionExecutionSnapshots.mockResolvedValue({
      data: [{
        id: 503,
        application_id: 2,
        decision_type: 'advance_to_interview',
        recommendation: 'advance',
        role_family: roleFamily,
      }],
    });
    renderPipeline();
    await switchToPipelineView();

    const reviewCard = (await screen.findByText('Priya Anand')).closest('.kanban-card');
    fireEvent.click(await within(reviewCard).findByRole('button', { name: /^Approve$/i }));

    expect(await screen.findByRole('heading', { name: 'Advance across linked roles?' })).toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByText(
      /AI Engineer #77, AI Native Engineer #101, ML Platform Engineer #109/,
    ))
      .toBeInTheDocument();
    expect(apiClient.agent.approveDecision).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Advance across all linked roles' }));
    await waitFor(() => expect(apiClient.agent.approveDecision).toHaveBeenCalledWith(503, {
      expected_decision_type: 'advance_to_interview',
      expected_role_family: roleFamily,
    }));
  });

  it('never invents an agent recommendation from the score when no decision is queued', async () => {
    // Regression: a review-stage candidate scoring < 50 with NO queued agent
    // decision must not show a "Reject recommended" badge. That score-band guess
    // reads as a real, actionable decision when there is nothing behind it.
    apiClient.roles.listApplications.mockResolvedValue({ data: [{
      id: 9, candidate_id: 99,
      candidate_name: 'Lowscore Lee', candidate_email: 'lee@example.com',
      pipeline_stage: 'review', application_outcome: 'open',
      taali_score: 31, status: 'completed',
      created_at: '2026-04-26T01:00:00Z', updated_at: '2026-04-26T01:00:00Z',
      score_summary: { taali_score: 31, assessment_id: 91 },
    }] });
    renderPipeline();

    const row = (await screen.findByText('Lowscore Lee')).closest('tr');
    expect(row).toBeTruthy();
    // No fabricated recommendation anywhere in the row.
    expect(within(row).queryByText(/recommended/i)).not.toBeInTheDocument();
    expect(within(row).queryByText(/^Reject$/)).not.toBeInTheDocument();
    // And the stage label is cleanly cased, not the raw lowercase enum.
    expect(within(row).getByText('Review')).toBeInTheDocument();
    expect(within(row).queryByText('review')).not.toBeInTheDocument();
  });

  it('opens the triage drawer when a kanban card is clicked', async () => {
    const onNavigate = vi.fn();
    renderPipeline({ onNavigate });
    await switchToPipelineView();

    const appliedCard = (await screen.findByText('Sam Patel')).closest('.kanban-card');
    const appliedLink = within(appliedCard).getByRole('link', { name: /Open Sam Patel/i });
    // Modifier-clicking a kanban card still falls through to the link's
    // default behaviour (open in new tab), so the href is preserved.
    expect(appliedLink).toHaveAttribute('href', '/candidates/1?from=jobs/101');

    fireEvent.click(appliedLink);

    // Plain click opens the triage drawer in-place — recruiters do most
    // of their move-stage / send-assessment / reject work without ever
    // leaving the role page. The Reject card's subtitle is unique to
    // the redesigned drawer.
    expect(await screen.findByText(/Closes the application/i)).toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalledWith('candidate-report', expect.anything());
  });

  it('patches just the rejected row instead of re-downloading the whole workspace', async () => {
    apiClient.roles.updateApplicationOutcome.mockResolvedValue({ data: null });
    // The single-row patch refetches ONLY the affected application.
    apiClient.roles.getApplication.mockResolvedValue({
      data: { ...baseApplications[0], application_outcome: 'rejected' },
    });
    renderPipeline();
    await switchToPipelineView();

    // Open the triage drawer for Sam, then reject.
    const appliedCard = (await screen.findByText('Sam Patel')).closest('.kanban-card');
    fireEvent.click(within(appliedCard).getByRole('link', { name: /Open Sam Patel/i }));
    await screen.findByText(/Closes the application/i);

    // listApplications ran twice on cold load (open + rejected). Rejecting must
    // NOT trigger a third/fourth call — the row is patched via getApplication.
    const beforeRejectCalls = apiClient.roles.listApplications.mock.calls.length;
    // Select the Reject option card, then confirm.
    fireEvent.click((await screen.findByText('Closes the application')).closest('button'));
    fireEvent.click(screen.getByRole('button', { name: /Reject candidate/i }));

    await waitFor(() => {
      expect(apiClient.roles.updateApplicationOutcome).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ application_outcome: 'rejected' }),
      );
    });
    await waitFor(() => {
      expect(apiClient.roles.getApplication).toHaveBeenCalledWith(1);
    });
    // No full-workspace refetch: the 2×2000-row listApplications call count is
    // unchanged after the reject.
    expect(apiClient.roles.listApplications.mock.calls.length).toBe(beforeRejectCalls);
  });

  it('does not apply a late single-row patch after navigating to another role', async () => {
    let releaseApplicationPatch;
    const pendingApplicationPatch = new Promise((resolve) => {
      releaseApplicationPatch = resolve;
    });
    const otherRole = {
      ...baseRole,
      id: 202,
      name: 'Data Platform Lead',
      active_candidates_count: 1,
    };
    const otherApplication = {
      ...baseApplications[1],
      id: 88,
      candidate_id: 880,
      candidate_name: 'New Role Candidate',
    };
    apiClient.roles.get.mockImplementation((requestedRoleId) => Promise.resolve({
      data: Number(requestedRoleId) === 202 ? otherRole : baseRole,
    }));
    apiClient.roles.getShell.mockImplementation((requestedRoleId) => Promise.resolve({
      data: Number(requestedRoleId) === 202 ? otherRole : baseRole,
    }));
    apiClient.roles.listApplications.mockImplementation((requestedRoleId) => Promise.resolve({
      data: Number(requestedRoleId) === 202 ? [otherApplication] : baseApplications,
    }));
    apiClient.roles.updateApplicationOutcome.mockResolvedValue({ data: null });
    apiClient.roles.getApplication.mockReturnValue(pendingApplicationPatch);
    renderPipeline({ roleSwitcher: true });
    await switchToPipelineView();

    const appliedCard = (await screen.findByText('Sam Patel')).closest('.kanban-card');
    fireEvent.click(within(appliedCard).getByRole('link', { name: /Open Sam Patel/i }));
    fireEvent.click((await screen.findByText('Closes the application')).closest('button'));
    fireEvent.click(screen.getByRole('button', { name: /Reject candidate/i }));
    await waitFor(() => expect(apiClient.roles.getApplication).toHaveBeenCalledWith(1));

    fireEvent.click(screen.getByRole('link', { name: 'Switch role' }));
    expect(await screen.findByText('New Role Candidate')).toBeInTheDocument();

    await act(async () => {
      releaseApplicationPatch({
        data: { ...baseApplications[0], application_outcome: 'rejected' },
      });
      await pendingApplicationPatch;
    });

    expect(screen.getByText('New Role Candidate')).toBeInTheDocument();
    expect(screen.queryByText('Sam Patel')).toBeNull();
    expect(screen.getAllByText('Data Platform Lead')).not.toHaveLength(0);
    expect(within(screen.getByText('In pipeline').closest('.kpi-tile')).getByText('1'))
      .toBeInTheDocument();
  });

  it('hides the prior role immediately and fences its late pending decisions during cold navigation', async () => {
    const oldDecisionRequest = deferred();
    const newShellRequest = deferred();
    const newDetailRequest = deferred();
    const otherRole = {
      ...baseRole,
      id: 202,
      name: 'Data Platform Lead',
      active_candidates_count: 1,
    };
    const otherApplication = {
      ...baseApplications[1],
      id: 88,
      candidate_id: 880,
      candidate_name: 'New Role Candidate',
    };
    apiClient.agent.listDecisionExecutionSnapshots.mockImplementation(({ role_id: requestedRoleId }) => (
      Number(requestedRoleId) === 101
        ? oldDecisionRequest.promise
        : Promise.resolve({ data: [] })
    ));
    apiClient.roles.getShell.mockImplementation((requestedRoleId) => (
      Number(requestedRoleId) === 202
        ? newShellRequest.promise
        : Promise.resolve({ data: baseRole })
    ));
    apiClient.roles.get.mockImplementation((requestedRoleId) => (
      Number(requestedRoleId) === 202
        ? newDetailRequest.promise
        : Promise.resolve({ data: baseRole })
    ));
    apiClient.roles.listApplications.mockImplementation((requestedRoleId) => Promise.resolve({
      data: Number(requestedRoleId) === 202 ? [otherApplication] : baseApplications,
    }));

    renderPipeline({ roleSwitcher: true });
    const oldCandidateRow = (await screen.findByText('Sam Patel')).closest('tr');
    fireEvent.click(oldCandidateRow);
    expect(await screen.findByText(/Closes the application/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('link', { name: 'Switch role' }));

    // The URL now names role 202, while its cold shell is deliberately held.
    // Nothing interactive from role 101 may remain painted in that window.
    expect(screen.queryByText('Sam Patel')).not.toBeInTheDocument();
    expect(screen.queryByText(/Closes the application/i)).not.toBeInTheDocument();

    await act(async () => {
      oldDecisionRequest.resolve({
        data: [{
          id: 990,
          application_id: 88,
          decision_type: 'reject',
          recommendation: 'Reject',
        }],
      });
      await oldDecisionRequest.promise;
    });

    expect(screen.queryByText('Sam Patel')).not.toBeInTheDocument();
    expect(screen.queryByText(/^Reject$/)).not.toBeInTheDocument();

    await act(async () => {
      newShellRequest.resolve({ data: otherRole });
      await newShellRequest.promise;
    });
    await act(async () => {
      newDetailRequest.resolve({ data: otherRole });
      await newDetailRequest.promise;
    });

    const newCandidateRow = (await screen.findByText('New Role Candidate')).closest('tr');
    expect(within(newCandidateRow).queryByText(/^Reject$/)).not.toBeInTheDocument();
    expect(screen.queryByText('Sam Patel')).not.toBeInTheDocument();
  });

  it('formats Workable job specs instead of showing flattened markdown', async () => {
    apiClient.roles.get.mockResolvedValueOnce({
      data: {
        ...baseRole,
        name: 'Portfolio Lead and Business Manager',
        description: `# Portfolio Lead and Business Manager
**Location:** Dubai, United Arab Emirates
**Employment type:** Full-time
**Application:** https://deeplight.workable.com/jobs/5757335/candidates/new
**State:** published

## Description
DeepLight AI is a specialist AI and data consultancy dedicated to transforming the regional corporate landscape.

DeepLight AI is a specialist AI and data consultancy dedicated to transforming the regional corporate landscape.

The Portfolio Lead and Business Manager is a high-impact leadership position responsible for the end-to-end operational, financial, and delivery excellence of the Data Platform. As a core member of the Senior Leadership Team, this role carries a high degree of organizational authority and accountability, requiring an individual who can command respect across technical and financial functions while demonstrating rigorous management over the platform's most critical strategic assets. You will serve as the primary link between technical engineering teams and corporate functions, ensuring that resources are optimized, budgets are controlled, and strategic programs are delivered with rigorous governance.

Your responsibilities within this role will include;
Financial & Resource Management
Delivery Governance & Leadership
Operational Excellence

*As an AI consultancy, our greatest asset is the expertise of our people. **Requirements** To be successful in this role, you'll need: - 8+ years leading AI, data, or platform delivery teams. - Strong communication with executive stakeholders.

It would be great if you also have;
Banking transformation experience

**Benefits** Benefits & Growth Opportunities - Shape the future of AI implementation with a senior team. - Inclusive interview and application process.`,
      },
    });

    const { container } = renderPipeline();

    await screen.findByRole('heading', { name: /Portfolio Lead and Business Manager/i });

    // Hero only shows the role metadata (location, etc.) per HANDOFF v2
    // §4.4 / canvas jobs-detail-* — the formatted spec body lives on the
    // Job spec tab, not in the persistent hero.
    expect(screen.getByText('Dubai, United Arab Emirates')).toBeInTheDocument();
    expect(screen.queryByText(/\*\*Location:\*\*/)).not.toBeInTheDocument();

    // Open the Job Specification tab to access the formatted description. (The
    // role description is now edited inline via <RoleSpecEditPanel>; the
    // formatted, non-flattened spec body lives in the source-description
    // section below — asserted next.)
    fireEvent.click(screen.getByRole('link', { name: /^Job spec$/i }));

    expect(screen.queryByText(/keeps recruiter scoring/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /View description/i }));

    expect(screen.getByText(/Workable source description/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open source posting/i })).toHaveAttribute('href', 'https://deeplight.workable.com/jobs/5757335/candidates/new');
    const querySectionTitle = (label) => screen.queryByText((_, element) => (
      element?.classList?.contains('role-sec-title') && element.textContent.includes(label)
    ));
    const sectionTitle = (label) => screen.getByText((_, element) => (
      element?.classList?.contains('role-sec-title') && element.textContent.includes(label)
    ));
    expect(sectionTitle('Description')).toBeInTheDocument();
    expect(sectionTitle('Requirements')).toBeInTheDocument();
    expect(sectionTitle('Benefits')).toBeInTheDocument();
    // Scope the no-duplication checks to the FORMATTED spec body — the role
    // description is now also editable inline (a textarea) above it, so a global
    // query would legitimately match twice.
    const specBody = within(container.querySelector('.role-sections'));
    expect(specBody.getAllByText(/DeepLight AI is a specialist AI and data consultancy/i)).toHaveLength(1);
    expect(specBody.getAllByText(/The Portfolio Lead and Business Manager is a high-impact leadership position/i)).toHaveLength(1);
    expect(specBody.getByText(/Your responsibilities within this role will include/i)).toBeInTheDocument();
    expect(specBody.getByText(/Financial & Resource Management/i).closest('li')).toBeInTheDocument();
    expect(specBody.getByText(/Delivery Governance & Leadership/i).closest('li')).toBeInTheDocument();
    expect(querySectionTitle('Full Description')).not.toBeInTheDocument();
    expect(querySectionTitle('Candidate Requirements')).not.toBeInTheDocument();
    const requirementsSection = sectionTitle('Requirements').closest('.role-sec');
    const benefitsSection = sectionTitle('Benefits').closest('.role-sec');
    expect(within(requirementsSection).getByText(/To be successful in this role/i)).toBeInTheDocument();
    expect(within(requirementsSection).getByText(/8\+ years leading AI/i).closest('li')).toBeInTheDocument();
    expect(within(requirementsSection).getByText(/Banking transformation experience/i).closest('li')).toBeInTheDocument();
    expect(within(benefitsSection).getByText(/Shape the future of AI implementation/i).closest('li')).toBeInTheDocument();
    // The formatter splits this into a clean "Benefits" section — the raw
    // "**Benefits** Benefits & Growth Opportunities" run shouldn't appear in the
    // formatted body (it's still in the editable description textarea above).
    expect(specBody.queryByText(/Benefits & Growth Opportunities/i)).not.toBeInTheDocument();
    expect(querySectionTitle('What we offer')).not.toBeInTheDocument();
  });

  it('adds a role-only criterion via the chip composer', async () => {
    const newCriterion = {
      id: 99,
      source: 'recruiter',
      bucket: 'must',
      text: 'Payments experience matters',
      org_criterion_id: null,
      ordering: 0,
      weight: 1.0,
      must_have: true,
    };
    apiClient.roles.createCriterion.mockResolvedValue({ data: newCriterion });
    apiClient.roles.get.mockResolvedValue({
      data: { ...baseRole, criteria: [newCriterion], suppressed_org_criterion_ids: [] },
    });

    renderPipeline();
    await openAgentSettingsTab('Guidance');
    await screen.findByRole('heading', { name: /Role criteria/i, level: 2 });

    fireEvent.change(screen.getByLabelText('Criterion text'), {
      target: { value: 'Payments experience matters' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Add$/ }));

    await waitFor(() => {
      expect(apiClient.roles.createCriterion).toHaveBeenCalledWith(
        101,
        expect.objectContaining({ text: 'Payments experience matters', bucket: 'must' }),
        7,
      );
    });
  });

  it('shows the inheritance state when the role has no customizations', async () => {
    // Role inherits two workspace chips (org_criterion_id set, no
    // customized_at, no role-only additions). The role-state pill must
    // read "Inheriting from workspace" rather than "Customized".
    apiClient.organizations.listCriteria.mockResolvedValueOnce({
      data: [
        { id: 5, bucket: 'must', text: '5+ years backend', ordering: 0, weight: 1.0, created_at: '2026-05-08T00:00:00Z' },
        { id: 6, bucket: 'preferred', text: 'Strong SQL', ordering: 1, weight: 1.0, created_at: '2026-05-08T00:00:00Z' },
      ],
    });
    apiClient.roles.get.mockResolvedValueOnce({
      data: {
        ...baseRole,
        suppressed_org_criterion_ids: [],
        criteria: [
          { id: 50, source: 'recruiter', bucket: 'must', text: '5+ years backend', org_criterion_id: 5, customized_at: null, ordering: 0, weight: 1.0, must_have: true },
          { id: 51, source: 'recruiter', bucket: 'preferred', text: 'Strong SQL', org_criterion_id: 6, customized_at: null, ordering: 1, weight: 1.0, must_have: false },
        ],
      },
    });
    renderPipeline();
    await openAgentSettingsTab('Guidance');

    expect(await screen.findByText(/Inheriting from workspace/i)).toBeInTheDocument();
  });

  it('shows job-spec-derived requirements on Agent settings, not just recruiter chips', async () => {
    apiClient.organizations.listCriteria.mockResolvedValueOnce({ data: [] });
    apiClient.roles.get.mockResolvedValueOnce({
      data: {
        ...baseRole,
        suppressed_org_criterion_ids: [],
        criteria: [
          { id: 60, source: 'derived_from_spec', bucket: 'must', text: 'Ships production ML systems', org_criterion_id: null, customized_at: null, ordering: 0, weight: 1.0, must_have: true },
          { id: 61, source: 'recruiter', bucket: 'preferred', text: 'Banking domain', org_criterion_id: null, customized_at: null, ordering: 1, weight: 1.0, must_have: false },
        ],
      },
    });
    renderPipeline();
    await openAgentSettingsTab('Guidance');

    // The spec-derived requirement is now visible + editable (previously the
    // Agent-settings editor filtered out source === 'derived_from_spec').
    expect(await screen.findByText(/Ships production ML systems/i)).toBeInTheDocument();
    expect(screen.getByText(/Banking domain/i)).toBeInTheDocument();
  });

  it('shows the customized state when the recruiter has added a role-only chip', async () => {
    apiClient.organizations.listCriteria.mockResolvedValueOnce({
      data: [
        { id: 5, bucket: 'must', text: '5+ years backend', ordering: 0, weight: 1.0, created_at: '2026-05-08T00:00:00Z' },
      ],
    });
    apiClient.roles.get.mockResolvedValueOnce({
      data: {
        ...baseRole,
        suppressed_org_criterion_ids: [],
        criteria: [
          { id: 50, source: 'recruiter', bucket: 'must', text: '5+ years backend', org_criterion_id: 5, customized_at: null, ordering: 0, weight: 1.0, must_have: true },
          { id: 51, source: 'recruiter', bucket: 'preferred', text: 'Custom for this role', org_criterion_id: null, customized_at: null, ordering: 1, weight: 1.0, must_have: false },
        ],
      },
    });
    renderPipeline();
    await openAgentSettingsTab('Guidance');

    expect(await screen.findByText(/Customized for this role/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sync workspace/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Reset to defaults/i })).toBeInTheDocument();
  });

  it('opens Agent settings and Job spec tabs (renamed from role fit / activity per HANDOFF v2 §4.1)', async () => {
    const { container } = renderPipeline();

    await screen.findByRole('heading', { name: /AI Native Engineer/i });

    fireEvent.click(screen.getByRole('link', { name: /^Agent settings$/i }));
    expect(await screen.findByText(/HOW THE AGENT RUNS THIS ROLE/i)).toBeInTheDocument();
    expect(container.querySelector('.funnel-board')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Role criteria/i })).not.toBeInTheDocument();

    const agentSectionNav = screen.getByRole('navigation', { name: 'Agent settings sections' });
    fireEvent.click(within(agentSectionNav).getByRole('link', { name: /^Guidance/i }));
    expect(await screen.findByRole('heading', { name: /Role criteria/i })).toBeInTheDocument();
    expect(screen.getByTestId('screening-question-editor')).toBeInTheDocument();

    fireEvent.click(within(agentSectionNav).getByRole('link', { name: /^Decision rules/i }));
    expect(screen.getByRole('heading', { name: /Screening threshold/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Actions without approval/i })).toBeInTheDocument();

    // HANDOFF v2 §4.4 / canvas jobs-detail-spec — the Job spec tab renders
    // the formatted Workable-ingested description + "At a glance" sidebar.
    // The pipeline-activity timeline that previously lived under this label
    // was a leftover from the v1 5-tab layout and is gone in v2.
    fireEvent.click(screen.getByRole('link', { name: /^Job spec$/i }));
    expect(await screen.findByRole('button', { name: /View description/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /At a glance/i })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Pipeline activity/i })).not.toBeInTheDocument();

    // Read-first: the spec shows with an Edit button; the focused document
    // editor is hidden until requested, and assessment configuration stays out
    // of this writing workflow.
    const editBtn = screen.getByRole('button', { name: /^Edit$/i });
    expect(screen.queryByText(/^Role title$/i)).not.toBeInTheDocument();
    fireEvent.click(editBtn);
    expect(await screen.findByText(/^Role title$/i)).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Write/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Preview/i })).toBeInTheDocument();
    expect(screen.queryByText(/Choose a job specification file/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Tasks · A\/B/i)).not.toBeInTheDocument();
  });

  it('saves the authoritative job spec through the dedicated endpoint', async () => {
    const originalSpec = '## About the role\nBuild reliable data products for teams across the business and own delivery outcomes.';
    const updatedSpec = `${originalSpec}\n\n## Requirements\n- AWS Glue\n- Python`;
    apiClient.roles.get.mockResolvedValue({ data: { ...baseRole, source: 'workable', job_spec_text: originalSpec } });
    apiClient.roles.updateJobSpec.mockResolvedValue({
      data: {
        applied: true,
        role: { ...baseRole, source: 'workable', job_spec_text: updatedSpec },
        diff: { added: 2, removed: 0, criteria_count: 2 },
        would_rescreen: { count: 2, est_cost_usd: 0.04 },
      },
    });
    // Task assignment belongs to Agent settings. Even when a role has linked
    // tasks, a job-spec-only save must not send a stale replacement roster.
    apiClient.roles.listTasks.mockResolvedValue({
      data: [{ id: 91, name: 'AWS ingestion exercise' }],
    });
    renderPipeline();

    fireEvent.click(await screen.findByRole('link', { name: /^Job spec$/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^Edit$/i }));
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: updatedSpec } });
    fireEvent.click(screen.getByRole('button', { name: /Save job spec/i }));

    await waitFor(() => {
      expect(apiClient.roles.updateJobSpec).toHaveBeenCalledWith(101, {
        job_spec_text: updatedSpec,
        expected_version: 7,
      });
    });
    expect(apiClient.roles.update).not.toHaveBeenCalledWith(
      101,
      expect.objectContaining({ description: expect.anything() }),
    );
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('updated criteria affect 2 existing candidates'),
      'success',
    ));
  });

  it('keeps a stale job-spec draft and offers the collaborator version on conflict', async () => {
    const originalSpec = '## About the role\nBuild reliable data products for teams across the business and own delivery outcomes.';
    const draftSpec = `${originalSpec}\n\n## Requirements\n- Recruiter draft requirement`;
    const latestSpec = `${originalSpec}\n\n## Requirements\n- Collaborator saved requirement`;
    const latestRole = { ...baseRole, version: 8, source: 'workable', job_spec_text: latestSpec };
    apiClient.roles.get.mockResolvedValue({
      data: { ...baseRole, source: 'workable', job_spec_text: originalSpec },
    });
    apiClient.roles.updateJobSpec.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            code: 'ROLE_VERSION_CONFLICT',
            message: 'This job was changed by another recruiter.',
            current_role: latestRole,
            current_version: 8,
            changed_by: { name: 'Aisha Khan' },
          },
        },
      },
    });
    renderPipeline();

    fireEvent.click(await screen.findByRole('link', { name: /^Job spec$/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^Edit$/i }));
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: draftSpec } });
    fireEvent.click(screen.getByRole('button', { name: /Save job spec/i }));

    expect(await screen.findByText(/A newer job specification is available/i)).toBeInTheDocument();
    expect(screen.getByText(/Aisha Khan saved changes while you were editing/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Job description')).toHaveValue(draftSpec);
    expect(apiClient.roles.updateJobSpec).toHaveBeenCalledWith(101, {
      job_spec_text: draftSpec,
      expected_version: 7,
    });

    fireEvent.click(screen.getByRole('button', { name: /Discard draft & load latest/i }));
    expect(screen.getByLabelText('Job description')).toHaveValue(latestSpec);
  });

  it('renders a Last updated column and sorts by it (independent of score) via the header', async () => {
    renderPipeline();

    // Default candidates table carries a sortable Last updated header.
    const lastUpdatedHeader = await screen.findByRole('button', { name: /Sort by last updated/i });
    expect(lastUpdatedHeader).toBeInTheDocument();

    const firstRowName = () => document.querySelector('.ctable tbody tr .name')?.textContent;

    // Default sort is score desc → Priya (64) ahead of Sam (63).
    await waitFor(() => expect(firstRowName()).toBe('Priya Anand'));

    // Sort by Last updated (desc) → Sam (2026-05-22) ahead of Priya (2026-04-20),
    // i.e. the opposite of the score order — proving the new dimension works.
    fireEvent.click(lastUpdatedHeader);
    await waitFor(() => expect(firstRowName()).toBe('Sam Patel'));

    // Clicking the active header again flips to ascending → Priya first.
    fireEvent.click(lastUpdatedHeader);
    await waitFor(() => expect(firstRowName()).toBe('Priya Anand'));
  });

  it('flips the agent strip to ON the instant Resume is clicked (optimistic, no poll wait)', async () => {
    // A role whose agent is enabled but paused — the strip shows PAUSED.
    apiClient.roles.get.mockResolvedValue({ data: { ...baseRole, agentic_mode_enabled: true } });
    apiClient.agent.status.mockResolvedValue({
      data: {
        paused_at: '2026-06-01T00:00:00Z',
        paused_reason: 'paused by you',
        monthly_spent_cents: 507,
        monthly_budget_cents: 10000,
        pending_decisions: 0,
      },
    });
    // Keep the resume call in-flight for the whole assertion: if the flip waited
    // on the server round-trip (the bug Sam hit), the strip would stay PAUSED.
    let resolveResume;
    apiClient.agent.resume.mockReturnValue(new Promise((res) => { resolveResume = res; }));

    renderPipeline();

    const resumeBtn = await screen.findByRole('button', { name: /^resume$/i });
    expect(screen.getByLabelText('Agent paused')).toBeInTheDocument();

    fireEvent.click(resumeBtn);

    // Optimistic: ON immediately, before the (still-pending) resume resolves.
    expect(await screen.findByText('Agent on')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^resume$/i })).not.toBeInTheDocument();
    // Resume hits the per-role soft-resume endpoint, NOT a role PATCH.
    expect(apiClient.agent.resume).toHaveBeenCalledWith(101, 7);
    expect(resolveResume).toBeTypeOf('function'); // resume was fired, not awaited
  });

  it('Pause soft-pauses via the agent endpoint (keeps the role enabled, no PATCH)', async () => {
    const enabledRole = { ...baseRole, agentic_mode_enabled: true };
    apiClient.roles.getShell.mockResolvedValue({ data: enabledRole });
    apiClient.roles.get.mockResolvedValue({ data: enabledRole });
    apiClient.agent.status.mockResolvedValue({
      data: { paused_at: null, monthly_spent_cents: 100, monthly_budget_cents: 10000, pending_decisions: 0 },
    });
    // Hold the request open so both the paused state and its viewer attribution
    // are proven to paint before the status poll returns.
    apiClient.agent.pause.mockReturnValue(new Promise(() => {}));

    renderPipeline();

    const pauseBtn = await screen.findByRole('button', { name: /^pause$/i });
    expect(screen.getByText('Agent on')).toBeInTheDocument();

    fireEvent.click(pauseBtn);

    // Optimistic flip to PAUSED; calls the soft-pause endpoint, never a role
    // PATCH (which would disable the agent and risk the queue).
    expect(await screen.findByLabelText('Agent paused')).toBeInTheDocument();
    expect(screen.getByLabelText('Paused by you · Saving…')).toBeInTheDocument();
    expect(apiClient.agent.pause).toHaveBeenCalledWith(101, 7);
    expect(apiClient.roles.update).not.toHaveBeenCalled();
  });

  it('renders role controls and agent settings read-only without CONTROL_AGENT', async () => {
    const enabledRole = { ...baseRole, agentic_mode_enabled: true };
    apiClient.roles.getShell.mockResolvedValue({ data: enabledRole });
    apiClient.roles.get.mockResolvedValue({ data: enabledRole });
    apiClient.agent.status.mockResolvedValue({
      data: {
        can_control_agent: false,
        enabled: true,
        paused: false,
        paused_at: null,
        monthly_spent_cents: 100,
        monthly_budget_cents: 10000,
        pending_decisions: 0,
      },
    });

    renderPipeline();

    const pause = await screen.findByRole('button', { name: /^pause$/i });
    expect(pause).toBeDisabled();
    expect(pause.getAttribute('title')).toContain('hiring managers');
    fireEvent.click(pause);
    expect(apiClient.agent.pause).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Configure agent' }));
    expect(await screen.findByText('Agent settings are read-only')).toBeInTheDocument();
    expect(screen.getByText(/recruiters assigned to this role/i)).toBeInTheDocument();
  });

  it('keeps sourced outreach read-only for an interviewer without role control', async () => {
    authState.user = { role: 'interviewer' };
    apiClient.roles.listApplications.mockResolvedValue({ data: [sourcedApplication] });
    apiClient.agent.status.mockResolvedValue({ data: { can_control_agent: false } });

    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^Sourced/i }));
    expect(await screen.findByText('Sam Patel')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', {
      name: 'Select all visible sourced candidates',
    })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Select Sam Patel' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Reach out/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Reach out to .* sourced candidate/i }))
      .not.toBeInTheDocument();
  });

  it('keeps sourced outreach original-role-owned on an editable related role', async () => {
    const sisterRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      ats_owner_role_id: 77,
      ats_owner_role_name: 'Original role',
    };
    apiClient.roles.getShell.mockResolvedValue({ data: sisterRole });
    apiClient.roles.get.mockResolvedValue({ data: sisterRole });
    apiClient.roles.listApplications.mockResolvedValue({ data: [sourcedApplication] });
    apiClient.agent.status.mockResolvedValue({ data: { can_control_agent: true } });

    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^Sourced/i }));
    expect(await screen.findByText('Sam Patel')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', {
      name: 'Select all visible sourced candidates',
    })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Select Sam Patel' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Reach out/i })).not.toBeInTheDocument();
  });

  it('allows authorized sourced outreach and clears it when role control is revoked', async () => {
    apiClient.roles.listApplications.mockResolvedValue({ data: [sourcedApplication] });
    apiClient.agent.status
      .mockResolvedValueOnce({ data: { can_control_agent: true } })
      .mockResolvedValueOnce({ data: { can_control_agent: false } })
      .mockResolvedValue({ data: { can_control_agent: true } });

    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /^Sourced/i }));
    const candidateSelection = await screen.findByRole('checkbox', { name: 'Select Sam Patel' });
    expect(candidateSelection).not.toBeChecked();
    fireEvent.click(candidateSelection);
    fireEvent.click(await screen.findByRole('button', { name: 'Reach out (1)' }));
    expect(await screen.findByRole('heading', { name: 'Reach out to 1 sourced candidate' }))
      .toBeInTheDocument();

    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(apiClient.agent.status).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole('heading', { name: 'Reach out to 1 sourced candidate' }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Select Sam Patel' })).not.toBeInTheDocument();

    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(apiClient.agent.status).toHaveBeenCalledTimes(3));
    expect(await screen.findByRole('checkbox', { name: 'Select Sam Patel' })).not.toBeChecked();
    expect(screen.queryByRole('button', { name: /^Reach out/i })).not.toBeInTheDocument();
  });

  it.each([
    ['loading', /Checking your role permissions/i],
    ['error', /permissions could not be loaded/i],
    ['read-only', /Only workspace owners, hiring managers/i],
  ])(
    'fails closed for pipeline mutations while role capability is %s',
    async (capabilityState, disabledReason) => {
      const governedRole = {
        ...baseRole,
        job_spec_text: 'A complete governed role specification.',
        job_status: 'open',
        client_id: 501,
        client_name: 'Platform',
      };
      apiClient.roles.getShell.mockResolvedValue({ data: governedRole });
      apiClient.roles.get.mockResolvedValue({ data: governedRole });
      clientApi.list.mockResolvedValue([
        { id: 501, name: 'Platform' },
        { id: 502, name: 'Research' },
      ]);
      if (capabilityState === 'loading') {
        apiClient.agent.status.mockReturnValue(new Promise(() => {}));
      } else if (capabilityState === 'error') {
        apiClient.agent.status.mockRejectedValue(new Error('permission lookup failed'));
      } else {
        apiClient.agent.status.mockResolvedValue({ data: { can_control_agent: false } });
      }

      renderPipeline();

      const process = await screen.findByRole('button', { name: /^Process candidates$/i });
      const createRelated = screen.getByRole('button', { name: /^Create related role$/i });
      expect(process).toBeDisabled();
      expect(process).toHaveAttribute('title', expect.stringMatching(disabledReason));
      expect(createRelated).toBeDisabled();
      expect(createRelated).toHaveAttribute('title', expect.stringMatching(disabledReason));
      expect(screen.queryByRole('button', { name: /^Edit job spec$/i })).not.toBeInTheDocument();

      const candidateRow = (await screen.findByText('Sam Patel')).closest('tr');
      fireEvent.click(candidateRow);
      expect(await screen.findByRole('note')).toHaveTextContent(/Candidate actions are read-only/i);
      expect(screen.getByRole('button', {
        name: /^Reject Closes the application$/i,
      })).toBeDisabled();
      fireEvent.click(screen.getByRole('button', { name: /Close candidate drawer/i }));

      fireEvent.click(screen.getByRole('link', { name: /^Job spec$/i }));
      expect(await screen.findByRole('heading', { name: /^Role specification$/i })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^Edit$/i })).not.toBeInTheDocument();

      const clientSelect = screen.getByRole('button', { name: 'Assign hiring department' });
      expect(clientSelect).toBeDisabled();
      expect(clientSelect).toHaveAttribute('title', expect.stringMatching(disabledReason));

      fireEvent.click(clientSelect);
      expect(apiClient.roles.setJobStatus).not.toHaveBeenCalled();
      expect(apiClient.roles.setClient).not.toHaveBeenCalled();
      expect(apiClient.roles.processRole).not.toHaveBeenCalled();
      expect(requisitionApi.createRelated).not.toHaveBeenCalled();
      expect(apiClient.roles.updateApplicationOutcome).not.toHaveBeenCalled();
    },
  );

  it.each(['loading', 'error', 'read-only'])(
    'keeps related-role re-scoring disabled while capability is %s',
    async (capabilityState) => {
      const sisterRole = {
        ...baseRole,
        role_kind: 'sister',
        source: 'sister',
        ats_owner_role_id: 77,
        ats_owner_role_name: 'Original role',
      };
      apiClient.roles.getShell.mockResolvedValue({ data: sisterRole });
      apiClient.roles.get.mockResolvedValue({ data: sisterRole });
      if (capabilityState === 'loading') {
        apiClient.agent.status.mockReturnValue(new Promise(() => {}));
      } else if (capabilityState === 'error') {
        apiClient.agent.status.mockRejectedValue(new Error('permission lookup failed'));
      } else {
        apiClient.agent.status.mockResolvedValue({ data: { can_control_agent: false } });
      }

      renderPipeline();

      const rescore = await screen.findByRole('button', { name: /^Re-score roster$/i });
      expect(rescore).toBeDisabled();
      fireEvent.click(rescore);
      expect(apiClient.roles.rescoreSister).not.toHaveBeenCalled();
    },
  );

  it('keeps pipeline mutations available to an authorized recruiter', async () => {
    const governedRole = {
      ...baseRole,
      job_spec_text: 'A complete governed role specification.',
      job_status: 'open',
      client_id: 501,
      client_name: 'Platform',
    };
    apiClient.roles.getShell.mockResolvedValue({ data: governedRole });
    apiClient.roles.get.mockResolvedValue({ data: governedRole });
    apiClient.agent.status.mockResolvedValue({ data: { can_control_agent: true } });
    clientApi.list.mockResolvedValue([
      { id: 501, name: 'Platform' },
      { id: 502, name: 'Research' },
    ]);

    renderPipeline();

    expect(await screen.findByRole('button', { name: /^Process candidates$/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: /^Create related role$/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: /^Edit job spec$/i })).toBeEnabled();

    const candidateRow = (await screen.findByText('Sam Patel')).closest('tr');
    fireEvent.click(candidateRow);
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
    expect(screen.getByRole('button', {
      name: /^Reject Closes the application$/i,
    })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: /Close candidate drawer/i }));

    fireEvent.click(screen.getByRole('link', { name: /^Job spec$/i }));
    expect(await screen.findByRole('button', { name: /^Edit$/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Assign hiring department' })).toBeEnabled();
  });

  it('loads related-role tasks independently from original-role candidate context', async () => {
    const sisterRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      ats_owner_role_id: 77,
      ats_owner_role_name: 'Original role',
    };
    const ownTasks = [{ id: 701, name: 'Related-role assessment task', is_active: true }];
    const originalTasks = [{ id: 702, name: 'Original assessment task', is_active: true }];
    apiClient.roles.getShell.mockResolvedValue({ data: sisterRole });
    apiClient.roles.get.mockResolvedValue({ data: sisterRole });
    apiClient.roles.listTasks.mockImplementation((roleId) => Promise.resolve({
      data: Number(roleId) === 101 ? ownTasks : originalTasks,
    }));

    renderPipeline();

    await waitFor(() => {
      expect(readCache('role-workspace:101')?.data).toEqual(expect.objectContaining({
        roleTasks: ownTasks,
        assessmentContextTasks: originalTasks,
      }));
    });
    expect(apiClient.roles.listTasks).toHaveBeenCalledTimes(2);
    expect(apiClient.roles.listTasks).toHaveBeenCalledWith(101);
    expect(apiClient.roles.listTasks).toHaveBeenCalledWith(77);
    expect(screen.getByRole('button', { name: /^Re-score roster$/i })).toBeEnabled();

    fireEvent.click((await screen.findByText('Sam Patel')).closest('tr'));
    fireEvent.click(screen.getByRole('tab', { name: /^Send assessment$/i }));
    expect(await screen.findByText('Original assessment task')).toBeInTheDocument();
  });

  it('uses an original role\'s own tasks for candidate assessment context', async () => {
    const ownTasks = [{ id: 703, name: 'Role assessment task', is_active: true }];
    apiClient.roles.listTasks.mockResolvedValue({ data: ownTasks });

    renderPipeline();

    await waitFor(() => {
      expect(readCache('role-workspace:101')?.data).toEqual(expect.objectContaining({
        roleTasks: ownTasks,
        assessmentContextTasks: ownTasks,
      }));
    });
    expect(apiClient.roles.listTasks).toHaveBeenCalledTimes(1);
    expect(apiClient.roles.listTasks).toHaveBeenCalledWith(101);

    fireEvent.click((await screen.findByText('Sam Patel')).closest('tr'));
    fireEvent.click(screen.getByRole('tab', { name: /^Send assessment$/i }));
    expect(await screen.findByRole('button', {
      name: /^Role assessment task/i,
    })).toBeInTheDocument();
  });

  it('describes only confirmed-active tasks in the job-spec assessment highlight', async () => {
    apiClient.roles.listTasks.mockResolvedValue({
      data: [
        { id: 703, name: 'Retired assessment task', is_active: false },
        { id: 704, name: 'Approved assessment task', is_active: true },
      ],
    });

    renderPipeline();
    fireEvent.click(await screen.findByRole('link', { name: /^Job spec$/i }));

    const highlights = await screen.findByRole('complementary', { name: /At a glance/i });
    expect(within(highlights).getByText('Approved assessment task')).toBeInTheDocument();
    expect(within(highlights).queryByText('Retired assessment task')).not.toBeInTheDocument();
  });

  it('fails candidate assessment controls closed when task context fails, then recovers on retry', async () => {
    apiClient.roles.listTasks
      .mockRejectedValueOnce({
        response: { data: { detail: 'Assessment context is temporarily unavailable.' } },
      })
      .mockResolvedValueOnce({
        data: [{ id: 704, name: 'Recovered assessment task', is_active: true }],
      });

    renderPipeline();

    fireEvent.click((await screen.findByText('Sam Patel')).closest('tr'));
    fireEvent.click(screen.getByRole('tab', { name: /^Send assessment$/i }));
    const unavailable = await screen.findByRole('alert');
    expect(unavailable).toHaveTextContent('Assessment tasks unavailable.');
    expect(unavailable).toHaveTextContent('Assessment context is temporarily unavailable.');
    expect(screen.queryByText('No tasks linked to this role yet.')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Send invite$/i })).toBeDisabled();
    const rosterRequestCount = apiClient.roles.listApplications.mock.calls.length;

    fireEvent.click(within(unavailable).getByRole('button', { name: 'Retry' }));

    await waitFor(() => {
      expect(screen.queryByText('Assessment tasks unavailable.')).not.toBeInTheDocument();
      expect(screen.getByText('Recovered assessment task')).toBeInTheDocument();
    });
    expect(apiClient.roles.listTasks).toHaveBeenCalledTimes(2);
    expect(apiClient.roles.listApplications).toHaveBeenCalledTimes(rosterRequestCount);
  });

  it('does not load the unused organisation task catalogue for a related scoring role', async () => {
    const sisterRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      ats_owner_role_id: 77,
      ats_owner_role_name: 'Original role',
    };
    apiClient.roles.getShell.mockResolvedValue({ data: sisterRole });
    apiClient.roles.get.mockResolvedValue({ data: sisterRole });

    renderPipeline();
    await openAgentSettingsTab('Decision rules');

    expect(await screen.findByRole('heading', { name: /Assessment tasks/i }))
      .toBeInTheDocument();
    expect(apiClient.tasks.list).not.toHaveBeenCalled();
  });

  it('reports an original-role task failure separately from sister-role task state', async () => {
    const sisterRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      ats_owner_role_id: 77,
      ats_owner_role_name: 'Original role',
    };
    apiClient.roles.getShell.mockResolvedValue({ data: sisterRole });
    apiClient.roles.get.mockResolvedValue({ data: sisterRole });
    apiClient.roles.listTasks.mockImplementation((id) => (
      Number(id) === 77
        ? Promise.reject({
            response: { data: { detail: 'Original role tasks could not be read.' } },
          })
        : Promise.resolve({ data: [{ id: 705, name: 'Sister-only task', is_active: true }] })
    ));

    renderPipeline();

    fireEvent.click((await screen.findByText('Sam Patel')).closest('tr'));
    fireEvent.click(screen.getByRole('tab', { name: /^Send assessment$/i }));
    const unavailable = await screen.findByRole('alert');
    expect(unavailable).toHaveTextContent('Assessment tasks unavailable.');
    expect(unavailable).toHaveTextContent('Original role tasks could not be read.');
    expect(screen.queryByText('Sister-only task')).not.toBeInTheDocument();
    expect(screen.queryByText('No shared assessment tasks are linked on the original role.'))
      .not.toBeInTheDocument();
    const retry = within(unavailable).getByRole('button', { name: 'Retry' });
    expect(apiClient.roles.listTasks).toHaveBeenCalledTimes(2);
    expect(apiClient.roles.listTasks).toHaveBeenCalledWith(101);
    expect(apiClient.roles.listTasks).toHaveBeenCalledWith(77);

    fireEvent.click(retry);
    await waitFor(() => expect(apiClient.roles.listTasks).toHaveBeenCalledTimes(3));
    expect(apiClient.roles.listTasks.mock.calls.map(([roleId]) => roleId)).toEqual([101, 77, 77]);
  });

  it('does not substitute sister tasks when the original-role link is missing', async () => {
    const unlinkedSisterRole = {
      ...baseRole,
      role_kind: 'sister',
      source: 'sister',
      ats_owner_role_id: null,
    };
    apiClient.roles.getShell.mockResolvedValue({ data: unlinkedSisterRole });
    apiClient.roles.get.mockResolvedValue({ data: unlinkedSisterRole });
    apiClient.roles.listTasks.mockResolvedValue({
      data: [{ id: 706, name: 'Sister-only task', is_active: true }],
    });

    renderPipeline();

    fireEvent.click((await screen.findByText('Sam Patel')).closest('tr'));
    fireEvent.click(screen.getByRole('tab', { name: /^Send assessment$/i }));
    const unavailable = await screen.findByRole('alert');
    expect(unavailable).toHaveTextContent('not linked to an original role');
    expect(screen.queryByText('Sister-only task')).not.toBeInTheDocument();
    expect(apiClient.roles.listTasks).toHaveBeenCalledTimes(1);
    expect(apiClient.roles.listTasks).toHaveBeenCalledWith(101);
  });

  it('keeps a budget-blocked 200 Resume no-op visibly paused and explains it', async () => {
    const enabledRole = { ...baseRole, agentic_mode_enabled: true };
    const pausedAt = '2026-07-15T15:00:00Z';
    apiClient.roles.getShell.mockResolvedValue({ data: enabledRole });
    apiClient.roles.get.mockResolvedValue({ data: enabledRole });
    apiClient.agent.status.mockResolvedValue({
      data: {
        can_control_agent: true,
        enabled: true,
        paused: true,
        pause_scope: 'role',
        paused_at: pausedAt,
        paused_reason: 'monthly USD cap reached',
        role_paused_at: pausedAt,
        role_paused_reason: 'monthly USD cap reached',
        monthly_spent_cents: 10000,
        monthly_budget_cents: 10000,
        pending_decisions: 0,
      },
    });
    apiClient.agent.resume.mockResolvedValue({
      data: { resumed: false, paused: true, reason: 'monthly USD cap reached' },
    });

    renderPipeline();
    fireEvent.click(await screen.findByRole('button', { name: /^resume$/i }));

    await waitFor(() => expect(apiClient.agent.resume).toHaveBeenCalledWith(101, 7));
    expect(await screen.findByRole('button', { name: /^resume$/i })).toBeInTheDocument();
    expect(screen.getByLabelText('Auto-paused')).toBeInTheDocument();
    expect(showToast).toHaveBeenCalledWith(
      'Monthly budget reached. Resolve the hold, then try Resume again.',
      'info',
    );
  });

  it('keeps Pause optimistic over a pre-click poll and reads authoritative status on success', async () => {
    const enabledRole = { ...baseRole, agentic_mode_enabled: true };
    const authoritativePauseAt = '2026-07-15T15:00:00Z';
    apiClient.roles.getShell.mockResolvedValue({ data: enabledRole });
    apiClient.roles.get.mockResolvedValue({ data: enabledRole });
    let resolveOldPoll;
    apiClient.agent.status
      .mockReset()
      .mockResolvedValueOnce({
        data: {
          paused: false,
          paused_at: null,
          monthly_spent_cents: 100,
          monthly_budget_cents: 10000,
          pending_decisions: 0,
        },
      })
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveOldPoll = resolve;
      }))
      .mockResolvedValueOnce({
        data: {
          paused: true,
          pause_scope: 'role',
          paused_at: authoritativePauseAt,
          paused_reason: 'paused by recruiter',
          paused_by: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
          role_paused_at: authoritativePauseAt,
          role_paused_reason: 'paused by recruiter',
          role_paused_by: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
          workspace_paused: false,
          monthly_spent_cents: 100,
          monthly_budget_cents: 10000,
          pending_decisions: 0,
        },
      });
    let resolvePause;
    apiClient.agent.pause.mockReset().mockImplementationOnce(() => new Promise((resolve) => {
      resolvePause = resolve;
    }));

    renderPipeline();

    const pauseBtn = await screen.findByRole('button', { name: /^pause$/i });
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(apiClient.agent.status).toHaveBeenCalledTimes(2));

    fireEvent.click(pauseBtn);
    expect(await screen.findByLabelText('Paused by you · Saving…')).toBeInTheDocument();

    // This response began before Pause and still says ON. It must be ignored
    // while the write is pending instead of making the control flicker back.
    await act(async () => {
      resolveOldPoll({
        data: {
          paused: false,
          paused_at: null,
          monthly_spent_cents: 100,
          monthly_budget_cents: 10000,
          pending_decisions: 0,
        },
      });
    });
    expect(screen.getByLabelText('Paused by you · Saving…')).toBeInTheDocument();

    await act(async () => {
      resolvePause({ data: { ...enabledRole, version: 8 } });
    });
    await waitFor(() => expect(apiClient.agent.status).toHaveBeenCalledTimes(3));
    expect(await screen.findByLabelText(/Paused by Aisha Khan/i)).toBeInTheDocument();
  });

  it('shows the workspace hold without losing this role\'s saved control', async () => {
    const enabledRole = { ...baseRole, agentic_mode_enabled: true };
    const now = new Date().toISOString();
    apiClient.roles.getShell.mockResolvedValue({ data: enabledRole });
    apiClient.roles.get.mockResolvedValue({ data: enabledRole });
    apiClient.agent.status.mockResolvedValue({
      data: {
        enabled: true,
        paused: true,
        pause_scope: 'workspace',
        paused_at: now,
        paused_reason: 'workspace paused by recruiter',
        paused_by: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
        role_paused_at: null,
        role_paused_reason: null,
        role_paused_by: null,
        workspace_paused: true,
        workspace_paused_at: now,
        workspace_paused_reason: 'workspace paused by recruiter',
        workspace_paused_by: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
        monthly_spent_cents: 100,
        monthly_budget_cents: 10000,
        pending_decisions: 0,
      },
    });
    apiClient.agent.pause.mockReturnValue(new Promise(() => {}));

    renderPipeline();

    expect(await screen.findByLabelText('All agents paused')).toBeInTheDocument();
    expect(screen.getByLabelText(/Paused by Aisha Khan/i)).toBeInTheDocument();
    expect(screen.getByText('This role remains on and will resume automatically.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Resume all agents' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Pause this role' }));

    expect(apiClient.agent.pause).toHaveBeenCalledWith(101, 7);
    expect(await screen.findByText(/Will remain paused after workspace resumes · Paused by you/i))
      .toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resume role later' })).toBeInTheDocument();
  });

  it('explains the combined review count in the bar and Home action', async () => {
    apiClient.roles.get.mockResolvedValue({ data: { ...baseRole, agentic_mode_enabled: true } });
    apiClient.agent.status.mockResolvedValue({
      data: {
        paused_at: null,
        monthly_spent_cents: 5441,
        monthly_budget_cents: 5000,
        pending_decisions: 176,
        pending_breakdown: { total: 176, decisions: 175, questions: 1 },
      },
    });

    renderPipeline();

    const barCount = await screen.findByLabelText(
      '176 awaiting review: 175 candidate decisions and 1 agent question',
    );
    expect(barCount).toHaveAttribute(
      'aria-label',
      '176 awaiting review: 175 candidate decisions and 1 agent question',
    );
    expect(barCount).toHaveTextContent('176 to review');
    const reviewAction = screen.getByRole('button', {
      name: /176 awaiting you: 175 candidate decisions and 1 agent question.*Home review queue/i,
    });
    expect(reviewAction).toHaveTextContent('Review 176 items');
  });

  it('Turn off confirms, then disables the agent and KEEPS decisions by default', async () => {
    const funnelCounts = {
      sourced: 3, applied: 65, scored: 19, invited: 4, advanced: 1, rejected: 42,
    };
    apiClient.roles.get.mockResolvedValue({
      data: { ...baseRole, stage_counts: funnelCounts, agentic_mode_enabled: true },
    });
    apiClient.roles.getShell.mockResolvedValue({
      data: { ...baseRole, agentic_mode_enabled: true },
    });
    apiClient.agent.status.mockResolvedValue({
      data: { paused_at: null, monthly_spent_cents: 100, monthly_budget_cents: 10000, pending_decisions: 4 },
    });

    renderPipeline();

    // The Turn off control is the icon-only Power button.
    fireEvent.click(await screen.findByRole('button', { name: /turn off agent/i }));

    // Confirm dialog appears, with the opt-in discard checkbox (pending > 0).
    expect(await screen.findByText(/turn off the agent for this role\?/i)).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /also discard/i })).toBeInTheDocument();
    expect(screen.getByText(/Workable intake is not closed by Taali/i)).toBeInTheDocument();
    expect(screen.queryByText(/Pause has the same intake hold/i)).not.toBeInTheDocument();

    // Confirm WITHOUT ticking discard → disable only, queue preserved.
    const beforeApplicationCalls = apiClient.roles.listApplications.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: /^turn off$/i }));

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(101, {
      agentic_mode_enabled: false,
      expected_version: 7,
    }));
    expect(apiClient.agent.discardPending).not.toHaveBeenCalled();
    // Agent state is independent of pipeline state: no expensive workspace
    // reload and no false count-to-zero animation while decisions are kept.
    expect(apiClient.roles.listApplications.mock.calls.length).toBe(beforeApplicationCalls);
    const funnel = document.querySelector('.funnel-board');
    expect(funnel).toBeInTheDocument();
    Object.values(funnelCounts).forEach((count) => {
      expect(within(funnel).getByLabelText(String(count))).toBeInTheDocument();
    });
  });

  it('restores the latest agent state when a stale Turn off conflicts', async () => {
    const enabledRole = { ...baseRole, agentic_mode_enabled: true };
    apiClient.roles.getShell.mockResolvedValue({ data: enabledRole });
    apiClient.roles.get.mockResolvedValue({ data: enabledRole });
    apiClient.agent.status.mockResolvedValue({
      data: { paused_at: null, monthly_spent_cents: 100, monthly_budget_cents: 10000, pending_decisions: 0 },
    });
    apiClient.roles.update.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            code: 'ROLE_VERSION_CONFLICT',
            message: 'This agent was changed by another recruiter.',
            current_role: { id: 101, version: 8, agentic_mode_enabled: true },
            current_version: 8,
            changed_by: { name: 'Aisha Khan' },
          },
        },
      },
    });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /turn off agent/i }));
    fireEvent.click(screen.getByRole('button', { name: /^turn off$/i }));

    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('Latest settings are shown'),
      'error',
    ));
    expect(apiClient.roles.update).toHaveBeenCalledWith(101, {
      agentic_mode_enabled: false,
      expected_version: 7,
    });
    expect(await screen.findByText('Agent on')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /AI Native Engineer/i })).toBeInTheDocument();
  });

  it('New CVs tile counts only auto-scorable candidates, breaking out held-back ones', async () => {
    // Mirrors backend _auto_enqueue_scoring: unscored apps that were
    // pre-screened OUT (below the pre-screen cutoff, no newer CV) or have no
    // CV are NOT "ready to score" — showing them as such made a fully
    // filtered cohort look like the agent was stuck.
    const base = {
      candidate_email: 'x@example.com', pipeline_stage: 'applied',
      application_outcome: 'open', status: 'applied',
      created_at: '2026-04-26T01:00:00Z', updated_at: '2026-04-26T01:00:00Z',
    };
    apiClient.roles.listApplications.mockResolvedValue({ data: [
      // Pre-screen filtered: score 12 < 30 cutoff, CV predates the run.
      { ...base, id: 1, candidate_id: 1, candidate_name: 'Filtered Fay', has_cv_text: true, pre_screen_score: 12, cv_uploaded_at: '2026-04-01T00:00:00Z', pre_screen_run_at: '2026-04-02T00:00:00Z' },
      // CV file exists but extraction produced no text — the auto-scorer
      // filters on cv_text, so this is held back, not "ready to score".
      { ...base, id: 2, candidate_id: 2, candidate_name: 'Nocv Ned', has_cv_text: false, cv_filename: 'ned.pdf', cv_uploaded_at: '2026-04-01T00:00:00Z' },
      // Never pre-screened, has CV text → scoreable.
      { ...base, id: 3, candidate_id: 3, candidate_name: 'Ready Ria', has_cv_text: true, cv_uploaded_at: '2026-04-01T00:00:00Z' },
      // Screened out BUT uploaded a newer CV since the run → scoreable again.
      { ...base, id: 4, candidate_id: 4, candidate_name: 'Fresh Finn', has_cv_text: true, pre_screen_score: 12, cv_uploaded_at: '2026-04-03T00:00:00Z', pre_screen_run_at: '2026-04-02T00:00:00Z' },
    ] });

    renderPipeline();

    const tile = (await screen.findByText('New CVs')).closest('.kpi-tile');
    expect(tile).toBeTruthy();
    // Value = the 2 genuinely scoreable candidates, not all 4 unscored.
    expect(within(tile).getByText('2')).toBeInTheDocument();
    expect(within(tile).getByText('ready to score · 1 pre-screen filtered · 1 no CV')).toBeInTheDocument();
  });

  it('New CVs tile reads 0 with a breakdown when every unscored candidate is held back', async () => {
    // The prod role-26 shape: "35 ready to score" with zero the agent would
    // touch. Must read 0 + the reason, not a big number.
    const base = {
      candidate_email: 'x@example.com', pipeline_stage: 'applied',
      application_outcome: 'open', status: 'applied',
      created_at: '2026-04-26T01:00:00Z', updated_at: '2026-04-26T01:00:00Z',
    };
    apiClient.roles.listApplications.mockResolvedValue({ data: [
      { ...base, id: 1, candidate_id: 1, candidate_name: 'Filtered Fay', has_cv_text: true, pre_screen_score: 12, cv_uploaded_at: '2026-04-01T00:00:00Z', pre_screen_run_at: '2026-04-02T00:00:00Z' },
      { ...base, id: 2, candidate_id: 2, candidate_name: 'Filtered Flo', has_cv_text: true, pre_screen_score: 8, cv_uploaded_at: '2026-04-01T00:00:00Z', pre_screen_run_at: '2026-04-02T00:00:00Z' },
      // No has_cv_text field at all (stale cached payload) and no CV file
      // metadata → falls back to the proxy and reads as no-CV.
      { ...base, id: 3, candidate_id: 3, candidate_name: 'Nocv Ned' },
    ] });

    renderPipeline();

    const tile = (await screen.findByText('New CVs')).closest('.kpi-tile');
    expect(within(tile).getByText('0')).toBeInTheDocument();
    expect(within(tile).getByText('2 pre-screen filtered · 1 no CV')).toBeInTheDocument();
    expect(within(tile).queryByText(/ready to score/)).not.toBeInTheDocument();
  });

  it('Turn off with "also discard" ticked disables AND discards the queue', async () => {
    apiClient.roles.get.mockResolvedValue({ data: { ...baseRole, agentic_mode_enabled: true } });
    apiClient.agent.status.mockResolvedValue({
      data: { paused_at: null, monthly_spent_cents: 100, monthly_budget_cents: 10000, pending_decisions: 4 },
    });

    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /turn off agent/i }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /also discard/i }));
    fireEvent.click(screen.getByRole('button', { name: /^turn off$/i }));

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(101, {
      agentic_mode_enabled: false,
      expected_version: 7,
    }));
    await waitFor(() => expect(apiClient.agent.discardPending).toHaveBeenCalledWith(101, 7));
  });
});
