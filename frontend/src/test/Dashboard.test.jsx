import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { AuthProvider } from '../context/AuthContext';
import {
  assessments as assessmentsApi,
  auth,
  roles as rolesApi,
} from '../shared/api';

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
    downloadDocument: vi.fn(),
  },
  roles: {
    list: vi.fn().mockResolvedValue({ data: [] }),
    get: vi.fn(),
    getApplication: vi.fn(),
    listApplicationsGlobal: vi.fn().mockResolvedValue({
      data: { items: [], total: 0, limit: 100, offset: 0 },
    }),
    listPipeline: vi.fn().mockResolvedValue({
      data: {
        role_id: 0,
        role_name: '',
        stage: 'all',
        stage_counts: { applied: 0, invited: 0, in_assessment: 0, review: 0 },
        active_candidates_count: 0,
        items: [],
        total: 0,
        limit: 200,
        offset: 0,
      },
    }),
    listApplicationEvents: vi.fn().mockResolvedValue({ data: [] }),
    listTasks: vi.fn().mockResolvedValue({ data: [] }),
    updateApplicationStage: vi.fn(),
    downloadApplicationReport: vi.fn(),
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
  RadarChart: ({ children }) => <div data-testid="radar-chart">{children}</div>,
  PolarGrid: () => <div />,
  PolarAngleAxis: () => <div />,
  PolarRadiusAxis: () => <div />,
  Radar: () => <div />,
  CartesianGrid: () => <div />,
  LineChart: ({ children }) => <div>{children}</div>,
  Line: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  Tooltip: () => <div />,
}));

const mockUser = {
  id: 1,
  email: 'admin@taali.ai',
  full_name: 'Admin User',
  organization_name: 'Taali',
  role: 'admin',
};

const renderApp = () => render(
  <AuthProvider>
    <App />
  </AuthProvider>,
);

const seedAuthenticatedUser = () => {
  localStorage.setItem('taali_access_token', 'fake-jwt-token');
  localStorage.setItem('taali_user', JSON.stringify(mockUser));
  auth.me.mockResolvedValue({ data: mockUser });
};

describe('Recruiter route smoke tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    seedAuthenticatedUser();

    rolesApi.list.mockResolvedValue({
      data: [{ id: 101, name: 'Backend Engineer', stage_counts: { applied: 1 } }],
    });
  });

  afterEach(() => {
    localStorage.clear();
    window.history.pushState({}, '', '/');
  });

  it('redirects /dashboard to the redesigned jobs dashboard', async () => {
    window.history.pushState({}, '', '/dashboard');
    renderApp();

    await waitFor(() => {
      expect(window.location.pathname).toBe('/jobs');
      expect(screen.getByRole('heading', { name: /Jobs/i })).toBeInTheDocument();
    });
  });

  it('redirects /assessments to the redesigned jobs dashboard', async () => {
    window.history.pushState({}, '', '/assessments');
    renderApp();

    await waitFor(() => {
      expect(window.location.pathname).toBe('/jobs');
      expect(screen.getByRole('heading', { name: /Jobs/i })).toBeInTheDocument();
    });
  });

  it('renders the redesigned recruiter nav tabs', async () => {
    window.history.pushState({}, '', '/jobs');
    renderApp();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Jobs' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Candidates' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Tasks' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Reporting' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument();
    });
  });

  it('loads the redesigned candidates workspace from the canonical route', async () => {
    window.history.pushState({}, '', '/candidates');
    renderApp();

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1, name: /Candidates/i })).toBeInTheDocument();
      expect(rolesApi.listApplicationsGlobal).toHaveBeenCalledWith(
        expect.objectContaining({
          application_outcome: 'open',
          sort_by: 'pipeline_stage_updated_at',
          sort_order: 'desc',
        }),
      );
    });
  });

  it('keeps the /assessments/:assessmentId deep-link working for the redesigned detail page', async () => {
    assessmentsApi.get.mockResolvedValue({
      data: {
        id: 42,
        candidate_name: 'Legacy Candidate',
        candidate_email: 'legacy@example.com',
        status: 'completed',
        task_name: 'Legacy Task',
        final_score: 82,
        prompt_quality_score: 7.8,
        error_recovery_score: 8.2,
        independence_score: 7.1,
        context_utilization_score: 7.5,
        design_thinking_score: 8.0,
        started_at: '2026-03-05T09:00:00Z',
        completed_at: '2026-03-05T09:45:00Z',
        timeline: [],
        results: [],
        prompts_list: [],
      },
    });

    window.history.pushState({}, '', '/assessments/42');
    renderApp();

    await waitFor(() => {
      expect(assessmentsApi.get).toHaveBeenCalledWith(42);
      expect(window.location.pathname).toBe('/assessments/42');
      expect(screen.getByText('legacy@example.com')).toBeInTheDocument();
    });
  });
});
