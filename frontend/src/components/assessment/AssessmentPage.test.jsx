import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import AssessmentPage from '../../features/assessment_runtime/AssessmentPage';

const mockSubmit = vi.fn();
const mockTerminalStatus = vi.fn();
const mockTerminalStop = vi.fn();
const mockClaude = vi.fn();

vi.mock('../../shared/api', () => ({
  assessments: {
    start: vi.fn(),
    execute: vi.fn(),
    terminalStatus: (...args) => mockTerminalStatus(...args),
    terminalStop: (...args) => mockTerminalStop(...args),
    claude: (...args) => mockClaude(...args),
    terminalWsUrl: (id, token) => `ws://localhost/api/v1/assessments/${id}/terminal/ws?token=${token}`,
    submit: (...args) => mockSubmit(...args),
  },
}));

vi.mock('./CodeEditor', () => ({
  default: ({ initialCode }) => <div data-testid="code-editor">editor:{initialCode}</div>,
}));

vi.mock('../../features/assessment_runtime/AssessmentTerminal', () => ({
  AssessmentTerminal: ({ statusText }) => (
    <div data-testid="assessment-terminal">
      terminal:{statusText}
    </div>
  ),
}));

describe('AssessmentPage tracking metadata', () => {
  const originalWebSocket = global.WebSocket;

  beforeEach(() => {
    vi.clearAllMocks();
    mockTerminalStatus.mockResolvedValue({ data: { state: 'running' } });
    mockTerminalStop.mockResolvedValue({ data: { success: true } });
    mockSubmit.mockResolvedValue({ data: { success: true } });
    mockClaude.mockResolvedValue({ data: { success: true, content: 'ok' } });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  afterEach(() => {
    global.WebSocket = originalWebSocket;
  });

  it('sends submit tab_switch_count metadata', async () => {
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


  it('renders task context and repository tree when provided', async () => {
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
    expect(await screen.findByRole('button', { name: /Repository/i })).toBeInTheDocument();
    expect(screen.getByText('src/')).toBeInTheDocument();
    expect(screen.getByText('backfill.py')).toBeInTheDocument();
    expect(screen.getByText('README.md')).toBeInTheDocument();
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
    expect(screen.queryByRole('button', { name: /Repository/i })).not.toBeInTheDocument();
  });


  it('shows instructions and clone command in context window', async () => {
    const startData = {
      assessment_id: 14,
      token: 'tok4',
      time_remaining: 1200,
      ai_mode: 'claude_cli_terminal',
      clone_command: 'git clone --branch assessment/14 mock://repo',
      task: {
        name: 'Instruction task',
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

    expect(await screen.findByRole('button', { name: /Instructions/i })).toBeInTheDocument();
    expect(screen.getByText(/Use the Ask Claude box for Cursor-style help with repo context/i)).toBeInTheDocument();
    expect(screen.getByText(/Workspace clone command/i)).toBeInTheDocument();
    expect(screen.queryByText('exploration')).not.toBeInTheDocument();
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
    const instructionsToggle = screen.getByRole('button', { name: /Instructions/i });

    expect(screen.getByText(/Investigate and patch the backfill job/i)).toBeInTheDocument();
    expect(screen.getByText(/Read the task context and inspect repository files before editing/i)).toBeInTheDocument();

    fireEvent.click(taskToggle);
    fireEvent.click(instructionsToggle);

    expect(screen.queryByText(/Investigate and patch the backfill job/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Read the task context and inspect repository files before editing/i)).not.toBeInTheDocument();

    fireEvent.click(taskToggle);
    fireEvent.click(instructionsToggle);

    expect(screen.getByText(/Investigate and patch the backfill job/i)).toBeInTheDocument();
    expect(screen.getByText(/Read the task context and inspect repository files before editing/i)).toBeInTheDocument();
  });

  it('renders terminal toggle in Claude CLI mode and initializes websocket', async () => {
    const sentMessages = [];
    class MockWebSocket {
      static OPEN = 1;

      constructor() {
        this.readyState = MockWebSocket.OPEN;
        setTimeout(() => {
          this.onopen?.();
        }, 0);
      }

      send(data) {
        sentMessages.push(JSON.parse(data));
      }

      close() {
        this.readyState = 3;
        this.onclose?.();
      }
    }
    global.WebSocket = MockWebSocket;

    const startData = {
      assessment_id: 21,
      token: 'tok-terminal',
      time_remaining: 1200,
      ai_mode: 'claude_cli_terminal',
      terminal_mode: true,
      terminal_capabilities: {
        permission_mode: 'default',
      },
      task: {
        name: 'CLI mode task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok-terminal" startData={startData} />);

    expect(await screen.findByRole('button', { name: /Show Terminal/i })).toBeInTheDocument();
    expect(screen.queryByTestId('assessment-terminal')).not.toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Show Terminal/i }));
    });
    expect(await screen.findByTestId('assessment-terminal')).toBeInTheDocument();
    expect(screen.queryByText('send-claude')).not.toBeInTheDocument();

    await waitFor(() => {
      expect(sentMessages.some((message) => message.type === 'init')).toBe(true);
    });
  });

  it('initializes terminal websocket in demo mode when Claude CLI mode is enabled', async () => {
    const sentMessages = [];
    class MockWebSocket {
      static OPEN = 1;

      constructor() {
        this.readyState = MockWebSocket.OPEN;
        setTimeout(() => {
          this.onopen?.();
        }, 0);
      }

      send(data) {
        sentMessages.push(JSON.parse(data));
      }

      close() {
        this.readyState = 3;
        this.onclose?.();
      }
    }
    global.WebSocket = MockWebSocket;

    const startData = {
      assessment_id: 22,
      token: 'tok-terminal-demo',
      time_remaining: 1200,
      ai_mode: 'claude_cli_terminal',
      terminal_mode: true,
      terminal_capabilities: {
        permission_mode: 'default',
      },
      task: {
        name: 'Demo CLI mode task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok-terminal-demo" startData={startData} demoMode />);

    expect(await screen.findByRole('button', { name: /Show Terminal/i })).toBeInTheDocument();
    expect(screen.queryByTestId('assessment-terminal')).not.toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Show Terminal/i }));
    });
    expect(await screen.findByTestId('assessment-terminal')).toBeInTheDocument();
    expect(screen.queryByText('send-claude')).not.toBeInTheDocument();

    await waitFor(() => {
      expect(sentMessages.some((message) => message.type === 'init')).toBe(true);
    });
  });

  it('shows budget exhausted alert when Claude budget is depleted', async () => {
    const startData = {
      assessment_id: 19,
      token: 'tok9',
      time_remaining: 1200,
      claude_budget: {
        enabled: true,
        limit_usd: 5,
        used_usd: 5,
        remaining_usd: 0,
        tokens_used: 5000,
        is_exhausted: true,
      },
      task: {
        name: 'Exhausted budget task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok9" startData={startData} />);

    expect(await screen.findByText(/Claude budget exhausted for this task/i)).toBeInTheDocument();
  });

  it('updates Claude credit display after prompt response', async () => {
    const startData = {
      assessment_id: 23,
      token: 'tok-claude-budget',
      time_remaining: 1200,
      claude_budget: {
        enabled: true,
        limit_usd: 1,
        used_usd: 0,
        remaining_usd: 1,
        tokens_used: 0,
        is_exhausted: false,
      },
      task: {
        name: 'Budget update task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    mockClaude.mockResolvedValueOnce({
      data: {
        success: true,
        content: 'Use stronger filtering.',
        claude_budget: {
          enabled: true,
          limit_usd: 1,
          used_usd: 0.25,
          remaining_usd: 0.75,
          tokens_used: 1000,
          is_exhausted: false,
        },
      },
    });

    render(<AssessmentPage token="tok-claude-budget" startData={startData} />);

    expect(await screen.findByText(/Claude Credit: \$1.00 left of \$1.00/i)).toBeInTheDocument();

    const promptInput = screen.getByPlaceholderText(/Ask Claude \(Cursor-style\)/i);
    fireEvent.change(promptInput, { target: { value: 'Help me debug' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Ask Claude/i }));
    });

    await waitFor(() => expect(mockClaude).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Claude Credit: \$0.75 left of \$1.00/i)).toBeInTheDocument();
  });

});
