import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

// Mock the API module
vi.mock('../shared/api', () => ({
  auth: {
    login: vi.fn(),
    register: vi.fn(),
    me: vi.fn(),
    verifyEmail: vi.fn(),
    resendVerification: vi.fn(),
    forgotPassword: vi.fn(),
    resetPassword: vi.fn(),
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
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    generate: vi.fn(),
  },
  candidates: {
    list: vi.fn().mockResolvedValue({ data: { items: [] } }),
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
    listApplicationsGlobal: vi.fn().mockResolvedValue({ data: { items: [], total: 0, limit: 50, offset: 0 } }),
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

// Mock recharts
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

// Mock monaco editor
vi.mock('@monaco-editor/react', () => ({
  default: () => <div data-testid="code-editor" />,
}));

import {
  auth,
  assessments as assessmentsApi,
  analytics as analyticsApi,
  tasks as tasksApi,
  roles as rolesApi,
  organizations as organizationsApi,
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

const mockAssessments = [
  {
    id: 1,
    candidate_name: 'Alice Johnson',
    candidate_email: 'alice@example.com',
    role_name: 'Backend Engineer',
    task: { name: 'Async Debugging' },
    task_name: 'Async Debugging',
    status: 'completed',
    score: 8.5,
    overall_score: 8.5,
    duration_taken: 2700,
    completed_at: '2026-01-15T10:00:00Z',
    token: 'tok-alice',
    prompt_count: 5,
    prompts_list: [],
    timeline: [],
    results: [],
    breakdown: null,
  },
  {
    id: 2,
    candidate_name: 'Bob Smith',
    candidate_email: 'bob@example.com',
    role_name: 'ML Engineer',
    task: { name: 'API Integration' },
    task_name: 'API Integration',
    status: 'in_progress',
    score: null,
    overall_score: null,
    duration_taken: null,
    completed_at: null,
    token: 'tok-bob',
    prompt_count: 0,
    prompts_list: [],
    timeline: [],
    results: [],
    breakdown: null,
  },
  {
    id: 3,
    candidate_name: 'Carol White',
    candidate_email: 'carol@example.com',
    role_name: 'Backend Engineer',
    task: { name: 'Data Pipeline' },
    task_name: 'Data Pipeline',
    status: 'completed',
    score: 6.0,
    overall_score: 6.0,
    duration_taken: 1800,
    completed_at: '2026-01-20T14:00:00Z',
    token: 'tok-carol',
    prompt_count: 12,
    prompts_list: [],
    timeline: [],
    results: [],
    breakdown: null,
  },
];

const mockTasks = [
  { id: 10, name: 'Async Debugging', task_type: 'debugging', difficulty: 'mid', duration_minutes: 45 },
  { id: 11, name: 'API Integration', task_type: 'ai_engineering', difficulty: 'senior', duration_minutes: 60 },
];

const mockRoles = [
  { id: 101, name: 'Backend Engineer' },
  { id: 102, name: 'ML Engineer' },
];

const setupAuthenticatedUser = () => {
  localStorage.setItem('taali_access_token', 'fake-jwt-token');
  localStorage.setItem('taali_user', JSON.stringify(mockUser));
  auth.me.mockResolvedValue({ data: mockUser });
};

const renderApp = () => {
  return render(
    <AuthProvider>
      <App />
    </AuthProvider>
  );
};

describe('AssessmentsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.location.hash = '';
    window.history.pushState({}, '', '/');
    setupAuthenticatedUser();
    organizationsApi.get.mockResolvedValue({ data: { id: 1, name: 'Acme Labs' } });
    tasksApi.list.mockResolvedValue({ data: mockTasks });
    rolesApi.list.mockResolvedValue({ data: mockRoles });
  });

  afterEach(() => {
    window.location.hash = '';
    window.history.pushState({}, '', '/');
    localStorage.clear();
  });

  it('renders loading state while fetching assessments', async () => {
    assessmentsApi.list.mockReturnValue(new Promise(() => {}));
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('Assessment Inbox')).toBeInTheDocument();
    });
  });

  it('routes authenticated users to jobs hub when workflow v2 is enabled', async () => {
    organizationsApi.get.mockResolvedValue({
      data: { id: 1, name: 'Acme Labs', recruiter_workflow_v2_enabled: true },
    });
    rolesApi.list.mockResolvedValue({
      data: [
        {
          id: 101,
          name: 'Backend Engineer',
          stage_counts: { applied: 2, invited: 1, in_assessment: 0, review: 0 },
          active_candidates_count: 3,
        },
      ],
    });
    renderApp();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Jobs' })).toBeInTheDocument();
    });
    expect(screen.queryByRole('heading', { name: 'Assessments' })).not.toBeInTheDocument();
  });

  it('opens the global candidates directory under workflow v2', async () => {
    organizationsApi.get.mockResolvedValue({
      data: { id: 1, name: 'Acme Labs', recruiter_workflow_v2_enabled: true },
    });
    rolesApi.list.mockResolvedValue({
      data: [{ id: 101, name: 'Backend Engineer' }],
    });
    rolesApi.listApplicationsGlobal.mockResolvedValue({
      data: {
        role_id: 101,
        role_name: 'Backend Engineer',
        stage: 'all',
        stage_counts: { applied: 0, invited: 1, in_assessment: 0, review: 0 },
        active_candidates_count: 1,
        items: [
          {
            id: 501,
            role_id: 101,
            role_name: 'Backend Engineer',
            candidate_name: 'Alice Johnson',
            candidate_email: 'alice@example.com',
            pipeline_stage: 'applied',
            application_outcome: 'open',
            taali_score: 84.5,
            version: 1,
            created_at: '2026-01-15T10:00:00Z',
            pipeline_stage_updated_at: '2026-01-15T10:00:00Z',
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      },
    });
    rolesApi.getApplication.mockResolvedValue({
      data: {
        id: 501,
        role_id: 101,
        role_name: 'Backend Engineer',
        candidate_name: 'Alice Johnson',
        candidate_email: 'alice@example.com',
        pipeline_stage: 'applied',
        application_outcome: 'open',
        taali_score: 84.5,
        version: 1,
        created_at: '2026-01-15T10:00:00Z',
        pipeline_stage_updated_at: '2026-01-15T10:00:00Z',
      },
    });

    renderApp();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Jobs' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /^Candidates$/ }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Candidates' })).toBeInTheDocument();
      expect(screen.getByText('Global candidate directory across all roles and stages.')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(rolesApi.listApplicationsGlobal).toHaveBeenCalledWith(
        expect.objectContaining({ application_outcome: 'open' })
      );
    });
  });

  it('opens role-scoped pipeline view for /jobs/:roleId under workflow v2', async () => {
    window.history.pushState({}, '', '/jobs/101');
    organizationsApi.get.mockResolvedValue({
      data: { id: 1, name: 'Acme Labs', recruiter_workflow_v2_enabled: true },
    });
    rolesApi.list.mockResolvedValue({
      data: [{ id: 101, name: 'Backend Engineer' }],
    });
    rolesApi.listPipeline.mockResolvedValue({
      data: {
        role_id: 101,
        role_name: 'Backend Engineer',
        stage: 'all',
        stage_counts: { applied: 0, invited: 1, in_assessment: 0, review: 0 },
        active_candidates_count: 1,
        items: [
          {
            id: 501,
            role_id: 101,
            role_name: 'Backend Engineer',
            candidate_name: 'Alice Johnson',
            candidate_email: 'alice@example.com',
            pipeline_stage: 'invited',
            application_outcome: 'open',
            taali_score: 84.5,
            version: 2,
            created_at: '2026-01-15T10:00:00Z',
            pipeline_stage_updated_at: '2026-01-15T10:00:00Z',
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      },
    });
    rolesApi.getApplication.mockResolvedValue({
      data: {
        id: 501,
        role_id: 101,
        role_name: 'Backend Engineer',
        candidate_name: 'Alice Johnson',
        candidate_email: 'alice@example.com',
        pipeline_stage: 'invited',
        application_outcome: 'open',
        taali_score: 84.5,
        version: 2,
        created_at: '2026-01-15T10:00:00Z',
        pipeline_stage_updated_at: '2026-01-15T10:00:00Z',
      },
    });

    renderApp();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Backend Engineer pipeline' })).toBeInTheDocument();
    });
    expect(screen.getByDisplayValue('Backend Engineer')).toBeDisabled();
    await waitFor(() => {
      const hasRoleScopedCall = rolesApi.listPipeline.mock.calls.some(
        ([roleId]) => Number(roleId) === 101
      );
      expect(hasRoleScopedCall).toBe(true);
    });
  });

  it('renders dashboard heading with user name', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('Assessments', { selector: 'h1' })).toBeInTheDocument();
      expect(screen.getByText(/Welcome back, Admin\./)).toBeInTheDocument();
    });
  });

  it('shows signed-in user and organization in the top-right nav', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('Admin User')).toBeInTheDocument();
      expect(screen.getByText('Acme Labs')).toBeInTheDocument();
    });
  });

  it('renders assessment list when data loads', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('Alice Johnson')).toBeInTheDocument();
      expect(screen.getByText('Bob Smith')).toBeInTheDocument();
      expect(screen.getByText('Carol White')).toBeInTheDocument();
    });
  });

  it('shows empty state when no assessments', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: [], total: 0 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText(/No assessments yet/)).toBeInTheDocument();
    });
  });

  it('renders stats cards', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getAllByText('Invited').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('In Progress').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('Completed Awaiting Review')).toBeInTheDocument();
      expect(screen.getByText('Expiring Soon')).toBeInTheDocument();
    });
  });

  it('shows correct assessment count in stats', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getAllByText('1').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('2').length).toBeGreaterThanOrEqual(1);
    });
  });

  it('renders status filter dropdown', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('All statuses')).toBeInTheDocument();
    });
  });

  it('filter by status re-fetches assessments', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('Filters')).toBeInTheDocument();
    });

    const statusSelect = screen.getByDisplayValue('All statuses');
    fireEvent.change(statusSelect, { target: { value: 'completed' } });

    await waitFor(() => {
      // Should have been called again with status filter
      expect(assessmentsApi.list).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'completed' })
      );
    });
  });

  it('renders role names in the assessment inbox table', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getAllByText('Backend Engineer').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('ML Engineer').length).toBeGreaterThanOrEqual(1);
    });
  });

  it('role filter re-fetches assessments with role_id', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('Filters')).toBeInTheDocument();
    });

    const roleSelect = screen.getByDisplayValue('All roles');
    fireEvent.change(roleSelect, { target: { value: '101' } });

    await waitFor(() => {
      expect(assessmentsApi.list).toHaveBeenCalledWith(
        expect.objectContaining({ role_id: '101' })
      );
    });
  });

  it('loads candidate detail from URL deep-link by assessment id', async () => {
    window.history.pushState({}, '', '/candidate-detail?assessmentId=1');
    assessmentsApi.get.mockResolvedValue({
      data: {
        ...mockAssessments[0],
        final_score: 85,
        role_name: 'Backend Engineer',
        application_status: 'applied',
      },
    });
    renderApp();

    await waitFor(() => {
      expect(window.location.pathname).toBe('/assessments/1');
      expect(assessmentsApi.get).toHaveBeenCalledWith(1);
    });

    await waitFor(() => {
      expect(screen.getByText('Alice Johnson')).toBeInTheDocument();
      expect(screen.getByText('Role: Backend Engineer')).toBeInTheDocument();
      expect(screen.getByText('Application: applied')).toBeInTheDocument();
    });
  });

  it('redirects /dashboard to /assessments', async () => {
    window.history.pushState({}, '', '/dashboard');
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(window.location.pathname).toBe('/assessments');
      expect(screen.getByRole('heading', { name: 'Assessments' })).toBeInTheDocument();
    });
  });

  it('redirects /analytics to /reporting', async () => {
    window.history.pushState({}, '', '/analytics');
    renderApp();

    await waitFor(() => {
      expect(window.location.pathname).toBe('/reporting');
      expect(analyticsApi.get).toHaveBeenCalled();
      expect(screen.getByRole('heading', { name: 'Reporting' })).toBeInTheDocument();
    });
  });

  it('filter by task re-fetches assessments', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('All tasks')).toBeInTheDocument();
    });

    const taskSelect = screen.getByDisplayValue('All tasks');
    fireEvent.change(taskSelect, { target: { value: '10' } });

    await waitFor(() => {
      expect(assessmentsApi.list).toHaveBeenCalledWith(
        expect.objectContaining({ task_id: '10' })
      );
    });
  });

  it('does not render New Assessment button and points to Candidates page', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: [], total: 0 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Assessments' })).toBeInTheDocument();
    });

    expect(screen.queryByText('New Assessment')).not.toBeInTheDocument();
    expect(screen.getByText('No assessments yet. Create an assessment from the Candidates page.')).toBeInTheDocument();
  });


  

  

  

  it('shows candidate scores in the table', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getAllByText('85.0').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('60.0').length).toBeGreaterThanOrEqual(1);
    });
  });

  it('renders status badges for completed and in-progress', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      const completedBadges = screen.getAllByText('Completed');
      expect(completedBadges.length).toBeGreaterThanOrEqual(1);
      const inProgressLabels = screen.getAllByText('In Progress');
      expect(inProgressLabels.length).toBeGreaterThanOrEqual(1);
    });
  });

  it('does not render export buttons on the assessments inbox', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.queryByText('Export CSV')).not.toBeInTheDocument();
      expect(screen.queryByText('Export JSON')).not.toBeInTheDocument();
    });
  });

  it('renders View results button for completed assessments', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      const viewButtons = screen.getAllByText('View results');
      expect(viewButtons.length).toBeGreaterThanOrEqual(1);
    });
  });

  it('renders In Progress button for in-progress assessments', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getAllByText('In Progress').length).toBeGreaterThanOrEqual(1);
    });
  });

  it('does not render legacy recent notifications panel', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.queryByText('Recent Notifications')).not.toBeInTheDocument();
    });
  });

  it('renders table headers correctly', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getAllByText('Candidate').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Role').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Task').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Status').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('TAALI Score').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Assessment Score').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Sent').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Completed').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Actions').length).toBeGreaterThanOrEqual(1);
    });
  });

  it('renders Copy link button for pending assessments with tokens', async () => {
    assessmentsApi.list.mockResolvedValue({
      data: {
        items: [
          ...mockAssessments,
          {
            id: 4,
            candidate_name: 'Dana Pending',
            candidate_email: 'dana@example.com',
            role_name: 'Backend Engineer',
            task: { name: 'Async Debugging' },
            task_name: 'Async Debugging',
            status: 'pending',
            score: null,
            token: 'tok-dana',
          },
        ],
        total: 4,
      },
    });
    renderApp();

    await waitFor(() => {
      const copyButtons = screen.getAllByText('Copy link');
      expect(copyButtons.length).toBeGreaterThanOrEqual(1);
    });
  });
});
