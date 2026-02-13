import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import AssessmentPage from './AssessmentPage';

const mockClaude = vi.fn();
const mockClaudeRetry = vi.fn();
const mockSubmit = vi.fn();
let lastClaudeReply = '';

vi.mock('../../lib/api', () => ({
  assessments: {
    start: vi.fn(),
    execute: vi.fn(),
    claude: (...args) => mockClaude(...args),
    claudeRetry: (...args) => mockClaudeRetry(...args),
    submit: (...args) => mockSubmit(...args),
  },
}));

vi.mock('./CodeEditor', () => ({
  default: ({ initialCode }) => <div data-testid="code-editor">editor:{initialCode}</div>,
}));

vi.mock('./ClaudeChat', () => ({
  default: ({ onSendMessage, onPaste, disabled = false }) => (
    <div>
      <button type="button" onClick={() => onPaste?.()}>paste</button>
      <button
        type="button"
        disabled={disabled}
        onClick={async () => {
          lastClaudeReply = await onSendMessage('Help me debug this', []);
        }}
      >
        send-claude
      </button>
      <div data-testid="claude-disabled">{disabled ? 'true' : 'false'}</div>
      <div data-testid="claude-reply">{lastClaudeReply}</div>
    </div>
  ),
}));

describe('AssessmentPage tracking metadata', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    lastClaudeReply = '';
    mockClaude.mockResolvedValue({ data: { response: 'ok' } });
    mockClaudeRetry.mockResolvedValue({ data: { success: true, is_timer_paused: false } });
    mockSubmit.mockResolvedValue({ data: { success: true } });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('sends prompt metadata and submit tab_switch_count', async () => {
    const startData = {
      assessment_id: 10,
      token: 'tok',
      time_remaining: 1800,
      task: {
        name: 'Debug task',
        starter_code: 'print("hi")',
        duration_minutes: 30,
        proctoring_enabled: true,
      },
    };

    render(<AssessmentPage token="tok" startData={startData} />);

    await act(async () => {
      fireEvent.click(screen.getByText('paste'));
    });

    await act(async () => {
      fireEvent.click(screen.getByText('send-claude'));
    });

    await waitFor(() => expect(mockClaude).toHaveBeenCalledTimes(1));
    const claudeArgs = mockClaude.mock.calls[0];
    expect(claudeArgs[0]).toBe(10);
    expect(claudeArgs[1]).toBe('Help me debug this');
    expect(claudeArgs[3]).toBe('tok');
    expect(claudeArgs[4]).toMatchObject({
      code_context: 'print("hi")',
      paste_detected: true,
      browser_focused: true,
    });

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    });
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await screen.findByText('This has been recorded.');

    await act(async () => {
      fireEvent.click(screen.getByText('Submit'));
    });

    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(1));
    expect(mockSubmit.mock.calls[0][3]).toMatchObject({ tab_switch_count: 1 });
  });


  it('renders task and repository context when provided', async () => {
    const startData = {
      assessment_id: 12,
      token: 'tok2',
      time_remaining: 1200,
      task: {
        name: 'History Backfill',
        description: 'Backfill account history for missing rows.',
        scenario: 'A migration left historical records incomplete.',
        starter_code: 'print("start")',
        duration_minutes: 30,
        repo_structure: {
          files: {
            'src/backfill.py': 'def run():\n    return True',
            'README.md': '# task',
          },
        },
      },
    };

    render(<AssessmentPage token="tok2" startData={startData} />);

    expect(await screen.findByText('Task Context')).toBeInTheDocument();
    expect(screen.getByText(/migration left historical records incomplete/i)).toBeInTheDocument();
    expect(screen.getByText('Repository Context')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'src/backfill.py' })).toBeInTheDocument();
    expect(screen.getByText(/def run\(\):/)).toBeInTheDocument();
  });

  it('shows fallback context copy when task metadata is missing', async () => {
    const startData = {
      assessment_id: 13,
      token: 'tok3',
      time_remaining: 1200,
      task: {
        name: 'Untitled task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok3" startData={startData} />);

    expect(await screen.findByText('Task Context')).toBeInTheDocument();
    expect(screen.getByText('Task context has not been provided yet.')).toBeInTheDocument();
    expect(screen.getByText('Repository Context')).toBeInTheDocument();
    expect(screen.getByText('No repository files provided for this assessment.')).toBeInTheDocument();
  });


  it('shows rubric categories and hides criteria text', async () => {
    const startData = {
      assessment_id: 14,
      token: 'tok4',
      time_remaining: 1200,
      clone_command: 'git clone --branch assessment/14 mock://repo',
      task: {
        name: 'Rubric task',
        starter_code: 'print("start")',
        duration_minutes: 30,
        rubric_categories: [
          { category: 'exploration', weight: 0.25 },
          { category: 'implementation_quality', weight: 0.35 },
        ],
        evaluation_rubric: {
          exploration: { weight: 0.25, criteria: { excellent: 'should never render' } },
        },
      },
    };

    render(<AssessmentPage token="tok4" startData={startData} />);

    expect(await screen.findByText(/How you'll be assessed/i)).toBeInTheDocument();
    expect(screen.getByText('exploration')).toBeInTheDocument();
    expect(screen.getByText('25%')).toBeInTheDocument();
    expect(screen.queryByText(/should never render/i)).not.toBeInTheDocument();
  });

  it('allows collapsing and expanding context sections', async () => {
    const startData = {
      assessment_id: 15,
      token: 'tok5',
      time_remaining: 1200,
      task: {
        name: 'Collapsible sections',
        scenario: 'Investigate and patch the backfill job.',
        starter_code: 'print("start")',
        duration_minutes: 30,
        repo_structure: {
          files: {
            'src/job.py': 'def run_job():\n    pass',
          },
        },
      },
    };

    render(<AssessmentPage token="tok5" startData={startData} />);

    const taskToggle = await screen.findByRole('button', { name: /Task Context/i });
    const rubricToggle = screen.getByRole('button', { name: /How you'll be assessed/i });
    const repoToggle = screen.getByRole('button', { name: /Repository Context/i });

    expect(screen.getByText(/Investigate and patch the backfill job/i)).toBeInTheDocument();
    expect(screen.getByText(/Rubric categories will be shown when available/i)).toBeInTheDocument();
    expect(screen.getByText(/def run_job\(\):/i)).toBeInTheDocument();

    fireEvent.click(taskToggle);
    fireEvent.click(rubricToggle);
    fireEvent.click(repoToggle);

    expect(screen.queryByText(/Investigate and patch the backfill job/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Rubric categories will be shown when available/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/def run_job\(\):/i)).not.toBeInTheDocument();

    fireEvent.click(taskToggle);
    fireEvent.click(rubricToggle);
    fireEvent.click(repoToggle);

    expect(screen.getByText(/Investigate and patch the backfill job/i)).toBeInTheDocument();
    expect(screen.getByText(/Rubric categories will be shown when available/i)).toBeInTheDocument();
    expect(screen.getByText(/def run_job\(\):/i)).toBeInTheDocument();
  });

  it('uses canonical response field from Claude payload', async () => {
    mockClaude.mockResolvedValueOnce({
      data: {
        success: true,
        response: 'canonical response text',
        content: 'legacy content fallback',
      },
    });

    const startData = {
      assessment_id: 16,
      token: 'tok6',
      time_remaining: 1200,
      task: {
        name: 'Response contract task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok6" startData={startData} />);

    await act(async () => {
      fireEvent.click(screen.getByText('send-claude'));
    });

    await waitFor(() => {
      expect(lastClaudeReply).toBe('canonical response text');
    });
    expect(screen.getByTestId('claude-reply')).toHaveTextContent('canonical response text');
  });

  it('pauses interaction on Claude outage and resumes on retry', async () => {
    mockClaude.mockResolvedValueOnce({
      data: {
        success: false,
        response: 'Claude is temporarily unavailable. Your timer is paused. Please retry in a moment.',
        is_timer_paused: true,
        pause_reason: 'claude_outage',
      },
    });
    mockClaudeRetry.mockResolvedValueOnce({
      data: {
        success: true,
        is_timer_paused: false,
        message: 'Claude recovered and assessment resumed',
      },
    });

    const startData = {
      assessment_id: 17,
      token: 'tok7',
      time_remaining: 1200,
      task: {
        name: 'Claude outage pause task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok7" startData={startData} />);

    await act(async () => {
      fireEvent.click(screen.getByText('send-claude'));
    });

    await waitFor(() => {
      expect(screen.getByText(/Assessment paused: Claude is currently unavailable/i)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled();
    expect(screen.getByText('send-claude')).toBeDisabled();
    expect(screen.getByTestId('claude-disabled')).toHaveTextContent('true');

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Retry Claude' }));
    });

    await waitFor(() => {
      expect(screen.queryByText(/Assessment paused: Claude is currently unavailable/i)).not.toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Submit' })).not.toBeDisabled();
    expect(screen.getByText('send-claude')).not.toBeDisabled();
    expect(screen.getByTestId('claude-disabled')).toHaveTextContent('false');
  });

});
