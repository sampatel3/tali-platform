import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import AssessmentPage from '../../features/assessment_runtime/AssessmentPage';

const mockExecute = vi.fn();
const mockSubmit = vi.fn();
const mockTerminalStatus = vi.fn();
const mockTerminalStop = vi.fn();
const mockClaude = vi.fn();
const mockSaveRepoFile = vi.fn();

vi.mock('../../shared/api', () => ({
  assessments: {
    start: vi.fn(),
    execute: (...args) => mockExecute(...args),
    saveRepoFile: (...args) => mockSaveRepoFile(...args),
    terminalStatus: (...args) => mockTerminalStatus(...args),
    terminalStop: (...args) => mockTerminalStop(...args),
    claude: (...args) => mockClaude(...args),
    terminalWsUrl: (id, token) => `ws://localhost/api/v1/assessments/${id}/terminal/ws?token=${token}`,
    submit: (...args) => mockSubmit(...args),
  },
}));

vi.mock('../../components/assessment/CodeEditor', () => ({
  default: ({ initialCode, value, onExecute, onSave }) => {
    const code = value ?? initialCode;
    return (
      <div data-testid="code-editor">
        <div>editor:{code}</div>
        <button type="button" onClick={() => onExecute?.(code)}>Run</button>
        <button type="button" onClick={() => onSave?.(code)}>Save</button>
      </div>
    );
  },
}));

vi.mock('../../features/assessment_runtime/AssessmentTerminal', () => ({
  AssessmentTerminal: ({ statusText }) => (
    <div data-testid="assessment-terminal">
      terminal:{statusText}
    </div>
  ),
}));

const createMockWebSocketClass = (sentMessages = []) => class MockWebSocket {
  static OPEN = 1;
  static instances = [];

  constructor() {
    this.readyState = MockWebSocket.OPEN;
    MockWebSocket.instances.push(this);
    setTimeout(() => {
      this.onopen?.();
    }, 0);
  }

  send(data) {
    sentMessages.push(JSON.parse(data));
  }

  emit(payload) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }
};

