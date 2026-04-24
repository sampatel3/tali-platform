import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider } from '../context/AuthContext';
import { JobPipelinePage } from '../features/jobs/JobPipelinePage';
import { roles as rolesApi } from '../shared/api';

vi.mock('../shared/api', () => ({
  auth: {
    login: vi.fn(),
    register: vi.fn(),
    me: vi.fn(),
    verifyEmail: vi.fn(),
    resendVerification: vi.fn(),
    forgotPassword: vi.fn(),
    resetPassword: vi.fn(),
    ssoCheck: vi.fn(),
  },
  assessments: {
    list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
  },
  billing: { usage: vi.fn(), costs: vi.fn(), credits: vi.fn(), createCheckoutSession: vi.fn() },
  organizations: { get: vi.fn() },
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
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    createWithCv: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    uploadCv: vi.fn(),
    uploadJobSpec: vi.fn(),
  },
  roles: {
    list: vi.fn(),
    get: vi.fn(),
    getApplication: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    uploadJobSpec: vi.fn(),
    listTasks: vi.fn().mockResolvedValue({ data: [] }),
    addTask: vi.fn(),
    removeTask: vi.fn(),
    listApplications: vi.fn().mockResolvedValue({ data: [] }),
    listApplicationsGlobal: vi.fn(),
    listPipeline: vi.fn(),
    createApplication: vi.fn(),
    updateApplication: vi.fn(),
    uploadApplicationCv: vi.fn(),
    createAssessment: vi.fn(),
    retakeAssessment: vi.fn(),
    listApplicationEvents: vi.fn().mockResolvedValue({ data: [] }),
  },
  team: { list: vi.fn(), invite: vi.fn() },
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

const mockUser = {
  id: 1,
  email: 'admin@taali.ai',
  full_name: 'Admin User',
  organization_name: 'Taali',
  role: 'admin',
};

const renderPage = (onNavigate = vi.fn()) => render(
  <AuthProvider>
    <MemoryRouter initialEntries={['/jobs/101']}>
      <Routes>
        <Route path="/jobs/:roleId" element={<JobPipelinePage onNavigate={onNavigate} />} />
      </Routes>
    </MemoryRouter>
  </AuthProvider>,
);

describe('Job pipeline redesign', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('taali_user', JSON.stringify(mockUser));

    rolesApi.get.mockResolvedValue({
      data: {
        id: 101,
        name: 'Payment Transformation and Compliance AI Project Manager',
        location: 'Abu Dhabi',
        description: `# Payment Transformation and Compliance AI Project Manager
**Location:** Abu Dhabi, United Arab Emirates
**Department:** DeepLight
**Employment type:** Full-time

Lead enterprise AI delivery across banking transformation workstreams and translate ambiguous delivery risk into a safe execution plan.

Own stakeholder communication, rollout governance, and audit-ready decision making.`,
        interview_focus: {
          questions: [
            {
              question: 'How did you decide when Claude was wrong?',
              what_to_listen_for: 'Specific moments where they verified the model output before changing release logic.',
              concerning_signals: 'They defer to the AI without explaining how they validated the claim.',
            },
          ],
        },
      },
    });

    rolesApi.listPipeline.mockResolvedValue({
      data: {
        role_id: 101,
        role_name: 'Payment Transformation and Compliance AI Project Manager',
        stage_counts: { applied: 0, invited: 0, in_assessment: 0, review: 1 },
        active_candidates_count: 1,
        items: [
          {
            id: 77,
            candidate_name: 'Priya Anand',
            candidate_email: 'priya@example.com',
            pipeline_stage: 'review',
            taali_score: 92,
            updated_at: '2026-04-24T10:00:00Z',
            valid_assessment_id: 901,
          },
        ],
        total: 1,
        limit: 200,
        offset: 0,
      },
    });
  });

  it('renders structured interview focus safely and opens the standing report', async () => {
    const onNavigate = vi.fn();
    renderPage(onNavigate);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Payment Transformation and Compliance AI Project Manager/i })).toBeInTheDocument();
    });

    expect(screen.getAllByText(/Location: Abu Dhabi, United Arab Emirates/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/How did you decide when Claude was wrong/i)).toBeInTheDocument();
    expect(screen.getByText(/Specific moments where they verified the model output/i)).toBeInTheDocument();
    expect(screen.getByText(/They defer to the AI without explaining/i)).toBeInTheDocument();

    fireEvent.click(screen.getByText('Priya Anand').closest('button'));

    expect(onNavigate).toHaveBeenCalledWith('candidate-report', {
      candidateApplicationId: 77,
    });
  });
});
