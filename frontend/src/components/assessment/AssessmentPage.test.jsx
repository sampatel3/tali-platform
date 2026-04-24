import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

describe('Assessment runtime redesign', () => {
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

  it('sends tab switch metadata when the assessment is submitted', async () => {
    render(
      <AssessmentPage
        token="tok"
        startData={{
          assessment_id: 10,
          token: 'tok',
          time_remaining: 1800,
          task: {
            name: 'Debug task',
            starter_code: 'print("hi")',
            duration_minutes: 30,
            proctoring_enabled: true,
          },
        }}
      />,
    );

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    });

    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await screen.findByText('This has been recorded.');

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    });
    await screen.findByRole('dialog');

    await act(async () => {
      fireEvent.click(screen.getAllByRole('button', { name: 'Submit' }).at(-1));
    });

    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(1));
    expect(mockSubmit.mock.calls[0][3]).toMatchObject({ tab_switch_count: 1 });
  });

  it('renders the redesigned live assessment context and repository shell', async () => {
    render(
      <AssessmentPage
        token="tok2"
        startData={{
          assessment_id: 12,
          token: 'tok2',
          time_remaining: 1200,
          clone_command: 'git clone --branch assessment/12 mock://repo',
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
        }}
      />,
    );

    expect(await screen.findByText('Live assessment context')).toBeInTheDocument();
    expect(screen.getAllByText(/A migration left historical records incomplete/i).length).toBe(2);
    expect(screen.getByText(/Clone command:/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Repository/i })).toBeInTheDocument();
    expect(screen.getByText('src/')).toBeInTheDocument();
    expect(screen.getByText('backfill.py')).toBeInTheDocument();
    expect(screen.getByText('README.md')).toBeInTheDocument();
  });

  it('lets the reviewer collapse and expand context details in the redesigned panel', async () => {
    render(
      <AssessmentPage
        token="tok3"
        startData={{
          assessment_id: 15,
          token: 'tok3',
          time_remaining: 1200,
          task: {
            name: 'Collapsible sections',
            scenario: 'Investigate and patch the backfill job.',
            starter_code: 'print("start")',
            duration_minutes: 30,
          },
        }}
      />,
    );

    expect(await screen.findByText('Task context')).toBeInTheDocument();
    expect(screen.getByText('Working rules')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Context' }));
    fireEvent.click(screen.getByRole('button', { name: 'Instructions' }));

    await waitFor(() => {
      expect(screen.queryByText('Task context')).not.toBeInTheDocument();
      expect(screen.queryByText('Working rules')).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Context' }));
    fireEvent.click(screen.getByRole('button', { name: 'Instructions' }));

    await waitFor(() => {
      expect(screen.getByText('Task context')).toBeInTheDocument();
      expect(screen.getByText('Working rules')).toBeInTheDocument();
    });
  });

  it('opens the terminal panel and initializes the websocket in Claude CLI mode', async () => {
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

    render(
      <AssessmentPage
        token="tok-terminal"
        startData={{
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
        }}
      />,
    );

    expect(await screen.findByRole('button', { name: /Show Terminal/i })).toBeInTheDocument();
    expect(screen.queryByTestId('assessment-terminal')).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Show Terminal/i }));
    });

    expect(await screen.findByTestId('assessment-terminal')).toBeInTheDocument();

    await waitFor(() => {
      expect(sentMessages.some((message) => message.type === 'init')).toBe(true);
    });
  });
});
