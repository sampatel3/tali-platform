import { render, screen, waitFor } from '@testing-library/react';
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
    list: vi.fn(),
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
    listApplicationsGlobal: vi.fn().mockResolvedValue({
      data: { items: [], total: 0, limit: 50, offset: 0 },
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
        limit: 50,
        offset: 0,
      },
    }),
    getApplication: vi.fn(),
    listApplicationEvents: vi.fn().mockResolvedValue({ data: [] }),
    listTasks: vi.fn().mockResolvedValue({ data: [] }),
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
  Legend: () => <div />,
  BarChart: () => <div />,
  Bar: () => <div />,
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

import {
  auth,
  assessments as assessmentsApi,
  roles as rolesApi,
} from '../shared/api';
import App from '../App';
import { AuthProvider } from '../context/AuthContext';

const mockUser = {
  id: 1,
  email: 'admin@taali.ai',
  full_name: 'Admin User',
  organization_id: 1,
  role: 'admin',
};

const renderApp = () => render(
  <AuthProvider>
    <App />
  </AuthProvider>
);

const setupAuthenticatedUser = () => {
  localStorage.setItem('taali_access_token', 'fake-jwt-token');
  localStorage.setItem('taali_user', JSON.stringify(mockUser));
  auth.me.mockResolvedValue({ data: mockUser });
};

describe('Recruiter V2 Hard Cutover Routes', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    setupAuthenticatedUser();
    rolesApi.list.mockResolvedValue({
      data: [
        {
          id: 101,
          name: 'Backend Engineer',
          stage_counts: { applied: 1, invited: 0, in_assessment: 0, review: 0 },
          active_candidates_count: 1,
        },
      ],
    });
    rolesApi.listApplicationsGlobal.mockResolvedValue({
      data: {
        items: [],
        total: 0,
        limit: 50,
        offset: 0,
      },
    });
  });

  afterEach(() => {
    localStorage.clear();
    window.history.pushState({}, '', '/');
  });

  it('redirects /dashboard to /jobs', async () => {
    window.history.pushState({}, '', '/dashboard');
    renderApp();

    await waitFor(() => {
      expect(window.location.pathname).toBe('/jobs');
      expect(screen.getByRole('heading', { name: 'Jobs' })).toBeInTheDocument();
    });
  });

  it('redirects /assessments to /jobs', async () => {
    window.history.pushState({}, '', '/assessments');
    renderApp();

    await waitFor(() => {
      expect(window.location.pathname).toBe('/jobs');
      expect(screen.getByRole('heading', { name: 'Jobs' })).toBeInTheDocument();
    });
  });

  it('renders V2 nav only (no legacy Assessments/Tasks entries)', async () => {
    window.history.pushState({}, '', '/jobs');
    renderApp();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Jobs' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Candidates' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Reporting' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Assessments' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Tasks' })).not.toBeInTheDocument();
    });
  });

  it('uses V2 candidates directory route directly', async () => {
    window.history.pushState({}, '', '/candidates');
    renderApp();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Candidates' })).toBeInTheDocument();
      expect(rolesApi.listApplicationsGlobal).toHaveBeenCalled();
    });
  });

  it('keeps /assessments/:assessmentId compatibility deep-link route', async () => {
    window.history.pushState({}, '', '/assessments/42');
    assessmentsApi.get.mockResolvedValue({
      data: {
        id: 42,
        candidate_name: 'Legacy Candidate',
        candidate_email: 'legacy@example.com',
        status: 'completed',
        task_name: 'Legacy Task',
        timeline: [],
        results: [],
        prompts_list: [],
      },
    });

    renderApp();

    await waitFor(() => {
      expect(assessmentsApi.get).toHaveBeenCalledWith(42);
      expect(window.location.pathname).toBe('/assessments/42');
    });
  });
});
