import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const showToast = vi.fn();

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
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
  },
  tasks: {
    list: vi.fn(),
  },
  organizations: {
    get: vi.fn().mockResolvedValue({ data: { default_role_requirements: [] } }),
  },
  agent: {
    status: vi.fn().mockResolvedValue({ data: null }),
    usageBreakdown: vi.fn().mockResolvedValue({ data: null }),
    listDecisions: vi.fn().mockResolvedValue({ data: [] }),
    approveDecision: vi.fn().mockResolvedValue({ data: null }),
    overrideDecision: vi.fn().mockResolvedValue({ data: null }),
    discardPending: vi.fn().mockResolvedValue({ data: null }),
    runNow: vi.fn().mockResolvedValue({ data: null }),
  },
}));

vi.mock('../candidates/CandidateSheet', () => ({
  CandidateSheet: () => null,
}));

vi.mock('../candidates/CandidatesDirectoryPage', () => ({
  CandidatesDirectoryPage: () => null,
}));

import * as apiClient from '../../shared/api';
import { JobPipelinePage } from './JobPipelinePage';

const baseRole = {
  id: 101,
  name: 'AI Native Engineer',
  source: 'workable',
  active_candidates_count: 2,
  auto_reject_threshold_100: null,
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
    fireEvent.click(await screen.findByRole('button', { name: /^Pipeline$/i }));
  };

  // Per HANDOFF v2 §4.3 / canvas jobs-detail-settings — CV scoring criteria
  // and Reject threshold live on the Agent settings tab now (the legacy
  // above-tabs score-panel was retired). Tests that assert on those
  // controls open the tab first.
  const openAgentSettingsTab = async () => {
    fireEvent.click(await screen.findByRole('button', { name: /^Agent settings$/i }));
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

  it('opens the full report directly from kanban cards', async () => {
    const onNavigate = vi.fn();
    renderPipeline({ onNavigate });
    await switchToPipelineView();

    const appliedCard = (await screen.findByText('Sam Patel')).closest('.kanban-card');
    expect(appliedCard).toHaveAttribute('href', '/candidates/1?from=jobs/101');

    fireEvent.click(appliedCard);

    expect(onNavigate).toHaveBeenCalledWith('candidate-report', { candidateApplicationId: 1, fromRoleId: 101 });
    expect(screen.queryByText(/Send Taali assessment/i)).not.toBeInTheDocument();
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

    // Open the Job spec tab to access the formatted description.
    fireEvent.click(screen.getByRole('button', { name: /^Job spec$/i }));

    expect(container.querySelector('.role-desc-summary')).toHaveTextContent(/The Portfolio Lead and Business Manager is a high-impact leadership position/i);
    expect(container.querySelector('.role-desc-summary')).not.toHaveTextContent(/DeepLight AI is a specialist AI and data consultancy/i);
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
    expect(screen.getAllByText(/DeepLight AI is a specialist AI and data consultancy/i)).toHaveLength(1);
    expect(screen.getAllByText(/The Portfolio Lead and Business Manager is a high-impact leadership position/i)).toHaveLength(1);
    expect(screen.getByText(/Your responsibilities within this role will include/i)).toBeInTheDocument();
    expect(screen.getByText(/Financial & Resource Management/i).closest('li')).toBeInTheDocument();
    expect(screen.getByText(/Delivery Governance & Leadership/i).closest('li')).toBeInTheDocument();
    expect(querySectionTitle('Full Description')).not.toBeInTheDocument();
    expect(querySectionTitle('Candidate Requirements')).not.toBeInTheDocument();
    const requirementsSection = sectionTitle('Requirements').closest('.role-sec');
    const benefitsSection = sectionTitle('Benefits').closest('.role-sec');
    expect(within(requirementsSection).getByText(/To be successful in this role/i)).toBeInTheDocument();
    expect(within(requirementsSection).getByText(/8\+ years leading AI/i).closest('li')).toBeInTheDocument();
    expect(within(requirementsSection).getByText(/Banking transformation experience/i).closest('li')).toBeInTheDocument();
    expect(within(benefitsSection).getByText(/Shape the future of AI implementation/i).closest('li')).toBeInTheDocument();
    expect(screen.queryByText(/Benefits & Growth Opportunities/i)).not.toBeInTheDocument();
    expect(querySectionTitle('What we offer')).not.toBeInTheDocument();
  });

  it('saves recruiter intent from the Agent settings tab', async () => {
    apiClient.roles.update.mockResolvedValue({ data: { ...baseRole, additional_requirements: 'Payments experience matters' } });

    renderPipeline();
    await openAgentSettingsTab();

    // The structured Must-have / Preferred row editor was replaced with
    // a freeform recruiter-intent textarea (system prompt v5 reads it
    // as guidance, not gates). The text round-trips verbatim — no
    // "Must have:" prefix injection on save.
    await screen.findByRole('heading', { name: /Role intent/i, level: 2 });

    fireEvent.change(screen.getByLabelText('Role intent'), {
      target: { value: 'Payments experience matters\nStakeholder governance is critical' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save role settings/i }));

    await waitFor(() => {
      expect(apiClient.roles.update).toHaveBeenCalledWith(101, expect.objectContaining({
        additional_requirements: 'Payments experience matters\nStakeholder governance is critical',
      }));
    });
  });

  it('shows the inheritance hint on the role intent textarea', async () => {
    apiClient.organizations.get.mockResolvedValueOnce({
      data: { default_role_requirements: ['5+ years backend', 'Strong SQL'] },
    });
    // Role with no override yet — should read as "Inheriting from org defaults".
    apiClient.roles.get.mockResolvedValueOnce({
      data: { ...baseRole, additional_requirements: '' },
    });
    renderPipeline();
    await openAgentSettingsTab();

    expect(await screen.findByText(/Inheriting from org defaults/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Role intent')).toHaveAttribute(
      'placeholder',
      expect.stringContaining('5+ years backend'),
    );
  });

  it('shows custom + revert when the role intent diverges from org defaults', async () => {
    apiClient.organizations.get.mockResolvedValueOnce({
      data: { default_role_requirements: ['5+ years backend', 'Strong SQL'] },
    });
    apiClient.roles.get.mockResolvedValueOnce({
      data: { ...baseRole, additional_requirements: 'Custom intent for this role' },
    });
    renderPipeline();
    await openAgentSettingsTab();

    expect(await screen.findByText(/Custom for this role/i)).toBeInTheDocument();
    const revert = screen.getByRole('button', { name: /Revert role intent to org defaults/i });
    fireEvent.click(revert);
    // After revert, the textarea content matches org defaults — pill flips back to "Inheriting".
    expect(await screen.findByText(/Inheriting from org defaults/i)).toBeInTheDocument();
  });

  it('opens Agent settings and Job spec tabs (renamed from role fit / activity per HANDOFF v2 §4.1)', async () => {
    renderPipeline();

    await screen.findByRole('heading', { name: /AI Native Engineer/i });

    fireEvent.click(screen.getByRole('button', { name: /^Agent settings$/i }));
    expect(await screen.findByRole('heading', { name: /Role intent/i })).toBeInTheDocument();
    expect(screen.getByText(/HOW THE AGENT RUNS THIS ROLE/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Reject threshold/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Autonomy rules/i })).toBeInTheDocument();

    // HANDOFF v2 §4.4 / canvas jobs-detail-spec — the Job spec tab renders
    // the formatted Workable-ingested description + "At a glance" sidebar.
    // The pipeline-activity timeline that previously lived under this label
    // was a leftover from the v1 5-tab layout and is gone in v2.
    fireEvent.click(screen.getByRole('button', { name: /^Job spec$/i }));
    expect(await screen.findByRole('button', { name: /Read full description/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /At a glance/i })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Pipeline activity/i })).not.toBeInTheDocument();
  });
});
