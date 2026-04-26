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
    update: vi.fn(),
    regenerateInterviewFocus: vi.fn(),
  },
  tasks: {
    list: vi.fn(),
  },
}));

vi.mock('../candidates/CandidateSheet', () => ({
  CandidateSheet: () => null,
}));

vi.mock('../candidates/RoleSheet', () => ({
  RoleSheet: () => null,
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

const renderPipeline = () => render(
  <MemoryRouter initialEntries={['/jobs/101']}>
    <Routes>
      <Route path="/jobs/:roleId" element={<JobPipelinePage onNavigate={vi.fn()} />} />
    </Routes>
  </MemoryRouter>
);

describe('JobPipelinePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiClient.roles.get.mockResolvedValue({ data: baseRole });
    apiClient.roles.listTasks.mockResolvedValue({ data: [] });
    apiClient.roles.listApplications.mockResolvedValue({ data: baseApplications });
    apiClient.roles.batchScoreStatus.mockResolvedValue({ data: { status: 'idle', total: 0, scored: 0, errors: 0 } });
    apiClient.roles.fetchCvsStatus.mockResolvedValue({ data: { status: 'idle', total: 0, fetched: 0, errors: 0 } });
    apiClient.tasks.list.mockResolvedValue({ data: [] });
  });

  it('does not treat an unset reject threshold as 0 percent', async () => {
    renderPipeline();

    await screen.findByRole('heading', { name: /Reject threshold/i, level: 3 });

    expect(screen.getByText(/the saved threshold/i)).toBeInTheDocument();
    expect(screen.queryByText(/below 0%/i)).not.toBeInTheDocument();
    expect(screen.getByRole('slider', { name: /Reject threshold/i })).toBeInTheDocument();
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
  });

  it('shows stage-aware card signals instead of pre-screen scores in early stages', async () => {
    renderPipeline();

    const appliedCard = (await screen.findByText('Sam Patel')).closest('button');
    const reviewCard = (await screen.findByText('Priya Anand')).closest('button');

    expect(appliedCard).toBeTruthy();
    expect(reviewCard).toBeTruthy();

    expect(within(appliedCard).queryByText('91')).not.toBeInTheDocument();
    expect(within(appliedCard).getByText('—')).toBeInTheDocument();

    await waitFor(() => {
      expect(within(reviewCard).getByText('64')).toBeInTheDocument();
    });
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

    expect(screen.getByText('Dubai, United Arab Emirates')).toBeInTheDocument();
    expect(screen.queryByText(/\*\*Location:\*\*/)).not.toBeInTheDocument();
    expect(container.querySelector('.role-desc-summary')).toHaveTextContent(/DeepLight AI is a specialist AI and data consultancy/i);
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

  it('saves recruiter criteria from the explicit edit flow', async () => {
    apiClient.roles.update.mockResolvedValue({ data: { ...baseRole, additional_requirements: 'Payments experience' } });

    renderPipeline();

    await screen.findByRole('heading', { name: /Scoring criteria/i, level: 3 });
    fireEvent.click(screen.getByRole('button', { name: /Edit criteria/i }));

    fireEvent.change(screen.getByPlaceholderText(/Add recruiter-specific requirements/i), {
      target: { value: 'Payments experience\nStakeholder governance' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save criteria/i }));

    await waitFor(() => {
      expect(apiClient.roles.update).toHaveBeenCalledWith(101, expect.objectContaining({
        additional_requirements: 'Payments experience\nStakeholder governance',
      }));
    });
  });

  it('opens role fit and activity as real candidate subviews', async () => {
    renderPipeline();

    await screen.findByRole('heading', { name: /AI Native Engineer/i });

    fireEvent.click(screen.getByRole('button', { name: /^Role fit$/i }));
    expect(await screen.findByRole('heading', { name: /Role fit/i })).toBeInTheDocument();
    expect(screen.getByText(/CV match sorted against this role/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Activity$/i }));
    expect(await screen.findByRole('heading', { name: /Pipeline activity/i })).toBeInTheDocument();
    expect(screen.getByText(/Recent candidate movement/i)).toBeInTheDocument();
  });
});
