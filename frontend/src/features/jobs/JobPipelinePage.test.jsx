import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
    listTasks: vi.fn(),
    listApplications: vi.fn(),
    batchScoreStatus: vi.fn(),
    fetchCvsStatus: vi.fn(),
    batchPreScreenStatus: vi.fn(),
    update: vi.fn(),
    regenerateInterviewFocus: vi.fn(),
    fetchCvs: vi.fn(),
    batchPreScreen: vi.fn(),
    batchScore: vi.fn(),
    createCriterion: vi.fn(),
    updateCriterion: vi.fn(),
    deleteCriterion: vi.fn(),
    syncCriteriaWithWorkspace: vi.fn(),
    resetCriteriaToWorkspace: vi.fn(),
  },
  tasks: {
    list: vi.fn(),
  },
  organizations: {
    get: vi.fn().mockResolvedValue({ data: { default_role_requirements: [] } }),
    listCriteria: vi.fn().mockResolvedValue({ data: [] }),
    getWorkableStages: vi.fn().mockResolvedValue({ data: { stages: [] } }),
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

import * as apiClient from '../../shared/api';
import { JobPipelinePage } from './JobPipelinePage';

const baseRole = {
  id: 101,
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
      </Routes>
    </MemoryRouter>
  ),
});

describe('JobPipelinePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiClient.roles.get.mockResolvedValue({ data: baseRole });
    apiClient.roles.update.mockResolvedValue({ data: baseRole });
    apiClient.roles.listTasks.mockResolvedValue({ data: [] });
    apiClient.roles.listApplications.mockResolvedValue({ data: baseApplications });
    apiClient.roles.batchScoreStatus.mockResolvedValue({ data: { status: 'idle', total: 0, scored: 0, errors: 0 } });
    apiClient.roles.fetchCvsStatus.mockResolvedValue({ data: { status: 'idle', total: 0, fetched: 0, errors: 0 } });
    apiClient.roles.batchPreScreenStatus.mockResolvedValue({ data: { status: 'idle', total: 0, processed: 0, errors: 0 } });
    apiClient.tasks.list.mockResolvedValue({ data: [] });
  });

  // Default view is the candidates table; pipeline kanban is opt-in. Tests
  // that assert on kanban cards switch to the Pipeline tab first.
  const switchToPipelineView = async () => {
    fireEvent.click(await screen.findByRole('link', { name: /^Pipeline$/i }));
  };

  // Per HANDOFF v2 §4.3 / canvas jobs-detail-settings — CV scoring criteria
  // and Reject threshold live on the Agent settings tab now (the legacy
  // above-tabs score-panel was retired). Tests that assert on those
  // controls open the tab first.
  const openAgentSettingsTab = async () => {
    fireEvent.click(await screen.findByRole('link', { name: /^Agent settings$/i }));
  };

  it('renders the reject-threshold slider on the Agent settings tab without a spinbutton', async () => {
    renderPipeline();
    await openAgentSettingsTab();

    await screen.findByRole('heading', { name: /Reject threshold/i, level: 2 });

    expect(screen.getByRole('slider', { name: /Reject threshold percent/i })).toBeInTheDocument();
    // The threshold is a slider only — no spinbutton anywhere on the tab.
    // (The agent bar's budget input is its own spinbutton, outside scope.)
    const settingsRegion = document.querySelector('.mc-agent-settings');
    expect(settingsRegion).toBeInTheDocument();
    expect(within(settingsRegion).queryByRole('spinbutton')).not.toBeInTheDocument();
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
    // Modifier-clicking a kanban card still falls through to the link's
    // default behaviour (open in new tab), so the href is preserved.
    expect(appliedCard).toHaveAttribute('href', '/candidates/1?from=jobs/101');

    fireEvent.click(appliedCard);

    // Plain click opens the triage drawer in-place — recruiters do most
    // of their move-stage / send-assessment / reject work without ever
    // leaving the role page. The Reject card's subtitle is unique to
    // the redesigned drawer.
    expect(await screen.findByText(/Closes the application/i)).toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalledWith('candidate-report', expect.anything());
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
    // formatted, non-flattened spec body lives in the "Read full description"
    // section below — asserted next.)
    fireEvent.click(screen.getByRole('link', { name: /^Job Specification$/i }));

    expect(screen.queryByText(/keeps recruiter scoring/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Read full description/i }));

    expect(screen.getByText(/Workable ingested job spec/i)).toBeInTheDocument();
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
    expect(screen.getByRole('heading', { name: /Reject threshold/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Autonomy rules/i })).toBeInTheDocument();

    // HANDOFF v2 §4.4 / canvas jobs-detail-spec — the Job spec tab renders
    // the formatted Workable-ingested description + "At a glance" sidebar.
    // The pipeline-activity timeline that previously lived under this label
    // was a leftover from the v1 5-tab layout and is gone in v2.
    fireEvent.click(screen.getByRole('link', { name: /^Job Specification$/i }));
    expect(await screen.findByRole('button', { name: /Read full description/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /At a glance/i })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Pipeline activity/i })).not.toBeInTheDocument();

    // Read-first: the spec shows with an Edit button; the editable Role name
    // field is hidden until you click Edit, and the job-spec file upload is
    // gone entirely (spec is updated by pasting into the agent).
    const editBtn = screen.getByRole('button', { name: /^Edit$/i });
    expect(screen.queryByText(/Role name/i)).not.toBeInTheDocument();
    fireEvent.click(editBtn);
    expect(await screen.findByText(/Role name/i)).toBeInTheDocument();
    expect(screen.queryByText(/Choose a job specification file/i)).not.toBeInTheDocument();
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
    expect(screen.getByText('Paused')).toBeInTheDocument();

    fireEvent.click(resumeBtn);

    // Optimistic: ON immediately, before the (still-pending) resume resolves.
    expect(await screen.findByText('Agent on')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^resume$/i })).not.toBeInTheDocument();
    // Resume hits the per-role soft-resume endpoint, NOT a role PATCH.
    expect(apiClient.agent.resume).toHaveBeenCalledWith(101);
    expect(resolveResume).toBeTypeOf('function'); // resume was fired, not awaited
  });

  it('Pause soft-pauses via the agent endpoint (keeps the role enabled, no PATCH)', async () => {
    apiClient.roles.get.mockResolvedValue({ data: { ...baseRole, agentic_mode_enabled: true } });
    apiClient.agent.status.mockResolvedValue({
      data: { paused_at: null, monthly_spent_cents: 100, monthly_budget_cents: 10000, pending_decisions: 0 },
    });

    renderPipeline();

    const pauseBtn = await screen.findByRole('button', { name: /^pause$/i });
    expect(screen.getByText('Agent on')).toBeInTheDocument();

    fireEvent.click(pauseBtn);

    // Optimistic flip to PAUSED; calls the soft-pause endpoint, never a role
    // PATCH (which would disable the agent and risk the queue).
    expect(await screen.findByText('Paused')).toBeInTheDocument();
    expect(apiClient.agent.pause).toHaveBeenCalledWith(101);
    expect(apiClient.roles.update).not.toHaveBeenCalled();
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

    // Confirm WITHOUT ticking discard → disable only, queue preserved.
    fireEvent.click(screen.getByRole('button', { name: /^turn off$/i }));

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(101, { agentic_mode_enabled: false }));
    expect(apiClient.agent.discardPending).not.toHaveBeenCalled();
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
      { ...base, id: 1, candidate_id: 1, candidate_name: 'Filtered Fay', pre_screen_score: 12, cv_uploaded_at: '2026-04-01T00:00:00Z', pre_screen_run_at: '2026-04-02T00:00:00Z' },
      // No CV at all — nothing to score.
      { ...base, id: 2, candidate_id: 2, candidate_name: 'Nocv Ned' },
      // Never pre-screened, has a CV → scoreable.
      { ...base, id: 3, candidate_id: 3, candidate_name: 'Ready Ria', cv_uploaded_at: '2026-04-01T00:00:00Z' },
      // Screened out BUT uploaded a newer CV since the run → scoreable again.
      { ...base, id: 4, candidate_id: 4, candidate_name: 'Fresh Finn', pre_screen_score: 12, cv_uploaded_at: '2026-04-03T00:00:00Z', pre_screen_run_at: '2026-04-02T00:00:00Z' },
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
      { ...base, id: 1, candidate_id: 1, candidate_name: 'Filtered Fay', pre_screen_score: 12, cv_uploaded_at: '2026-04-01T00:00:00Z', pre_screen_run_at: '2026-04-02T00:00:00Z' },
      { ...base, id: 2, candidate_id: 2, candidate_name: 'Filtered Flo', pre_screen_score: 8, cv_uploaded_at: '2026-04-01T00:00:00Z', pre_screen_run_at: '2026-04-02T00:00:00Z' },
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

    await waitFor(() => expect(apiClient.roles.update).toHaveBeenCalledWith(101, { agentic_mode_enabled: false }));
    await waitFor(() => expect(apiClient.agent.discardPending).toHaveBeenCalledWith(101));
  });
});
