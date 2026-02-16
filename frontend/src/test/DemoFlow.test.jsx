import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { AuthProvider } from '../context/AuthContext';
import { assessments, auth } from '../shared/api';

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
    list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
    get: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    resend: vi.fn(),
    downloadReport: vi.fn(),
    addNote: vi.fn(),
    uploadCv: vi.fn(),
    postToWorkable: vi.fn(),
    startDemo: vi.fn().mockResolvedValue({
      data: {
        assessment_id: 321,
        token: 'demo-token',
        sandbox_id: 'sandbox-demo',
        task: {
          name: 'Demo task',
          description: 'Demo description',
          duration_minutes: 30,
          starter_code: "print('hello')",
          repo_structure: { files: { 'main.py': "print('hello')" } },
          rubric_categories: [],
          proctoring_enabled: false,
        },
        claude_budget: { enabled: false },
        time_remaining: 1800,
        is_timer_paused: false,
        pause_reason: null,
        total_paused_seconds: 0,
      },
    }),
    start: vi.fn(),
    execute: vi.fn(),
    terminalStatus: vi.fn(),
    terminalStop: vi.fn().mockResolvedValue({ data: { success: true } }),
    terminalWsUrl: vi.fn().mockReturnValue('ws://localhost/api/v1/assessments/321/terminal/ws?token=demo-token'),
    claude: vi.fn(),
    claudeRetry: vi.fn(),
    submit: vi.fn(),
  },
  billing: { usage: vi.fn(), costs: vi.fn(), credits: vi.fn(), createCheckoutSession: vi.fn() },
  organizations: { get: vi.fn(), update: vi.fn() },
  analytics: { get: vi.fn().mockResolvedValue({ data: {} }) },
  tasks: { list: vi.fn().mockResolvedValue({ data: [] }), get: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(), generate: vi.fn() },
  candidates: { list: vi.fn().mockResolvedValue({ data: { items: [] } }), get: vi.fn(), create: vi.fn(), createWithCv: vi.fn(), update: vi.fn(), remove: vi.fn(), uploadCv: vi.fn(), uploadJobSpec: vi.fn() },
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
}));

vi.mock('@monaco-editor/react', () => ({
  default: () => <div data-testid="code-editor" />,
}));

const renderApp = () => render(
  <AuthProvider>
    <App />
  </AuthProvider>
);

describe('Demo flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.history.replaceState(null, '', '/');
    auth.me.mockRejectedValue(new Error('Not authenticated'));
    assessments.startDemo.mockResolvedValue({
      data: {
        assessment_id: 321,
        token: 'demo-token',
        sandbox_id: 'sandbox-demo',
        task: {
          name: 'Demo task',
          description: 'Demo description',
          duration_minutes: 30,
          starter_code: "print('hello')",
          repo_structure: { files: { 'main.py': "print('hello')" } },
          rubric_categories: [],
          proctoring_enabled: false,
        },
        claude_budget: { enabled: false },
        time_remaining: 1800,
        is_timer_paused: false,
        pause_reason: null,
        total_paused_seconds: 0,
      },
    });
    assessments.submit.mockResolvedValue({
      data: {
        score: 7.4,
        prompt_scores: {
          prompt_clarity: 7.0,
          prompt_efficiency: 6.8,
          context_utilization: 7.2,
          written_communication: 6.9,
          requirement_comprehension: 7.1,
          design_thinking: 7.3,
          independence: 6.7,
        },
        component_scores: {
          tests_score: 72,
          time_efficiency: 68,
        },
      },
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    window.scrollTo = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it('navigates to /demo from landing nav', async () => {
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: 'Demo' }));

    expect(
      await screen.findByText('Try a candidate assessment', {}, { timeout: 5000 })
    ).toBeInTheDocument();
  });

  it('requires credential fields before starting demo', async () => {
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: 'Demo' }));
    await screen.findByText('Try a candidate assessment', {}, { timeout: 5000 });

    fireEvent.click(screen.getByRole('button', { name: 'Start demo assessment' }));

    await waitFor(() => {
      expect(screen.getByText(/Please complete:/i)).toBeInTheDocument();
    });
  });

  it('shows demo summary after submit', async () => {
    renderApp();
    fireEvent.click(screen.getByRole('button', { name: 'Demo' }));
    await screen.findByText('Try a candidate assessment', {}, { timeout: 5000 });

    fireEvent.change(screen.getByLabelText('Full name'), { target: { value: 'Jane Doe' } });
    fireEvent.change(screen.getByLabelText('Position'), { target: { value: 'Engineering Manager' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'jane@email.com' } });
    fireEvent.change(screen.getByLabelText('Work email'), { target: { value: 'jane@company.com' } });
    fireEvent.change(screen.getByLabelText('Company'), { target: { value: 'Acme' } });
    fireEvent.change(screen.getByLabelText('Company size'), { target: { value: '51-200' } });

    fireEvent.click(screen.getByRole('button', { name: 'Start demo assessment' }));

    const submitButton = await screen.findByRole('button', { name: 'Submit' });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByText('TAALI PROFILE')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Join TAALI' })).toBeInTheDocument();
      expect(screen.getByText('Compared with successful candidates')).toBeInTheDocument();
      expect(screen.getByTestId('radar-chart')).toBeInTheDocument();
    });
  });
});
