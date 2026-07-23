import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import { CandidateWelcomePage } from './CandidateWelcomePage';
import { recoverCandidateRuntimeToken } from '../../shared/assessment/candidateProofBinding';

const mockPreview = vi.fn();
const mockStart = vi.fn();

vi.mock('../../shared/api', () => ({
  assessments: {
    preview: (...args) => mockPreview(...args),
    start: (...args) => mockStart(...args),
  },
}));

vi.mock('../../shared/ui/Branding', () => ({
  Logo: () => <div>TAALI</div>,
  BrandLabel: ({ children }) => <div>{children}</div>,
  TaaliTile: () => <span aria-hidden="true" />,
  TaaliLines: () => <span aria-hidden="true" />,
  TaaliRoundel: () => <span aria-hidden="true" />,
}));

describe('CandidateWelcomePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    window.localStorage.clear();
    mockPreview.mockResolvedValue({
      data: {
        assessment_id: 12,
        token: 'candidate-token',
        duration_minutes: 30,
        start_gate: {
          can_start: false,
          reason: 'insufficient_credits',
          message: 'This assessment is not available yet. Please contact the hiring team to continue.',
        },
        task: {
          name: 'Debug task',
          role: 'Backend Engineer',
          duration_minutes: 30,
          calibration_enabled: false,
          has_cv_on_file: false,
        },
      },
    });
    mockStart.mockResolvedValue({ data: {} });
  });

  it('shows the candidate-safe credit blocker and disables start', async () => {
    render(
      <CandidateWelcomePage
        token="candidate-token"
        onNavigate={vi.fn()}
        onStarted={vi.fn()}
      />,
    );

    expect(await screen.findByText(/Please contact the hiring team to continue/i)).toBeInTheDocument();

    const startButton = screen.getByRole('button', { name: /Assessment unavailable/i });
    expect(startButton).toBeDisabled();

    await waitFor(() => expect(mockStart).not.toHaveBeenCalled());
  });

  it('binds a live start to the browser session without putting the key in navigation', async () => {
    mockPreview.mockResolvedValueOnce({
      data: {
        assessment_id: 13,
        duration_minutes: 30,
        start_gate: { can_start: true },
        task: { name: 'Live task', duration_minutes: 30 },
      },
    });
    mockStart.mockResolvedValueOnce({ data: { assessment_id: 13 } });
    const onNavigate = vi.fn();
    const onStarted = vi.fn();

    render(
      <CandidateWelcomePage
        token="candidate-token"
        onNavigate={onNavigate}
        onStarted={onStarted}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Start assessment' }));

    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1));
    expect(mockStart).toHaveBeenCalledWith('candidate-token', {
      candidate_session_key: expect.stringMatching(/^[A-Za-z0-9_-]{32,}$/),
    });
    expect(onStarted).toHaveBeenCalledWith({ assessment_id: 13, token: 'candidate-token' });
    expect(onNavigate).toHaveBeenCalledWith('assessment', {
      assessmentToken: null,
      replace: true,
    });
    expect(JSON.stringify({ ...window.localStorage })).not.toContain('candidate-token');
  });

  it('ignores a late start response after the welcome token changes', async () => {
    let resolveTokenAStart;
    mockPreview.mockImplementation((token) => Promise.resolve({
      data: {
        assessment_id: token === 'token-a' ? 21 : 22,
        candidate_name: token === 'token-a' ? 'Alpha Candidate' : 'Beta Candidate',
        duration_minutes: 30,
        start_gate: { can_start: true },
        task: { name: `${token} task`, duration_minutes: 30 },
      },
    }));
    mockStart.mockImplementationOnce(() => new Promise((resolve) => {
      resolveTokenAStart = resolve;
    }));
    const onNavigate = vi.fn();
    const onStarted = vi.fn();
    const view = render(
      <CandidateWelcomePage token="token-a" onNavigate={onNavigate} onStarted={onStarted} />,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Start assessment' }));
    await waitFor(() => expect(mockStart).toHaveBeenCalledWith('token-a', {
      candidate_session_key: expect.stringMatching(/^[A-Za-z0-9_-]{32,}$/),
    }));

    view.rerender(
      <CandidateWelcomePage token="token-b" onNavigate={onNavigate} onStarted={onStarted} />,
    );
    expect(await screen.findByText(/Hi Beta/i)).toBeInTheDocument();

    await act(async () => {
      resolveTokenAStart({ data: { assessment_id: 21 } });
    });

    expect(onStarted).not.toHaveBeenCalled();
    expect(onNavigate).not.toHaveBeenCalled();
    expect(recoverCandidateRuntimeToken()).toBeNull();
  });

  it('confirms an approved clipboard accommodation before start', async () => {
    mockPreview.mockResolvedValueOnce({
      data: {
        assessment_id: 14,
        duration_minutes: 30,
        allow_external_clipboard: true,
        start_gate: { can_start: true },
        task: { name: 'Accessible task', duration_minutes: 30 },
      },
    });

    render(<CandidateWelcomePage token="candidate-token" onNavigate={vi.fn()} onStarted={vi.fn()} />);

    expect(await screen.findByText(/approved clipboard accommodation is active/i)).toBeInTheDocument();
    expect(screen.queryByText(/contact support@taali\.ai/i)).not.toBeInTheDocument();
  });

  it('discloses the advisory workspace signals before the candidate starts', async () => {
    mockPreview.mockResolvedValueOnce({
      data: {
        assessment_id: 15,
        duration_minutes: 30,
        start_gate: { can_start: true },
        task: { name: 'Live task', duration_minutes: 30 },
      },
    });

    render(<CandidateWelcomePage token="candidate-token" onNavigate={vi.fn()} onStarted={vi.fn()} />);

    const disclosure = await screen.findByTestId('welcome-recording-disclosure');
    expect(disclosure).toHaveTextContent(/We record your work in this session/i);
    // The gap this disclosure closes: the workspace logs these with proctoring
    // off, so the consent screen has to say so — briefly, and with the reason.
    expect(disclosure).toHaveTextContent(/keep the assessment fair/i);
    expect(disclosure).toHaveTextContent(/when the tab loses focus/i);
    expect(disclosure).toHaveTextContent(/not the content of what you type or copy/i);
    expect(disclosure).toHaveTextContent(/do not record your screen, camera, or microphone/i);
  });

  it('drops the workspace-signal disclosure when the layer is accommodated off', async () => {
    mockPreview.mockResolvedValueOnce({
      data: {
        assessment_id: 16,
        duration_minutes: 30,
        allow_external_clipboard: true,
        start_gate: { can_start: true },
        task: { name: 'Accessible task', duration_minutes: 30 },
      },
    });

    render(<CandidateWelcomePage token="candidate-token" onNavigate={vi.fn()} onStarted={vi.fn()} />);

    const disclosure = await screen.findByTestId('welcome-recording-disclosure');
    expect(disclosure).toHaveTextContent(/We record your work in this session/i);
    expect(disclosure).not.toHaveTextContent(/when the tab loses focus/i);
  });
});
