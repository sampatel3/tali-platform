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
    startDemo: vi.fn(),
    start: vi.fn(),
    execute: vi.fn(),
    terminalStatus: vi.fn(),
    terminalStop: vi.fn().mockResolvedValue({ data: { success: true } }),
    terminalWsUrl: vi.fn().mockReturnValue('ws://localhost/api/v1/assessments/321/terminal/ws?token=demo-token'),
    claude: vi.fn(),
    claudeRetry: vi.fn(),
    submit: vi.fn().mockResolvedValue({ data: { id: 321, status: 'completed' } }),
  },
  billing: { usage: vi.fn(), costs: vi.fn(), credits: vi.fn(), createCheckoutSession: vi.fn() },
  organizations: { get: vi.fn() },
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

vi.mock('@monaco-editor/react', () => ({
  default: () => <div data-testid="code-editor" />,
}));

const renderApp = () => render(
  <AuthProvider>
    <App />
  </AuthProvider>,
);

describe('Demo flow redesign', () => {
  const expectDemoHero = async () => {
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Try a candidate/i);
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/assessment/i);
    }, { timeout: 5000 });
  };

  const openDemoPage = async () => {
    fireEvent.click(await screen.findByRole('button', { name: 'Demo' }));
    await expectDemoHero();
  };

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.history.replaceState(null, '', '/');
    window.scrollTo = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    auth.me.mockRejectedValue(new Error('Not authenticated'));
    assessments.startDemo.mockResolvedValue({
      data: {
        assessment_id: 321,
        token: 'demo-token',
        task: {
          name: 'Demo task',
          description: 'Demo description',
          scenario: 'Recover a production workflow after a failed deploy.',
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
  });

  it('navigates from the marketing nav to the redesigned demo page', async () => {
    renderApp();

    await openDemoPage();
    expect(screen.getByText(/Complete this short intake/i)).toBeInTheDocument();
  });

  it('keeps the theme toggle button in both landing and demo navigation', async () => {
    renderApp();

    expect(await screen.findByRole('button', { name: 'Toggle theme' })).toBeInTheDocument();
    await openDemoPage();
    expect(screen.getByRole('button', { name: 'Toggle theme' })).toBeInTheDocument();
  });

  it('shows the redesigned intake validation before a demo can start', async () => {
    renderApp();

    await openDemoPage();

    fireEvent.click(screen.getByRole('button', { name: /Start demo assessment/i }));

    await waitFor(() => {
      expect(screen.getByText(/Please complete:/i)).toBeInTheDocument();
      expect(assessments.startDemo).not.toHaveBeenCalled();
    });
  });

  it('starts the selected demo track and opens the assessment runtime', async () => {
    renderApp();

    await openDemoPage();

    fireEvent.change(screen.getByLabelText(/^Full name$/i), { target: { value: 'Jane Doe' } });
    fireEvent.change(screen.getByLabelText(/^Position$/i), { target: { value: 'Engineering Manager' } });
    fireEvent.change(screen.getByLabelText(/^Email$/i), { target: { value: 'jane@email.com' } });
    fireEvent.change(screen.getByLabelText(/^Work email$/i), { target: { value: 'jane@company.com' } });
    fireEvent.change(screen.getByLabelText(/^Company$/i), { target: { value: 'Acme' } });
    fireEvent.change(screen.getByLabelText(/^Company size$/i), { target: { value: '51-200' } });

    fireEvent.click(screen.getByRole('button', { name: /GenAI Production Readiness Review/i }));
    fireEvent.click(screen.getByRole('button', { name: /Start demo assessment/i }));

    await waitFor(() => {
      expect(assessments.startDemo).toHaveBeenCalledWith(
        expect.objectContaining({
          assessment_track: 'ai_eng_genai_production_readiness',
          full_name: 'Jane Doe',
          company_size: '51-200',
        }),
      );
    });

    expect(await screen.findByText('Live assessment context')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument();
    expect(screen.getByTestId('code-editor')).toBeInTheDocument();
  });
});
