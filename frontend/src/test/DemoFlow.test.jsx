import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DemoExperiencePage } from '../features/demo/DemoExperiencePage';
import { LandingPage } from '../features/marketing/LandingPage';
import { assessments } from '../shared/api';

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

const renderLanding = (onNavigate = vi.fn()) => render(<LandingPage onNavigate={onNavigate} />);
const renderDemo = (onNavigate = vi.fn()) => render(<DemoExperiencePage onNavigate={onNavigate} />);

describe('Demo flow redesign', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    window.scrollTo = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    assessments.startDemo.mockResolvedValue({
      data: {
        assessment_id: 321,
        token: 'demo-token',
      },
    });
  });

  it('navigates from the marketing nav to the redesigned demo page', async () => {
    const onNavigate = vi.fn();
    renderLanding(onNavigate);

    fireEvent.click(screen.getByRole('button', { name: 'Demo' }));

    expect(onNavigate).toHaveBeenCalledWith('demo');
  });

  it('keeps the theme toggle button in both landing and demo navigation', async () => {
    const { unmount } = renderLanding();
    expect(screen.getByRole('button', { name: 'Toggle theme' })).toBeInTheDocument();

    unmount();
    renderDemo();
    expect(screen.getByRole('button', { name: 'Toggle theme' })).toBeInTheDocument();
  });

  it('shows the redesigned intake validation before a callback can be requested', async () => {
    renderDemo();

    fireEvent.click(screen.getByRole('button', { name: /See the showcase/i }));

    await waitFor(() => {
      expect(screen.getByText(/Please complete:/i)).toBeInTheDocument();
      expect(assessments.startDemo).not.toHaveBeenCalled();
    });
  });

  it('queues the landing section when using the demo nav section links', async () => {
    const onNavigate = vi.fn();
    renderDemo(onNavigate);

    fireEvent.click(screen.getByRole('button', { name: 'How it works' }));

    expect(onNavigate).toHaveBeenCalledWith('landing');
    expect(sessionStorage.getItem('taali.pendingMarketingSection')).toBe('how-it-works');
  });

  it('submits the selected assessment track and shows the product showcase state', async () => {
    renderDemo();

    fireEvent.change(screen.getByLabelText(/^Full name$/i), { target: { value: 'Jane Doe' } });
    fireEvent.change(screen.getByLabelText(/^Position$/i), { target: { value: 'Engineering Manager' } });
    fireEvent.change(screen.getByLabelText(/^Email$/i), { target: { value: 'jane@email.com' } });
    fireEvent.change(screen.getByLabelText(/^Work email$/i), { target: { value: 'jane@company.com' } });
    fireEvent.change(screen.getByLabelText(/^Company$/i), { target: { value: 'Acme' } });
    fireEvent.change(screen.getByLabelText(/^Company size$/i), { target: { value: '51-200' } });

    fireEvent.click(screen.getByRole('button', { name: /GenAI Production Readiness Review/i }));
    fireEvent.click(screen.getByRole('button', { name: /See the showcase/i }));

    await waitFor(() => {
      expect(assessments.startDemo).toHaveBeenCalledWith(
        expect.objectContaining({
          assessment_track: 'ai_eng_genai_production_readiness',
          full_name: 'Jane Doe',
          company_size: '51-200',
        }),
      );
    });

    expect(await screen.findByRole('heading', { level: 1, name: /Here's the product flow/i })).toBeInTheDocument();
    expect(screen.getAllByText(/GenAI Production Readiness Review/).length).toBeGreaterThan(0);
    expect(
      screen.getByText((_, element) => element?.textContent === 'Priya Anand - where she stands in the pipeline.')
    ).toBeInTheDocument();
  });
});
