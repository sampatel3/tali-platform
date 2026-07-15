import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const showToast = vi.fn();

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
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
    listDecisions: vi.fn().mockResolvedValue({ data: [] }),
    approveDecision: vi.fn().mockResolvedValue({ data: null }),
    overrideDecision: vi.fn().mockResolvedValue({ data: null }),
    discardPending: vi.fn().mockResolvedValue({ data: null }),
    pause: vi.fn().mockResolvedValue({ data: null }),
    resume: vi.fn().mockResolvedValue({ data: null }),
    runNow: vi.fn().mockResolvedValue({ data: null }),
  },
}));

vi.mock('../candidates/CandidateSheet', () => ({
  CandidateSheet: () => null,
}));

// CRUD behavior has its own focused test. Keep this large pipeline suite from
// scheduling a second independent settings fetch on every Agent settings case.
vi.mock('./RoleScreeningQuestions', () => ({
  default: () => <div data-testid="screening-question-editor" />,
}));

import * as apiClient from '../../shared/api';
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

const renderPipeline = ({ onNavigate = vi.fn() } = {}) => ({
  onNavigate,
  ...render(
    <MemoryRouter initialEntries={['/jobs/101']}>
      <Routes>
        <Route path="/jobs/:roleId" element={<JobPipelinePage onNavigate={onNavigate} />} />
        <Route path="/chat/agents/:roleId" element={<div>Role agent chat route</div>} />
      </Routes>
    </MemoryRouter>
  ),
});

