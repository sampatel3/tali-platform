import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
  organizations: { get: vi.fn(), update: vi.fn() },
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
    list: vi.fn().mockResolvedValue({ data: [] }),
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
    listApplicationsGlobal: vi.fn(),
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

import { auth, roles as rolesApi } from '../shared/api';
import App from '../App';
import { AuthProvider } from '../context/AuthContext';

const mockUser = {
  id: 1,
  email: 'admin@taali.ai',
  full_name: 'Admin User',
  organization_id: 1,
  role: 'admin',
};

const applicationItems = [
  {
    id: 101,
    role_id: 1,
    role_name: 'Backend Engineer',
    candidate_id: 500,
    candidate_name: 'Taylor Lane',
    candidate_email: 'taylor@example.com',
    candidate_position: 'Senior Engineer',
    status: 'applied',
    pipeline_stage: 'applied',
    application_outcome: 'open',
    taali_score: 90,
    version: 2,
    pipeline_stage_updated_at: '2026-03-05T10:00:00Z',
    updated_at: '2026-03-05T10:00:00Z',
    created_at: '2026-03-05T09:00:00Z',
  },
  {
    id: 102,
    role_id: 2,
    role_name: 'Data Engineer',
    candidate_id: 500,
    candidate_name: 'Taylor Lane',
    candidate_email: 'taylor@example.com',
    candidate_position: 'Senior Engineer',
    status: 'review',
    pipeline_stage: 'review',
    application_outcome: 'open',
    taali_score: 88,
    version: 1,
    pipeline_stage_updated_at: '2026-03-04T10:00:00Z',
    updated_at: '2026-03-04T10:00:00Z',
    created_at: '2026-03-04T09:00:00Z',
  },
];

const setupAuthenticatedUser = () => {
  localStorage.setItem('taali_access_token', 'fake-jwt-token');
  localStorage.setItem('taali_user', JSON.stringify(mockUser));
  auth.me.mockResolvedValue({ data: mockUser });
};

const renderOnCandidatesPage = () => {
  window.history.pushState({}, '', '/candidates');
  return render(
    <AuthProvider>
      <App />
    </AuthProvider>
  );
};

describe('Candidates Directory V2', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    setupAuthenticatedUser();

    rolesApi.list.mockResolvedValue({
      data: [
        { id: 1, name: 'Backend Engineer' },
        { id: 2, name: 'Data Engineer' },
      ],
    });

    rolesApi.listApplicationsGlobal.mockImplementation(async (params = {}) => {
      const limit = Number(params.limit || 50);
      const offset = Number(params.offset || 0);
      return {
        data: {
          items: applicationItems.slice(offset, offset + limit),
          total: applicationItems.length,
          limit,
          offset,
        },
      };
    });
  });

  afterEach(() => {
    localStorage.clear();
    window.history.pushState({}, '', '/');
  });

  it('loads V2 candidates directory with direct global applications API', async () => {
    renderOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Candidates' })).toBeInTheDocument();
      expect(rolesApi.listApplicationsGlobal).toHaveBeenCalled();
    });

    const calledWithDefaultOpenOutcome = rolesApi.listApplicationsGlobal.mock.calls.some(([params]) => (
      params && params.application_outcome === 'open'
    ));
    expect(calledWithDefaultOpenOutcome).toBe(true);
  });

  it('applies TAALI sort and min score filters through canonical query params', async () => {
    renderOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Candidates' })).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText('Sort'), {
      target: { value: 'taali_score:asc' },
    });
    fireEvent.change(screen.getByLabelText('Min TAALI'), {
      target: { value: '90' },
    });

    await waitFor(() => {
      const hasFilteredCall = rolesApi.listApplicationsGlobal.mock.calls.some(([params]) => (
        params
        && params.sort_by === 'taali_score'
        && params.sort_order === 'asc'
        && Number(params.min_taali_score) === 90
      ));
      expect(hasFilteredCall).toBe(true);
    });
  });

  it('renders TAALI score ring and multi-role application badge for shared candidates', async () => {
    renderOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getAllByRole('img', { name: /TAALI score/i }).length).toBeGreaterThan(0);
      expect(screen.getAllByText('2 role applications').length).toBeGreaterThan(0);
    });
  });
});
