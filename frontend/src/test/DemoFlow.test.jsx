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
    requestDemo: vi.fn(),
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
    assessments.requestDemo.mockResolvedValue({ data: { success: true, candidate_id: 321 } });
  });

  it('routes marketing nav entry points across the landing page instead of to demo', async () => {
    const onNavigate = vi.fn();
    renderLanding(onNavigate);

    fireEvent.click(screen.getByRole('button', { name: 'Platform' }));

    expect(onNavigate).not.toHaveBeenCalled();
  });

  it('renders the restored candidate workspace and how-it-works sections on landing', async () => {
    renderLanding();

    expect(screen.getByRole('heading', { name: /Hire engineers who can\s*ship\s*with AI\./i })).toBeInTheDocument();
    expect(screen.getByText(/Six-axis AI-collaboration scoring — now live/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Try the walkthrough/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /See a sample report/i })).toBeInTheDocument();
    expect(screen.getByText(/Strong hire - recommend on-site/i)).toBeInTheDocument();
    expect(screen.getByText(/Maya Chen/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /What your\s*candidate\s*actually sees\./i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /From job requirement\s*to confident\s*hire\./i })).toBeInTheDocument();
    expect(screen.getByText(/Start from the job requirement\./i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'How it works' })).toBeInTheDocument();
  });

  it('keeps the theme toggle button in both landing and demo navigation', async () => {
    const { unmount } = renderLanding();
    expect(screen.getByRole('button', { name: 'Toggle theme' })).toBeInTheDocument();

    unmount();
    renderDemo();
    expect(screen.getByRole('button', { name: 'Toggle theme' })).toBeInTheDocument();
  });

  it('shows intake validation before a demo request can be saved', async () => {
    renderDemo();

    fireEvent.click(screen.getByRole('button', { name: /Open walkthrough/i }));

    await waitFor(() => {
      expect(screen.getByText(/Please complete:/i)).toBeInTheDocument();
      expect(assessments.requestDemo).not.toHaveBeenCalled();
    });
  });

  it('uses shared nav buttons on the demo page to jump back to landing sections', async () => {
    const onNavigate = vi.fn();
    renderDemo(onNavigate);

    fireEvent.click(screen.getByRole('button', { name: 'Platform' }));

    expect(onNavigate).toHaveBeenCalledWith('landing');
    expect(sessionStorage.getItem('taali.pendingMarketingSection')).toBe('platform');
  });

  it('gates the walkthrough on /demo until details are submitted', async () => {
    renderDemo();

    expect(screen.getByRole('heading', { level: 1, name: /See Taali/i })).toBeInTheDocument();
    expect(screen.getByText(/No fake provisioning/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 2, name: /What candidates and hiring teams/i })).not.toBeInTheDocument();
    expect(screen.queryByTitle(/^What candidates see$/i)).not.toBeInTheDocument();
    expect(screen.queryByTitle(/^What hiring teams see$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Demo' })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^Track/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Start my session/i })).not.toBeInTheDocument();
  });

  it('saves the demo request and unlocks the actual product walkthrough', async () => {
    const onNavigate = vi.fn();
    renderDemo(onNavigate);

    fireEvent.change(screen.getByLabelText(/^Full name$/i), { target: { value: 'Jane Doe' } });
    fireEvent.change(screen.getByLabelText(/^Position$/i), { target: { value: 'Engineering Manager' } });
    fireEvent.change(screen.getByLabelText(/^Work email$/i), { target: { value: 'jane@company.com' } });
    fireEvent.change(screen.getByLabelText(/^Company$/i), { target: { value: 'Acme' } });
    fireEvent.change(screen.getByLabelText(/^Company size$/i), { target: { value: '51–200' } });

    fireEvent.click(screen.getByRole('button', { name: /Open walkthrough/i }));

    await waitFor(() => {
      expect(assessments.requestDemo).toHaveBeenCalledWith(
        expect.objectContaining({
          full_name: 'Jane Doe',
          email: 'jane@company.com',
          work_email: 'jane@company.com',
          company_name: 'Acme',
          company_size: '51–200',
        }),
      );
    });

    expect(screen.getByRole('heading', { level: 2, name: /What candidates and hiring teams/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /What candidates see/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /What hiring teams see/i })).toBeInTheDocument();
    const candidateFrame = screen.getByTitle('What candidates see');
    const reportFrame = screen.getByTitle('What hiring teams see');
    expect(candidateFrame).toBeInTheDocument();
    expect(candidateFrame).toHaveAttribute('src', '/assessment/live?demo=1&showcase=1');
    expect(candidateFrame).toHaveAttribute('sandbox', 'allow-scripts allow-same-origin');
    expect(reportFrame).toHaveAttribute('src', '/c/demo?view=interview&k=demo-token&showcase=1');
    expect(reportFrame).toHaveAttribute('sandbox', 'allow-scripts allow-same-origin');
    expect(screen.getAllByText('Locked preview')).toHaveLength(2);
    expect(screen.queryByText(/Open full size/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Platform' })).not.toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalledWith('candidate-welcome', expect.anything());
  });
});
