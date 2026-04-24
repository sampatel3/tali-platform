import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider } from '../context/AuthContext';
import { ToastProvider } from '../context/ToastContext';
import { CandidatesPage } from '../features/candidates/CandidatesPage';
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

const applications = [
  {
    id: 101,
    role_id: 1,
    role_name: 'Backend Engineer',
    candidate_id: 500,
    candidate_name: 'Taylor Lane',
    candidate_email: 'taylor@example.com',
    pipeline_stage: 'review',
    taali_score: 90,
    cv_match_score: 84,
    role_reject_threshold: 60,
    workable_sourced: true,
    workable_score_raw: 78,
    updated_at: '2026-03-05T10:00:00Z',
    valid_assessment_id: 901,
    assessment_preview: {
      category_scores: {
        prompt_quality: 9.1,
        independence: 8.5,
      },
    },
  },
  {
    id: 102,
    role_id: 2,
    role_name: 'Data Engineer',
    candidate_id: 500,
    candidate_name: 'Taylor Lane',
    candidate_email: 'taylor@example.com',
    pipeline_stage: 'applied',
    taali_score: 88,
    cv_match_score: 72,
    role_reject_threshold: 60,
    updated_at: '2026-03-04T10:00:00Z',
  },
  {
    id: 103,
    role_id: 2,
    role_name: 'Data Engineer',
    candidate_id: 501,
    candidate_name: 'Jamie Stone',
    candidate_email: 'jamie@example.com',
    pipeline_stage: 'invited',
    taali_score: 62,
    cv_match_score: 55,
    role_reject_threshold: 60,
    updated_at: '2026-03-03T10:00:00Z',
  },
];

const mockUser = {
  id: 1,
  email: 'admin@taali.ai',
  full_name: 'Admin User',
  organization_name: 'Taali',
  role: 'admin',
};

const renderPage = (onNavigate = vi.fn()) => render(
  <AuthProvider>
    <ToastProvider>
      <CandidatesPage onNavigate={onNavigate} />
    </ToastProvider>
  </AuthProvider>,
);

describe('Candidates page redesign', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('taali_user', JSON.stringify(mockUser));
    rolesApi.list.mockResolvedValue({
      data: [
        { id: 1, name: 'Backend Engineer' },
        { id: 2, name: 'Data Engineer' },
      ],
    });
    rolesApi.listApplicationsGlobal.mockResolvedValue({
      data: {
        items: applications,
        total: applications.length,
        limit: 100,
        offset: 0,
      },
    });
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('loads the redesigned candidates workspace with workable and threshold indicators', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Candidates/i })).toBeInTheDocument();
      expect(screen.getAllByText('Taylor Lane').length).toBe(2);
      expect(screen.getByText('Jamie Stone')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Below threshold · 1/i })).toBeInTheDocument();
      expect(screen.getByText(/From Workable/i)).toBeInTheDocument();
      expect(screen.getByText((_, element) => element?.textContent === 'WK 78')).toBeInTheDocument();
      expect(screen.getByText('84%')).toBeInTheDocument();
      expect(rolesApi.listApplicationsGlobal).toHaveBeenCalledWith(
        expect.objectContaining({
          application_outcome: 'open',
          limit: 100,
        }),
      );
    });
  });

  it('applies the redesigned local search and cv threshold filters', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Jamie Stone')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText('Search by name, email, or role…'), {
      target: { value: 'Jamie' },
    });

    await waitFor(() => {
      expect(screen.getByText('Jamie Stone')).toBeInTheDocument();
      expect(screen.queryAllByText('Taylor Lane')).toHaveLength(0);
    });

    fireEvent.change(screen.getByPlaceholderText('Search by name, email, or role…'), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Below threshold · 1/i }));

    await waitFor(() => {
      expect(screen.getByText('Jamie Stone')).toBeInTheDocument();
      expect(screen.queryAllByText('Taylor Lane')).toHaveLength(0);
    });

    fireEvent.click(screen.getByRole('button', { name: /Below threshold · 1/i }));
    fireEvent.change(screen.getByLabelText('CV match minimum'), {
      target: { value: '80' },
    });

    await waitFor(() => {
      expect(screen.getByText('Taylor Lane')).toBeInTheDocument();
      expect(screen.queryByText('Jamie Stone')).not.toBeInTheDocument();
    });
  });

  it('navigates into the assessment detail when an application has an attached assessment', async () => {
    const onNavigate = vi.fn();
    renderPage(onNavigate);

    await waitFor(() => {
      expect(screen.getAllByText('Taylor Lane').length).toBe(2);
    });

    fireEvent.click(screen.getAllByText('Taylor Lane')[0].closest('button'));

    expect(onNavigate).toHaveBeenCalledWith('candidate-report', {
      candidateApplicationId: 101,
    });
  });

  it('falls back to role pipelines when the global candidates feed fails', async () => {
    rolesApi.listApplicationsGlobal.mockRejectedValueOnce(new Error('boom'));
    rolesApi.listPipeline.mockImplementation((roleId) => Promise.resolve({
      data: {
        role_id: roleId,
        role_name: roleId === 1 ? 'Backend Engineer' : 'Data Engineer',
        stage_counts: { applied: 0, invited: 0, in_assessment: 0, review: 0 },
        active_candidates_count: 1,
        items: applications.filter((item) => item.role_id === roleId),
        total: applications.filter((item) => item.role_id === roleId).length,
        limit: 100,
        offset: 0,
      },
    }));

    renderPage();

    await waitFor(() => {
      expect(screen.getAllByText('Taylor Lane').length).toBe(2);
      expect(screen.getByText('Jamie Stone')).toBeInTheDocument();
    });

    expect(screen.queryByText(/Failed to load candidates/i)).not.toBeInTheDocument();
    expect(rolesApi.listPipeline).toHaveBeenCalledTimes(2);
  });
});
