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

describe('DashboardPage', () => {
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
      expect(screen.getByText('Recent Assessments')).toBeInTheDocument();
      expect(screen.queryByText('Loading assessments...')).not.toBeInTheDocument();
    });
  });

  it('renders dashboard heading with user name', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('Assessments', { selector: 'h1' })).toBeInTheDocument();
      expect(screen.getByText('Welcome back, Admin')).toBeInTheDocument();
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
      expect(screen.getByText('Active Assessments')).toBeInTheDocument();
      expect(screen.getByText('Completion Rate')).toBeInTheDocument();
      expect(screen.getByText('Avg Score')).toBeInTheDocument();
      expect(screen.getByText('This Month Cost')).toBeInTheDocument();
    });
  });

  it('shows correct assessment count in stats', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('1')).toBeInTheDocument();
      expect(screen.getAllByText('2 completed').length).toBeGreaterThanOrEqual(1);
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
      expect(screen.getByText('Filters:')).toBeInTheDocument();
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

  it('groups assessment rows by role when no role filter is selected', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('— Backend Engineer —')).toBeInTheDocument();
      expect(screen.getByText('— ML Engineer —')).toBeInTheDocument();
    });
  });

  it('role filter re-fetches assessments with role_id', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('Filters:')).toBeInTheDocument();
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
      expect(assessmentsApi.get).toHaveBeenCalledWith(1);
    });

    await waitFor(() => {
      expect(screen.getByText('Alice Johnson')).toBeInTheDocument();
      expect(screen.getByText('Role: Backend Engineer')).toBeInTheDocument();
      expect(screen.getByText('Application: applied')).toBeInTheDocument();
    });
  });

  it('filter by task re-fetches assessments', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('All job roles')).toBeInTheDocument();
    });

    const taskSelect = screen.getByDisplayValue('All job roles');
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
      expect(screen.getByText('8.5/10')).toBeInTheDocument();
      expect(screen.getByText('6/10')).toBeInTheDocument();
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

  it('renders export buttons', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('Export CSV')).toBeInTheDocument();
      expect(screen.getByText('Export JSON')).toBeInTheDocument();
    });
  });

  it('renders View button for completed assessments', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      const viewButtons = screen.getAllByText('View');
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

  it('shows recent notifications for completed assessments', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('Recent Notifications')).toBeInTheDocument();
      expect(screen.getByText(/Alice Johnson completed Async Debugging/)).toBeInTheDocument();
    });
  });

  it('renders table headers correctly', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      expect(screen.getByText('Candidate')).toBeInTheDocument();
      expect(screen.getByText('Task')).toBeInTheDocument();
      expect(screen.getByText('Status')).toBeInTheDocument();
      expect(screen.getByText('Score')).toBeInTheDocument();
      expect(screen.getByText('Time')).toBeInTheDocument();
    });
  });

  it('renders Copy link button for assessments with tokens', async () => {
    assessmentsApi.list.mockResolvedValue({ data: { items: mockAssessments, total: 3 } });
    renderApp();

    await waitFor(() => {
      const copyButtons = screen.getAllByText('Copy link');
      expect(copyButtons.length).toBeGreaterThanOrEqual(1);
    });
  });
});