describe('AssessmentPage tracking metadata', () => {
  const originalWebSocket = global.WebSocket;

  beforeEach(() => {
    vi.clearAllMocks();
    mockExecute.mockResolvedValue({ data: { success: true, stdout: '', stderr: '', error: null, results: [] } });
    mockSaveRepoFile.mockResolvedValue({ data: { success: true } });
    mockTerminalStatus.mockResolvedValue({ data: { state: 'running' } });
    mockTerminalStop.mockResolvedValue({ data: { success: true } });
    mockSubmit.mockResolvedValue({ data: { success: true } });
    mockClaude.mockResolvedValue({ data: { success: true, content: 'ok' } });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  afterEach(() => {
    global.WebSocket = originalWebSocket;
    vi.useRealTimers();
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
      fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]);
    });
    await screen.findByRole('dialog');
    await act(async () => {
      fireEvent.click(screen.getAllByRole('button', { name: 'Submit' }).at(-1));
    });

    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(1));
    expect(mockSubmit.mock.calls[0][3]).toMatchObject({ tab_switch_count: 1 });
  });

  it('shows only a simple submitted confirmation in demo task preview mode', async () => {
    const startData = {
      assessment_id: 26,
      token: null,
      time_remaining: 1800,
      task: {
        name: 'AWS Glue Pipeline Recovery',
        starter_code: 'print("hi")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage startData={startData} demoMode />);

    expect(await screen.findByText('Assessment brief')).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]);
    });
    await screen.findByRole('dialog');
    await act(async () => {
      fireEvent.click(screen.getAllByRole('button', { name: 'Submit' }).at(-1));
    });

    expect(await screen.findByRole('heading', { name: /Task submitted/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
    expect(screen.queryByText(/TAALI Demo Results/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Your TAALI profile/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Try another demo/i)).not.toBeInTheDocument();
    expect(mockSubmit).not.toHaveBeenCalled();
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

    expect(await screen.findByText('Assessment brief')).toBeInTheDocument();
    expect(screen.getAllByText(/migration left historical records incomplete/i).length).toBeGreaterThan(0);
    expect(await screen.findByText('Repository')).toBeInTheDocument();
    expect(screen.getByText('src')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^backfill\.py$/i })).toBeInTheDocument();
    expect(screen.getByText('README.md')).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Collapse repository/i }));
    });
    expect(screen.queryByRole('button', { name: /^backfill\.py$/i })).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Expand repository/i }));
    });
    expect(screen.getByRole('button', { name: /^backfill\.py$/i })).toBeInTheDocument();
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

    expect(await screen.findByText('Assessment brief')).toBeInTheDocument();
    expect(screen.getByText('Task context has not been provided yet.')).toBeInTheDocument();
    expect(screen.queryByText('Repository')).not.toBeInTheDocument();
  });


  it('shows working guidance and clone command in context window', async () => {
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

    expect(await screen.findByText('Assessment brief')).toBeInTheDocument();
    expect(screen.getByText(/Use Claude for scoped help, then validate the patch path yourself/i)).toBeInTheDocument();
    expect(screen.getByText(/Clone command:/i)).toBeInTheDocument();
    expect(screen.queryByText('exploration')).not.toBeInTheDocument();
    expect(screen.queryByText(/should never render/i)).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Collapse brief/i }));
    });
    expect(screen.queryByText(/Clone command:/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Clone command available/i)).toBeInTheDocument();
  });

  it('keeps candidate rubric and inferred tests out of the runtime context window', async () => {
    const startData = {
      assessment_id: 15,
      token: 'tok5',
      time_remaining: 1200,
      task: {
        name: 'Collapsible sections',
        scenario: 'Investigate and patch the backfill job.',
        starter_code: 'print("start")',
        duration_minutes: 30,
        rubric_categories: [
          { category: 'implementation_quality', weight: 0.5 },
        ],
        repo_structure: {
          files: {
            'src/job.py': 'def run_job():\n    pass',
            'tests/test_job.py': 'def test_run_job():\n    assert True',
          },
        },
      },
    };

    render(<AssessmentPage token="tok5" startData={startData} />);

    expect(await screen.findByText('Assessment brief')).toBeInTheDocument();
    expect(screen.getAllByText(/Investigate and patch the backfill job/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Use Claude for scoped help, then validate the patch path yourself/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Rubric$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Tests$/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Candidate-safe rubric/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/What to validate/i)).not.toBeInTheDocument();
  });

  it('renders terminal toggle in Claude CLI mode and initializes websocket', async () => {
    const sentMessages = [];
    const MockWebSocket = createMockWebSocketClass(sentMessages);
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

    const showTerminalButton = await screen.findByRole('button', { name: /^Terminal$/ });
    expect(showTerminalButton).toBeInTheDocument();
    expect(screen.queryByTestId('assessment-terminal')).not.toBeInTheDocument();
    await act(async () => {
      fireEvent.click(showTerminalButton);
    });
    expect(await screen.findByTestId('assessment-terminal')).toBeInTheDocument();
    expect(screen.getByText(/Workspace dock/i)).toBeInTheDocument();
    expect(screen.queryByText('send-claude')).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Collapse$/ }));
    });
    await waitFor(() => {
      expect(screen.queryByTestId('assessment-terminal')).not.toBeInTheDocument();
    });

    await waitFor(() => {
      expect(sentMessages.some((message) => message.type === 'init')).toBe(true);
    });
  });

  it('shows a clear success message when run finishes without stdout or stderr', async () => {
    const startData = {
      assessment_id: 22,
      token: 'tok-run-output',
      time_remaining: 1200,
      ai_mode: 'claude_cli_terminal',
      task: {
        name: 'Run output task',
        starter_code: 'answer = 42',
        duration_minutes: 30,
      },
    };

    mockExecute.mockResolvedValueOnce({
      data: {
        success: true,
        stdout: '',
        stderr: '',
        error: null,
        results: [],
      },
    });

    render(<AssessmentPage token="tok-run-output" startData={startData} />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Run$/i }));
    });

    await waitFor(() => expect(mockExecute).toHaveBeenCalledTimes(1));
    expect(mockExecute.mock.calls[0][1]).toMatchObject({
      code: 'answer = 42',
      selected_file_path: null,
      repo_files: [],
    });
    expect(await screen.findByText(/Code executed successfully\. No stdout\/stderr was produced\./i)).toBeInTheDocument();
  });

  it('routes Ask Claude through the terminal transport and mirrors the response back into chat', async () => {
    const sentMessages = [];
    const MockWebSocket = createMockWebSocketClass(sentMessages);
    global.WebSocket = MockWebSocket;

    const startData = {
      assessment_id: 27,
      token: 'tok-claude-repo',
      time_remaining: 1200,
      task: {
        name: 'Claude repo task',
        duration_minutes: 30,
        repo_structure: {
          files: {
            'src/main.py': 'print("hi")',
            'README.md': '# Demo repo',
          },
        },
      },
    };

    render(<AssessmentPage token="tok-claude-repo" startData={startData} />);

    const promptInput = screen.getByPlaceholderText(/Ask Claude, attach files with @, run a tool with \//i);
    fireEvent.change(promptInput, { target: { value: 'What files matter?' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Send/i }));
    });

    await waitFor(() => expect(mockSaveRepoFile).toHaveBeenCalledTimes(1));
    expect(mockSaveRepoFile.mock.calls[0][1]).toMatchObject({
      path: 'src/main.py',
      content: 'print("hi")',
    });
    await waitFor(() => {
      expect(sentMessages.some((message) => (
        message.type === 'claude_prompt'
        && message.message === 'What files matter?'
        && message.selected_file_path === 'src/main.py'
      ))).toBe(true);
    });
    expect(mockClaude).not.toHaveBeenCalled();

    const ws = MockWebSocket.instances.at(-1);
    await act(async () => {
      ws.emit({
        type: 'claude_chat_done',
        request_id: sentMessages.find((message) => message.type === 'claude_prompt')?.request_id,
        content: 'Start with `src/main.py`, then read `README.md` for task context.',
      });
    });

    expect(await screen.findByText(/Start with/i)).toBeInTheDocument();
    expect(screen.getByText('src/main.py')).toBeInTheDocument();
  }, 15000);

  it('surfaces a clear error if terminal-backed Claude stalls too long', async () => {
    vi.useFakeTimers();
    const sentMessages = [];
    const MockWebSocket = createMockWebSocketClass(sentMessages);
    global.WebSocket = MockWebSocket;

    const startData = {
      assessment_id: 30,
      token: 'tok-claude-stall',
      time_remaining: 1200,
      ai_mode: 'claude_cli_terminal',
      terminal_mode: true,
      terminal_capabilities: {
        permission_mode: 'default',
      },
      task: {
        name: 'Claude stall task',
        duration_minutes: 30,
        repo_structure: {
          files: {
            'src/main.py': 'print("hi")',
          },
        },
      },
    };

    render(<AssessmentPage token="tok-claude-stall" startData={startData} />);

    const promptInput = screen.getByPlaceholderText(/Ask Claude, attach files with @, run a tool with \//i);
    fireEvent.change(promptInput, { target: { value: 'Can you run the tests?' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Send/i }));
      vi.advanceTimersByTime(1);
    });

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(screen.getByText(/Still working in the live repo session/i)).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(35000);
    });
    expect(screen.getByText(/taking longer than expected in the live repo session/i)).toBeInTheDocument();
  });

  it('creates a new repo file and saves it into the live workspace', async () => {
    const startData = {
      assessment_id: 28,
      token: 'tok-new-file',
      time_remaining: 1200,
      task: {
        name: 'New file task',
        duration_minutes: 30,
        repo_structure: {
          files: {
            'src/main.py': 'print("hi")',
          },
        },
      },
    };

    render(<AssessmentPage token="tok-new-file" startData={startData} />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /New File/i }));
    });
    fireEvent.change(screen.getByPlaceholderText('src/new_file.py'), {
      target: { value: 'src/new_file.py' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Create$/i }));
    });

    expect(screen.getByText('new_file.py')).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Save$/i }));
    });

    await waitFor(() => expect(mockSaveRepoFile).toHaveBeenCalledTimes(1));
    expect(mockSaveRepoFile.mock.calls[0][1]).toMatchObject({
      path: 'src/new_file.py',
      content: '',
    });
  });

  it('surfaces runtime execution errors in the output dock', async () => {
    const startData = {
      assessment_id: 26,
      token: 'tok-run-error',
      time_remaining: 1200,
      ai_mode: 'claude_cli_terminal',
      task: {
        name: 'Run error task',
        starter_code: 'broken(',
        duration_minutes: 30,
      },
    };

    mockExecute.mockResolvedValueOnce({
      data: {
        success: false,
        stdout: '',
        stderr: 'Traceback (most recent call last)',
        error: 'SyntaxError: unexpected EOF while parsing',
        results: [],
      },
    });

    render(<AssessmentPage token="tok-run-error" startData={startData} />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Run$/i }));
    });

    await waitFor(() => expect(mockExecute).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/SyntaxError: unexpected EOF while parsing/i)).toBeInTheDocument();
    expect(screen.getByText(/Traceback \(most recent call last\)/i)).toBeInTheDocument();
  });

  it('initializes terminal websocket in demo mode when Claude CLI mode is enabled', async () => {
    const sentMessages = [];
    const MockWebSocket = createMockWebSocketClass(sentMessages);
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

    expect(await screen.findByRole('button', { name: /^Terminal$/ })).toBeInTheDocument();
    expect(screen.queryByTestId('assessment-terminal')).not.toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Terminal$/ }));
    });
    expect(await screen.findByTestId('assessment-terminal')).toBeInTheDocument();
    expect(screen.getByText(/Workspace dock/i)).toBeInTheDocument();
    expect(screen.queryByText('send-claude')).not.toBeInTheDocument();

    await waitFor(() => {
      expect(sentMessages.some((message) => message.type === 'init')).toBe(true);
    });
  });

  it('restarts the terminal session from the runtime header', async () => {
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
      assessment_id: 29,
      token: 'tok-terminal-restart',
      time_remaining: 1200,
      ai_mode: 'claude_cli_terminal',
      terminal_mode: true,
      terminal_capabilities: {
        permission_mode: 'default',
      },
      task: {
        name: 'Restartable terminal task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok-terminal-restart" startData={startData} />);

    fireEvent.click(await screen.findByRole('button', { name: /^Terminal$/ }));
    expect(await screen.findByRole('button', { name: /Restart terminal/i })).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Restart terminal/i }));
    });

    await waitFor(() => expect(mockTerminalStop).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(sentMessages.filter((message) => message.type === 'init').length).toBeGreaterThanOrEqual(2);
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
    const sentMessages = [];
    const MockWebSocket = createMockWebSocketClass(sentMessages);
    global.WebSocket = MockWebSocket;

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

    render(<AssessmentPage token="tok-claude-budget" startData={startData} />);

    expect(await screen.findByText(/\$1\.00 of \$1\.00/i)).toBeInTheDocument();

    const promptInput = screen.getByPlaceholderText(/Ask Claude, attach files with @, run a tool with \//i);
    fireEvent.change(promptInput, { target: { value: 'Help me debug' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Send/i }));
    });

    const ws = MockWebSocket.instances.at(-1);
    const requestId = sentMessages.find((message) => message.type === 'claude_prompt')?.request_id;
    await act(async () => {
      ws.emit({
        type: 'claude_chat_done',
        request_id: requestId,
        content: 'Use stronger filtering.',
        claude_budget: {
          enabled: true,
          limit_usd: 1,
          used_usd: 0.25,
          remaining_usd: 0.75,
          tokens_used: 1000,
          is_exhausted: false,
        },
      });
    });

    expect(await screen.findByText(/\$0\.75 of \$1\.00/i)).toBeInTheDocument();
  });

  it('shows higher precision Claude credit when prompt spend is below one cent', async () => {
    const sentMessages = [];
    const MockWebSocket = createMockWebSocketClass(sentMessages);
    global.WebSocket = MockWebSocket;

    const startData = {
      assessment_id: 24,
      token: 'tok-claude-precision',
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
        name: 'Budget precision task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok-claude-precision" startData={startData} />);

    const promptInput = screen.getByPlaceholderText(/Ask Claude, attach files with @, run a tool with \//i);
    fireEvent.change(promptInput, { target: { value: 'Help me debug' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Send/i }));
    });

    const ws = MockWebSocket.instances.at(-1);
    const requestId = sentMessages.find((message) => message.type === 'claude_prompt')?.request_id;
    await act(async () => {
      ws.emit({
        type: 'claude_chat_done',
        request_id: requestId,
        content: 'Use the stack trace to narrow the failing branch.',
        claude_budget: {
          enabled: true,
          limit_usd: 1,
          used_usd: 0.0016,
          remaining_usd: 0.9984,
          tokens_used: 620,
          is_exhausted: false,
        },
      });
    });

    expect(await screen.findByText(/\$0\.9984 of \$1\.00/i)).toBeInTheDocument();
  });

  it('strips leaked Claude tool markup from chat responses before rendering', async () => {
    const sentMessages = [];
    const MockWebSocket = createMockWebSocketClass(sentMessages);
    global.WebSocket = MockWebSocket;

    const startData = {
      assessment_id: 25,
      token: 'tok-claude-sanitize',
      time_remaining: 1200,
      task: {
        name: 'Claude sanitize task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok-claude-sanitize" startData={startData} />);

    const promptInput = screen.getByPlaceholderText(/Ask Claude, attach files with @, run a tool with \//i);
    fireEvent.change(promptInput, { target: { value: 'Help me debug' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Send/i }));
    });

    const ws = MockWebSocket.instances.at(-1);
    const requestId = sentMessages.find((message) => message.type === 'claude_prompt')?.request_id;
    await act(async () => {
      ws.emit({
        type: 'claude_chat_done',
        request_id: requestId,
        content: [
          "Let me start by understanding the situation. I'll review the key files first.",
          '',
          '<read_file>',
          '<path>diagnostics/audit_findings.md</path>',
          '</read_file>',
        ].join('\n'),
      });
    });

    expect(await screen.findByText(/Let me start by understanding the situation/i)).toBeInTheDocument();
    expect(screen.queryByText(/<read_file>/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/diagnostics\/audit_findings\.md/i)).not.toBeInTheDocument();
  });

  it('defaults the assessment runtime to light mode', async () => {
    const startData = {
      assessment_id: 24,
      token: 'tok-theme',
      time_remaining: 1200,
      task: {
        name: 'Theme toggle task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok-theme" startData={startData} />);

    expect((await screen.findAllByText('Theme toggle task')).length).toBeGreaterThan(0);
    expect(localStorage.getItem('taali_assessment_theme')).toBe('light');
  });

});
