import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider } from '../context/AuthContext';
import { ToastProvider } from '../context/ToastContext';
import { TasksPage } from '../features/tasks/TasksPage';
import { tasks as tasksApi } from '../shared/api';

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
    list: vi.fn(),
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

const mockTasks = [
  {
    id: 10,
    name: 'Async Pipeline Debugging',
    description: 'Fix three bugs in an async data pipeline.',
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
      <TasksPage onNavigate={onNavigate} />
    </ToastProvider>
  </AuthProvider>,
);

describe('Tasks page redesign', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('taali_user', JSON.stringify(mockUser));
    tasksApi.list.mockResolvedValue({ data: mockTasks });
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('renders the redesigned task library with grouped tasks and summary stats', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /What needs you, today/i })).toBeInTheDocument();
      expect(screen.getAllByText('Async Pipeline Debugging').length).toBeGreaterThan(0);
      expect(screen.getAllByText('AI Agent Integration').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Template Task').length).toBeGreaterThan(0);
      expect(screen.getByText('● Custom tasks')).toBeInTheDocument();
      expect(screen.getByText('● Template tasks')).toBeInTheDocument();
    });
  });

  it('shows the redesigned empty state when no tasks are available', async () => {
    tasksApi.list.mockResolvedValue({ data: [] });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('No tasks available', { selector: 'h2' })).toBeInTheDocument();
      expect(screen.getByText('Create your first task to start evaluating candidates.')).toBeInTheDocument();
    });
  });

  it('opens the task overview modal from the redesigned task table', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getAllByText('Async Pipeline Debugging').length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByRole('button', { name: 'View' })[0]);

    await waitFor(() => {
      expect(screen.getByText('Task Overview')).toBeInTheDocument();
      expect(screen.getByDisplayValue('Async Pipeline Debugging')).toBeInTheDocument();
    });
  });
});