describe('JobPipelinePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiClient.roles.get.mockResolvedValue({ data: baseRole });
    apiClient.roles.update.mockResolvedValue({ data: baseRole });
    apiClient.roles.updateJobSpec.mockResolvedValue({
      data: {
        applied: true,
        role: baseRole,
        diff: { added: 0, removed: 0, criteria_count: 0 },
        would_rescreen: { count: 0, est_cost_usd: 0 },
      },
    });
    apiClient.roles.listTasks.mockResolvedValue({ data: [] });
    apiClient.roles.listApplications.mockResolvedValue({ data: baseApplications });
    apiClient.roles.batchScoreStatus.mockResolvedValue({ data: { status: 'idle', total: 0, scored: 0, errors: 0 } });
    apiClient.roles.fetchCvsStatus.mockResolvedValue({ data: { status: 'idle', total: 0, fetched: 0, errors: 0 } });
    apiClient.roles.batchPreScreenStatus.mockResolvedValue({ data: { status: 'idle', total: 0, processed: 0, errors: 0 } });
    apiClient.roles.listFeedbackNotes.mockResolvedValue({ data: [] });
    apiClient.roles.listScreeningQuestions.mockResolvedValue({ data: [] });
    apiClient.roles.distribution.mockResolvedValue({ data: { published: false } });
    apiClient.roles.sisterScoringStatus.mockResolvedValue({
      data: { status: 'completed', progress_percent: 100, counts: { done: 2 } },
    });
    apiClient.agent.listDecisions.mockResolvedValue({ data: [] });
    apiClient.tasks.list.mockResolvedValue({ data: [] });
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
  const openAgentSettingsTab = async () => {
    fireEvent.click(await screen.findByRole('link', { name: /^Agent settings$/i }));
  };

  const confirmTurnOnPolicy = async () => {
    fireEvent.click(await screen.findByRole('button', { name: /^turn on$/i }));
    expect(await screen.findByRole('heading', { name: /Turn on this role’s agent/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Turn on with this policy/i }));
  };

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

  it('keeps legacy reject flags aligned behind the single deterministic control', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: { ...baseRole, auto_reject: true, auto_reject_pre_screen: true },
    });
    renderPipeline();
    await openAgentSettingsTab();

    fireEvent.click(await screen.findByRole('button', {
      name: 'Auto-reject pre-screen failures',
    }));
    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(101, {
      auto_reject: false,
      auto_reject_pre_screen: false,
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

  it('shows a coupled sister role with separate sister and original fit scores', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        role_kind: 'sister',
        source: 'sister',
        ats_owner_role_id: 77,
        ats_owner_role_name: 'AI Engineer · Workable',
        effective_workable_job_id: 'AI-ENG',
      },
    });
    apiClient.roles.listApplications.mockResolvedValue({
      data: [{ ...baseApplications[0], taali_score: 91, source_role_score: 72, score_status: 'done' }],
    });

    renderPipeline();

    expect(await screen.findByText('Related · Workable')).toBeInTheDocument();
    expect(screen.getByText((_, element) => (
      element?.tagName === 'SPAN'
      && element.textContent.includes('This is a scoring view coupled to AI Engineer · Workable')
    ))).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /Original fit/i })).toBeInTheDocument();
    const row = screen.getByText('Sam Patel').closest('tr');
    expect(within(row).getByText('91')).toBeInTheDocument();
    expect(within(row).getByText('72')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Open original role/i })).toBeInTheDocument();
    expect(screen.queryByText(/Not published/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Process \d+ candidate/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Edit job spec$/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('link', { name: /^Job spec$/i }));
    expect(await screen.findByRole('heading', { name: /^Role specification$/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Edit$/i })).not.toBeInTheDocument();
    expect(apiClient.roles.updateJobSpec).not.toHaveBeenCalled();
  });

  it('opens related-role creation directly from the job header', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        job_spec_text: 'AI engineer role requiring Python, production machine learning systems, evaluation, observability, and reliable delivery.',
      },
    });
    apiClient.roles.previewSister.mockResolvedValue({
      data: { candidates_total: 2, candidates_with_cv: 2, candidates_missing_cv: 0 },
    });
    renderPipeline();

    fireEvent.click(await screen.findByRole('button', { name: /Create related role/i }));

    expect(await screen.findByRole('heading', { name: /Create a related role/i })).toBeInTheDocument();
    expect(apiClient.roles.previewSister).toHaveBeenCalledWith(baseRole.id);
    expect(screen.getByRole('button', { name: /Create and score candidates/i })).toBeEnabled();
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
    expect(await screen.findByText(/saved policy keeps running after you close this page/i)).toBeInTheDocument();
    expect(screen.getByText(/native job page opens for applications/i)).toBeInTheDocument();
    expect(screen.getByText(/Full CV-score and assessment rejections still need approval/i)).toBeInTheDocument();
    expect(apiClient.roles.update).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /Turn on with this policy/i }));
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
    'keeps %s as intake when previewing agent activation',
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

      expect(await screen.findByText(
        new RegExp(`${label} remains the intake source`, 'i'),
      )).toBeInTheDocument();
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

  it('keeps an untouched first Turn on fully autonomous instead of sending the DB-default legacy false', async () => {
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
    expect(await screen.findByText(/Initial assessments send automatically; resends run automatically/i)).toBeInTheDocument();
    expect(screen.getByText(/Qualified candidates advance automatically to recruiter handoff/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Turn on with this policy/i }));

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

  it('uses a validated generated assessment from the single Turn on authorization', async () => {
    apiClient.roles.listTasks.mockResolvedValue({ data: [{
      id: 707,
      name: 'Generated debugging exercise',
      description: 'Repair the supplied service and explain the trade-offs.',
      scenario: 'Repair the supplied service and explain the trade-offs.',
      duration_minutes: 45,
      is_active: false,
      generated: true,
      needs_review: true,
      battle_test: { verdict: 'pass', failed_checks: [] },
    }] });
    renderPipeline();

    await confirmTurnOnPolicy();

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(
      101,
      expect.objectContaining({
        agentic_mode_enabled: true,
        monthly_usd_budget_cents: 5000,
        activation_assessment_action: 'approve_when_ready',
      }),
    ));
    expect(screen.queryByRole('button', { name: /Approve task & turn on/i })).not.toBeInTheDocument();
  });

  it('keeps first activation OFF when the authoritative PATCH is pending or rejected', async () => {
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

    // A slow server response is not an ON state, and the status payload must
    // not be optimistically rewritten to the bootstrap "starting" state.
    expect(screen.getByText('Agent off')).toBeInTheDocument();
    expect(screen.queryByText('Agent on')).not.toBeInTheDocument();
    expect(screen.queryByText('Agent starting')).not.toBeInTheDocument();

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

  it('can explicitly skip a pending generated assessment in the same Turn on action', async () => {
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

    await confirmTurnOnPolicy();
    expect(await screen.findByText(/battle test is still pending/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Approve task & turn on/i })).not.toBeInTheDocument();
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

  it('persists pending activation immediately without relying on later polling', async () => {
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
    let resolveActivation;
    apiClient.roles.update.mockReturnValue(new Promise((resolve) => {
      resolveActivation = resolve;
    }));
    renderPipeline();

    await confirmTurnOnPolicy();
    expect(await screen.findByText(/battle test is still pending/i)).toBeInTheDocument();

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(
      101,
      expect.objectContaining({
        agentic_mode_enabled: true,
        activation_assessment_action: 'approve_when_ready',
      }),
    ));
    expect(screen.getAllByText(/Saving Turn-on…/i).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Your Turn-on request is saved/i)).not.toBeInTheDocument();

    await act(async () => {
      resolveActivation({
        data: {
          ...baseRole,
          agentic_mode_enabled: false,
          assessment_task_provisioning: {
            activation_intent: { status: 'pending', last_error: null },
          },
        },
      });
    });
    expect(await screen.findByText(/Your Turn-on request is saved/i)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /^close$/i }).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: /Approve task & turn on/i })).not.toBeInTheDocument();
  });

  it('shows a failed durable Turn-on as unsaved and leaves retry available', async () => {
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

    await confirmTurnOnPolicy();

    expect(await screen.findByText(/The Turn-on request was not saved/i)).toBeInTheDocument();
    expect(screen.getByText('Turn-on request failed')).toBeInTheDocument();
    expect(screen.getByText('Activation authorization could not be persisted.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry request/i })).toBeInTheDocument();
    expect(screen.queryByText(/Your Turn-on request is saved/i)).not.toBeInTheDocument();
    expect(screen.getByText('Agent off')).toBeInTheDocument();
  });

  it('shows a persisted queued activation after page reload', async () => {
    apiClient.roles.get.mockResolvedValue({
      data: {
        ...baseRole,
        agentic_mode_enabled: false,
        assessment_task_provisioning: {
          status: 'succeeded',
          activation_intent: { status: 'pending', last_error: null },
        },
      },
    });
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
    apiClient.agent.listDecisions.mockResolvedValue({
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
    await openAgentSettingsTab();
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
    await openAgentSettingsTab();

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
    await openAgentSettingsTab();

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
    await openAgentSettingsTab();

    expect(await screen.findByText(/Customized for this role/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sync workspace/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Reset to defaults/i })).toBeInTheDocument();
  });

  it('opens Agent settings and Job spec tabs (renamed from role fit / activity per HANDOFF v2 §4.1)', async () => {
    renderPipeline();

    await screen.findByRole('heading', { name: /AI Native Engineer/i });

    fireEvent.click(screen.getByRole('link', { name: /^Agent settings$/i }));
    expect(await screen.findByRole('heading', { name: /Role criteria/i })).toBeInTheDocument();
    expect(screen.getByText(/HOW THE AGENT RUNS THIS ROLE/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Screening threshold/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Automatic actions/i })).toBeInTheDocument();

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
    apiClient.roles.get.mockResolvedValue({ data: { ...baseRole, agentic_mode_enabled: true } });
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
    apiClient.roles.get.mockResolvedValue({ data: { ...baseRole, agentic_mode_enabled: true } });
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
    fireEvent.click(screen.getByRole('button', { name: /^turn off$/i }));

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(101, {
      agentic_mode_enabled: false,
      expected_version: 7,
    }));
    expect(apiClient.agent.discardPending).not.toHaveBeenCalled();
  });

  it('restores the latest agent state when a stale Turn off conflicts', async () => {
    const enabledRole = { ...baseRole, agentic_mode_enabled: true };
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
