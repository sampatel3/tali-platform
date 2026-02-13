import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

// Mock the API module
vi.mock('../lib/api.js', () => ({
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
  billing: { usage: vi.fn() },
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
    update: vi.fn(),
    remove: vi.fn(),
    uploadCv: vi.fn(),
    uploadJobSpec: vi.fn(),
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

import { auth, tasks as tasksApi, assessments as assessmentsApi } from '../lib/api.js';
import App from '../App';
import { AuthProvider } from '../context/AuthContext';

const mockUser = {
  id: 1,
  email: 'admin@tali.com',
  full_name: 'Admin User',
  organization_id: 1,
  role: 'admin',
};

const mockTasks = [
  {
    id: 10,
    name: 'Async Pipeline Debugging',
    description: 'Fix 3 bugs in an async data pipeline that processes streaming JSON events.',
    task_type: 'debugging',
    difficulty: 'mid',
    duration_minutes: 45,
    is_template: false,
    starter_code: 'async function process() {}',
    test_code: 'test("works", () => {});',
  },
  {
    id: 11,
    name: 'AI Agent Integration',
    description: 'Build an AI agent that can answer questions about a codebase.',
    task_type: 'ai_engineering',
    difficulty: 'senior',
    duration_minutes: 60,
    is_template: false,
    starter_code: 'function agent() {}',
    test_code: 'test("agent", () => {});',
  },
  {
    id: 12,
    name: 'Template Task',
    description: 'A built-in template task.',
    task_type: 'optimization',
    difficulty: 'junior',
    duration_minutes: 30,
    is_template: true,
    starter_code: '',
    test_code: '',
  },
];

const setupAuthenticatedUser = () => {
  localStorage.setItem('tali_access_token', 'fake-jwt-token');
  localStorage.setItem('tali_user', JSON.stringify(mockUser));
  auth.me.mockResolvedValue({ data: mockUser });
};

const renderAppOnTasksPage = async () => {
  assessmentsApi.list.mockResolvedValue({ data: { items: [], total: 0 } });

  const result = render(
    <AuthProvider>
      <App />
    </AuthProvider>
  );

  // Wait for dashboard to render
  await waitFor(() => {
    expect(screen.getByText('Assessments', { selector: 'h1' })).toBeInTheDocument();
  });

  // Navigate to Tasks via nav
  const tasksNav = screen.getByText('Tasks', { selector: 'button' });
  await act(async () => {
    fireEvent.click(tasksNav);
  });

  return result;
};

describe('TasksPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.location.hash = '';
    setupAuthenticatedUser();
    tasksApi.list.mockResolvedValue({ data: mockTasks });
  });

  afterEach(() => {
    window.location.hash = '';
    localStorage.clear();
  });

  it('renders Tasks heading', async () => {
    await renderAppOnTasksPage();

    await waitFor(() => {
      expect(screen.getByText('Tasks', { selector: 'h1' })).toBeInTheDocument();
      expect(screen.getByText('Manage assessment task templates')).toBeInTheDocument();
    });
  });

  it('renders task list', async () => {
    await renderAppOnTasksPage();

    await waitFor(() => {
      expect(screen.getByText('Async Pipeline Debugging')).toBeInTheDocument();
      expect(screen.getByText('AI Agent Integration')).toBeInTheDocument();
      expect(screen.getByText('Template Task')).toBeInTheDocument();
    });
  });

  it('renders task descriptions', async () => {
    await renderAppOnTasksPage();

    await waitFor(() => {
      expect(screen.getByText(/Fix 3 bugs in an async data pipeline/)).toBeInTheDocument();
      expect(screen.getByText(/Build an AI agent that can answer questions/)).toBeInTheDocument();
    });
  });

  it('renders difficulty badges', async () => {
    await renderAppOnTasksPage();

    await waitFor(() => {
      expect(screen.getByText('MID')).toBeInTheDocument();
      expect(screen.getByText('SENIOR')).toBeInTheDocument();
      expect(screen.getByText('JUNIOR')).toBeInTheDocument();
    });
  });

  it('renders duration for each task', async () => {
    await renderAppOnTasksPage();

    await waitFor(() => {
      expect(screen.getByText('45min')).toBeInTheDocument();
      expect(screen.getByText('60min')).toBeInTheDocument();
      expect(screen.getByText('30min')).toBeInTheDocument();
    });
  });

  it('renders New Task button', async () => {
    await renderAppOnTasksPage();

    await waitFor(() => {
      expect(screen.getByText('New Task')).toBeInTheDocument();
    });
  });

  it('New Task button opens create modal with choose step', async () => {
    await renderAppOnTasksPage();

    await waitFor(() => {
      expect(screen.getByText('New Task')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('New Task'));

    await waitFor(() => {
      // The CreateTaskModal should render with the "choose" step first
      expect(screen.getByText('Create New Task')).toBeInTheDocument();
      expect(screen.getByText('Generate with AI')).toBeInTheDocument();
      expect(screen.getByText('Create Manually')).toBeInTheDocument();
    });
  });

  it('shows empty state when no tasks exist', async () => {
    tasksApi.list.mockResolvedValue({ data: [] });

    await renderAppOnTasksPage();

    await waitFor(() => {
      expect(screen.getByText('No tasks yet')).toBeInTheDocument();
      expect(screen.getByText('Create your first task template to start assessing candidates')).toBeInTheDocument();
    });
  });

  it('shows loading state while fetching tasks', async () => {
    tasksApi.list.mockReturnValue(new Promise(() => {}));

    await renderAppOnTasksPage();

    await waitFor(() => {
      expect(screen.getByText('Loading tasks...')).toBeInTheDocument();
    });
  });

  it('delete button calls tasksApi.delete after confirmation', async () => {
    const confirmMock = vi.spyOn(window, 'confirm').mockReturnValue(true);
    tasksApi.delete.mockResolvedValue({ data: {} });

    await renderAppOnTasksPage();

    await waitFor(() => {
      expect(screen.getByText('Async Pipeline Debugging')).toBeInTheDocument();
    });

    // Find delete buttons (only non-template tasks have them)
    // The delete button has a Trash2 icon, we find it by title
    const deleteButtons = screen.getAllByTitle('Delete task');
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalled();
      expect(tasksApi.delete).toHaveBeenCalledWith(10);
    });

    confirmMock.mockRestore();
  });

  it('shows task type badges', async () => {
    await renderAppOnTasksPage();

    await waitFor(() => {
      expect(screen.getByText('debugging')).toBeInTheDocument();
      expect(screen.getByText('ai engineering')).toBeInTheDocument();
      expect(screen.getByText('optimization')).toBeInTheDocument();
    });
  });

  it('template tasks show template label instead of edit/delete', async () => {
    await renderAppOnTasksPage();

    await waitFor(() => {
      expect(screen.getByText('template')).toBeInTheDocument();
    });
  });
});
